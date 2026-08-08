"""Adjudication: reconciling signals that disagree.

Pure functions, no I/O, no model. Extracted here so the LangGraph node and the
existing service call exactly the same logic rather than drifting apart — two
implementations of a safety decision is one too many.

The rule that matters: **we do not average confidences.** Averaging lets one
strongly anomalous signal be diluted by several nominal ones, which is precisely
the dilution that makes a contaminated fastener look acceptable. Instead we
reason about which signal is informative under these specific conditions, in a
fixed precedence order:

    1. a failed deterministic verifier   — a measurement, decisive on its own
    2. fusion-only                       — every signal passes, the shape does not
    3. an out-of-spec endpoint           — decisive, and signature was not needed
    4. anomalous but not decisive        — escalate; ambiguity is a real answer
    5. nothing anomalous                 — pass

Precedence is deliberate. A verifier outranks a signature because counting five
fasteners is exact while a score is an estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from forge.domain.enums import Severity, SignalKind, Verdict

if TYPE_CHECKING:
    from collections.abc import Sequence

    from forge.domain.torque import SignatureBaseline, SignatureVerdict

# Fused confidence inside this band is too uncertain to act on alone.
HITL_CONFIDENCE_BAND = (0.45, 0.70)


@dataclass(frozen=True, slots=True)
class FastenerOutcome:
    """One inspection point's signature result."""

    position: int
    signature: SignatureVerdict

    @property
    def fusion_only(self) -> bool:
        return self.signature.fusion_only


@dataclass(frozen=True, slots=True)
class Adjudication:
    verdict: Verdict
    severity: Severity
    confidence: float
    primary_signal: SignalKind
    reasoning: str
    fusion_only: bool
    # True when the signals disagree enough to justify one re-query. The graph
    # reads this to decide whether to fire the reflection loop, so the trigger
    # is a measured condition rather than a vibe.
    signals_conflict: bool = False


def adjudicate(
    outcomes: Sequence[FastenerOutcome],
    baseline: SignatureBaseline,
    *,
    verifiers_passed: bool,
    verifier_detail: str = "",
) -> Adjudication:
    """Reconcile vision, process and spec into one verdict."""
    if not outcomes:
        return Adjudication(
            verdict=Verdict.ESCALATE,
            severity=Severity.MAJOR,
            confidence=0.0,
            primary_signal=SignalKind.NONE,
            reasoning=(
                "No analysable signals were produced, so no verdict can be computed. "
                "A missing measurement is not a passing measurement."
            ),
            fusion_only=False,
        )

    anomalous = [o for o in outcomes if o.signature.signature_anomalous]
    fusion = [o for o in outcomes if o.fusion_only]
    endpoints_ok = all(o.signature.endpoint_in_spec for o in outcomes)

    # 1. A deterministic verifier failed. Exact measurement beats every estimate.
    if not verifiers_passed:
        return Adjudication(
            verdict=Verdict.DEFECT,
            severity=Severity.CRITICAL,
            confidence=0.99,
            primary_signal=SignalKind.VISION,
            reasoning=(
                f"Geometric verifier failed{': ' + verifier_detail if verifier_detail else ''}. "
                f"This is a deterministic measurement against the pack specification, not an "
                f"estimate, so it is decisive on its own."
            ),
            fusion_only=False,
        )

    # 2. THE case: every individual signal passes, the signature does not.
    if fusion and verifiers_passed:
        worst = max(fusion, key=lambda o: o.signature.anomaly_score)
        f = worst.signature.features
        return Adjudication(
            verdict=Verdict.DEFECT,
            severity=Severity.CRITICAL,
            confidence=min(0.60 + worst.signature.anomaly_score * 0.35, 0.97),
            primary_signal=SignalKind.FUSION,
            reasoning=(
                f"Final torque is in spec at {f.final_torque_nm:.1f} Nm and vision confirms the "
                f"fastener is present and seated, so both signals individually PASS. However the "
                f"elastic slope at position {worst.position} is "
                f"{f.elastic_slope_nm_per_deg:.2f} Nm/deg against a "
                f"{baseline.elastic_slope_nm_per_deg:.2f} baseline, and the seating knee is "
                f"delayed to {f.knee_angle_deg:.1f}deg from {baseline.knee_angle_deg:.1f}deg. "
                f"That signature is consistent with contaminated threads, under which torque is "
                f"not a valid proxy for clamp load. Neither signal catches this alone."
            ),
            fusion_only=True,
            # Vision and spec say pass, process says fail. That IS the conflict.
            signals_conflict=True,
        )

    # 3. Endpoint out of spec. Decisive; signature analysis was not needed.
    if not endpoints_ok:
        bad = next(o for o in outcomes if not o.signature.endpoint_in_spec)
        f = bad.signature.features
        return Adjudication(
            verdict=Verdict.DEFECT,
            severity=Severity.CRITICAL,
            confidence=0.95,
            primary_signal=SignalKind.PROCESS,
            reasoning=(
                f"Final torque {f.final_torque_nm:.1f} Nm at position {bad.position} is outside "
                f"the {baseline.spec_lo_nm:.0f}-{baseline.spec_hi_nm:.0f} Nm specification. The "
                f"endpoint check is decisive here; signature analysis was not needed."
            ),
            fusion_only=False,
        )

    # 4. Anomalous, but nothing decisive. Escalating is the correct answer.
    if anomalous:
        worst = max(anomalous, key=lambda o: o.signature.anomaly_score)
        return Adjudication(
            verdict=Verdict.ESCALATE,
            severity=worst.signature.severity,
            confidence=0.45 + worst.signature.anomaly_score * 0.20,
            primary_signal=SignalKind.PROCESS,
            reasoning=(
                f"Signature at position {worst.position} scores "
                f"{worst.signature.anomaly_score:.2f} but no single signal is decisive. "
                f"Escalating is the correct outcome when the evidence is genuinely ambiguous."
            ),
            fusion_only=False,
            signals_conflict=True,
        )

    # 5. Nominal.
    return Adjudication(
        verdict=Verdict.PASS,
        severity=Severity.MINOR,
        confidence=0.94,
        primary_signal=SignalKind.FUSION,
        reasoning=(
            f"All {len(outcomes)} inspection points within spec and signature nominal. "
            f"Verifiers confirm count, seating and spacing."
        ),
        fusion_only=False,
    )


def needs_human(adjudication: Adjudication, *, triage_requires_human: bool) -> tuple[bool, str]:
    """Whether a human must decide, and why.

    Two independent triggers: the cost engine asking for one, or a fused
    confidence sitting in the band where we are not confident enough to act
    alone. PASS never routes to a human on confidence — a passing unit is not
    ambiguous, it is fine.
    """
    if triage_requires_human:
        return True, ""
    if (
        adjudication.verdict is not Verdict.PASS
        and HITL_CONFIDENCE_BAND[0] <= adjudication.confidence <= HITL_CONFIDENCE_BAND[1]
    ):
        return True, (
            f"Fused confidence {adjudication.confidence:.2f} sits in the uncertainty band "
            f"{HITL_CONFIDENCE_BAND[0]}-{HITL_CONFIDENCE_BAND[1]}; an inspector decides."
        )
    return False, ""
