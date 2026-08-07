"""Torque-angle signature analysis.

This module is the technical core of the product. Everything else -- the
agents, the dashboards, the MES write-back -- exists to act on what this
computes.

The physics
-----------
A wheel fastener torque specification assumes **clean, dry threads**. Torque is
not the quantity engineering actually cares about; *clamp load* is. The two are
related through thread friction:

    T  ~=  K * F * d          T = torque, F = clamp load, d = nominal diameter,
                              K = nut factor, dominated by thread friction

Contaminate the threads with oil, grit or corrosion and K changes. The torque
gun still reads a value inside the specification band while the delivered clamp
load is materially below target. This is the failure mode behind NHTSA campaign
24V237000 (General Motors, 2023 Chevrolet Colorado / GMC Canyon): *"the front
wheel hub bolts may have been over-tightened and damaged during installation."*

    Torque endpoint alone   118 Nm in a 110-125 Nm band   -> PASS
    Vision alone            bolt present, seated, count 5 -> PASS
    Reality                 clamp load ~30% under target  -> FAIL

Neither signal is wrong. Both are individually correct. The defect exists only
in the disagreement between them, which is precisely what a multi-agent system
is for.

What we measure instead
-----------------------
A fastener run traces torque against rotation angle, and that trace has shape:

      torque
        |                                   ______  yield / plastic
        |                              ____/
        |                        ____/            <- elastic slope
        |                   ____/
        |   ____________ __/                      <- knee (seating point)
        |  /            v
        +---------------------------------- angle
           run-down       elastic region

    clean, dry thread     knee ~14 deg, elastic slope ~3.2 Nm/deg, low residual
    contaminated thread   knee delayed to ~22 deg, slope ~2.1 Nm/deg
                          *** FINAL TORQUE STILL LANDS IN SPEC ***
    cross-threaded        erratic run-down, early false knee, high residual
    over-torqued          slope flattens past yield (plastic deformation)
    under-torqued         run terminates before the elastic region completes

We score the **shape**, not the endpoint. That is a standard fastening-process
technique (torque-angle signature monitoring), not something invented for a
hackathon, and it is what makes the fusion case detectable at all.

Design constraints
------------------
Pure standard library. No numpy, no scipy, no framework. The domain layer
imports nothing (CLAUDE.md architecture rules), and a curve is a few hundred
points, so hand-written least squares is both fast enough and completely
auditable -- a judge can read every line of the maths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from forge.domain.enums import Severity

if TYPE_CHECKING:
    from collections.abc import Sequence

# A two-segment fit needs enough points on each side to be meaningful.
MIN_SEGMENT_POINTS = 5
MIN_CURVE_POINTS = 2 * MIN_SEGMENT_POINTS

# How far past the tolerance edge a deviation must go before it counts at full
# strength. At 1.0 tolerance units the feature sits exactly at the edge of
# normal and contributes nothing; at 1.0 + SATURATION it contributes its full
# weight. Beyond that, more magnitude does not make the signal any more certain
# than it already is, so the contribution is clamped.
DEVIATION_SATURATION = 1.5


class TorqueCurveError(ValueError):
    """The curve cannot be analysed. Raised only for structurally invalid input.

    A curve that is *anomalous* is a normal result, not an error. This is raised
    only when the data itself is unusable -- too few points, non-finite values,
    non-monotonic angle -- and the Ingestion agent's data-quality gate converts
    it into DataQuality.REJECTED with a field-level reason.
    """


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TorqueSample:
    angle_deg: float
    torque_nm: float


@dataclass(frozen=True, slots=True)
class TorqueAngleCurve:
    """One fastener run at one position."""

    fastener_position: int          # 1..N, clockwise from the valve stem
    samples: tuple[TorqueSample, ...]
    tool_id: str = ""
    unit_id: str = ""

    def __post_init__(self) -> None:
        if len(self.samples) < MIN_CURVE_POINTS:
            raise TorqueCurveError(
                f"curve has {len(self.samples)} samples, need >= {MIN_CURVE_POINTS} "
                f"for a two-segment fit"
            )
        prev = -math.inf
        for i, s in enumerate(self.samples):
            if not (math.isfinite(s.angle_deg) and math.isfinite(s.torque_nm)):
                raise TorqueCurveError(f"non-finite sample at index {i}: {s}")
            if s.angle_deg < prev:
                raise TorqueCurveError(
                    f"angle must be non-decreasing; index {i} goes {prev} -> {s.angle_deg}"
                )
            prev = s.angle_deg


@dataclass(frozen=True, slots=True)
class SignatureBaselineSpec:
    """How a pack wants its baseline derived, plus its engineering spec limits.

    Declared in the pack's `sensors.yaml`. Separating this from `SignatureBaseline`
    keeps the distinction that matters: spec limits are *given* by engineering,
    while tolerances are *learned* from the process. A pack may not state a
    tolerance -- only how many sigma to use and how many clean runs to demand.
    """

    spec_lo_nm: float
    spec_hi_nm: float
    sigma: float = 3.0
    min_clean_runs: int = 20
    yield_flattening_ratio: float = 0.60
    anomaly_threshold: float = 0.45


@dataclass(frozen=True, slots=True)
class SignatureBaseline:
    """Expected signature for a fastener class.

    Tolerances are the *half-width* of the acceptable band, so a deviation of
    one tolerance unit sits exactly at the edge of normal.

    Prefer `from_clean_runs` over hand-writing these numbers. Thresholds picked
    by eye are the thing a quality engineer will challenge first, and rightly:
    they encode an opinion. Thresholds derived from the measured distribution of
    known-good runs encode the process. It is the same argument as the vision
    memory bank -- model normal, then flag departures from it -- applied to the
    sensor modality, and it means onboarding a new fastener class needs clean
    runs rather than labelled defects.

    Spec limits are different in kind: they come from the engineering
    specification (and, in production, from the ERPNext Quality Inspection
    template), never from observed data. A process that has drifted must not be
    allowed to redefine what is in spec.
    """

    knee_angle_deg: float
    knee_angle_tolerance_deg: float
    elastic_slope_nm_per_deg: float
    elastic_slope_tolerance: float
    residual_variance_max: float
    rundown_torque_max_nm: float
    spec_lo_nm: float
    spec_hi_nm: float
    # Ratio of late-elastic slope to early-elastic slope below which the
    # fastener is judged to have entered plastic deformation.
    yield_flattening_ratio: float = 0.60
    reversal_count_max: float = 2.0
    # Provenance of the tolerances, rendered on the provenance strip.
    derived_from_runs: int = 0
    sigma_multiplier: float = 0.0

    @classmethod
    def from_clean_runs(
        cls,
        features: Sequence[SignatureFeatures],
        *,
        spec_lo_nm: float,
        spec_hi_nm: float,
        sigma: float = 3.0,
        min_runs: int = 20,
    ) -> SignatureBaseline:
        """Derive a baseline from known-good runs.

        Tolerance is `sigma` standard deviations of the clean distribution, so a
        deviation of one tolerance unit is a `sigma`-sigma departure from the
        process as actually measured. One-sided ceilings (residual variance,
        run-down torque, reversals) are set at mean + sigma*sd.

        Raises if given too few runs: a baseline fitted to a handful of samples
        is worse than no baseline, because it will be believed.
        """
        n = len(features)
        if n < min_runs:
            raise TorqueCurveError(
                f"baseline needs >= {min_runs} clean runs to be meaningful, got {n}"
            )

        def stats(values: Sequence[float]) -> tuple[float, float]:
            mean = math.fsum(values) / n
            var = math.fsum((v - mean) ** 2 for v in values) / (n - 1)
            return mean, math.sqrt(var)

        knee_mean, knee_sd = stats([f.knee_angle_deg for f in features])
        slope_mean, slope_sd = stats([f.elastic_slope_nm_per_deg for f in features])
        resid_mean, resid_sd = stats([f.elastic_residual_variance for f in features])
        rundown_mean, rundown_sd = stats([f.rundown_torque_mean_nm for f in features])
        rev_mean, rev_sd = stats([float(f.reversal_count) for f in features])

        # A degenerate (zero-variance) feature would make every observation
        # infinitely anomalous. Floor each tolerance at a small fraction of the
        # mean so a perfectly repeatable simulator cannot produce a divide-by-zero
        # detector that fires on sensor noise.
        def tol(sd: float, mean: float) -> float:
            return max(sigma * sd, abs(mean) * 0.02, 1e-6)

        return cls(
            knee_angle_deg=knee_mean,
            knee_angle_tolerance_deg=tol(knee_sd, knee_mean),
            elastic_slope_nm_per_deg=slope_mean,
            elastic_slope_tolerance=tol(slope_sd, slope_mean),
            residual_variance_max=resid_mean + sigma * resid_sd,
            rundown_torque_max_nm=rundown_mean + sigma * rundown_sd,
            spec_lo_nm=spec_lo_nm,
            spec_hi_nm=spec_hi_nm,
            reversal_count_max=max(rev_mean + sigma * rev_sd, 1.0),
            derived_from_runs=n,
            sigma_multiplier=sigma,
        )


# ---------------------------------------------------------------------------
# Least squares over a slice, using prefix sums so the breakpoint sweep is O(n)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Fit:
    slope: float
    intercept: float
    sse: float
    n: int

    @property
    def residual_variance(self) -> float:
        dof = self.n - 2
        return self.sse / dof if dof > 0 else 0.0


class _PrefixSums:
    """Cumulative sums enabling a constant-time OLS fit over any contiguous slice."""

    __slots__ = ("n", "sx", "sxx", "sxy", "sy", "syy")

    def __init__(self, xs: Sequence[float], ys: Sequence[float]) -> None:
        n = len(xs)
        self.n = n
        self.sx = [0.0] * (n + 1)
        self.sy = [0.0] * (n + 1)
        self.sxx = [0.0] * (n + 1)
        self.sxy = [0.0] * (n + 1)
        self.syy = [0.0] * (n + 1)
        for i in range(n):
            x, y = xs[i], ys[i]
            self.sx[i + 1] = self.sx[i] + x
            self.sy[i + 1] = self.sy[i] + y
            self.sxx[i + 1] = self.sxx[i] + x * x
            self.sxy[i + 1] = self.sxy[i] + x * y
            self.syy[i + 1] = self.syy[i] + y * y

    def fit(self, lo: int, hi: int) -> _Fit:
        """OLS over [lo, hi). Returns a zero-slope fit when x has no spread."""
        n = hi - lo
        if n < 2:
            return _Fit(0.0, 0.0, 0.0, n)
        sx = self.sx[hi] - self.sx[lo]
        sy = self.sy[hi] - self.sy[lo]
        sxx = self.sxx[hi] - self.sxx[lo]
        sxy = self.sxy[hi] - self.sxy[lo]
        syy = self.syy[hi] - self.syy[lo]

        denom = n * sxx - sx * sx
        if abs(denom) < 1e-12:
            # No spread in x: best fit is the horizontal mean line.
            mean = sy / n
            sse = max(syy - n * mean * mean, 0.0)
            return _Fit(0.0, mean, sse, n)

        slope = (n * sxy - sx * sy) / denom
        intercept = (sy - slope * sx) / n
        # SSE = Syy - b0*Sy - b1*Sxy, clamped for floating-point noise.
        sse = max(syy - intercept * sy - slope * sxy, 0.0)
        return _Fit(slope, intercept, sse, n)

    def total_sum_squares(self, lo: int, hi: int) -> float:
        n = hi - lo
        if n < 2:
            return 0.0
        sy = self.sy[hi] - self.sy[lo]
        syy = self.syy[hi] - self.syy[lo]
        return max(syy - sy * sy / n, 0.0)


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SignatureFeatures:
    """Measured shape of one fastener run. Every field is a measured quantity.

    These are what the Adjudicator reasons over and what the UI plots against
    the baseline overlay. They are facts, not inferences.
    """

    knee_angle_deg: float
    knee_torque_nm: float
    rundown_slope_nm_per_deg: float
    rundown_torque_mean_nm: float
    elastic_slope_nm_per_deg: float
    elastic_r2: float
    elastic_residual_variance: float
    elastic_span_deg: float
    late_to_early_slope_ratio: float
    final_torque_nm: float
    peak_torque_nm: float
    reversal_count: int
    sample_count: int


def extract_features(curve: TorqueAngleCurve) -> SignatureFeatures:
    """Fit a two-segment piecewise-linear model and measure the shape.

    The breakpoint (the seating knee) is found by sweeping every admissible
    split and choosing the one minimising total residual sum of squares across
    both segments. With prefix sums each candidate is O(1), so the whole sweep
    is O(n) and exact -- no gradient descent, no initial guess, no local minima.
    """
    xs = [s.angle_deg for s in curve.samples]
    ys = [s.torque_nm for s in curve.samples]
    n = len(xs)
    pre = _PrefixSums(xs, ys)

    best_k = MIN_SEGMENT_POINTS
    best_sse = math.inf
    for k in range(MIN_SEGMENT_POINTS, n - MIN_SEGMENT_POINTS + 1):
        sse = pre.fit(0, k).sse + pre.fit(k, n).sse
        if sse < best_sse:
            best_sse = sse
            best_k = k

    rundown = pre.fit(0, best_k)
    elastic = pre.fit(best_k, n)

    knee_angle = xs[best_k]
    # Take the knee torque from the fitted elastic line rather than the raw
    # sample, so a single noisy point at the breakpoint cannot move it.
    knee_torque = elastic.slope * knee_angle + elastic.intercept

    sst = pre.total_sum_squares(best_k, n)
    elastic_r2 = 1.0 - (elastic.sse / sst) if sst > 1e-12 else 1.0

    rundown_mean = pre.sy[best_k] / best_k if best_k > 0 else 0.0

    # Yield detection: compare the slope of the last quarter of the elastic
    # region with the first half of it. A fastener driven past yield flattens.
    elastic_n = n - best_k
    late_ratio = 1.0
    if elastic_n >= 2 * MIN_SEGMENT_POINTS:
        mid = best_k + elastic_n // 2
        late_start = best_k + (3 * elastic_n) // 4
        early = pre.fit(best_k, mid).slope
        late = pre.fit(late_start, n).slope
        if abs(early) > 1e-9:
            late_ratio = late / early

    # Reversals: torque going backwards by more than sensor noise indicates a
    # binding or stick-slip run, characteristic of cross-threading.
    noise_floor = max(0.01 * max(ys), 0.05)
    reversals = sum(1 for i in range(1, n) if ys[i - 1] - ys[i] > noise_floor)

    return SignatureFeatures(
        knee_angle_deg=knee_angle,
        knee_torque_nm=knee_torque,
        rundown_slope_nm_per_deg=rundown.slope,
        rundown_torque_mean_nm=rundown_mean,
        elastic_slope_nm_per_deg=elastic.slope,
        elastic_r2=elastic_r2,
        elastic_residual_variance=elastic.residual_variance,
        elastic_span_deg=xs[-1] - knee_angle,
        late_to_early_slope_ratio=late_ratio,
        final_torque_nm=ys[-1],
        peak_torque_nm=max(ys),
        reversal_count=reversals,
        sample_count=n,
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Deviation:
    """One measured departure from baseline, in tolerance units.

    Carries its own sentence because this text goes straight onto the Defect
    Workbench and into the Adjudicator prompt. Cited numbers, not adjectives.
    """

    feature: str
    observed: float
    expected: float
    tolerance_units: float
    weight: float
    statement: str


@dataclass(frozen=True, slots=True)
class SignatureVerdict:
    features: SignatureFeatures
    anomaly_score: float                       # 0..1, noisy-OR of weighted deviations
    deviations: tuple[Deviation, ...]
    endpoint_in_spec: bool
    signature_anomalous: bool
    ranked_classes: tuple[tuple[str, float, str], ...] = field(default=())

    @property
    def fusion_only(self) -> bool:
        """True when the endpoint passes but the shape does not.

        This flag is the product. It marks exactly the population of defects
        that a torque gun reading the final value cannot see, and it is the
        metric reported as `fusion_only_detection_rate` in `make eval`.
        """
        return self.endpoint_in_spec and self.signature_anomalous

    @property
    def severity(self) -> Severity:
        if not self.signature_anomalous:
            return Severity.MINOR
        return Severity.CRITICAL if self.anomaly_score >= 0.60 else Severity.MAJOR


def _deviation(
    name: str,
    observed: float,
    expected: float,
    tolerance: float,
    weight: float,
    statement: str,
    *,
    directional: int = 0,
) -> Deviation | None:
    """Build a Deviation when the observation is outside tolerance.

    `directional` restricts which side counts as anomalous: +1 only above
    expected, -1 only below, 0 either side. Direction matters physically -- a
    steeper-than-baseline elastic slope is not the contamination signature.
    """
    if tolerance <= 0:
        return None
    delta = observed - expected
    if directional > 0 and delta <= 0:
        return None
    if directional < 0 and delta >= 0:
        return None
    units = abs(delta) / tolerance
    if units <= 1.0:
        return None
    return Deviation(
        feature=name,
        observed=observed,
        expected=expected,
        tolerance_units=units,
        weight=weight,
        statement=statement,
    )


def score_signature(
    features: SignatureFeatures,
    baseline: SignatureBaseline,
    *,
    anomaly_threshold: float = 0.45,
) -> SignatureVerdict:
    """Score a measured signature against its baseline.

    Deviations combine by weighted noisy-OR:

        score = 1 - PROD_i (1 - w_i * s_i)

    rather than by averaging. Averaging is wrong here: it lets one strongly
    anomalous feature be diluted by several nominal ones, which is exactly the
    dilution that makes a contaminated fastener look acceptable. Noisy-OR is
    monotonic in every input and stays bounded in [0, 1].
    """
    deviations: list[Deviation] = []

    # Contamination signature: the knee arrives late AND the elastic slope is
    # shallow. Either alone is suggestive; together they are diagnostic.
    if d := _deviation(
        "knee_angle_deg",
        features.knee_angle_deg,
        baseline.knee_angle_deg,
        baseline.knee_angle_tolerance_deg,
        0.35,
        f"Seating knee at {features.knee_angle_deg:.1f} deg against a "
        f"{baseline.knee_angle_deg:.1f} deg baseline.",
        directional=+1,
    ):
        deviations.append(d)

    if d := _deviation(
        "elastic_slope_nm_per_deg",
        features.elastic_slope_nm_per_deg,
        baseline.elastic_slope_nm_per_deg,
        baseline.elastic_slope_tolerance,
        0.40,
        f"Elastic slope {features.elastic_slope_nm_per_deg:.2f} Nm/deg against a "
        f"{baseline.elastic_slope_nm_per_deg:.2f} Nm/deg baseline.",
        directional=-1,
    ):
        deviations.append(d)

    # Cross-threading signature: erratic run-down and a poor linear fit.
    if d := _deviation(
        "elastic_residual_variance",
        features.elastic_residual_variance,
        baseline.residual_variance_max,
        max(baseline.residual_variance_max, 1e-6),
        0.25,
        f"Elastic-region residual variance {features.elastic_residual_variance:.3f} "
        f"exceeds the {baseline.residual_variance_max:.3f} ceiling; the run is not smooth.",
        directional=+1,
    ):
        deviations.append(d)

    if d := _deviation(
        "reversal_count",
        float(features.reversal_count),
        baseline.reversal_count_max,
        max(baseline.reversal_count_max, 1.0),
        0.20,
        f"{features.reversal_count} torque reversals during the run; a fastener seating "
        f"normally advances monotonically.",
        directional=+1,
    ):
        deviations.append(d)

    if d := _deviation(
        "rundown_torque_mean_nm",
        features.rundown_torque_mean_nm,
        baseline.rundown_torque_max_nm,
        max(baseline.rundown_torque_max_nm, 1e-6),
        0.30,
        f"Run-down torque averaged {features.rundown_torque_mean_nm:.1f} Nm before seating, "
        f"above the {baseline.rundown_torque_max_nm:.1f} Nm ceiling; the fastener met "
        f"resistance while it should still have been spinning free.",
        directional=+1,
    ):
        deviations.append(d)

    # Yield signature: the elastic region flattens, i.e. plastic deformation.
    if features.late_to_early_slope_ratio < baseline.yield_flattening_ratio:
        shortfall = baseline.yield_flattening_ratio - features.late_to_early_slope_ratio
        deviations.append(
            Deviation(
                feature="late_to_early_slope_ratio",
                observed=features.late_to_early_slope_ratio,
                expected=baseline.yield_flattening_ratio,
                tolerance_units=1.0 + shortfall / max(baseline.yield_flattening_ratio, 1e-6),
                weight=0.45,
                statement=(
                    f"Elastic slope decayed to "
                    f"{features.late_to_early_slope_ratio:.2f} of its initial value; "
                    f"the fastener is deforming plastically rather than loading elastically."
                ),
            )
        )

    complement = 1.0
    for d in deviations:
        strength = min((d.tolerance_units - 1.0) / DEVIATION_SATURATION, 1.0)
        complement *= 1.0 - d.weight * strength
    anomaly_score = 1.0 - complement

    endpoint_in_spec = baseline.spec_lo_nm <= features.final_torque_nm <= baseline.spec_hi_nm
    signature_anomalous = anomaly_score >= anomaly_threshold

    return SignatureVerdict(
        features=features,
        anomaly_score=anomaly_score,
        deviations=tuple(sorted(deviations, key=lambda d: -d.weight * d.tolerance_units)),
        endpoint_in_spec=endpoint_in_spec,
        signature_anomalous=signature_anomalous,
        ranked_classes=classify(features, baseline),
    )


# ---------------------------------------------------------------------------
# Transparent classification
# ---------------------------------------------------------------------------
def classify(
    features: SignatureFeatures, baseline: SignatureBaseline
) -> tuple[tuple[str, float, str], ...]:
    """Rank likely defect classes from the signature alone.

    Deliberately a transparent rule-and-statistics engine, not an LLM. Every
    score below traces to a named measurement against a named baseline, which
    means it can be argued with, unit-tested, and shown to a quality engineer.
    Claiming a language model independently discovered a fastening failure mode
    would be neither true nor defensible -- see docs/DECISIONS.md ADR-0005.

    Returns (class_name, confidence, evidence) sorted by confidence.
    """
    out: list[tuple[str, float, str]] = []

    knee_late = (features.knee_angle_deg - baseline.knee_angle_deg) / max(
        baseline.knee_angle_tolerance_deg, 1e-6
    )
    slope_low = (baseline.elastic_slope_nm_per_deg - features.elastic_slope_nm_per_deg) / max(
        baseline.elastic_slope_tolerance, 1e-6
    )
    residual_high = features.elastic_residual_variance / max(baseline.residual_variance_max, 1e-6)
    rundown_high = features.rundown_torque_mean_nm / max(baseline.rundown_torque_max_nm, 1e-6)

    # thread_contamination: late knee AND shallow slope, endpoint irrelevant.
    if knee_late > 1.0 and slope_low > 1.0:
        conf = min(0.95, 0.45 + 0.15 * min(knee_late, 3.0) + 0.15 * min(slope_low, 3.0))
        out.append((
            "thread_contamination",
            conf,
            f"Knee delayed {features.knee_angle_deg - baseline.knee_angle_deg:+.1f} deg and "
            f"elastic slope reduced to {features.elastic_slope_nm_per_deg:.2f} Nm/deg "
            f"(baseline {baseline.elastic_slope_nm_per_deg:.2f}). Friction has changed, so the "
            f"torque endpoint is not a valid proxy for clamp load.",
        ))

    # cross_threading: erratic run-down, reversals, poor linear fit.
    if residual_high > 1.0 and (rundown_high > 1.0 or features.reversal_count >= 3):
        conf = min(0.92, 0.40 + 0.18 * min(residual_high, 3.0) + 0.05 * features.reversal_count)
        out.append((
            "cross_threading",
            conf,
            f"Residual variance {features.elastic_residual_variance:.3f} with "
            f"{features.reversal_count} torque reversals and run-down torque "
            f"{features.rundown_torque_mean_nm:.1f} Nm. The fastener bound during run-down.",
        ))

    # over_torque: plastic flattening, or endpoint above the band.
    if features.late_to_early_slope_ratio < baseline.yield_flattening_ratio:
        conf = min(0.93, 0.50 + (baseline.yield_flattening_ratio
                                 - features.late_to_early_slope_ratio))
        out.append((
            "over_torque",
            conf,
            f"Elastic slope decayed to {features.late_to_early_slope_ratio:.2f} of initial; "
            f"peak {features.peak_torque_nm:.1f} Nm. Consistent with yield -- this is the "
            f"NHTSA 24V237000 failure mode.",
        ))
    elif features.final_torque_nm > baseline.spec_hi_nm:
        out.append((
            "over_torque",
            0.85,
            f"Final torque {features.final_torque_nm:.1f} Nm exceeds the "
            f"{baseline.spec_hi_nm:.1f} Nm upper limit.",
        ))

    # under_torque: run stopped before the elastic region completed.
    if features.final_torque_nm < baseline.spec_lo_nm:
        out.append((
            "under_torque",
            0.88,
            f"Final torque {features.final_torque_nm:.1f} Nm is below the "
            f"{baseline.spec_lo_nm:.1f} Nm lower limit over a "
            f"{features.elastic_span_deg:.1f} deg elastic span.",
        ))

    return tuple(sorted(out, key=lambda t: -t[1]))
