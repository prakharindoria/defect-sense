"""Torque-angle signature analysis.

The test at the centre of this file is `test_contamination_is_fusion_only`. If
it fails, the product's central claim is false and the demo has no money shot.
Treat a failure here as a stop-the-line event, not a flaky test.
"""

from __future__ import annotations

import math

import pytest
from data.generators.torque_curve import (
    CurveClass,
    CurveSpec,
    generate,
    generate_wheel,
    learn_baseline,
)

from forge.domain.enums import Severity
from forge.domain.torque import (
    MIN_CURVE_POINTS,
    SignatureBaseline,
    TorqueAngleCurve,
    TorqueCurveError,
    TorqueSample,
    extract_features,
    score_signature,
)

SPEC = CurveSpec()


@pytest.fixture(scope="module")
def baseline() -> SignatureBaseline:
    """Learned from clean runs, exactly as production would derive it."""
    return learn_baseline(SPEC, runs=120, sigma=3.0)


def score(label: CurveClass, severity: float, baseline: SignatureBaseline, seed: int = 42):  # noqa: ANN201
    return score_signature(extract_features(generate(label, severity=severity, seed=seed).curve),
                           baseline)


# ---------------------------------------------------------------------------
# THE claim
# ---------------------------------------------------------------------------
@pytest.mark.demo_path
def test_contamination_is_fusion_only(baseline: SignatureBaseline) -> None:
    """Contaminated threads: torque endpoint passes, signature fails.

    This is NHTSA campaign 24V237000's failure mode and the reason the system
    is multi-agent at all. Both halves must hold:
      1. the final torque lands INSIDE the specification band, so a torque gun
         reading the endpoint reports PASS;
      2. the signature is nonetheless anomalous.
    """
    v = score(CurveClass.THREAD_CONTAMINATION, 0.8, baseline)

    assert v.endpoint_in_spec, (
        f"final torque {v.features.final_torque_nm:.1f} Nm fell outside "
        f"{baseline.spec_lo_nm}-{baseline.spec_hi_nm} Nm. If the endpoint already "
        f"fails, this is not a fusion-only case and the demo claim is wrong."
    )
    assert v.signature_anomalous
    assert v.fusion_only
    assert v.severity is Severity.CRITICAL
    assert v.ranked_classes[0][0] == "thread_contamination"


@pytest.mark.demo_path
def test_contamination_detected_across_severities(baseline: SignatureBaseline) -> None:
    """The case must fire reliably, not just on one lucky seed.

    `make eval` reports fusion_only_detection_rate with a target > 0.80; this
    is the unit-level version of that measurement.
    """
    caught = sum(
        1
        for seed in range(40)
        for sev in (0.5, 0.7, 0.9)
        if score(CurveClass.THREAD_CONTAMINATION, sev, baseline, seed).fusion_only
    )
    total = 40 * 3
    rate = caught / total
    assert rate > 0.80, f"fusion-only detection rate {rate:.2%} is below the 80% target"


def test_contamination_shape_matches_the_physics(baseline: SignatureBaseline) -> None:
    """Late knee AND shallow slope -- the specific signature, not just 'anomalous'."""
    f = score(CurveClass.THREAD_CONTAMINATION, 0.8, baseline).features
    assert f.knee_angle_deg > baseline.knee_angle_deg + baseline.knee_angle_tolerance_deg
    assert f.elastic_slope_nm_per_deg < (
        baseline.elastic_slope_nm_per_deg - baseline.elastic_slope_tolerance
    )


# ---------------------------------------------------------------------------
# The other classes
# ---------------------------------------------------------------------------
def test_clean_runs_are_not_flagged(baseline: SignatureBaseline) -> None:
    """False positives cost real money in unnecessary rework. Measure the rate."""
    flagged = sum(
        1 for seed in range(100) if score(CurveClass.CLEAN, 0.0, baseline, seed).signature_anomalous
    )
    assert flagged / 100 <= 0.02, f"false positive rate {flagged}% exceeds the 2% budget"


def test_clean_run_recovers_the_generating_parameters(baseline: SignatureBaseline) -> None:
    """The breakpoint fit must actually find the knee it was given."""
    f = score(CurveClass.CLEAN, 0.0, baseline).features
    assert f.knee_angle_deg == pytest.approx(SPEC.knee_angle_deg, abs=1.5)
    assert f.elastic_slope_nm_per_deg == pytest.approx(SPEC.elastic_slope_nm_per_deg, abs=0.2)
    assert f.elastic_r2 > 0.98


def test_cross_threading_shows_erratic_run(baseline: SignatureBaseline) -> None:
    v = score(CurveClass.CROSS_THREADING, 0.8, baseline)
    assert v.signature_anomalous
    assert v.features.elastic_residual_variance > baseline.residual_variance_max
    assert v.ranked_classes[0][0] == "cross_threading"


def test_over_torque_flattens_past_yield(baseline: SignatureBaseline) -> None:
    v = score(CurveClass.OVER_TORQUE, 0.9, baseline)
    assert v.features.late_to_early_slope_ratio < baseline.yield_flattening_ratio
    assert any(c[0] == "over_torque" for c in v.ranked_classes)


def test_under_torque_is_caught_by_the_endpoint_not_the_shape(
    baseline: SignatureBaseline,
) -> None:
    """An honest negative result.

    Under-torque leaves a normal-shaped curve that simply stops early, so the
    signature score stays low and the ENDPOINT check is what catches it. We
    assert that rather than pretending signature analysis catches everything --
    knowing which mechanism catches which defect is the point of having both.
    """
    v = score(CurveClass.UNDER_TORQUE, 0.8, baseline)
    assert not v.endpoint_in_spec
    assert not v.fusion_only, "under-torque is an endpoint failure, not a fusion-only case"
    assert any(c[0] == "under_torque" for c in v.ranked_classes)


# ---------------------------------------------------------------------------
# Baseline derivation
# ---------------------------------------------------------------------------
def test_baseline_is_learned_not_hardcoded(baseline: SignatureBaseline) -> None:
    assert baseline.derived_from_runs == 120
    assert baseline.sigma_multiplier == 3.0
    assert baseline.knee_angle_deg == pytest.approx(SPEC.knee_angle_deg, abs=0.5)
    # Spec limits must come from engineering, never from observed data.
    assert baseline.spec_lo_nm == SPEC.spec_lo_nm
    assert baseline.spec_hi_nm == SPEC.spec_hi_nm


def test_baseline_refuses_too_few_runs() -> None:
    """A baseline fitted to a handful of samples is worse than none: it gets believed."""
    features = [extract_features(generate(CurveClass.CLEAN, seed=i).curve) for i in range(5)]
    with pytest.raises(TorqueCurveError, match="clean runs"):
        SignatureBaseline.from_clean_runs(features, spec_lo_nm=110, spec_hi_nm=125)


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------
def test_rejects_too_few_samples() -> None:
    with pytest.raises(TorqueCurveError, match="need >="):
        TorqueAngleCurve(1, tuple(TorqueSample(float(i), 1.0) for i in range(MIN_CURVE_POINTS - 1)))


def test_rejects_non_finite_samples() -> None:
    samples = [TorqueSample(float(i), 1.0) for i in range(MIN_CURVE_POINTS)]
    samples[3] = TorqueSample(3.0, math.nan)
    with pytest.raises(TorqueCurveError, match="non-finite"):
        TorqueAngleCurve(1, tuple(samples))


def test_rejects_non_monotonic_angle() -> None:
    samples = [TorqueSample(float(i), 1.0) for i in range(MIN_CURVE_POINTS)]
    samples[5] = TorqueSample(2.0, 1.0)
    with pytest.raises(TorqueCurveError, match="non-decreasing"):
        TorqueAngleCurve(1, tuple(samples))


# ---------------------------------------------------------------------------
# Determinism -- required by MWAI-FRS AI-002
# ---------------------------------------------------------------------------
def test_same_seed_gives_identical_features(baseline: SignatureBaseline) -> None:
    """FRS AI-002: replaying the same curve five times returns identical features."""
    runs = [score(CurveClass.THREAD_CONTAMINATION, 0.8, baseline, seed=7).features
            for _ in range(5)]
    assert all(r == runs[0] for r in runs)


# ---------------------------------------------------------------------------
# Whole wheel
# ---------------------------------------------------------------------------
def test_one_bad_fastener_among_four_good(baseline: SignatureBaseline) -> None:
    """Realistic defect shape: a single contaminated position, not five."""
    wheel = generate_wheel(
        {3: (CurveClass.THREAD_CONTAMINATION, 0.8)}, seed=11, unit_id="VIN-TEST-0001"
    )
    verdicts = [score_signature(extract_features(g.curve), baseline) for g in wheel]

    assert len(verdicts) == 5
    flagged = [i + 1 for i, v in enumerate(verdicts) if v.signature_anomalous]
    assert flagged == [3], f"expected only position 3 flagged, got {flagged}"
    assert verdicts[2].fusion_only
    assert all(v.endpoint_in_spec for v in verdicts), (
        "every fastener should read in-spec torque; that is what makes this invisible"
    )
