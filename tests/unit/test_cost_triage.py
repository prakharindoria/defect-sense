"""Cost Triage engine.

These tests encode the behaviour we will be asked to defend on stage: that the
engine is conservative on safety-critical parts *because of the cost asymmetry*
rather than because someone hardcoded it, and that it never recommends halting a
line on its own.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from forge.domain.cost import CostModel, TriageContext, sensitivity, triage
from forge.domain.enums import Disposition, Severity

# Demo plant economics, wheel assembly pack. Synthetic but internally coherent.
WHEEL = CostModel(
    unit_material_cost=8_000.0,
    rework_minutes=16.0,
    labour_rate_per_hour=2_250.0,
    rework_parts_cost=2_600.0,
    line_rate_units_per_hour=60.0,
    margin_per_unit=3_500.0,
    field_failure_rate=0.08,
    warranty_cost_per_failure=45_000.0,
    recall_exposure_per_unit=120_000.0,
    inspection_cost_per_unit=150.0,
    currency="INR",
)


def contaminated(**over: object) -> TriageContext:
    """The §2 case: 12 units since the last known-good verdict."""
    base = {
        "defect_probability": 0.87,
        "probability_uncertainty": 0.06,
        "severity": Severity.CRITICAL,
        "containment_scope": 12,
        "expected_halt_hours": 2.4,
        "is_safety_critical": True,
    }
    return TriageContext(**{**base, **over})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The demo decision
# ---------------------------------------------------------------------------
@pytest.mark.demo_path
def test_containment_beats_halting_on_expected_cost() -> None:
    """The 7:30 demo beat: contain for ~38k against a line halt of ~500k.

    Note the engine picks QUARANTINE over REWORK by a few hundred rupees,
    because holding and inspecting only pays for repair on the units that turn
    out to need it. Both are containment actions and the beat is unchanged;
    docs/DEMO_SCRIPT.md says "contain", not "rework", for exactly this reason.
    We did not tune the model to match a script.
    """
    d = triage(WHEEL, contaminated())
    by_action = {r.action: r for r in d.ranked}

    assert d.recommended in {Disposition.QUARANTINE, Disposition.REWORK}
    assert by_action[Disposition.REWORK].expected_cost == pytest.approx(38_400, rel=0.02)
    assert by_action[Disposition.HALT_LINE].expected_cost > 490_000
    assert d.savings_vs_worst > 400_000


@pytest.mark.demo_path
def test_near_tie_between_containment_actions_asks_a_human() -> None:
    """Quarantine and rework land within a few hundred rupees of each other.

    The engine declines to break that tie silently. This is a stronger demo
    beat than a confident recommendation would be: it shows the system knows
    the difference between a decision and a coin flip.
    """
    d = triage(WHEEL, contaminated())
    assert d.requires_human
    assert "indifferent" in d.human_reason
    assert d.margin_over_runner_up < 0.10 * d.ranked[0].expected_cost


@pytest.mark.demo_path
def test_halt_is_never_an_autonomous_recommendation() -> None:
    """Even when halting is the cheapest option, a human decides.

    config/rbac.yaml lists line.halt under autonomy.never_autonomous. This is
    the test that makes that line true rather than aspirational.
    """
    # Make halting genuinely cheap: a brief stop, huge containment scope.
    ctx = contaminated(containment_scope=400, expected_halt_hours=0.05)
    d = triage(WHEEL, ctx)

    assert d.ranked[0].action is Disposition.HALT_LINE, "precondition: halting should win here"
    assert d.recommended is not Disposition.HALT_LINE
    assert d.requires_human
    assert "without human approval" in d.human_reason
    # The number is still shown -- it is the argument the human needs.
    assert any(r.action is Disposition.HALT_LINE for r in d.ranked)


def test_accepting_a_safety_critical_unit_always_escalates() -> None:
    """FORGE may reject a part alone. It may never pass one."""
    d = triage(WHEEL, contaminated(defect_probability=0.02, probability_uncertainty=0.01))
    if d.recommended is Disposition.ACCEPT:
        assert d.requires_human
        assert "may not pass one" in d.human_reason


# ---------------------------------------------------------------------------
# The engine reasons, it does not look up
# ---------------------------------------------------------------------------
def test_containment_scope_changes_the_answer() -> None:
    """One unit and two hundred units are different decisions, not the same one scaled."""
    small = triage(WHEEL, contaminated(containment_scope=1, expected_halt_hours=0.0))
    large = triage(WHEEL, contaminated(containment_scope=200, expected_halt_hours=0.0))
    assert large.ranked[0].expected_cost > small.ranked[0].expected_cost * 100


def test_low_probability_flips_the_recommendation_away_from_scrap() -> None:
    """Behaviour driven by P(defect), not by a severity lookup."""
    confident = triage(WHEEL, contaminated(defect_probability=0.97,
                                           probability_uncertainty=0.02,
                                           expected_halt_hours=0.0))
    doubtful = triage(WHEEL, contaminated(defect_probability=0.05,
                                          probability_uncertainty=0.02,
                                          expected_halt_hours=0.0))
    assert confident.recommended is not Disposition.ACCEPT
    assert doubtful.ranked[0].expected_cost < confident.ranked[0].expected_cost


def test_recall_exposure_is_what_makes_escape_expensive() -> None:
    """Zero the recall exposure and ACCEPT stops looking dangerous.

    This makes the mechanism explicit: the engine is conservative because
    escaping a wheel fastener defect is expensive, not because a rule says
    'critical -> scrap'.
    """
    cheap = replace(WHEEL, recall_exposure_per_unit=0.0, warranty_cost_per_failure=0.0)
    ctx = contaminated(expected_halt_hours=0.0)
    accept_costly = next(r for r in triage(WHEEL, ctx).ranked if r.action is Disposition.ACCEPT)
    accept_cheap = next(r for r in triage(cheap, ctx).ranked if r.action is Disposition.ACCEPT)
    assert accept_costly.expected_cost > accept_cheap.expected_cost
    assert accept_cheap.expected_cost == 0.0


# ---------------------------------------------------------------------------
# Honesty
# ---------------------------------------------------------------------------
def test_uncertainty_propagates_to_a_cost_interval() -> None:
    """A point estimate with no interval is the number judges rightly distrust."""
    d = triage(WHEEL, contaminated(probability_uncertainty=0.25))
    accept = next(r for r in d.ranked if r.action is Disposition.ACCEPT)
    assert accept.cost_low < accept.expected_cost < accept.cost_high


def test_wide_uncertainty_escalates() -> None:
    d = triage(WHEEL, contaminated(defect_probability=0.5, probability_uncertainty=0.45))
    assert d.requires_human


def test_near_tie_escalates_rather_than_breaking_it_silently() -> None:
    d = triage(WHEEL, contaminated(defect_probability=0.5, probability_uncertainty=0.01,
                                   expected_halt_hours=0.0))
    if d.margin_over_runner_up < 0.10 * d.ranked[0].expected_cost:
        assert d.requires_human


def test_assumptions_travel_with_the_number() -> None:
    d = triage(WHEEL, contaminated())
    joined = " ".join(d.assumptions)
    assert "P(defect)" in joined
    assert "Containment scope" in joined
    assert "field failure rate" in joined.lower()
    assert "SYNTHETIC" in joined, "cost figures must never be presented as audited plant data"


def test_every_action_explains_itself() -> None:
    d = triage(WHEEL, contaminated())
    assert all(len(r.rationale) > 40 for r in d.ranked)


# ---------------------------------------------------------------------------
# Sensitivity -- drives the /roi slider
# ---------------------------------------------------------------------------
def test_sensitivity_sweep_shows_where_the_recommendation_flips() -> None:
    sweep = sensitivity(
        WHEEL,
        contaminated(expected_halt_hours=0.0),
        probabilities=[0.0, 0.05, 0.1, 0.25, 0.5, 0.75, 0.95, 1.0],
    )
    assert len(sweep) == 8
    actions = [a for _, a, _ in sweep]
    assert len(set(actions)) > 1, "a model whose answer never changes is not a model"
    # Cost must rise monotonically with the probability of a real defect.
    costs = [c for _, _, c in sweep]
    assert costs == sorted(costs)
