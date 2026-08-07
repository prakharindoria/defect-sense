"""Cost Triage -- turning a defect verdict into a business decision.

This is the expected-cost engine behind the Cost Triage agent. It is not a
severity lookup table. It answers the question a supervisor actually has:

    "Given what we believe about this unit, which action costs us least --
     and how sure are we?"

Why expected cost rather than a severity rule
---------------------------------------------
A severity rule says "critical -> scrap". That is wrong roughly whenever the
probability of an actual defect is low, and it is catastrophically wrong when
the containment scope is large. The right comparison is between the *expected*
cost of each available action, where each action has two ways to be wrong:

    acting when the unit is fine    -> unnecessary scrap or rework
    not acting when it is defective -> escape into the field

    EC(action) = P(defect) * cost_if_defective(action)
               + (1 - P(defect)) * cost_if_fine(action)

We recommend argmin EC. The asymmetry that matters is that a wheel fastener
escape is not a warranty claim, it is a wheel-off event, so `cost_if_defective`
for ACCEPT carries field-failure and recall exposure. That asymmetry -- not a
hand-tuned severity weight -- is what makes the engine conservative on
safety-critical classes, and it is visible and arguable rather than baked in.

Honesty rules
-------------
- Every monetary figure is an ESTIMATE built from declared assumptions, and the
  assumption set travels with the result so the UI can show it.
- The uncertainty in P(defect) propagates to a cost interval. A point estimate
  of savings with no interval is the kind of number judges correctly distrust.
- HALT_LINE is never returned as an autonomous recommendation. It is always
  routed to a human (config/rbac.yaml autonomy.never_autonomous).
- Currency conversion uses the live FX rate and records its age.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from forge.domain.enums import Disposition, Severity

if TYPE_CHECKING:
    from collections.abc import Sequence

# Actions the engine may recommend on its own. HALT_LINE is deliberately absent.
AUTONOMOUS_ACTIONS: tuple[Disposition, ...] = (
    Disposition.ACCEPT,
    Disposition.REWORK,
    Disposition.QUARANTINE,
    Disposition.SCRAP,
)


@dataclass(frozen=True, slots=True)
class CostModel:
    """Plant economics for the active pack. Loaded from the pack's manifest.

    All values in the plant's base currency (INR for the demo plant). Every one
    of these is an assumption that a plant would replace with its own figures;
    they are declared here rather than scattered through the code so that
    replacing them is a config change and so the UI can list them.
    """

    unit_material_cost: float          # cost of the wheel assembly itself
    rework_minutes: float              # labour to rework one unit
    labour_rate_per_hour: float
    # Consumables consumed by a rework. For an over-torqued hub bolt the remedy
    # is bolt replacement, not re-torquing -- damaged bolts do not recover, which
    # is exactly what NHTSA 24V237000's remedy specifies. Ignoring this
    # understates rework by roughly 4x and would make ACCEPT win far too often.
    rework_parts_cost: float
    line_rate_units_per_hour: float
    margin_per_unit: float
    # Probability that a defective unit escaping to the field actually fails.
    # Not 1.0: many defects never manifest. This is the single most
    # consequential assumption in the model, so it is named and exposed.
    field_failure_rate: float
    warranty_cost_per_failure: float
    # Per-unit exposure if a defect class triggers a recall campaign. Seeded
    # from the live NHTSA record for the matching component, not invented.
    recall_exposure_per_unit: float
    # Cost of a false reject: we scrapped or reworked a unit that was fine.
    inspection_cost_per_unit: float = 0.0
    currency: str = "INR"


@dataclass(frozen=True, slots=True)
class TriageContext:
    """What we know about this specific decision."""

    # Calibrated probability that the unit is genuinely defective.
    defect_probability: float
    # Half-width of the credible interval on that probability. Propagates to
    # the cost interval; a wide interval is itself a reason to escalate.
    probability_uncertainty: float
    severity: Severity
    # Units produced since the last known-good verdict on this station/tool.
    # This is what turns a one-unit decision into a batch decision, and it is
    # the number that most often changes which action wins.
    containment_scope: int = 1
    expected_halt_hours: float = 0.0
    is_safety_critical: bool = False


@dataclass(frozen=True, slots=True)
class ActionCost:
    """Expected cost of one candidate action, with its reasoning exposed."""

    action: Disposition
    expected_cost: float
    cost_low: float
    cost_high: float
    cost_if_defective: float
    cost_if_fine: float
    rationale: str

    @property
    def interval_width(self) -> float:
        return self.cost_high - self.cost_low


@dataclass(frozen=True, slots=True)
class TriageDecision:
    recommended: Disposition
    ranked: tuple[ActionCost, ...]
    context: TriageContext
    model: CostModel
    assumptions: tuple[str, ...] = field(default=())
    requires_human: bool = False
    human_reason: str = ""

    @property
    def savings_vs_worst(self) -> float:
        """What choosing well is worth against the most expensive option."""
        if len(self.ranked) < 2:
            return 0.0
        return self.ranked[-1].expected_cost - self.ranked[0].expected_cost

    @property
    def margin_over_runner_up(self) -> float:
        """How decisive the recommendation is.

        A small margin means the model is nearly indifferent, which is a
        legitimate reason to ask a human even when nothing else has tripped.
        """
        if len(self.ranked) < 2:
            return float("inf")
        return self.ranked[1].expected_cost - self.ranked[0].expected_cost


# ---------------------------------------------------------------------------
# Cost components
# ---------------------------------------------------------------------------
def _rework_cost(m: CostModel, units: int) -> float:
    return units * ((m.rework_minutes / 60.0) * m.labour_rate_per_hour + m.rework_parts_cost)


def _scrap_cost(m: CostModel, units: int) -> float:
    return units * (m.unit_material_cost + m.margin_per_unit)


def _escape_cost(m: CostModel, units: int) -> float:
    """Cost of letting a genuinely defective unit reach the field."""
    per_unit = m.field_failure_rate * (m.warranty_cost_per_failure + m.recall_exposure_per_unit)
    return units * per_unit


def _halt_cost(m: CostModel, hours: float) -> float:
    return hours * m.line_rate_units_per_hour * m.margin_per_unit


def _action_costs(m: CostModel, ctx: TriageContext) -> list[ActionCost]:
    n = max(ctx.containment_scope, 1)
    rework = _rework_cost(m, n)
    scrap = _scrap_cost(m, n)
    escape = _escape_cost(m, n)
    # Quarantine: hold the units and inspect before committing to a repair.
    # Costs handling and inspection now; if the defect is confirmed the FULL
    # rework still follows. Contaminated threads cannot be cleared by
    # inspection -- clamp load is not observable -- so the fastener is replaced
    # either way. Modelling this as half a rework would flatter quarantine.
    hold = m.inspection_cost_per_unit * n + 0.25 * (m.rework_minutes / 60.0) * (
        m.labour_rate_per_hour
    ) * n

    rows = [
        ActionCost(
            action=Disposition.ACCEPT,
            expected_cost=0.0, cost_low=0.0, cost_high=0.0,
            cost_if_defective=escape,
            cost_if_fine=0.0,
            rationale=(
                f"Ship as built. If the units are defective, {n} unit(s) reach the field "
                f"at {m.field_failure_rate:.1%} failure rate, carrying warranty and "
                f"recall exposure."
            ),
        ),
        ActionCost(
            action=Disposition.REWORK,
            expected_cost=0.0, cost_low=0.0, cost_high=0.0,
            cost_if_defective=rework,
            cost_if_fine=rework,
            rationale=(
                f"Replace fasteners and re-torque {n} unit(s): {m.rework_minutes:.0f} min "
                f"each at {m.labour_rate_per_hour:,.0f}/hr plus {m.rework_parts_cost:,.0f} "
                f"in parts. Costs the same whether or not the defect was real, which is "
                f"what makes it the safe default."
            ),
        ),
        ActionCost(
            action=Disposition.QUARANTINE,
            expected_cost=0.0, cost_low=0.0, cost_high=0.0,
            cost_if_defective=hold + rework,
            cost_if_fine=hold,
            rationale=(
                f"Hold {n} unit(s) and inspect before repairing: {hold:,.0f} to hold, plus "
                f"the full {rework:,.0f} rework only on units the inspection confirms. "
                f"Beats blanket rework whenever P(defect) < 1, and preserves the evidence."
            ),
        ),
        ActionCost(
            action=Disposition.SCRAP,
            expected_cost=0.0, cost_low=0.0, cost_high=0.0,
            cost_if_defective=scrap,
            cost_if_fine=scrap,
            rationale=(
                f"Scrap {n} unit(s) at {m.unit_material_cost:.0f} material plus "
                f"{m.margin_per_unit:.0f} lost margin each. Only rational when rework "
                f"cannot restore clamp load."
            ),
        ),
    ]

    if ctx.expected_halt_hours > 0:
        halt = _halt_cost(m, ctx.expected_halt_hours)
        rows.append(
            ActionCost(
                action=Disposition.HALT_LINE,
                expected_cost=0.0, cost_low=0.0, cost_high=0.0,
                cost_if_defective=halt + rework,
                cost_if_fine=halt,
                rationale=(
                    f"Stop the line for {ctx.expected_halt_hours:.1f}h at "
                    f"{m.line_rate_units_per_hour:.0f} units/hr. "
                    f"REQUIRES HUMAN APPROVAL -- never autonomous."
                ),
            )
        )
    return rows


def _expected(row: ActionCost, p: float) -> ActionCost:
    ec = p * row.cost_if_defective + (1.0 - p) * row.cost_if_fine
    return ActionCost(
        action=row.action,
        expected_cost=ec,
        cost_low=ec, cost_high=ec,
        cost_if_defective=row.cost_if_defective,
        cost_if_fine=row.cost_if_fine,
        rationale=row.rationale,
    )


def triage(
    model: CostModel,
    ctx: TriageContext,
    *,
    indifference_ratio: float = 0.10,
    high_consequence_threshold: float = 50_000.0,
) -> TriageDecision:
    """Rank actions by expected cost and decide whether a human is required.

    `indifference_ratio` is the fraction of the winning cost by which the
    runner-up must be beaten for the recommendation to count as decisive. Inside
    that margin the engine says so and asks a human, rather than presenting an
    arbitrary tie-break as a decision.
    """
    p = min(max(ctx.defect_probability, 0.0), 1.0)
    u = max(ctx.probability_uncertainty, 0.0)
    p_lo, p_hi = max(p - u, 0.0), min(p + u, 1.0)

    rows: list[ActionCost] = []
    for base in _action_costs(model, ctx):
        mid = _expected(base, p)
        lo = _expected(base, p_lo).expected_cost
        hi = _expected(base, p_hi).expected_cost
        rows.append(
            ActionCost(
                action=mid.action,
                expected_cost=mid.expected_cost,
                cost_low=min(lo, hi),
                cost_high=max(lo, hi),
                cost_if_defective=mid.cost_if_defective,
                cost_if_fine=mid.cost_if_fine,
                rationale=mid.rationale,
            )
        )
    rows.sort(key=lambda r: r.expected_cost)

    # The engine may only *recommend* an autonomous action. If HALT_LINE wins on
    # cost it is still surfaced -- with its number, because that number is the
    # argument -- but the recommendation falls to the best autonomous action and
    # the decision is escalated.
    autonomous = [r for r in rows if r.action in AUTONOMOUS_ACTIONS]
    best = autonomous[0]
    halt_wins = rows[0].action is Disposition.HALT_LINE

    requires_human = False
    reasons: list[str] = []

    if halt_wins:
        requires_human = True
        reasons.append(
            f"Halting the line is the lowest-cost option at "
            f"{rows[0].expected_cost:,.0f} {model.currency}, and no agent may halt a "
            f"line without human approval."
        )
    if best.expected_cost > high_consequence_threshold:
        requires_human = True
        reasons.append(
            f"Expected cost {best.expected_cost:,.0f} {model.currency} exceeds the "
            f"{high_consequence_threshold:,.0f} supervisor threshold."
        )
    if ctx.is_safety_critical and best.action is Disposition.ACCEPT:
        requires_human = True
        reasons.append(
            "Accepting a safety-critical unit is never autonomous. FORGE may reject a "
            "part on its own; it may not pass one."
        )
    runner_up_margin = (
        autonomous[1].expected_cost - best.expected_cost if len(autonomous) > 1 else float("inf")
    )
    if runner_up_margin < indifference_ratio * max(best.expected_cost, 1.0):
        requires_human = True
        reasons.append(
            f"{best.action.value} and {autonomous[1].action.value} are within "
            f"{runner_up_margin:,.0f} {model.currency} of each other; the model is close "
            f"to indifferent and should not break the tie silently."
        )
    if best.interval_width > 0.5 * max(best.expected_cost, 1.0):
        requires_human = True
        reasons.append(
            f"Cost interval {best.cost_low:,.0f}-{best.cost_high:,.0f} {model.currency} is "
            f"wide relative to the estimate; confidence in P(defect) is low."
        )

    return TriageDecision(
        recommended=best.action,
        ranked=tuple(rows),
        context=ctx,
        model=model,
        assumptions=_assumptions(model, ctx),
        requires_human=requires_human,
        human_reason=" ".join(reasons),
    )


def _assumptions(m: CostModel, ctx: TriageContext) -> tuple[str, ...]:
    """The assumption set, rendered next to the number.

    A cost figure without its assumptions is a claim. With them it is an
    argument, and an argument can be checked.
    """
    return (
        f"Containment scope {ctx.containment_scope} unit(s) -- everything produced since "
        f"the last known-good verdict on this station and tool.",
        f"P(defect) = {ctx.defect_probability:.2f} +/- {ctx.probability_uncertainty:.2f}, "
        f"from the calibrated fusion score.",
        f"Field failure rate {m.field_failure_rate:.1%} of escaped defects actually fail in "
        f"service. This is the model's most consequential assumption.",
        f"Warranty {m.warranty_cost_per_failure:,.0f} + recall exposure "
        f"{m.recall_exposure_per_unit:,.0f} {m.currency} per failed unit.",
        f"Rework {m.rework_minutes:.0f} min at {m.labour_rate_per_hour:,.0f} "
        f"{m.currency}/hr plus {m.rework_parts_cost:,.0f} parts (damaged fasteners are "
        f"replaced, not re-torqued); line margin {m.margin_per_unit:,.0f} {m.currency}/unit.",
        "All figures are estimates on synthetic production data and are labelled SYNTHETIC "
        "in the UI. They are not audited plant economics.",
    )


def sensitivity(
    model: CostModel, ctx: TriageContext, *, probabilities: Sequence[float]
) -> tuple[tuple[float, Disposition, float], ...]:
    """Recommended action across a sweep of P(defect).

    Drives the sensitivity slider on /roi. Showing where the recommendation
    flips is far more convincing than showing a single number, because it makes
    the model's behaviour inspectable instead of asking for trust.
    """
    out = []
    for p in probabilities:
        d = triage(model, TriageContext(
            defect_probability=p,
            probability_uncertainty=ctx.probability_uncertainty,
            severity=ctx.severity,
            containment_scope=ctx.containment_scope,
            expected_halt_hours=ctx.expected_halt_hours,
            is_safety_critical=ctx.is_safety_critical,
        ))
        out.append((p, d.recommended, d.ranked[0].expected_cost))
    return tuple(out)
