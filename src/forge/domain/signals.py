"""Generic signal analysis.

This replaces the component-specific torque analyser with an engine that works
on any measured series. The physics did not go away -- it moved into
configuration:

    "seating knee angle"  IS  piecewise_linear.breakpoint_x
    "elastic slope"       IS  piecewise_linear.slope_after
    "residual variance"   IS  piecewise_linear.residual_variance

A wheel fastener produces a torque-vs-angle *sweep*; a bearing produces a
vibration *timeseries*; a PCB produces almost no sensor data at all. The same
extractors, baseline learner and scorer serve all three. What differs is which
channels a component declares and which extractors it asks for -- and that lives
in the component definition, not in this file.

Three properties are preserved from the original torque module because they are
what make the analysis defensible:

1. **Baselines are learned, never hand-written.** Tolerance is k standard
   deviations of the measured clean distribution. No threshold in this file is
   chosen by a developer.
2. **Spec limits are separate and are NEVER learned.** They come from the
   engineering specification. A process that has drifted must not be allowed to
   redefine what is in spec.
3. **Deviations combine by weighted noisy-OR, not by averaging.** Averaging lets
   one strongly anomalous feature be diluted by several nominal ones, which is
   exactly the dilution that makes a contaminated fastener look acceptable.

Pure standard library. The domain layer imports nothing, and a reviewer can read
every line of the maths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# A two-segment fit needs enough points either side to mean anything.
MIN_SEGMENT_POINTS = 5
MIN_SERIES_POINTS = 2 * MIN_SEGMENT_POINTS

# How far past the tolerance edge a deviation must go to count at full strength.
# At 1.0 tolerance units the feature sits exactly at the edge of normal and
# contributes nothing; at 1.0 + SATURATION it contributes its full weight.
DEVIATION_SATURATION = 1.5


class SignalError(ValueError):
    """The series cannot be analysed.

    Raised only for structurally unusable input -- too few points, non-finite
    values, non-monotonic x on a sweep. A series that is *anomalous* is a normal
    result, not an error; the ingestion gate turns this into a rejection with a
    field-level reason.
    """


class SeriesKind(StrEnum):
    """How a series should be interpreted.

    SWEEP     x is an independent ordinate that advances monotonically
              (torque vs rotation angle, force vs displacement).
    TIMESERIES x is time; ordering matters but the value may go anywhere
              (vibration, temperature).
    SCALAR    a single reading with no series (tool age, humidity).
    """

    SWEEP = "sweep"
    TIMESERIES = "timeseries"
    SCALAR = "scalar"


@dataclass(frozen=True, slots=True)
class Sample:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class SignalSeries:
    """One measured channel from one inspection point."""

    channel: str                      # "torque", "vibration", "temperature"
    samples: tuple[Sample, ...]
    kind: SeriesKind = SeriesKind.SWEEP
    x_unit: str = ""
    y_unit: str = ""
    # Which inspection point this came from: fastener 1..5, pad 1..N, bearing A/B.
    position: int = 1
    source_id: str = ""               # tool / sensor identifier
    unit_id: str = ""

    def __post_init__(self) -> None:
        if len(self.samples) < MIN_SERIES_POINTS:
            raise SignalError(
                f"channel '{self.channel}' has {len(self.samples)} samples, "
                f"need >= {MIN_SERIES_POINTS}"
            )
        prev = -math.inf
        for i, s in enumerate(self.samples):
            if not (math.isfinite(s.x) and math.isfinite(s.y)):
                raise SignalError(f"channel '{self.channel}': non-finite sample at index {i}")
            if self.kind is SeriesKind.SWEEP and s.x < prev:
                raise SignalError(
                    f"channel '{self.channel}': a sweep must advance monotonically; "
                    f"index {i} goes {prev} -> {s.x}"
                )
            prev = s.x

    @property
    def xs(self) -> list[float]:
        return [s.x for s in self.samples]

    @property
    def ys(self) -> list[float]:
        return [s.y for s in self.samples]


# ---------------------------------------------------------------------------
# Least squares over any slice, via prefix sums
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
    """Cumulative sums giving a constant-time OLS fit over any contiguous slice.

    This is what makes the breakpoint search O(n) and exact -- every candidate
    split is evaluated in constant time, so we sweep them all rather than
    running a gradient descent that could land in a local minimum.
    """

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
            mean = sy / n
            return _Fit(0.0, mean, max(syy - n * mean * mean, 0.0), n)

        slope = (n * sxy - sx * sy) / denom
        intercept = (sy - slope * sx) / n
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
# Extractors. Each is pure: SignalSeries -> {feature_name: value}
# ---------------------------------------------------------------------------
def piecewise_linear(series: SignalSeries) -> dict[str, float]:
    """Fit two straight segments and report where they meet.

    The breakpoint is found by sweeping every admissible split and choosing the
    one minimising total residual sum of squares across both segments. With
    prefix sums each candidate costs O(1), so the whole sweep is O(n) and exact:
    no gradient descent, no initial guess, no local minima.

    On a fastener sweep the breakpoint IS the seating knee and `slope_after` IS
    the elastic slope -- which is how this one extractor replaces the whole
    torque-specific analyser.
    """
    xs, ys = series.xs, series.ys
    n = len(xs)
    pre = _PrefixSums(xs, ys)

    best_k, best_sse = MIN_SEGMENT_POINTS, math.inf
    for k in range(MIN_SEGMENT_POINTS, n - MIN_SEGMENT_POINTS + 1):
        sse = pre.fit(0, k).sse + pre.fit(k, n).sse
        if sse < best_sse:
            best_sse, best_k = sse, k

    before, after = pre.fit(0, best_k), pre.fit(best_k, n)
    sst = pre.total_sum_squares(best_k, n)
    r2 = 1.0 - (after.sse / sst) if sst > 1e-12 else 1.0

    # Take the breakpoint's y from the fitted line rather than the raw sample,
    # so one noisy point at the split cannot move it.
    break_x = xs[best_k]
    break_y = after.slope * break_x + after.intercept

    # Late/early slope ratio: does the second segment itself flatten? On a
    # fastener that means plastic deformation past yield.
    after_n = n - best_k
    late_early = 1.0
    if after_n >= 2 * MIN_SEGMENT_POINTS:
        mid = best_k + after_n // 2
        late_start = best_k + (3 * after_n) // 4
        early_slope = pre.fit(best_k, mid).slope
        late_slope = pre.fit(late_start, n).slope
        if abs(early_slope) > 1e-9:
            late_early = late_slope / early_slope

    return {
        "breakpoint_x": break_x,
        "breakpoint_y": break_y,
        "slope_before": before.slope,
        "slope_after": after.slope,
        "mean_before": pre.sy[best_k] / best_k if best_k else 0.0,
        "residual_variance": after.residual_variance,
        "r2": r2,
        "span_after": xs[-1] - break_x,
        "late_early_slope_ratio": late_early,
    }


def endpoint(series: SignalSeries) -> dict[str, float]:
    """Terminal and extreme values. What a simple limit check would look at."""
    ys = series.ys
    return {
        "final_y": ys[-1],
        "peak_y": max(ys),
        "min_y": min(ys),
        "span_x": series.xs[-1] - series.xs[0],
    }


def stability(series: SignalSeries) -> dict[str, float]:
    """How smoothly the series advanced.

    Reversals and a poor monotonic fraction indicate binding or stick-slip --
    on a fastener, cross-threading.
    """
    ys = series.ys
    noise_floor = max(0.01 * max(abs(y) for y in ys), 1e-6)
    reversals = sum(1 for i in range(1, len(ys)) if ys[i - 1] - ys[i] > noise_floor)
    forward = sum(1 for i in range(1, len(ys)) if ys[i] >= ys[i - 1])
    return {
        "reversal_count": float(reversals),
        "monotonic_fraction": forward / max(len(ys) - 1, 1),
    }


def trend(series: SignalSeries) -> dict[str, float]:
    """Single-line fit. Drift detection for a timeseries channel."""
    xs, ys = series.xs, series.ys
    fit = _PrefixSums(xs, ys).fit(0, len(xs))
    sst = _PrefixSums(xs, ys).total_sum_squares(0, len(xs))
    span = xs[-1] - xs[0]
    return {
        "slope": fit.slope,
        "r2": 1.0 - (fit.sse / sst) if sst > 1e-12 else 1.0,
        "drift": fit.slope * span,
        "residual_variance": fit.residual_variance,
    }


def distribution(series: SignalSeries) -> dict[str, float]:
    """Shape of the value distribution, ignoring order.

    Kurtosis matters for vibration: impulsive bearing faults raise it well
    before RMS moves, which is why it is here and not just mean/std.
    """
    ys = series.ys
    n = len(ys)
    mean = math.fsum(ys) / n
    var = math.fsum((y - mean) ** 2 for y in ys) / max(n - 1, 1)
    sd = math.sqrt(var)
    ordered = sorted(ys)

    def pct(p: float) -> float:
        idx = min(int(p * (n - 1)), n - 1)
        return ordered[idx]

    rms = math.sqrt(math.fsum(y * y for y in ys) / n)
    peak = max(abs(y) for y in ys)
    kurt = (
        math.fsum((y - mean) ** 4 for y in ys) / (n * var * var) if var > 1e-12 else 0.0
    )
    return {
        "mean": mean,
        "std": sd,
        "p95": pct(0.95),
        "rms": rms,
        "peak": peak,
        "crest_factor": peak / rms if rms > 1e-12 else 0.0,
        "kurtosis": kurt,
        "outlier_count": float(sum(1 for y in ys if abs(y - mean) > 3 * sd)) if sd > 0 else 0.0,
    }


def _goertzel(ys: Sequence[float], normalised_freq: float) -> float:
    """Power at one frequency, O(n) and pure stdlib.

    A full DFT would be O(n^2) here. We only ever need power at a handful of
    known frequencies -- bearing fault frequencies are computed from geometry,
    not discovered -- so Goertzel is both faster and a better fit.
    """
    w = 2.0 * math.pi * normalised_freq
    coeff = 2.0 * math.cos(w)
    s1 = s2 = 0.0
    for y in ys:
        s0 = y + coeff * s1 - s2
        s2, s1 = s1, s0
    return s1 * s1 + s2 * s2 - coeff * s1 * s2


def spectral(series: SignalSeries, *, bands: Sequence[float] = ()) -> dict[str, float]:
    """Energy at declared frequencies, plus the dominant one found by scan.

    `bands` are normalised frequencies (cycles per sample) the component
    declares -- e.g. computed bearing fault frequencies.
    """
    ys = series.ys
    n = len(ys)
    total = math.fsum(y * y for y in ys) or 1.0

    out: dict[str, float] = {}
    for i, f in enumerate(bands):
        out[f"band_{i}_energy"] = _goertzel(ys, f) / total

    # Coarse scan for the dominant component. Resolution is deliberately modest:
    # we want "is there a peak and roughly where", not a spectrogram.
    best_f, best_p = 0.0, -1.0
    steps = min(n // 2, 64)
    for k in range(1, max(steps, 2)):
        f = k / (2.0 * steps)
        p = _goertzel(ys, f)
        if p > best_p:
            best_p, best_f = p, f
    out["dominant_freq"] = best_f
    out["dominant_energy_ratio"] = best_p / total
    return out


Extractor = "Callable[[SignalSeries], dict[str, float]]"

EXTRACTORS: dict[str, object] = {
    "piecewise_linear": piecewise_linear,
    "endpoint": endpoint,
    "stability": stability,
    "trend": trend,
    "distribution": distribution,
    "spectral": spectral,
}


def extract(series: SignalSeries, extractors: Sequence[str]) -> dict[str, float]:
    """Run the named extractors and return namespaced features.

    Keys are `extractor.feature`, e.g. `piecewise_linear.slope_after`. The
    namespace prevents two extractors that both report `r2` from silently
    overwriting each other -- a collision that would be invisible and would
    corrupt every baseline built from it.
    """
    features: dict[str, float] = {}
    for name in extractors:
        fn = EXTRACTORS.get(name)
        if fn is None:
            raise SignalError(
                f"unknown extractor '{name}'; available: {sorted(EXTRACTORS)}"
            )
        for key, value in fn(series).items():  # type: ignore[operator]
            features[f"{name}.{key}"] = value
    return features


# ---------------------------------------------------------------------------
# Learned baseline
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class FeatureBaseline:
    """What normal looks like, measured from known-good runs.

    Prefer `from_clean_runs` over writing these numbers. Thresholds picked by
    eye encode an opinion and are the first thing a quality engineer will
    challenge; thresholds derived from the distribution of good runs encode the
    process. It is the same argument as a vision memory bank -- model normal,
    then flag departures -- applied to any measured channel.
    """

    means: dict[str, float] = field(default_factory=dict)
    tolerances: dict[str, float] = field(default_factory=dict)
    derived_from_runs: int = 0
    sigma: float = 0.0

    @classmethod
    def from_clean_runs(
        cls,
        runs: Sequence[dict[str, float]],
        *,
        sigma: float = 3.0,
        min_runs: int = 20,
    ) -> FeatureBaseline:
        """Derive means and k-sigma tolerances from good runs.

        Refuses too few runs: a baseline fitted to a handful of samples is worse
        than no baseline, because it will be believed.
        """
        n = len(runs)
        if n < min_runs:
            raise SignalError(
                f"baseline needs >= {min_runs} clean runs to be meaningful, got {n}"
            )
        keys = sorted({k for r in runs for k in r})
        means: dict[str, float] = {}
        tolerances: dict[str, float] = {}

        for key in keys:
            values = [r[key] for r in runs if key in r]
            if len(values) < 2:
                continue
            mean = math.fsum(values) / len(values)
            var = math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1)
            sd = math.sqrt(var)
            means[key] = mean
            # Floor the tolerance. A perfectly repeatable simulator would
            # otherwise produce a zero-variance feature and a detector that
            # fires on floating-point noise.
            tolerances[key] = max(sigma * sd, abs(mean) * 0.02, 1e-6)

        return cls(means=means, tolerances=tolerances, derived_from_runs=n, sigma=sigma)


# ---------------------------------------------------------------------------
# Spec limits -- given by engineering, never learned
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SpecLimit:
    feature: str
    lo: float | None = None
    hi: float | None = None
    label: str = ""

    def satisfied_by(self, features: dict[str, float]) -> bool:
        value = features.get(self.feature)
        if value is None:
            return False
        if self.lo is not None and value < self.lo:
            return False
        return not (self.hi is not None and value > self.hi)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DeviationRule:
    """How much one feature's departure from baseline matters, and which way.

    `direction` restricts which side counts: +1 above baseline only, -1 below
    only, 0 either. Direction is physical -- a steeper-than-baseline elastic
    slope is not the contamination signature, so it must not score as one.
    """

    feature: str
    weight: float
    direction: int = 0
    statement: str = ""


@dataclass(frozen=True, slots=True)
class Deviation:
    feature: str
    observed: float
    expected: float
    tolerance_units: float
    weight: float
    statement: str


@dataclass(frozen=True, slots=True)
class AnomalyResult:
    score: float
    deviations: tuple[Deviation, ...]
    in_spec: bool
    anomalous: bool

    @property
    def fusion_only(self) -> bool:
        """Every declared limit is satisfied, yet the shape is anomalous.

        This is the population of defects an endpoint check cannot see. It is
        reported as `fusion_only_detection_rate` by the eval harness and it is
        the entire argument for combining signals.
        """
        return self.in_spec and self.anomalous


def score_features(
    features: dict[str, float],
    baseline: FeatureBaseline,
    rules: Sequence[DeviationRule],
    limits: Sequence[SpecLimit] = (),
    *,
    threshold: float = 0.45,
) -> AnomalyResult:
    """Score measured features against a learned baseline.

    Deviations combine by weighted noisy-OR:

        score = 1 - PROD_i (1 - w_i * s_i)

    rather than by averaging. Noisy-OR is monotonic in every input, stays
    bounded in [0, 1], and -- critically -- cannot be diluted. One strongly
    anomalous feature among several nominal ones still raises the score, which
    is the whole point when the anomalous one is the safety-relevant signal.
    """
    deviations: list[Deviation] = []

    for rule in rules:
        observed = features.get(rule.feature)
        expected = baseline.means.get(rule.feature)
        tolerance = baseline.tolerances.get(rule.feature)
        if observed is None or expected is None or not tolerance:
            continue

        delta = observed - expected
        if rule.direction > 0 and delta <= 0:
            continue
        if rule.direction < 0 and delta >= 0:
            continue

        units = abs(delta) / tolerance
        if units <= 1.0:
            continue

        deviations.append(
            Deviation(
                feature=rule.feature,
                observed=observed,
                expected=expected,
                tolerance_units=units,
                weight=rule.weight,
                statement=(
                    rule.statement.format(observed=observed, expected=expected, units=units)
                    if rule.statement
                    else (
                        f"{rule.feature} measured {observed:.3f} against a {expected:.3f} "
                        f"baseline ({units:.1f} tolerance units)."
                    )
                ),
            )
        )

    complement = 1.0
    for d in deviations:
        strength = min((d.tolerance_units - 1.0) / DEVIATION_SATURATION, 1.0)
        complement *= 1.0 - d.weight * strength
    score = 1.0 - complement

    return AnomalyResult(
        score=score,
        deviations=tuple(sorted(deviations, key=lambda d: -d.weight * d.tolerance_units)),
        in_spec=all(limit.satisfied_by(features) for limit in limits),
        anomalous=score >= threshold,
    )
