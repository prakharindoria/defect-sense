"""The inspection agent graph.

Two graphs, deliberately.

**VERDICT graph** — fully deterministic, no model call, completes in
milliseconds. Ingestion fans out to Vision / Process / Context in parallel, they
converge on the Adjudicator, and Cost Triage prices the outcome.

**NARRATIVE** — a separate LLM step fired as a background task after the verdict
has already been returned to the UI, streamed in over the WebSocket.

Splitting them is the architectural claim, not an optimisation: a safety verdict
must not wait on a language model, and must not depend on one. Measured LLM
latency on this endpoint is 1.4–7 s; the verdict path is 2–5 ms. If the model is
down — as TCS was on 2026-08-07 — the product is unchanged and the narrative
falls back to the deterministic explanation, visibly marked as such.

The fan-out is real parallelism, which is why `QCState.trace` carries an
`operator.add` reducer: three nodes append spans concurrently and LangGraph
concatenates them instead of last-write-wins.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

from forge.agents.prompts import load as load_prompt
from forge.domain.adjudication import (
    Adjudication,
    FastenerOutcome,
    adjudicate,
    needs_human,
)
from forge.domain.cost import CostModel, TriageContext, triage
from forge.domain.enums import (
    AgentName,
    DataQuality,
    DegradationKind,
    ModelTier,
    ProvenanceSource,
    Severity,
    Verdict,
)
from forge.domain.provenance import Provenance
from forge.domain.state import (
    FastenerResult,
    FusionVerdict,
    ProcessVerdict,
    QCState,
    SignalRecord,
    TraceSpan,
    TriageResult,
    VerifierResult,
    VisionVerdict,
)
from forge.domain.torque import extract_features, score_signature

if TYPE_CHECKING:
    from collections.abc import Sequence

    from forge.application.ports.llm import LLMPort
    from forge.domain.torque import SignatureBaseline, TorqueAngleCurve


# ---------------------------------------------------------------------------
# Graph inputs that are not part of QCState
# ---------------------------------------------------------------------------
class InspectionInputs:
    """Per-run dependencies handed to the nodes via LangGraph's config.

    Kept out of QCState because state is persisted and replayed -- a baseline
    object or a live LLM client has no business in an audit record.
    """

    def __init__(
        self,
        curves: Sequence[TorqueAngleCurve],
        baseline: SignatureBaseline,
        cost_model: CostModel,
        *,
        verifiers_passed: bool = True,
        verifier_results: Sequence[VerifierResult] = (),
        containment_scope: int = 1,
        expected_halt_hours: float = 2.4,
        custom_vision_verdict: VisionVerdict | None = None,
    ) -> None:
        self.curves = list(curves)
        self.baseline = baseline
        self.cost_model = cost_model
        self.verifiers_passed = verifiers_passed
        self.verifier_results = list(verifier_results)
        self.containment_scope = containment_scope
        self.expected_halt_hours = expected_halt_hours
        self.custom_vision_verdict = custom_vision_verdict
        # Populated by the process node, read by adjudicator and narrative.
        self.outcomes: list[FastenerOutcome] = []
        self.adjudication: Adjudication | None = None


def _inputs(config: dict[str, Any]) -> InspectionInputs:
    return config["configurable"]["inputs"]


def _span(agent: AgentName, started: float, summary: str, **kw: Any) -> TraceSpan:
    from datetime import UTC, datetime  # noqa: PLC0415

    return TraceSpan(
        agent=agent,
        started_at=datetime.now(UTC),
        duration_ms=int((time.perf_counter() - started) * 1000),
        summary=summary,
        **kw,
    )


def _measured(producer: str, latency_ms: int) -> Provenance:
    return Provenance(
        source=ProvenanceSource.MEASURED, producer=producer, latency_ms=latency_ms
    )


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
async def ingestion(state: QCState, config: dict[str, Any]) -> dict[str, Any]:
    """Validate and gate. Refuses to pass a unit on absent evidence."""
    started = time.perf_counter()
    inputs = _inputs(config)

    reasons: list[str] = []
    valid = []
    for curve in inputs.curves:
        if len(curve.samples) < 10:
            reasons.append(f"position {curve.fastener_position}: too few samples")
        else:
            valid.append(curve)
    inputs.curves = valid

    if not valid:
        quality = DataQuality.REJECTED
        reasons.append("no analysable signals; refusing to produce a verdict")
    elif reasons:
        quality = DataQuality.DEGRADED
    else:
        quality = DataQuality.GOOD

    return {
        "data_quality": quality,
        "data_quality_reasons": tuple(reasons),
        "status": "analyzing",
        "trace": [
            _span(
                AgentName.INGESTION, started,
                f"{len(valid)}/{len(inputs.curves) or len(valid)} signals accepted, "
                f"quality={quality.value}",
                ok=quality is not DataQuality.REJECTED,
                error="data quality gate rejected the input"
                if quality is DataQuality.REJECTED else None,
            )
        ],
        "degradations": (
            [DegradationKind.DATA_QUALITY_DEGRADED] if quality is DataQuality.DEGRADED else []
        ),
    }


async def vision(state: QCState, config: dict[str, Any]) -> dict[str, Any]:
    """Geometric verifiers or VLM vision analysis.

    Evaluates visual evidence (uploaded image VLM analysis or geometric verifiers).
    """
    started = time.perf_counter()
    inputs = _inputs(config)
    latency = int((time.perf_counter() - started) * 1000)

    if inputs.custom_vision_verdict is not None:
        verdict = inputs.custom_vision_verdict
    else:
        verdict = VisionVerdict(
            anomaly_score=0.0 if inputs.verifiers_passed else 1.0,
            confidence=0.99,
            frames_evaluated=1,
            frames_anomalous=0 if inputs.verifiers_passed else 1,
            temporal_consensus=True,
            verifiers=tuple(inputs.verifier_results),
            description="",
            provenance=_measured("verifiers", latency),
        )
    failed = [v.name for v in verdict.failed_verifiers]
    return {
        "vision": verdict,
        "trace": [
            _span(
                AgentName.VISION_INSPECTOR, started,
                f"vision {'PASS' if verdict.anomaly_score < 0.5 else 'FAIL'}"
                + (f" - {verdict.description or ', '.join(failed)}" if (failed or verdict.description) else ""),
            )
        ],
    }


async def process_sentinel(state: QCState, config: dict[str, Any]) -> dict[str, Any]:
    """Torque-angle signature scoring. The signal that catches fusion-only."""
    started = time.perf_counter()
    inputs = _inputs(config)

    outcomes = [
        FastenerOutcome(c.fastener_position, score_signature(extract_features(c), inputs.baseline))
        for c in sorted(inputs.curves, key=lambda c: c.fastener_position)
    ]
    inputs.outcomes = outcomes
    latency = int((time.perf_counter() - started) * 1000)

    if not outcomes:
        return {
            "trace": [_span(AgentName.PROCESS_SENTINEL, started, "no signals to score", ok=False,
                            error="no analysable curves")]
        }

    worst = max(outcomes, key=lambda o: o.signature.anomaly_score)
    verdict = ProcessVerdict(
        fasteners=tuple(
            FastenerResult(
                position=o.position,
                final_torque_nm=o.signature.features.final_torque_nm,
                knee_angle_deg=o.signature.features.knee_angle_deg,
                elastic_slope_nm_per_deg=o.signature.features.elastic_slope_nm_per_deg,
                anomaly_score=o.signature.anomaly_score,
                endpoint_in_spec=o.signature.endpoint_in_spec,
                signature_anomalous=o.signature.signature_anomalous,
                deviations=tuple(d.statement for d in o.signature.deviations),
                likely_class=(
                    o.signature.ranked_classes[0][0] if o.signature.ranked_classes else None
                ),
            )
            for o in outcomes
        ),
        anomaly_score=worst.signature.anomaly_score,
        confidence=0.95,
        provenance=_measured("torque-signature/1.0", latency),
    )
    return {
        "process": verdict,
        # Keep the measurements, not only the conclusions drawn from them.
        "signals": [
            SignalRecord(
                channel="torque", position=c.fastener_position,
                x_unit="deg", y_unit="Nm",
                samples=[[round(s.angle_deg, 3), round(s.torque_nm, 3)] for s in c.samples],
            )
            for c in sorted(inputs.curves, key=lambda c: c.fastener_position)
        ],
        "trace": [
            _span(
                AgentName.PROCESS_SENTINEL, started,
                f"{len(outcomes)} points scored; worst anomaly "
                f"{worst.signature.anomaly_score:.2f} at position {worst.position}",
            )
        ],
    }


async def context(state: QCState, config: dict[str, Any]) -> dict[str, Any]:
    """MES and live ambient context.

    Currently records that it ran with no source attached. It does NOT invent a
    tool age or a humidity reading -- a fabricated context value would flow
    straight into the Adjudicator's reasoning as though it were measured.
    Wired to the real MES and Open-Meteo in step B.
    """
    started = time.perf_counter()
    return {
        "trace": [
            _span(
                AgentName.CONTEXT, started,
                "no MES or ambient source configured; proceeding without context",
            )
        ],
        "degradations": [DegradationKind.STALE_CONTEXT],
    }


async def adjudicator(state: QCState, config: dict[str, Any]) -> dict[str, Any]:
    """Reconcile the signals. Deterministic — no model call on this path."""
    started = time.perf_counter()
    inputs = _inputs(config)

    result = adjudicate(
        inputs.outcomes,
        inputs.baseline,
        verifiers_passed=inputs.verifiers_passed,
        verifier_detail=", ".join(
            f"{v.name} expected {v.expected} observed {v.observed}"
            for v in inputs.verifier_results if not v.passed
        ),
    )
    inputs.adjudication = result

    fusion = FusionVerdict(
        verdict=result.verdict,
        confidence=result.confidence,
        severity=result.severity,
        reasoning=result.reasoning,
        primary_signal=result.primary_signal,
        fusion_only=result.fusion_only,
        escalation_reason=(
            result.reasoning if result.verdict is Verdict.ESCALATE else None
        ),
        provenance=Provenance(source=ProvenanceSource.RULE, producer="adjudicator/1.0",
                              confidence=result.confidence,
                              latency_ms=int((time.perf_counter() - started) * 1000)),
    )
    return {
        "fusion": fusion,
        "status": "adjudicating",
        "retries": {"adjudicator": 1} if result.signals_conflict else {},
        "trace": [
            _span(
                AgentName.ADJUDICATOR, started,
                f"{result.verdict.value.upper()} conf {result.confidence:.2f}"
                + (" (FUSION-ONLY)" if result.fusion_only else "")
                + (" [signals conflict -> re-examined]" if result.signals_conflict else ""),
                retry_count=1 if result.signals_conflict else 0,
            )
        ],
    }


async def cost_triage(state: QCState, config: dict[str, Any]) -> dict[str, Any]:
    """Price the outcome. Only runs when something is actually wrong.

    Asking "what do we do about this defect" of a unit we just passed is a
    category error, and it produces an incoherent answer: containment economics
    always prefer holding stock over shipping it once escape carries recall
    exposure. A PASS ships.
    """
    started = time.perf_counter()
    inputs = _inputs(config)
    adj = inputs.adjudication
    if adj is None:
        return {"trace": [_span(AgentName.COST_TRIAGE, started, "skipped - no adjudication",
                                ok=False, error="adjudicator did not run")]}

    if adj.verdict is Verdict.PASS:
        result = TriageResult(
            recommended="accept", expected_cost=0.0, cost_low=0.0, cost_high=0.0,
            currency=inputs.cost_model.currency,
            assumptions=(
                "Unit passed inspection; no containment action and therefore no incremental "
                "cost. Triage is not consulted on a passing unit.",
            ),
            requires_human=False,
            provenance=Provenance(source=ProvenanceSource.RULE, producer="expected-cost/1.0"),
        )
        summary = "skipped - unit passed, no containment required"
    else:
        decision = triage(
            inputs.cost_model,
            TriageContext(
                defect_probability=adj.confidence,
                probability_uncertainty=0.06,
                severity=adj.severity,
                containment_scope=inputs.containment_scope,
                expected_halt_hours=inputs.expected_halt_hours,
                is_safety_critical=adj.severity is Severity.CRITICAL,
            ),
        )
        best = decision.ranked[0]
        human, why = needs_human(adj, triage_requires_human=decision.requires_human)
        result = TriageResult(
            recommended=decision.recommended.value,
            expected_cost=best.expected_cost,
            cost_low=best.cost_low,
            cost_high=best.cost_high,
            currency=inputs.cost_model.currency,
            alternatives=tuple((r.action.value, r.expected_cost) for r in decision.ranked[1:]),
            assumptions=decision.assumptions,
            requires_human=human,
            human_reason=decision.human_reason or why,
            provenance=Provenance(source=ProvenanceSource.RULE, producer="expected-cost/1.0"),
        )
        summary = (
            f"{decision.recommended.value} at {best.expected_cost:,.0f} "
            f"{inputs.cost_model.currency}" + (" - human required" if human else "")
        )

    return {
        "triage": result,
        "status": "awaiting_human" if result.requires_human else "complete",
        "trace": [_span(AgentName.COST_TRIAGE, started, summary)],
    }


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
def build_verdict_graph():  # noqa: ANN201 - LangGraph's compiled type
    """Deterministic verdict path. No model call anywhere in it."""
    g = StateGraph(QCState)
    # Node names must not collide with QCState field names -- LangGraph reserves
    # those as state channels. Hence `vision_inspector` rather than `vision`.
    g.add_node("ingestion", ingestion)
    g.add_node("vision_inspector", vision)
    g.add_node("process_sentinel", process_sentinel)
    g.add_node("mes_context", context)
    g.add_node("adjudicator", adjudicator)
    g.add_node("cost_triage", cost_triage)

    g.add_edge(START, "ingestion")
    # Genuine fan-out: three specialists run concurrently and converge on the
    # Adjudicator. Their outputs only meet there, which is exactly where a
    # fusion-only defect becomes visible.
    for node in ("vision_inspector", "process_sentinel", "mes_context"):
        g.add_edge("ingestion", node)
        g.add_edge(node, "adjudicator")
    g.add_edge("adjudicator", "cost_triage")
    g.add_edge("cost_triage", END)
    return g.compile()


VERDICT_GRAPH = build_verdict_graph()


# ---------------------------------------------------------------------------
# Narrative — the LLM step, off the critical path
# ---------------------------------------------------------------------------
async def narrate(
    state: QCState, llm: LLMPort, *, timeout_s: float = 20.0
) -> tuple[str, TraceSpan]:
    """Generate the human-readable explanation of an already-decided verdict.

    Returns (narrative, span). Never raises: if the model is unreachable, the
    deterministic reasoning already on the state is returned and the span
    records the degradation. The verdict is unaffected either way.
    """
    from forge.application.ports.llm import (  # noqa: PLC0415
        GenerationRequest,
        LLMError,
        Message,
    )

    started = time.perf_counter()
    fusion = state.fusion
    fallback = fusion.reasoning if fusion else ""

    if fusion is None:
        return fallback, _span(AgentName.ADJUDICATOR, started, "no verdict to narrate", ok=False,
                               error="fusion verdict missing")

    evidence = {
        "verdict": fusion.verdict.value,
        "severity": fusion.severity.value,
        "confidence": round(fusion.confidence, 3),
        "fusion_only": fusion.fusion_only,
        "primary_signal": fusion.primary_signal.value,
        "deterministic_reasoning": fusion.reasoning,
        "fasteners": [
            {
                "position": f.position,
                "final_torque_nm": round(f.final_torque_nm, 1),
                "knee_angle_deg": round(f.knee_angle_deg, 1),
                "elastic_slope_nm_per_deg": round(f.elastic_slope_nm_per_deg, 2),
                "endpoint_in_spec": f.endpoint_in_spec,
                "signature_anomalous": f.signature_anomalous,
                "likely_class": f.likely_class,
            }
            for f in (state.process.fasteners if state.process else ())
        ],
        "failed_verifiers": [
            {"name": v.name, "expected": v.expected, "observed": v.observed}
            for v in (state.vision.failed_verifiers if state.vision else ())
        ],
    }

    prompt = load_prompt("adjudicator_narrative")
    request = GenerationRequest(
        tier=ModelTier.REASONING,
        messages=(
            Message("system", prompt.render(evidence=json.dumps(evidence, indent=2))),
            Message("user", "Explain this verdict."),
        ),
        response_schema={
            "type": "object",
            "properties": {
                "narrative": {"type": "string", "description": "the reasoning"},
                "key_evidence": {"type": "array", "items": {"type": "string"}},
                "what_to_check": {"type": "string"},
            },
            "required": ["narrative", "key_evidence"],
        },
        pack_id=state.pack_id,
        correlation_id=state.correlation_id,
    )

    try:
        completion = await asyncio.wait_for(llm.generate(request), timeout=timeout_s)
    except (LLMError, TimeoutError) as exc:
        return fallback, _span(
            AgentName.ADJUDICATOR, started,
            f"narrative unavailable ({type(exc).__name__}); showing deterministic explanation",
            degradations=(DegradationKind.LLM_UNAVAILABLE,),
            prompt_version=prompt.version,
        )

    try:
        payload = json.loads(completion.text)
        narrative = str(payload["narrative"]).strip()
    except (json.JSONDecodeError, KeyError, TypeError):
        # The model answered but not in the shape we asked for. We do NOT
        # salvage prose out of a malformed structured response -- that is how a
        # half-parsed answer becomes an authoritative-looking explanation.
        return fallback, _span(
            AgentName.ADJUDICATOR, started,
            "narrative rejected: response did not match schema",
            degradations=(DegradationKind.LLM_UNAVAILABLE,),
            prompt_version=prompt.version,
        )

    # When the chain fell all the way through to the deterministic stand-in, no
    # model was involved -- so the provenance must not say LLM. A strip reading
    # "LLM" over text nothing generated is precisely the confusion the whole
    # provenance mechanism exists to prevent.
    from_model = DegradationKind.RULE_ENGINE_FALLBACK not in completion.degradations

    return narrative, _span(
        AgentName.ADJUDICATOR, started,
        f"narrative via {completion.provider_used}:{completion.model_used}"
        + ("" if from_model else " (no model available - deterministic stand-in)"),
        prompt_version=prompt.version,
        degradations=completion.degradations,
        provenance=Provenance(
            source=ProvenanceSource.LLM if from_model else ProvenanceSource.RULE,
            producer="adjudicator-narrative",
            tier=completion.tier_used,
            model_id=completion.model_used,
            latency_ms=completion.latency_ms,
            tokens=completion.tokens,
            prompt_version=prompt.version,
            degradations=completion.degradations,
            cache_age_seconds=completion.cache_age_seconds,
        ),
    )


async def run_inspection(
    state: QCState, inputs: InspectionInputs
) -> QCState:
    """Execute the deterministic verdict graph and return the final state."""
    result = await VERDICT_GRAPH.ainvoke(
        state, config={"configurable": {"inputs": inputs}}
    )
    # LangGraph returns a dict for pydantic state; rebuild the model.
    return QCState.model_validate(result) if isinstance(result, dict) else result


__all__ = [
    "VERDICT_GRAPH",
    "InspectionInputs",
    "build_verdict_graph",
    "narrate",
    "run_inspection",
]
