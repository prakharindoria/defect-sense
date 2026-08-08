"""Generic signal engine.

The first test class is the one that matters: it proves the generalized engine
reproduces the component-specific torque analyser **exactly**, feature by
feature, across every defect class and severity. That is the evidence the
refactor lost nothing — without it, "we generalized it" is a claim.

Everything else here is the engine's own behaviour: learned baselines, noisy-OR
scoring, and the structural guards that stop a bad series producing a confident
answer.
"""

from __future__ import annotations

import math
import random

import pytest
from data.generators.torque_curve import CurveClass, CurveSpec, generate

from forge.domain import signals as S  # noqa: N812 - module alias, reads better in maths
from forge.domain.torque import extract_features

SPEC = CurveSpec()
WHEEL_EXTRACTORS = ["piecewise_linear", "endpoint", "stability"]

# What the wheel component declares. Weights and directions are physical: a
# STEEPER elastic slope is not the contamination signature, so slope is -1.
WHEEL_RULES = (
    S.DeviationRule("piecewise_linear.breakpoint_x", 0.35, +1,
                    "Seating knee at {observed:.1f} deg against a {expected:.1f} deg baseline."),
    S.DeviationRule("piecewise_linear.slope_after", 0.40, -1,
                    "Elastic slope {observed:.2f} Nm/deg against a {expected:.2f} baseline."),
    S.DeviationRule("piecewise_linear.residual_variance", 0.35, +1,
                    "Residual variance {observed:.3f}; the run is not smooth."),
    S.DeviationRule("piecewise_linear.mean_before", 0.30, +1,
                    "Run-down torque averaged {observed:.1f} Nm before seating."),
    S.DeviationRule("piecewise_linear.late_early_slope_ratio", 0.45, -1,
                    "Slope decayed to {observed:.2f} of initial; deforming plastically."),
    S.DeviationRule("stability.reversal_count", 0.20, +1,
                    "{observed:.0f} torque reversals during the run."),
)
# Spec limits are GIVEN by engineering, never learned from the data.
WHEEL_LIMITS = (S.SpecLimit("endpoint.final_y", lo=SPEC.spec_lo_nm, hi=SPEC.spec_hi_nm),)


def to_series(curve) -> S.SignalSeries:  # noqa: ANN001
    return S.SignalSeries(
        channel="torque",
        kind=S.SeriesKind.SWEEP,
        samples=tuple(S.Sample(s.angle_deg, s.torque_nm) for s in curve.samples),
        x_unit="deg", y_unit="Nm", position=curve.fastener_position,
    )


def features_for(label: CurveClass, severity: float = 0.8, seed: int = 42) -> dict[str, float]:
    return S.extract(to_series(generate(label, severity=severity, seed=seed).curve),
                     WHEEL_EXTRACTORS)


@pytest.fixture(scope="module")
def baseline() -> S.FeatureBaseline:
    rng = random.Random(20260807)  # noqa: S311
    runs = [
        S.extract(to_series(generate(CurveClass.CLEAN, spec=SPEC,
                                     seed=rng.randint(0, 2**31 - 1)).curve), WHEEL_EXTRACTORS)
        for _ in range(120)
    ]
    return S.FeatureBaseline.from_clean_runs(runs, sigma=3.0)


# ---------------------------------------------------------------------------
# The refactor lost nothing
# ---------------------------------------------------------------------------
class TestEquivalenceWithTorqueAnalyser:
    """Generic extractors must reproduce the torque-specific analyser exactly.

    The mapping is the whole point of the generalization:

        seating knee angle  ->  piecewise_linear.breakpoint_x
        elastic slope       ->  piecewise_linear.slope_after
        run-down torque     ->  piecewise_linear.mean_before
    """

    PAIRS = (
        ("knee_angle_deg", "piecewise_linear.breakpoint_x"),
        ("elastic_slope_nm_per_deg", "piecewise_linear.slope_after"),
        ("rundown_slope_nm_per_deg", "piecewise_linear.slope_before"),
        ("rundown_torque_mean_nm", "piecewise_linear.mean_before"),
        ("elastic_residual_variance", "piecewise_linear.residual_variance"),
        ("elastic_r2", "piecewise_linear.r2"),
        ("late_to_early_slope_ratio", "piecewise_linear.late_early_slope_ratio"),
        ("final_torque_nm", "endpoint.final_y"),
        ("peak_torque_nm", "endpoint.peak_y"),
    )

    @pytest.mark.parametrize("label", list(CurveClass))
    @pytest.mark.parametrize("severity", [0.2, 0.5, 0.8])
    def test_features_match_bit_for_bit(self, label: CurveClass, severity: float) -> None:
        curve = generate(label, severity=severity, seed=7).curve
        old = extract_features(curve)
        new = S.extract(to_series(curve), WHEEL_EXTRACTORS)

        for old_name, new_name in self.PAIRS:
            assert math.isclose(
                getattr(old, old_name), new[new_name], rel_tol=1e-12, abs_tol=1e-12
            ), f"{old_name} != {new_name} for {label.value} at severity {severity}"

    def test_reversal_count_matches(self) -> None:
        curve = generate(CurveClass.CROSS_THREADING, severity=0.8, seed=7).curve
        assert float(extract_features(curve).reversal_count) == (
            S.extract(to_series(curve), WHEEL_EXTRACTORS)["stability.reversal_count"]
        )


# ---------------------------------------------------------------------------
# Behaviour: the money shot survives generalization
# ---------------------------------------------------------------------------
@pytest.mark.demo_path
def test_contamination_is_still_fusion_only(baseline: S.FeatureBaseline) -> None:
    """Torque endpoint passes, shape does not. NHTSA 24V237000's failure mode."""
    result = S.score_features(
        features_for(CurveClass.THREAD_CONTAMINATION, 0.8), baseline, WHEEL_RULES, WHEEL_LIMITS
    )
    assert result.in_spec, "if the endpoint already fails, this is not a fusion-only case"
    assert result.anomalous
    assert result.fusion_only


@pytest.mark.demo_path
def test_fusion_only_detection_rate(baseline: S.FeatureBaseline) -> None:
    """Must fire reliably, not on one lucky seed. Eval target is > 0.80."""
    caught = sum(
        1
        for seed in range(40)
        for sev in (0.5, 0.7, 0.9)
        if S.score_features(
            features_for(CurveClass.THREAD_CONTAMINATION, sev, seed),
            baseline, WHEEL_RULES, WHEEL_LIMITS,
        ).fusion_only
    )
    rate = caught / 120
    assert rate > 0.80, f"fusion-only detection rate {rate:.2%} is below target"


def test_clean_runs_are_not_flagged(baseline: S.FeatureBaseline) -> None:
    """False positives cost real money in unnecessary rework."""
    flagged = sum(
        1 for seed in range(100)
        if S.score_features(
            features_for(CurveClass.CLEAN, 0.0, seed), baseline, WHEEL_RULES, WHEEL_LIMITS
        ).anomalous
    )
    assert flagged / 100 <= 0.02, f"false positive rate {flagged}% exceeds the 2% budget"


def test_under_torque_caught_by_endpoint_not_shape(baseline: S.FeatureBaseline) -> None:
    """An honest negative.

    Under-torque leaves a normal-shaped curve that simply stops early, so the
    SHAPE score stays near zero and the spec limit is what catches it. Knowing
    which mechanism catches which defect is the point of having both.
    """
    result = S.score_features(
        features_for(CurveClass.UNDER_TORQUE, 0.8), baseline, WHEEL_RULES, WHEEL_LIMITS
    )
    assert not result.in_spec
    assert not result.fusion_only
    assert result.score < 0.2


def test_learned_ceilings_beat_hand_picked_ones(baseline: S.FeatureBaseline) -> None:
    """Mild over-torque that stays in spec.

    The previous torque analyser used hand-set one-sided ceilings and scored
    this 0.27 — a miss, documented as a known limit. Learning the ceiling from
    the clean distribution instead catches it. Generalizing improved sensitivity
    rather than costing it.
    """
    result = S.score_features(
        features_for(CurveClass.OVER_TORQUE, 0.4), baseline, WHEEL_RULES, WHEEL_LIMITS
    )
    assert result.anomalous
    assert result.score > 0.45


# ---------------------------------------------------------------------------
# Extractors work on other signal kinds
# ---------------------------------------------------------------------------
def _timeseries(values: list[float], channel: str = "vibration") -> S.SignalSeries:
    return S.SignalSeries(
        channel=channel, kind=S.SeriesKind.TIMESERIES,
        samples=tuple(S.Sample(float(i), v) for i, v in enumerate(values)),
    )


def test_trend_detects_drift() -> None:
    """A bearing warming up is a rising temperature trend, not a sweep."""
    rising = _timeseries([20.0 + 0.5 * i for i in range(60)], "temperature")
    features = S.extract(rising, ["trend"])
    assert features["trend.slope"] == pytest.approx(0.5, abs=1e-9)
    assert features["trend.r2"] > 0.99
    assert features["trend.drift"] == pytest.approx(0.5 * 59, abs=1e-6)


def test_distribution_flags_impulsive_signal() -> None:
    """Kurtosis rises on impulsive bearing faults well before RMS moves."""
    rng = random.Random(1)  # noqa: S311
    smooth = _timeseries([rng.gauss(0, 1) for _ in range(400)])
    spiky = [rng.gauss(0, 1) for _ in range(400)]
    for i in range(0, 400, 50):
        spiky[i] += 12.0

    assert (
        S.extract(_timeseries(spiky), ["distribution"])["distribution.kurtosis"]
        > S.extract(smooth, ["distribution"])["distribution.kurtosis"]
    )


def test_spectral_finds_a_planted_tone() -> None:
    n = 512
    freq = 0.08
    tone = _timeseries([math.sin(2 * math.pi * freq * i) for i in range(n)])
    features = S.spectral(tone, bands=[freq, 0.3])

    assert features["band_0_energy"] > features["band_1_energy"] * 10
    assert features["dominant_freq"] == pytest.approx(freq, abs=0.03)


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------
def test_baseline_refuses_too_few_runs() -> None:
    """A baseline fitted to a handful of samples is worse than none: it is believed."""
    runs = [features_for(CurveClass.CLEAN, 0.0, seed) for seed in range(5)]
    with pytest.raises(S.SignalError, match="clean runs"):
        S.FeatureBaseline.from_clean_runs(runs)


def test_baseline_floors_zero_variance_tolerance() -> None:
    """A perfectly repeatable feature must not become an infinitely sensitive detector."""
    runs = [{"const.value": 5.0} for _ in range(30)]
    baseline = S.FeatureBaseline.from_clean_runs(runs)
    assert baseline.tolerances["const.value"] > 0


def test_spec_limits_are_not_learned(baseline: S.FeatureBaseline) -> None:
    """Spec limits come from engineering. A drifted process must not redefine them."""
    assert "endpoint.final_y" in baseline.means      # baseline knows the mean
    assert WHEEL_LIMITS[0].lo == SPEC.spec_lo_nm     # but the limit is declared
    assert WHEEL_LIMITS[0].hi == SPEC.spec_hi_nm


# ---------------------------------------------------------------------------
# Scoring properties
# ---------------------------------------------------------------------------
def test_noisy_or_is_not_diluted_by_nominal_features(baseline: S.FeatureBaseline) -> None:
    """The property that makes contamination detectable.

    Averaging would let one strongly anomalous feature be washed out by several
    nominal ones. Adding rules for features that are perfectly normal must not
    lower the score.
    """
    features = features_for(CurveClass.THREAD_CONTAMINATION, 0.8)
    focused = S.score_features(features, baseline, WHEEL_RULES[:2], WHEEL_LIMITS)
    padded = S.score_features(features, baseline, WHEEL_RULES, WHEEL_LIMITS)
    assert padded.score >= focused.score


def test_direction_is_physical(baseline: S.FeatureBaseline) -> None:
    """A steeper-than-baseline slope is not the contamination signature."""
    features = dict(features_for(CurveClass.CLEAN, 0.0))
    features["piecewise_linear.slope_after"] = baseline.means[
        "piecewise_linear.slope_after"
    ] + 10 * baseline.tolerances["piecewise_linear.slope_after"]

    rule = S.DeviationRule("piecewise_linear.slope_after", 0.4, -1)
    assert S.score_features(features, baseline, [rule]).score == 0.0


def test_deviation_statements_carry_the_numbers(baseline: S.FeatureBaseline) -> None:
    """These go straight onto the workbench and into the prompt. Cited numbers, not adjectives."""
    result = S.score_features(
        features_for(CurveClass.THREAD_CONTAMINATION, 0.8), baseline, WHEEL_RULES, WHEEL_LIMITS
    )
    assert result.deviations
    assert any(char.isdigit() for char in result.deviations[0].statement)


def test_missing_feature_is_skipped_not_guessed(baseline: S.FeatureBaseline) -> None:
    """A rule over a channel this component does not have must contribute nothing."""
    rule = S.DeviationRule("spectral.band_9_energy", 0.9, +1)
    assert S.score_features(features_for(CurveClass.CLEAN, 0.0), baseline, [rule]).score == 0.0


# ---------------------------------------------------------------------------
# Structural guards
# ---------------------------------------------------------------------------
def test_rejects_too_few_samples() -> None:
    with pytest.raises(S.SignalError, match="need >="):
        S.SignalSeries(channel="t", samples=tuple(S.Sample(float(i), 1.0) for i in range(4)))


def test_rejects_non_finite_values() -> None:
    samples = [S.Sample(float(i), 1.0) for i in range(20)]
    samples[3] = S.Sample(3.0, math.nan)
    with pytest.raises(S.SignalError, match="non-finite"):
        S.SignalSeries(channel="t", samples=tuple(samples))


def test_sweep_must_advance_monotonically() -> None:
    samples = [S.Sample(float(i), 1.0) for i in range(20)]
    samples[7] = S.Sample(2.0, 1.0)
    with pytest.raises(S.SignalError, match="monotonic"):
        S.SignalSeries(channel="t", samples=tuple(samples), kind=S.SeriesKind.SWEEP)


def test_timeseries_may_go_backwards_in_value() -> None:
    """Only a sweep requires monotonic x. Vibration goes wherever it likes."""
    S.SignalSeries(
        channel="vibration", kind=S.SeriesKind.TIMESERIES,
        samples=tuple(S.Sample(float(i), math.sin(i)) for i in range(40)),
    )


def test_unknown_extractor_is_an_error() -> None:
    series = to_series(generate(CurveClass.CLEAN, seed=1).curve)
    with pytest.raises(S.SignalError, match="unknown extractor"):
        S.extract(series, ["does_not_exist"])


def test_features_are_namespaced() -> None:
    """Two extractors both reporting r2 must not silently overwrite each other."""
    features = S.extract(to_series(generate(CurveClass.CLEAN, seed=1).curve),
                         ["piecewise_linear", "trend"])
    assert "piecewise_linear.r2" in features
    assert "trend.r2" in features
    assert features["piecewise_linear.r2"] != features["trend.r2"]


def test_extraction_is_deterministic() -> None:
    series = to_series(generate(CurveClass.THREAD_CONTAMINATION, severity=0.8, seed=3).curve)
    runs = [S.extract(series, WHEEL_EXTRACTORS) for _ in range(5)]
    assert all(r == runs[0] for r in runs)
