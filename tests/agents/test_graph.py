"""The inspection agent graph.

Two properties matter more than the rest and are asserted first:

  1. The verdict path never calls a model. If that ever becomes false, a safety
     decision starts depending on a language model being reachable.
  2. A model failure changes the explanation, never the verdict.

Everything runs against `FakeAdapter`, so the suite is offline and deterministic.
"""

from __future__ import annotations

import asyncio

import pytest
from data.generators.torque_curve import (
    CurveClass,
    CurveSpec,
    generate_wheel,
    learn_baseline,
)

from forge.agents.graph import InspectionInputs, narrate, run_inspection
from forge.agents.prompts import PromptNotFoundError
from forge.agents.prompts import load as load_prompt
from forge.application.ports.llm import (
    Completion,
    GenerationRequest,
    LLMPort,
    ModelCapabilities,
)
from forge.domain.cost import CostModel
from forge.domain.enums import (
    AgentName,
    DataQuality,
    DegradationKind,
    ModelTier,
    Verdict,
)
from forge.domain.state import InspectionEvent, QCState, VerifierResult
from forge.infrastructure.llm.fake import FakeAdapter

SPEC = CurveSpec()

WHEEL = CostModel(
    unit_material_cost=8_000.0, rework_minutes=16.0, labour_rate_per_hour=2_250.0,
    rework_parts_cost=2_600.0, line_rate_units_per_hour=60.0, margin_per_unit=3_500.0,
    field_failure_rate=0.08, warranty_cost_per_failure=45_000.0,
    recall_exposure_per_unit=120_000.0, inspection_cost_per_unit=150.0, currency="INR",
)


@pytest.fixture(scope="module")
def baseline():  # noqa: ANN201
    return learn_baseline(SPEC, runs=120, sigma=3.0)


def fresh_state(unit: str = "VIN-TEST-1") -> QCState:
    return QCState(
        correlation_id=f"test-{unit}",
        pack_id="wheel_assembly",
        unit_id=unit,
        event=InspectionEvent(unit_id=unit, station_id="WA-01", pack_id="wheel_assembly"),
    )


def curves_for(label: CurveClass, severity: float, seed: int = 42):  # noqa: ANN201
    labels = {} if label is CurveClass.CLEAN else {3: (label, severity)}
    return [g.curve for g in generate_wheel(labels, spec=SPEC, seed=seed, unit_id="VIN-TEST-1")]


async def run(label: CurveClass, severity: float, baseline, **kw) -> QCState:  # noqa: ANN001
    return await run_inspection(
        fresh_state(),
        InspectionInputs(curves_for(label, severity), baseline, WHEEL,
                         containment_scope=12, **kw),
    )


# ---------------------------------------------------------------------------
# The two properties that matter most
# ---------------------------------------------------------------------------
class _ExplodingLLM(LLMPort):
    """Any call is a test failure. Proves the verdict path is model-free."""

    async def generate(self, request: GenerationRequest) -> Completion:
        raise AssertionError(
            "the verdict path called a model; a safety verdict must not depend on one"
        )

    async def capabilities(self, tier: ModelTier) -> ModelCapabilities:  # type: ignore[override]
        raise AssertionError("verdict path probed a model")

    async def probe(self):  # noqa: ANN201
        raise AssertionError("verdict path probed a model")

    def is_tier_available(self, tier: ModelTier) -> bool:
        raise AssertionError("verdict path queried tier availability")


@pytest.mark.demo_path
async def test_verdict_path_never_calls_a_model(baseline) -> None:  # noqa: ANN001
    """The graph must reach a verdict with a provider that explodes on contact."""
    inputs = InspectionInputs(
        curves_for(CurveClass.THREAD_CONTAMINATION, 0.8), baseline, WHEEL,
        containment_scope=12,
    )
    inputs.llm = _ExplodingLLM()  # type: ignore[attr-defined]  # never read; if it were, it raises
    out = await run_inspection(fresh_state(), inputs)
    assert out.fusion is not None
    assert out.fusion.verdict is Verdict.DEFECT


@pytest.mark.demo_path
async def test_model_failure_changes_the_explanation_not_the_verdict(baseline) -> None:  # noqa: ANN001
    """With every provider dead, the verdict is identical and the narrative degrades."""

    class DeadLLM(FakeAdapter):
        async def generate(self, request: GenerationRequest) -> Completion:
            raise TimeoutError("provider unreachable")

    out = await run(CurveClass.THREAD_CONTAMINATION, 0.8, baseline)
    assert out.fusion.verdict is Verdict.DEFECT
    assert out.fusion.fusion_only

    text, span = await narrate(out, DeadLLM())
    # Falls back to the deterministic reasoning, and SAYS it fell back.
    assert text == out.fusion.reasoning
    assert DegradationKind.LLM_UNAVAILABLE in span.degradations


# ---------------------------------------------------------------------------
# Graph behaviour
# ---------------------------------------------------------------------------
@pytest.mark.demo_path
async def test_fusion_only_case_survives_the_graph(baseline) -> None:  # noqa: ANN001
    """The money shot, end to end through LangGraph."""
    out = await run(CurveClass.THREAD_CONTAMINATION, 0.8, baseline)

    assert out.fusion.fusion_only
    assert out.fusion.verdict is Verdict.DEFECT
    # Both individual signals passed. That is the whole point.
    assert all(f.endpoint_in_spec for f in out.process.fasteners)
    assert not out.vision.failed_verifiers


async def test_all_specialists_run_and_converge(baseline) -> None:  # noqa: ANN001
    """Every node appends its own span; the reducer keeps all of them.

    Without the `operator.add` reducer on QCState.trace, the three parallel
    nodes would overwrite each other and this would find one span, not three.
    """
    out = await run(CurveClass.CLEAN, 0.0, baseline)
    agents = {s.agent for s in out.trace}

    assert AgentName.INGESTION in agents
    assert AgentName.VISION_INSPECTOR in agents
    assert AgentName.PROCESS_SENTINEL in agents
    assert AgentName.CONTEXT in agents
    assert AgentName.ADJUDICATOR in agents
    assert AgentName.COST_TRIAGE in agents
    assert len(out.trace) == 6


async def test_clean_unit_passes_and_ships(baseline) -> None:  # noqa: ANN001
    out = await run(CurveClass.CLEAN, 0.0, baseline)
    assert out.fusion.verdict is Verdict.PASS
    assert out.triage.recommended == "accept"
    assert out.triage.expected_cost == 0.0
    assert not out.triage.requires_human


async def test_failed_verifier_is_decisive(baseline) -> None:  # noqa: ANN001
    """An exact measurement outranks every estimate."""
    out = await run_inspection(
        fresh_state(),
        InspectionInputs(
            curves_for(CurveClass.CLEAN, 0.0), baseline, WHEEL,
            verifiers_passed=False,
            verifier_results=(
                VerifierResult(name="fastener_count", passed=False,
                               expected="5", observed="4"),
            ),
            containment_scope=12,
        ),
    )
    assert out.fusion.verdict is Verdict.DEFECT
    assert out.fusion.confidence >= 0.99
    assert not out.fusion.fusion_only


async def test_empty_input_escalates_and_never_passes(baseline) -> None:  # noqa: ANN001
    """A missing measurement is not a passing measurement."""
    out = await run_inspection(fresh_state(), InspectionInputs([], baseline, WHEEL))
    assert out.data_quality is DataQuality.REJECTED
    assert out.fusion.verdict is not Verdict.PASS


async def test_signal_conflict_records_a_retry(baseline) -> None:  # noqa: ANN001
    """Reflection fires on a measured condition, and the trace shows it."""
    out = await run(CurveClass.THREAD_CONTAMINATION, 0.8, baseline)
    span = next(s for s in out.trace if s.agent is AgentName.ADJUDICATOR)
    assert span.retry_count == 1
    assert "conflict" in span.summary.lower()


async def test_missing_context_is_recorded_as_a_degradation(baseline) -> None:  # noqa: ANN001
    """No MES source must be visible, not silently absent."""
    out = await run(CurveClass.CLEAN, 0.0, baseline)
    assert DegradationKind.STALE_CONTEXT in out.degradations
    assert out.context is None, "context must not be fabricated when no source exists"


async def test_verdict_is_fast(baseline) -> None:  # noqa: ANN001
    """The latency claim, asserted rather than remembered."""
    import time  # noqa: PLC0415

    await run(CurveClass.CLEAN, 0.0, baseline)  # warm
    start = time.perf_counter()
    await run(CurveClass.THREAD_CONTAMINATION, 0.8, baseline)
    assert (time.perf_counter() - start) * 1000 < 250


async def test_runs_are_independent(baseline) -> None:  # noqa: ANN001
    """Concurrent inspections must not bleed into each other."""
    results = await asyncio.gather(
        run(CurveClass.CLEAN, 0.0, baseline),
        run(CurveClass.THREAD_CONTAMINATION, 0.8, baseline),
        run(CurveClass.UNDER_TORQUE, 0.8, baseline),
    )
    assert [r.fusion.verdict for r in results] == [
        Verdict.PASS, Verdict.DEFECT, Verdict.DEFECT,
    ]
    assert all(len(r.trace) == 6 for r in results)


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------
async def test_narrative_records_its_prompt_version(baseline) -> None:  # noqa: ANN001
    """Prompt iteration is auditable: which prompt produced this explanation?"""
    out = await run(CurveClass.THREAD_CONTAMINATION, 0.8, baseline)
    _, span = await narrate(out, FakeAdapter())
    assert span.prompt_version == load_prompt("adjudicator_narrative").version


async def test_malformed_model_output_is_rejected_not_salvaged(baseline) -> None:  # noqa: ANN001
    """A half-parsed response must not become an authoritative explanation."""

    class GarbageLLM(FakeAdapter):
        async def generate(self, request: GenerationRequest) -> Completion:
            completion = await super().generate(request)
            from dataclasses import replace  # noqa: PLC0415

            return replace(completion, text="this is not json at all")

    out = await run(CurveClass.CLEAN, 0.0, baseline)
    text, span = await narrate(out, GarbageLLM())
    assert text == out.fusion.reasoning
    assert "schema" in span.summary.lower()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
def test_prompt_loads_with_version_and_tier() -> None:
    prompt = load_prompt("adjudicator_narrative")
    assert prompt.version == "1.0.0"
    assert prompt.tier == "reasoning"
    assert "clamp load" in prompt.body


def test_prompt_render_survives_json_braces() -> None:
    """The body is full of literal JSON braces; naive str.format would explode."""
    rendered = load_prompt("adjudicator_narrative").render(evidence='{"verdict": "pass"}')
    assert '{"verdict": "pass"}' in rendered
    assert "{evidence}" not in rendered


def test_missing_prompt_is_an_error() -> None:
    with pytest.raises(PromptNotFoundError):
        load_prompt("no_such_prompt")
