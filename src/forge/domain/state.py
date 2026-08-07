"""QCState and the per-agent verdicts. FROZEN CONTRACT -- CLAUDE.md rule 1.

Every agent reads and writes this structure, every transition is persisted, and
that one persisted table is simultaneously:

    the audit log          who/what decided, with the exact inputs they saw
    the Agent Console feed  /agents renders `trace` directly
    the eval harness input  `make eval` replays stored states
    the replay feature      re-run a past event through a changed graph

One table, four capabilities. Persisting less than the whole state would break
all four, so `trace` is append-only and no node may mutate another node's slot.

Modelled with Pydantic rather than TypedDict so the boundary validates itself.
Pydantic is the one third-party import the domain layer permits -- it is a data
modelling library, not a framework, and it performs no I/O. The architecture
test enforces that allowance explicitly rather than leaving it to convention.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from forge.domain.enums import (
    AgentName,
    DataQuality,
    DegradationKind,
    Disposition,
    GuardrailKind,
    HitlDecision,
    HitlGate,
    Role,
    Severity,
    SignalKind,
    Verdict,
)
from forge.domain.provenance import Citation, Confidence, Provenance

Score = Annotated[float, Field(ge=0.0, le=1.0)]


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------
class TraceSpan(BaseModel):
    """One agent node execution. Append-only; this IS the audit record."""

    model_config = ConfigDict(frozen=True)

    agent: AgentName
    started_at: datetime
    duration_ms: int
    ok: bool = True
    # Human-readable one-liner shown on the Agent Console node.
    summary: str = ""
    tools_called: tuple[str, ...] = ()
    prompt_version: str | None = None
    provenance: Provenance | None = None
    retry_count: int = 0
    degradations: tuple[DegradationKind, ...] = ()
    error: str | None = None

    @model_validator(mode="after")
    def _failures_explain_themselves(self) -> Self:
        if not self.ok and not self.error:
            raise ValueError("a failed span must record why it failed")
        return self


class BudgetLedger(BaseModel):
    """Per-request token, cost and latency budget.

    The Orchestrator enforces this. Exceeding it downgrades the offending node
    to the fast tier and annotates `budget_exceeded` rather than silently
    running long or silently truncating.
    """

    max_tokens: int = 20_000
    max_cost_usd: float = 0.25
    max_latency_ms: int = 4_000

    tokens_used: int = 0
    cost_usd: float = 0.0
    elapsed_ms: int = 0

    @property
    def tokens_remaining(self) -> int:
        return max(self.max_tokens - self.tokens_used, 0)

    @property
    def exhausted(self) -> bool:
        return (
            self.tokens_used >= self.max_tokens
            or self.cost_usd >= self.max_cost_usd
            or self.elapsed_ms >= self.max_latency_ms
        )


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
class InspectionEvent(BaseModel):
    """What arrives at the station. The trigger for one graph run."""

    model_config = ConfigDict(frozen=True)

    unit_id: str                       # VIN or serial; tracked across frames
    station_id: str
    pack_id: str
    frame_uris: tuple[str, ...] = ()
    torque_curve_ids: tuple[str, ...] = ()
    operator_token: str = ""           # already tokenised; never a raw operator ID
    occurred_at: datetime = Field(default_factory=_now)
    is_synthetic: bool = True


# ---------------------------------------------------------------------------
# Per-signal verdicts
# ---------------------------------------------------------------------------
class VerifierResult(BaseModel):
    """A deterministic geometric check. Exact, cheap, and fully explainable.

    These do not have a confidence: counting five fasteners either finds five or
    it does not. Do not make a neural net do arithmetic.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool
    expected: str
    observed: str
    tolerance: str = ""


class FrameVerdict(BaseModel):
    """Per-frame vision result, aggregated temporally before it counts."""

    model_config = ConfigDict(frozen=True)

    frame_index: int
    anomaly_score: Score
    heatmap_uri: str | None = None
    latency_ms: int = 0


class VisionVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    anomaly_score: Score
    confidence: Confidence
    frames_evaluated: int = 1
    frames_anomalous: int = 0
    # A defect fires only if it persists across consecutive frames on the same
    # tracked unit. This is the false-positive killer -- glare and motion blur
    # do not survive it -- and it is why the video pipeline earns its cost.
    temporal_consensus: bool = False
    verifiers: tuple[VerifierResult, ...] = ()
    matched_class: str | None = None
    match_similarity: float | None = None
    description: str = ""
    heatmap_uri: str | None = None
    provenance: Provenance

    @property
    def is_novel(self) -> bool:
        """Anomalous but matching no known exemplar -> a learning event, not a guess."""
        return self.anomaly_score > 0.5 and self.matched_class is None

    @property
    def failed_verifiers(self) -> tuple[VerifierResult, ...]:
        return tuple(v for v in self.verifiers if not v.passed)


class FastenerResult(BaseModel):
    """One fastener position's torque-angle analysis."""

    model_config = ConfigDict(frozen=True)

    position: int
    final_torque_nm: float
    knee_angle_deg: float
    elastic_slope_nm_per_deg: float
    anomaly_score: Score
    endpoint_in_spec: bool
    signature_anomalous: bool
    deviations: tuple[str, ...] = ()
    likely_class: str | None = None

    @property
    def fusion_only(self) -> bool:
        """The endpoint passes but the shape does not. THE case."""
        return self.endpoint_in_spec and self.signature_anomalous


class ProcessVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    fasteners: tuple[FastenerResult, ...]
    anomaly_score: Score
    confidence: Confidence
    drift_detected: bool = False
    # Forecast risk for the next N units. Detection is reactive; this is not.
    forecast_risk: Score | None = None
    forecast_horizon_units: int = 0
    provenance: Provenance

    @property
    def fusion_only_positions(self) -> tuple[int, ...]:
        return tuple(f.position for f in self.fasteners if f.fusion_only)


class MESContext(BaseModel):
    """Order spec and station context pulled from the MES."""

    model_config = ConfigDict(frozen=True)

    work_order: str = ""
    job_card: str = ""
    bom_variant: str = ""
    batch: str = ""
    material_lot: str = ""
    workstation: str = ""
    tool_id: str = ""
    tool_age_cycles: int = 0
    tool_days_since_calibration: int = 0
    shift: str = ""
    # Live ambient conditions. Humidity is not decoration: it drives thread
    # surface condition, which is the mechanism behind the fusion case.
    ambient_temperature_c: float | None = None
    ambient_humidity_pct: float | None = None
    is_stale: bool = False
    provenance: Provenance


# ---------------------------------------------------------------------------
# Adjudication and downstream
# ---------------------------------------------------------------------------
class FusionVerdict(BaseModel):
    """The Adjudicator's output. Reconciles signals that disagree."""

    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    confidence: Confidence
    severity: Severity
    reasoning: str
    primary_signal: SignalKind
    # True when NO single signal would have caught this. The metric reported as
    # fusion_only_detection_rate, and the entire argument for multi-agent.
    fusion_only: bool = False
    defect_classes: tuple[str, ...] = ()
    reliability_notes: dict[str, str] = Field(default_factory=dict)
    escalation_reason: str | None = None
    provenance: Provenance

    @model_validator(mode="after")
    def _escalation_is_explained(self) -> Self:
        if self.verdict is Verdict.ESCALATE and not self.escalation_reason:
            raise ValueError(
                "escalation must state its reason -- escalating is a correct outcome, "
                "but an unexplained one is not actionable"
            )
        return self


class Hypothesis(BaseModel):
    model_config = ConfigDict(frozen=True)

    cause: str
    confidence: Confidence
    # FACT (observed or retrieved) is kept separate from INFERENCE (reasoning).
    # Collapsing the two is how a plausible story becomes an unfounded one.
    facts: tuple[str, ...] = ()
    inferences: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()
    would_confirm: str = ""
    would_refute: str = ""


class RootCauseReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    hypotheses: tuple[Hypothesis, ...] = ()
    groundedness: Score = 0.0
    retrieval_rounds: int = 1
    # "Insufficient evidence" is a correct and valuable answer.
    insufficient_evidence: bool = False
    provenance: Provenance

    @model_validator(mode="after")
    def _claims_are_cited(self) -> Self:
        for h in self.hypotheses:
            if h.confidence >= 0.4 and not h.citations and not h.facts:
                raise ValueError(
                    f"hypothesis '{h.cause}' claims {h.confidence:.2f} confidence with no "
                    f"citation and no observed fact; uncited claims are stripped"
                )
        return self


class TriageResult(BaseModel):
    """Cost Triage output. See forge.domain.cost for the engine."""

    model_config = ConfigDict(frozen=True)

    recommended: Disposition
    expected_cost: float
    cost_low: float
    cost_high: float
    currency: str = "INR"
    cost_usd: float | None = None
    fx_rate_age_seconds: int | None = None
    alternatives: tuple[tuple[Disposition, float], ...] = ()
    assumptions: tuple[str, ...] = ()
    requires_human: bool = False
    human_reason: str = ""
    provenance: Provenance


class GuardrailEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: GuardrailKind
    blocked: bool
    detail: str
    detected_at: datetime = Field(default_factory=_now)


class GuardrailReport(BaseModel):
    """Fail closed. An empty report means the checks ran and found nothing --
    never that they were skipped."""

    model_config = ConfigDict(frozen=True)

    checks_run: tuple[GuardrailKind, ...] = ()
    events: tuple[GuardrailEvent, ...] = ()
    passed: bool = True

    @model_validator(mode="after")
    def _blocking_event_fails_the_report(self) -> Self:
        if self.passed and any(e.blocked for e in self.events):
            raise ValueError("a report containing a blocking event cannot be marked passed")
        return self


class ActionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: str                        # "mes.create_qi", "slack.notify", ...
    succeeded: bool
    idempotency_key: str
    external_ref: str | None = None    # e.g. the ERPNext Quality Inspection name
    attempts: int = 1
    sent_to_dlq: bool = False
    detail: str = ""
    at: datetime = Field(default_factory=_now)


class HitlRequest(BaseModel):
    """A pause for a human. Resolvable identically from the UI or from Slack."""

    model_config = ConfigDict(frozen=True)

    gate: HitlGate
    approver_roles: tuple[Role, ...]
    question: str
    timeout_seconds: int | None = None
    # What happens on timeout. Always the conservative option, never "proceed".
    on_timeout: Disposition | None = None
    requested_at: datetime = Field(default_factory=_now)

    decision: HitlDecision | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    # MODIFY is the one that matters: it feeds the Learning Agent.
    modification: str | None = None

    @property
    def is_pending(self) -> bool:
        return self.decision is None


# ---------------------------------------------------------------------------
# The state
# ---------------------------------------------------------------------------
class QCState(BaseModel):
    """The complete state of one inspection run through the agent graph."""

    model_config = ConfigDict(validate_assignment=True)

    correlation_id: str
    pack_id: str
    unit_id: str
    event: InspectionEvent

    data_quality: DataQuality = DataQuality.GOOD
    data_quality_reasons: tuple[str, ...] = ()

    frames: list[FrameVerdict] = Field(default_factory=list)
    vision: VisionVerdict | None = None
    process: ProcessVerdict | None = None
    context: MESContext | None = None
    fusion: FusionVerdict | None = None
    root_cause: RootCauseReport | None = None
    triage: TriageResult | None = None
    guardrail: GuardrailReport = Field(default_factory=GuardrailReport)
    actions: list[ActionRecord] = Field(default_factory=list)
    hitl: HitlRequest | None = None

    trace: list[TraceSpan] = Field(default_factory=list)
    retries: dict[str, int] = Field(default_factory=dict)
    budget: BudgetLedger = Field(default_factory=BudgetLedger)
    degradations: list[DegradationKind] = Field(default_factory=list)

    status: str = "ingesting"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    # -- derived -----------------------------------------------------------
    @property
    def total_latency_ms(self) -> int:
        return sum(s.duration_ms for s in self.trace)

    @property
    def is_degraded(self) -> bool:
        return bool(self.degradations)

    @property
    def fusion_only(self) -> bool:
        return bool(self.fusion and self.fusion.fusion_only)

    @property
    def awaiting_human(self) -> bool:
        return self.hitl is not None and self.hitl.is_pending

    def record(self, span: TraceSpan) -> None:
        """Append a span and roll its degradations up to the run.

        The only supported way to advance the trace. Degradations recorded on a
        span must surface at run level too, or a node could degrade without the
        UI ever showing it -- the exact silent failure CLAUDE.md rule 4 forbids.
        """
        self.trace.append(span)
        for d in span.degradations:
            if d not in self.degradations:
                self.degradations.append(d)
        if span.retry_count:
            self.retries[span.agent.value] = span.retry_count
        self.updated_at = _now()
