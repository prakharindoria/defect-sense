"""Provenance -- where every displayed number actually came from.

FROZEN CONTRACT. CLAUDE.md rule 5: provenance on every AI output, no exceptions.

In a quality-control product, *where did this number come from* is not metadata,
it is the job. An inspector who cannot tell a measured torque reading from a
model's estimate from a language model's paraphrase cannot do their job, and a
regulator will not accept the record.

This renders as the app's signature UI element, a hairline mono strip under
every panel showing an AI-derived value:

    VISION-patchcore-r18   conf 0.94   38ms   5.0fps   live   pack:wheel

The rule that makes it useful is that it is never optional. A panel with no
provenance strip is a bug, and `tests/integration/test_provenance_coverage.py`
asserts every AI-derived field in every API response carries one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from forge.domain.enums import DegradationKind, ModelTier, ProvenanceSource

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class Citation(BaseModel):
    """A pointer into the retrieval corpus, resolvable by the UI to the source text.

    Every causal claim the Root Cause agent makes must carry one of these or a
    direct sensor observation. Uncited claims are stripped before display --
    see the groundedness check in the agent layer.
    """

    model_config = ConfigDict(frozen=True)

    doc_id: str
    chunk_id: str
    title: str
    # Verbatim supporting span. Kept short: it is evidence, not content.
    quote: str = Field(max_length=400)
    score: float | None = None
    source_kind: Literal["sop", "standard", "manual", "incident", "external_api"] = "sop"
    url: str | None = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Provenance(BaseModel):
    """Attribution for a single displayed value.

    `source` is the field that matters most. MEASURED means an instrument or a
    deterministic verifier produced it and it is a fact. MODEL and LLM mean
    something inferred it, and the confidence is a claim about that inference,
    not about physical reality.
    """

    model_config = ConfigDict(frozen=True)

    source: ProvenanceSource
    # Component that produced it: "patchcore-r18", "torque-signature/1.0",
    # "open-meteo", "verifiers.fastener_count". NOT a raw model ID for LLM
    # calls -- that goes in model_id, and the tier is what callers reason about.
    producer: str
    confidence: Confidence | None = None
    latency_ms: int | None = None

    # LLM-specific. model_id is recorded for audit but is never chosen in code
    # (CLAUDE.md rule 7) -- it is whatever the tier's chain resolved to.
    tier: ModelTier | None = None
    model_id: str | None = None
    tokens: TokenUsage | None = None
    prompt_version: str | None = None

    # Honesty fields. A value served from cache or produced under degradation is
    # still shown, but it is never shown as though it were fresh and nominal.
    degradations: tuple[DegradationKind, ...] = ()
    cache_age_seconds: int | None = None
    is_synthetic: bool = True
    citations: tuple[Citation, ...] = ()
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _require_honesty(self) -> Self:
        """Structural guarantees that keep the strip trustworthy."""
        if self.source is ProvenanceSource.LLM and self.tier is None:
            raise ValueError("LLM-sourced values must record which tier produced them")
        if self.source is ProvenanceSource.CACHED and self.cache_age_seconds is None:
            raise ValueError("cached values must record their age; stale data must look stale")
        if self.source is ProvenanceSource.RETRIEVED and not self.citations:
            raise ValueError("retrieved values must carry at least one citation")
        if self.source is ProvenanceSource.MEASURED and self.confidence is not None:
            raise ValueError(
                "measured values must not carry a confidence -- a sensor reading is a fact, "
                "and attaching a pseudo-confidence to it invites exactly the confusion "
                "this class exists to prevent"
            )
        return self

    @property
    def is_degraded(self) -> bool:
        return bool(self.degradations)

    def strip(self) -> str:
        """The one-line mono footer rendered under the panel."""
        parts = [f"{self.source.value.upper()}-{self.producer}"]
        if self.confidence is not None:
            parts.append(f"conf {self.confidence:.2f}")
        if self.latency_ms is not None:
            parts.append(f"{self.latency_ms}ms")
        if self.tokens and self.tokens.total_tokens:
            parts.append(f"{self.tokens.total_tokens}tok ${self.tokens.cost_usd:.4f}")
        if self.cache_age_seconds is not None:
            parts.append(f"cached {self.cache_age_seconds}s")
        parts.append("degraded" if self.is_degraded else "live")
        if self.is_synthetic:
            parts.append("SYNTHETIC")
        return "  ".join(parts)


class Evidenced[T](BaseModel):
    """A value bound to its provenance.

    Use this instead of a bare field wherever the number reaches a human. It
    makes "display this without saying where it came from" impossible to express
    rather than merely discouraged.
    """

    model_config = ConfigDict(frozen=True)

    value: T
    provenance: Provenance


def measured(value: float, producer: str, latency_ms: int | None = None) -> Evidenced[float]:
    """A sensor reading or deterministic verifier result. A fact, not an estimate."""
    return Evidenced(
        value=value,
        provenance=Provenance(
            source=ProvenanceSource.MEASURED, producer=producer, latency_ms=latency_ms
        ),
    )
