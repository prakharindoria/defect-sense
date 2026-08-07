"""Platform ports: persistence, events, notification, packs, time.

`UseCasePackRepository` is the one that carries architectural weight. Loading a
pack through a port -- rather than reading YAML wherever it is convenient -- is
what makes "adding an inspection task is a folder, not a project" a property of
the design instead of a claim. Paired with the pack-isolation architecture test,
it is the strongest available answer to a question about scale.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003 - dataclass field type; see llm.py
from typing import TYPE_CHECKING, Any

from forge.domain.cost import CostModel
from forge.domain.torque import SignatureBaselineSpec

if TYPE_CHECKING:
    # Used only in method signatures, so these stay lazy.
    from collections.abc import AsyncIterator, Sequence

    from forge.domain.state import QCState


# ---------------------------------------------------------------------------
# Use Case Packs
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DefectClass:
    id: str
    label: str
    severity: str
    detected_by: tuple[str, ...]
    description: str = ""
    # True for the held-out class that is never seeded into any bank or exemplar
    # set. It exists to measure unseen-defect detection honestly.
    is_holdout: bool = False


@dataclass(frozen=True, slots=True)
class VerifierSpec:
    """A deterministic geometric check declared by the pack.

    These are exact, cheap and explainable. A neural net is not asked to do
    arithmetic.
    """

    name: str
    kind: str                       # "count" | "diameter" | "spacing" | "presence" | "flushness"
    expected: float
    tolerance: float
    unit: str = ""
    severity_on_fail: str = "critical"


@dataclass(frozen=True, slots=True)
class PackManifest:
    id: str
    name: str
    version: str
    station: str
    component: str
    cycle_time_seconds: float
    # How many discrete features this station inspects -- fasteners on a wheel,
    # weld points on a body panel. Deliberately generic: the architecture test
    # rejected a `fastener_count` field here, and it was right to. The specific
    # count and what it counts are declared by the pack's verifier specs.
    inspection_points: int = 0


@dataclass(frozen=True, slots=True)
class UseCasePack:
    """Everything that makes one inspection task different from another.

    Swapping this object swaps the product's behaviour with zero code change.
    """

    manifest: PackManifest
    taxonomy: tuple[DefectClass, ...]
    verifiers: tuple[VerifierSpec, ...]
    cost_model: CostModel
    signature_spec: SignatureBaselineSpec
    prompt_overrides: dict[str, str] = field(default_factory=dict)
    knowledge_dir: str = ""

    def defect_class(self, class_id: str) -> DefectClass | None:
        return next((d for d in self.taxonomy if d.id == class_id), None)

    @property
    def holdout_classes(self) -> tuple[DefectClass, ...]:
        return tuple(d for d in self.taxonomy if d.is_holdout)


class UseCasePackRepository(ABC):
    @abstractmethod
    def list_packs(self) -> tuple[PackManifest, ...]: ...

    @abstractmethod
    def load(self, pack_id: str) -> UseCasePack: ...

    @abstractmethod
    def active(self) -> UseCasePack: ...

    @abstractmethod
    async def activate(self, pack_id: str) -> UseCasePack:
        """Swap the active pack. Must complete in under 10 seconds."""


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class InspectionSummary:
    """List-view projection. Loading full QCState for a table would be wasteful."""

    correlation_id: str
    unit_id: str
    pack_id: str
    status: str
    verdict: str | None
    severity: str | None
    fusion_only: bool
    created_at: datetime
    latency_ms: int
    is_synthetic: bool = True


class InspectionRepository(ABC):
    """Persistence for QCState.

    Every transition is saved, not just the terminal one. That single stored
    sequence is the audit log, the Agent Console feed, the eval harness input,
    and the replay source -- so partial persistence would silently break four
    capabilities at once.
    """

    @abstractmethod
    async def save(self, state: QCState) -> None: ...

    @abstractmethod
    async def get(self, correlation_id: str) -> QCState | None: ...

    @abstractmethod
    async def list(
        self,
        *,
        pack_id: str | None = None,
        verdict: str | None = None,
        fusion_only: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[InspectionSummary], int]:
        """Returns (page, total_count)."""


@dataclass(frozen=True, slots=True)
class AuditEntry:
    actor: str
    role: str
    action: str
    resource: str
    correlation_id: str
    at: datetime
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    ip: str = ""


class AuditLog(ABC):
    @abstractmethod
    async def record(self, entry: AuditEntry) -> None: ...

    @abstractmethod
    async def query(
        self, *, actor: str | None = None, action: str | None = None, limit: int = 100
    ) -> Sequence[AuditEntry]: ...


# ---------------------------------------------------------------------------
# Events and notification
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Event:
    topic: str
    correlation_id: str
    payload: dict[str, Any]
    at: datetime


class EventBusPort(ABC):
    @abstractmethod
    async def publish(self, event: Event) -> None: ...

    @abstractmethod
    def subscribe(self, *topics: str) -> AsyncIterator[Event]: ...


@dataclass(frozen=True, slots=True)
class Notification:
    title: str
    body: str
    severity: str
    correlation_id: str
    # Populated when the notification carries an approval decision. The buttons
    # resolve the same interrupt() the UI does -- one gate, two front doors.
    approval_gate: str | None = None
    link: str = ""


class NotifierPort(ABC):
    @abstractmethod
    async def notify(self, notification: Notification) -> bool:
        """Returns whether it was actually delivered.

        A notifier that cannot reach its channel returns False and the caller
        records the degradation. It never pretends to have sent something.
        """


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------
class ClockPort(ABC):
    """Injected so DEMO_MODE can pin time and tests can control it.

    Anything reading the wall clock directly is untestable and unreplayable.
    """

    @abstractmethod
    def now(self) -> datetime: ...

    @abstractmethod
    def monotonic_ms(self) -> int: ...
