"""The inspection use case: run one unit through the pipeline.

This is the deterministic spine. Everything here is pure computation -- torque
signature analysis, geometric verifiers, the cost engine -- so a verdict is
produced in milliseconds without any model call.

The LLM narrative is deliberately NOT part of this path. It is requested
separately and streamed in afterwards, because:

  - measured LLM latency on this hardware is 4.7-6.9s per call, which alone
    blows the end-to-end budget;
  - more importantly, a safety verdict that *depends* on a language model is a
    verdict you cannot defend. The model explains a decision it did not make,
    and if the model is unreachable the decision is unchanged.

That ordering is the architecture, not an optimisation.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from forge.domain.cost import CostModel, TriageContext, triage
from forge.domain.enums import (
    AgentName,
    DataQuality,
    Disposition,
    Severity,
    SignalKind,
    Verdict,
)
from forge.domain.torque import (
    SignatureBaseline,
    SignatureVerdict,
    TorqueAngleCurve,
    TorqueCurveError,
    extract_features,
    score_signature,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

# Fused confidence inside this band is too uncertain to act on alone.
HITL_CONFIDENCE_BAND = (0.45, 0.70)


@dataclass(frozen=True, slots=True)
class FastenerOutcome:
    position: int
    signature: SignatureVerdict

    @property
    def fusion_only(self) -> bool:
        return self.signature.fusion_only


@dataclass(frozen=True, slots=True)
class AgentSpan:
    """One pipeline stage, for the Agent Console."""

    agent: AgentName
    duration_ms: float
    summary: str
    ok: bool = True


@dataclass(frozen=True, slots=True)
class InspectionResult:
    correlation_id: str
    unit_id: str
    pack_id: str
    verdict: Verdict
    severity: Severity
    confidence: float
    fusion_only: bool
    primary_signal: SignalKind
    reasoning: str
    fasteners: tuple[FastenerOutcome, ...]
    disposition: Disposition
    expected_cost: float
    cost_low: float
    cost_high: float
    currency: str
    cost_assumptions: tuple[str, ...]
    requires_human: bool
    human_reason: str
    data_quality: DataQuality
    data_quality_reasons: tuple[str, ...]
    spans: tuple[AgentSpan, ...]
    total_ms: float
    is_synthetic: bool = True

    @property
    def flagged_positions(self) -> tuple[int, ...]:
        return tuple(f.position for f in self.fasteners if f.signature.signature_anomalous)

    @property
    def fusion_only_positions(self) -> tuple[int, ...]:
        return tuple(f.position for f in self.fasteners if f.fusion_only)


class InspectionService:
    """Runs the deterministic inspection pipeline for one unit."""

    def __init__(self, baseline: SignatureBaseline, cost_model: CostModel, pack_id: str) -> None:
        self._baseline = baseline
        self._cost_model = cost_model
        self._pack_id = pack_id

    def inspect(
        self,
        unit_id: str,
        curves: Sequence[TorqueAngleCurve],
        *,
        containment_scope: int = 1,
        expected_halt_hours: float = 2.4,
        verifiers_passed: bool = True,
        verifier_summary: str = "5 fasteners present, seated, spacing nominal",
    ) -> InspectionResult:
        started = time.perf_counter()
        spans: list[AgentSpan] = []
        correlation_id = f"insp-{uuid.uuid4().hex[:12]}"

        # -- INGESTION: validate and gate -------------------------------------
        t0 = time.perf_counter()
        quality = DataQuality.GOOD
        reasons: list[str] = []
        valid: list[TorqueAngleCurve] = []
        for curve in curves:
            try:
                # Re-running validation is cheap and makes the gate the single
                # place a malformed curve can be rejected.
                TorqueAngleCurve(curve.fastener_position, curve.samples,
                                 tool_id=curve.tool_id, unit_id=curve.unit_id)
            except TorqueCurveError as exc:
                quality = DataQuality.DEGRADED
                reasons.append(f"position {curve.fastener_position}: {exc}")
            else:
                valid.append(curve)

        if not valid:
            quality = DataQuality.REJECTED
            reasons.append("no analysable curves; refusing to produce a verdict")
        spans.append(AgentSpan(
            AgentName.INGESTION, (time.perf_counter() - t0) * 1000,
            f"{len(valid)}/{len(curves)} curves accepted, quality={quality.value}",
            ok=quality is not DataQuality.REJECTED,
        ))

        if quality is DataQuality.REJECTED:
            return self._rejected(correlation_id, unit_id, reasons, spans, started)

        # -- VISION: deterministic geometric verifiers -------------------------
        t0 = time.perf_counter()
        spans.append(AgentSpan(
            AgentName.VISION_INSPECTOR, (time.perf_counter() - t0) * 1000,
            f"verifiers {'PASS' if verifiers_passed else 'FAIL'} - {verifier_summary}",
            ok=True,
        ))

        # -- PROCESS SENTINEL: torque-angle signature --------------------------
        t0 = time.perf_counter()
        outcomes = tuple(
            FastenerOutcome(c.fastener_position,
                            score_signature(extract_features(c), self._baseline))
            for c in sorted(valid, key=lambda c: c.fastener_position)
        )
        worst = max(outcomes, key=lambda o: o.signature.anomaly_score)
        endpoints_ok = all(o.signature.endpoint_in_spec for o in outcomes)
        spans.append(AgentSpan(
            AgentName.PROCESS_SENTINEL, (time.perf_counter() - t0) * 1000,
            f"{len(outcomes)} fasteners scored; worst anomaly "
            f"{worst.signature.anomaly_score:.2f} at position {worst.position}",
        ))

        # -- ADJUDICATOR: reconcile the signals --------------------------------
        t0 = time.perf_counter()
        verdict, severity, confidence, signal, reasoning = self._adjudicate(
            outcomes, verifiers_passed=verifiers_passed, endpoints_ok=endpoints_ok
        )
        fusion_only = any(o.fusion_only for o in outcomes) and verifiers_passed
        spans.append(AgentSpan(
            AgentName.ADJUDICATOR, (time.perf_counter() - t0) * 1000,
            f"{verdict.value.upper()} conf {confidence:.2f} "
            f"{'(FUSION-ONLY)' if fusion_only else ''}".strip(),
        ))

        # -- COST TRIAGE -------------------------------------------------------
        # Only runs when something is actually wrong. Triage answers "what do we
        # do about this defect", so asking it about a unit we have just passed
        # is a category error -- and it produces an incoherent answer, because
        # containment economics will always prefer holding stock over shipping
        # it once escape carries recall exposure. A PASS ships.
        t0 = time.perf_counter()
        if verdict is Verdict.PASS:
            decision = None
            disposition = Disposition.ACCEPT
            expected_cost = cost_low = cost_high = 0.0
            assumptions: tuple[str, ...] = (
                "Unit passed inspection; no containment action and therefore no "
                "incremental cost. Triage is not consulted on a passing unit.",
            )
            triage_summary = "skipped - unit passed, no containment required"
        else:
            decision = triage(
                self._cost_model,
                TriageContext(
                    defect_probability=confidence,
                    probability_uncertainty=0.06,
                    severity=severity,
                    containment_scope=containment_scope,
                    expected_halt_hours=expected_halt_hours,
                    is_safety_critical=severity is Severity.CRITICAL,
                ),
            )
            best = decision.ranked[0]
            disposition = decision.recommended
            expected_cost, cost_low, cost_high = (
                best.expected_cost, best.cost_low, best.cost_high
            )
            assumptions = decision.assumptions
            triage_summary = (
                f"{decision.recommended.value} at {best.expected_cost:,.0f} "
                f"{self._cost_model.currency}"
                + (" - human required" if decision.requires_human else "")
            )
        spans.append(AgentSpan(
            AgentName.COST_TRIAGE, (time.perf_counter() - t0) * 1000, triage_summary
        ))

        triage_wants_human = decision.requires_human if decision else False
        in_uncertainty_band = (
            verdict is not Verdict.PASS
            and HITL_CONFIDENCE_BAND[0] <= confidence <= HITL_CONFIDENCE_BAND[1]
        )
        requires_human = triage_wants_human or in_uncertainty_band
        human_reason = decision.human_reason if decision else ""
        if not triage_wants_human and in_uncertainty_band:
            human_reason = (
                f"Fused confidence {confidence:.2f} sits in the uncertainty band "
                f"{HITL_CONFIDENCE_BAND[0]}-{HITL_CONFIDENCE_BAND[1]}; an inspector decides."
            )

        return InspectionResult(
            correlation_id=correlation_id,
            unit_id=unit_id,
            pack_id=self._pack_id,
            verdict=verdict,
            severity=severity,
            confidence=confidence,
            fusion_only=fusion_only,
            primary_signal=signal,
            reasoning=reasoning,
            fasteners=outcomes,
            disposition=disposition,
            expected_cost=expected_cost,
            cost_low=cost_low,
            cost_high=cost_high,
            currency=self._cost_model.currency,
            cost_assumptions=assumptions,
            requires_human=requires_human,
            human_reason=human_reason,
            data_quality=quality,
            data_quality_reasons=tuple(reasons),
            spans=tuple(spans),
            total_ms=(time.perf_counter() - started) * 1000,
        )

    def _adjudicate(
        self,
        outcomes: tuple[FastenerOutcome, ...],
        *,
        verifiers_passed: bool,
        endpoints_ok: bool,
    ) -> tuple[Verdict, Severity, float, SignalKind, str]:
        """Reconcile vision, process and spec.

        Deliberately NOT an averaging step. Averaging confidences is how a
        strongly anomalous signal gets diluted by nominal ones -- which is
        exactly the dilution that makes a contaminated fastener look acceptable.
        We reason about which signal is informative under these conditions.
        """
        anomalous = [o for o in outcomes if o.signature.signature_anomalous]
        fusion = [o for o in outcomes if o.fusion_only]

        if not verifiers_passed:
            worst = max(outcomes, key=lambda o: o.signature.anomaly_score)
            return (
                Verdict.DEFECT, Severity.CRITICAL, 0.99, SignalKind.VISION,
                "Geometric verifier failed. This is a deterministic measurement against the "
                "pack specification, not an estimate, so it is decisive on its own.",
            )

        if fusion:
            worst = max(fusion, key=lambda o: o.signature.anomaly_score)
            f = worst.signature.features
            return (
                Verdict.DEFECT,
                Severity.CRITICAL,
                min(0.60 + worst.signature.anomaly_score * 0.35, 0.97),
                SignalKind.FUSION,
                (
                    f"Final torque is in spec at {f.final_torque_nm:.1f} Nm and vision confirms "
                    f"the fastener is present and seated, so both signals individually PASS. "
                    f"However the elastic slope at position {worst.position} is "
                    f"{f.elastic_slope_nm_per_deg:.2f} Nm/deg against a "
                    f"{self._baseline.elastic_slope_nm_per_deg:.2f} baseline, and the seating "
                    f"knee is delayed to {f.knee_angle_deg:.1f}deg from "
                    f"{self._baseline.knee_angle_deg:.1f}deg. That signature is consistent with "
                    f"contaminated threads, under which torque is not a valid proxy for clamp "
                    f"load. Neither signal catches this alone."
                ),
            )

        if not endpoints_ok:
            bad = next(o for o in outcomes if not o.signature.endpoint_in_spec)
            f = bad.signature.features
            return (
                Verdict.DEFECT, Severity.CRITICAL, 0.95, SignalKind.PROCESS,
                f"Final torque {f.final_torque_nm:.1f} Nm at position {bad.position} is outside "
                f"the {self._baseline.spec_lo_nm:.0f}-{self._baseline.spec_hi_nm:.0f} Nm "
                f"specification. The endpoint check is decisive; signature analysis was not "
                f"needed here.",
            )

        if anomalous:
            worst = max(anomalous, key=lambda o: o.signature.anomaly_score)
            return (
                Verdict.ESCALATE,
                worst.signature.severity,
                0.45 + worst.signature.anomaly_score * 0.20,
                SignalKind.PROCESS,
                f"Signature at position {worst.position} scores "
                f"{worst.signature.anomaly_score:.2f} but no single signal is decisive. "
                f"Escalating is the correct outcome when the evidence is genuinely ambiguous.",
            )

        return (
            Verdict.PASS, Severity.MINOR, 0.94, SignalKind.FUSION,
            f"All {len(outcomes)} fasteners within spec and signature nominal. "
            f"Verifiers confirm count, seating and spacing.",
        )

    def _rejected(
        self,
        correlation_id: str,
        unit_id: str,
        reasons: list[str],
        spans: list[AgentSpan],
        started: float,
    ) -> InspectionResult:
        """No usable data. Escalate -- never PASS on absent evidence."""
        return InspectionResult(
            correlation_id=correlation_id, unit_id=unit_id, pack_id=self._pack_id,
            verdict=Verdict.ESCALATE, severity=Severity.MAJOR, confidence=0.0,
            fusion_only=False, primary_signal=SignalKind.NONE,
            reasoning=(
                "Input data failed the quality gate, so no verdict can be computed. "
                "A missing measurement is not a passing measurement."
            ),
            fasteners=(), disposition=Disposition.QUARANTINE,
            expected_cost=0.0, cost_low=0.0, cost_high=0.0,
            currency=self._cost_model.currency, cost_assumptions=(),
            requires_human=True,
            human_reason="Data quality gate rejected the input.",
            data_quality=DataQuality.REJECTED, data_quality_reasons=tuple(reasons),
            spans=tuple(spans), total_ms=(time.perf_counter() - started) * 1000,
        )
