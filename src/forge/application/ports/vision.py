"""Vision ports.

Two deliberately separate abstractions, because they answer different questions
and have very different reliability:

  `GeometricVerifierPort`  "does this match the specification?"
                           Deterministic, exact, explainable. Counting five
                           fasteners either finds five or it does not, so results
                           carry NO confidence -- attaching one would imply a
                           doubt that does not exist. v1 ships this.

  `AnomalyScorerPort`      "does this look unlike anything normal?"
                           Learned, approximate, and the only thing that can
                           catch a defect class nobody has specified. v2 ships
                           this (PatchCore memory bank + FAISS).

Keeping them apart is what lets v1 be honest about what it does and does not
detect, and it is why adding the learned scorer in v2 changes no existing code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from forge.application.ports.platform import VerifierSpec
    from forge.domain.state import VerifierResult


@dataclass(frozen=True, slots=True)
class Frame:
    """One inspection image, already normalised by the ingestion stage."""

    image_bytes: bytes
    width: int
    height: int
    frame_index: int = 0
    unit_id: str = ""
    uri: str = ""
    is_synthetic: bool = True


@dataclass(frozen=True, slots=True)
class RegionOfInterest:
    """Canonical crop the analysis runs on.

    Never run the network on a full frame -- ROI extraction is what makes the
    latency budget achievable, on a GPU or without one.
    """

    frame: Frame
    center_x: int
    center_y: int
    radius_px: int
    rotation_deg: float          # normalised on a fiducial feature
    found: bool = True
    detail: str = ""


class ROIExtractorPort(ABC):
    @abstractmethod
    def extract(self, frame: Frame) -> RegionOfInterest:
        """Locate the component and return a canonical, rotation-normalised crop.

        Must report `found=False` with a reason rather than returning a
        misaligned crop. A confident verdict on the wrong region of the image is
        worse than no verdict.
        """


class GeometricVerifierPort(ABC):
    """Deterministic checks declared by the active pack. v1."""

    @abstractmethod
    def run(
        self, roi: RegionOfInterest, specs: Sequence[VerifierSpec]
    ) -> tuple[VerifierResult, ...]:
        """Run every spec and return one result each.

        Results are exact measurements against declared tolerances. A verifier
        that cannot be evaluated (ROI not found, occlusion) must fail with a
        stated reason -- never pass by default.
        """


@dataclass(frozen=True, slots=True)
class AnomalyMap:
    """Per-patch distances to the nearest normal patch, plus the image score."""

    image_score: float           # 99th percentile patch distance, robust to noise
    patch_scores: tuple[float, ...]
    grid_width: int
    grid_height: int
    heatmap_uri: str | None = None
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class ExemplarMatch:
    class_id: str
    similarity: float


class AnomalyScorerPort(ABC):
    """Learned anomaly detection against a memory bank of normal images. v2."""

    @abstractmethod
    async def score(self, roi: RegionOfInterest) -> AnomalyMap: ...

    @abstractmethod
    async def nearest_exemplar(self, roi: RegionOfInterest) -> ExemplarMatch | None:
        """Closest labelled exemplar, or None below the similarity floor.

        Returning None is the correct answer for a novel defect. It routes to a
        human as UNKNOWN rather than being forced into the nearest known class,
        because a confidently wrong class name is worse than an admission.
        """

    @abstractmethod
    async def append_exemplar(self, roi: RegionOfInterest, class_id: str) -> None:
        """Register a newly named class. The learning loop's write path."""

    @abstractmethod
    def bank_stats(self) -> dict[str, int | float]:
        """Size, coreset ratio, resident bytes -- shown on /packs."""
