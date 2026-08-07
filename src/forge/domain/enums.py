"""Domain vocabulary. FROZEN CONTRACT -- see CLAUDE.md rule 1.

Every value here appears in a database column, an API response, a UI label, or
an agent prompt. Renaming one is a breaking change; append instead, and record
the request in docs/CONTRACT_CHANGE_REQUESTS.md.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """The three personas. Mirrors config/rbac.yaml -- kept in sync by a test.

    Deliberately three, not five. An earlier draft split quality authority
    across inspector / supervisor / quality-manager, which is how a real plant
    is organised but which produces three dashboards a demo cannot distinguish.
    QA holds the whole quality authority here.

    The separation that actually matters is preserved: ADMIN runs the platform
    and cannot rule on quality; QA rules on quality and cannot change the
    platform. A judge asking "can your admin rubber-stamp a defect?" gets "no,
    and here is the test."
    """

    SHOP_FLOOR_WORKER = "SHOP_FLOOR_WORKER"
    QA = "QA"
    ADMIN = "ADMIN"


class Severity(StrEnum):
    """Consequence class of a defect, not a probability.

    CRITICAL means a wheel-off or loss-of-control failure mode. It is the reason
    the autonomy ceiling exists: FORGE may reject a critical part on its own but
    may never pass one.
    """

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    COSMETIC = "cosmetic"


class Verdict(StrEnum):
    """Terminal outcome of an inspection.

    ESCALATE is a first-class correct outcome, never an error (CLAUDE.md rule 6).
    A system that never escalates is a system that guesses.
    """

    PASS = "pass"  # noqa: S105 - an inspection outcome, not a credential
    DEFECT = "defect"
    ESCALATE = "escalate"


class Disposition(StrEnum):
    """What physically happens to the unit. Distinct from the verdict.

    A DEFECT verdict may end in REWORK or SCRAP; the Cost Triage agent chooses
    between them on expected cost, and HALT always requires a human.
    """

    ACCEPT = "accept"
    REWORK = "rework"
    QUARANTINE = "quarantine"
    SCRAP = "scrap"
    HALT_LINE = "halt_line"


class SignalKind(StrEnum):
    """Which independent inspection signal produced an observation.

    The whole thesis lives in the fact that these disagree. Keep them separable
    all the way to the UI so the Adjudicator's reasoning can be audited.
    """

    VISION = "vision"
    PROCESS = "process"
    SPEC = "spec"
    FUSION = "fusion"
    NONE = "none"


class DataQuality(StrEnum):
    """Ingestion gate outcome. DEGRADED still flows, but every downstream
    confidence is annotated and the UI says why."""

    GOOD = "good"
    DEGRADED = "degraded"
    REJECTED = "rejected"


class RunStatus(StrEnum):
    """LangGraph run lifecycle. Persisted on every transition; this is the
    audit log, the Agent Console feed, and the replay source."""

    INGESTING = "ingesting"
    ANALYZING = "analyzing"
    ADJUDICATING = "adjudicating"
    AWAITING_HUMAN = "awaiting_human"
    ACTING = "acting"
    COMPLETE = "complete"
    FAILED = "failed"


class AgentName(StrEnum):
    """The eleven agents. Used as trace span names and Prometheus label values."""

    ORCHESTRATOR = "orchestrator"
    INGESTION = "ingestion"
    VISION_INSPECTOR = "vision_inspector"
    PROCESS_SENTINEL = "process_sentinel"
    CONTEXT = "context"
    ADJUDICATOR = "adjudicator"
    ROOT_CAUSE = "root_cause"
    COST_TRIAGE = "cost_triage"
    GUARDIAN = "guardian"
    ACTION = "action"
    LEARNING = "learning"
    ANALYST = "analyst"


class ModelTier(StrEnum):
    """Tiers declared in config/models.yaml. Never a model ID (CLAUDE.md rule 7)."""

    REASONING = "reasoning"
    FAST = "fast"
    VISION = "vision"


class ProvenanceSource(StrEnum):
    """Where a displayed value actually came from.

    Rendered on the provenance strip under every AI-derived panel. In a quality
    product, *where did this number come from* is the entire job.
    """

    MEASURED = "measured"          # a sensor reading or a deterministic verifier
    MODEL = "model"                # a learned model's output
    LLM = "llm"                    # generated text or a structured LLM judgement
    RETRIEVED = "retrieved"        # pulled from the RAG corpus, carries a citation
    EXTERNAL_API = "external_api"  # a live third-party API
    RULE = "rule"                  # the deterministic rule engine
    HUMAN = "human"                # an operator or inspector supplied it
    CACHED = "cached"              # served from cache; carries the original age


class DegradationKind(StrEnum):
    """Why a result is worse than the nominal path.

    Every one of these is surfaced in the trace AND in the UI. Silent
    degradation is the failure mode this enum exists to prevent
    (CLAUDE.md rule 4).
    """

    LLM_TIER_DOWNGRADE = "llm_tier_downgrade"
    LLM_UNAVAILABLE = "llm_unavailable"
    VISION_TIER_UNAVAILABLE = "vision_tier_unavailable"
    BUDGET_EXCEEDED = "budget_exceeded"
    CIRCUIT_OPEN = "circuit_open"
    CACHE_SERVED = "cache_served"
    STALE_CONTEXT = "stale_context"
    STALE_EXTERNAL_DATA = "stale_external_data"
    DATA_QUALITY_DEGRADED = "data_quality_degraded"
    TEMPORAL_WINDOW_SHORT = "temporal_window_short"
    RETRIEVAL_RELAXED = "retrieval_relaxed"
    RULE_ENGINE_FALLBACK = "rule_engine_fallback"


class GuardrailKind(StrEnum):
    """Guardrail categories. Fail closed on all of them."""

    PII_DETECTED = "pii_detected"
    PROMPT_INJECTION = "prompt_injection"
    UNSAFE_RECOMMENDATION = "unsafe_recommendation"
    UNAUTHORIZED_TOOL = "unauthorized_tool"
    SCHEMA_VIOLATION = "schema_violation"
    UNGROUNDED_CLAIM = "ungrounded_claim"
    AUTONOMY_CEILING = "autonomy_ceiling"
    COST_CEILING = "cost_ceiling"


class HitlGate(StrEnum):
    """Why a human was asked. Drives approver role and SLA."""

    LOW_CONFIDENCE = "low_confidence"
    HIGH_CONSEQUENCE = "high_consequence"
    NOVEL_CLASS = "novel_class"
    SAFETY_OVERRIDE = "safety_override"


class HitlDecision(StrEnum):
    """MODIFY is the one that matters -- it is what feeds the Learning Agent
    and closes the loop the problem statement asks for."""

    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"
    TIMEOUT = "timeout"


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
