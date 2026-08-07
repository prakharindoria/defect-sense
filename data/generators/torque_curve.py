"""Synthetic torque-angle curve generator.

Produces physically-shaped fastener runs with **recorded ground truth**, so the
eval harness can score detection against what was actually injected rather than
against a human's later opinion.

Every curve is synthetic and is stamped as such. It is never presented as real
plant data (CLAUDE.md, Data honesty).

The generator models the same three phases the analyser measures -- run-down,
seating knee, elastic loading -- and perturbs the parameters that physically
change under each failure mode. The critical property, and the one the whole
demo depends on, is that `thread_contamination` produces a curve whose **final
torque lands inside the specification band**. If that stops being true the
fusion case is no longer a fusion case, so it is asserted in the tests.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum

from forge.domain.torque import TorqueAngleCurve, TorqueSample

# Sampling resolution of a real fastening controller trace.
SAMPLES_PER_DEGREE = 4.0


class CurveClass(StrEnum):
    """Ground-truth label recorded with every generated curve."""

    CLEAN = "clean"
    THREAD_CONTAMINATION = "thread_contamination"
    CROSS_THREADING = "cross_threading"
    OVER_TORQUE = "over_torque"
    UNDER_TORQUE = "under_torque"


@dataclass(frozen=True, slots=True)
class CurveSpec:
    """Nominal fastening process for a wheel hub bolt, M14x1.5, 5 per wheel."""

    target_torque_nm: float = 118.0
    spec_lo_nm: float = 110.0
    spec_hi_nm: float = 125.0
    knee_angle_deg: float = 14.0
    elastic_slope_nm_per_deg: float = 3.2
    rundown_torque_nm: float = 2.4
    seating_torque_nm: float = 6.0
    sensor_noise_nm: float = 0.35


@dataclass(frozen=True, slots=True)
class GeneratedCurve:
    """A curve plus the ground truth used to score detection."""

    curve: TorqueAngleCurve
    label: CurveClass
    severity: float                        # 0.0 nominal .. 1.0 extreme
    truth: dict[str, float] = field(default_factory=dict)
    is_synthetic: bool = True
    generator_version: str = "torque_curve/1.0.0"


def _emit(
    spec: CurveSpec,
    rng: random.Random,
    *,
    knee_angle: float,
    elastic_slope: float,
    final_torque: float,
    rundown_torque: float,
    rundown_jitter: float,
    elastic_jitter: float,
    yield_ratio: float,
    position: int,
    unit_id: str,
    tool_id: str,
) -> TorqueAngleCurve:
    """Render one curve from explicit phase parameters."""
    samples: list[TorqueSample] = []

    # --- run-down: fastener spinning free, torque low and roughly flat -------
    n_rundown = max(int(knee_angle * SAMPLES_PER_DEGREE), 6)
    for i in range(n_rundown):
        angle = knee_angle * i / n_rundown
        torque = rundown_torque + rng.gauss(0.0, spec.sensor_noise_nm + rundown_jitter)
        samples.append(TorqueSample(angle, max(torque, 0.0)))

    # --- elastic loading -----------------------------------------------------
    # Integrated stepwise rather than solved in closed form, because a yielding
    # fastener has a slope that varies along the run. The controller drives
    # until it reaches its torque target, so the run ENDS at final_torque
    # regardless of how the slope behaved on the way there -- which is exactly
    # why the endpoint alone tells you so little.
    step = 1.0 / SAMPLES_PER_DEGREE
    angle = knee_angle
    ideal = spec.seating_torque_nm
    rise = max(final_torque - spec.seating_torque_nm, 1e-6)
    max_span = 4.0 * rise / max(elastic_slope * yield_ratio, 0.1)  # runaway guard

    samples.append(TorqueSample(angle, ideal + rng.gauss(0.0, spec.sensor_noise_nm)))
    while ideal < final_torque and (angle - knee_angle) < max_span:
        progress = (ideal - spec.seating_torque_nm) / rise
        # Past the halfway point of the load, the slope decays toward
        # yield_ratio of its initial value: plastic deformation.
        effective = elastic_slope
        if yield_ratio < 1.0 and progress > 0.5:
            decay = min((progress - 0.5) / 0.5, 1.0)
            effective = elastic_slope * (1.0 - decay * (1.0 - yield_ratio))
        ideal += effective * step
        angle += step
        torque = ideal + rng.gauss(0.0, spec.sensor_noise_nm + elastic_jitter)
        samples.append(TorqueSample(angle, max(torque, 0.0)))

    return TorqueAngleCurve(
        fastener_position=position,
        samples=tuple(samples),
        tool_id=tool_id,
        unit_id=unit_id,
    )


def generate(
    label: CurveClass,
    *,
    severity: float = 0.7,
    spec: CurveSpec | None = None,
    seed: int | None = None,
    position: int = 1,
    unit_id: str = "",
    tool_id: str = "TOOL-DGD-04",
) -> GeneratedCurve:
    """Generate one labelled curve.

    `severity` scales how far the failure mode departs from nominal, so the
    eval set can include marginal cases rather than only obvious ones.
    """
    spec = spec or CurveSpec()
    rng = random.Random(seed)  # noqa: S311 - synthetic data, not cryptography
    sev = max(0.0, min(severity, 1.0))

    knee = spec.knee_angle_deg + rng.gauss(0.0, 0.4)
    slope = spec.elastic_slope_nm_per_deg + rng.gauss(0.0, 0.05)
    final = spec.target_torque_nm + rng.gauss(0.0, 1.2)
    rundown = spec.rundown_torque_nm
    rundown_jitter = 0.0
    elastic_jitter = 0.0
    yield_ratio = 1.0
    truth: dict[str, float] = {}

    if label is CurveClass.THREAD_CONTAMINATION:
        # Contamination changes thread friction: the knee arrives late and the
        # elastic slope is shallower. The controller still drives to its torque
        # target, so the ENDPOINT REMAINS IN SPEC -- that is the entire point.
        knee += 8.0 * sev
        slope -= 1.1 * sev
        rundown += 1.2 * sev
        truth["clamp_load_deficit_pct"] = 30.0 * sev
        truth["knee_delay_deg"] = 8.0 * sev

    elif label is CurveClass.CROSS_THREADING:
        # Binding during run-down: erratic torque, reversals, poor linear fit.
        knee -= 3.0 * sev
        rundown += 6.0 * sev
        rundown_jitter = 2.2 * sev
        elastic_jitter = 2.8 * sev
        slope -= 0.4 * sev
        truth["stud_damage_probability"] = 0.75 * sev

    elif label is CurveClass.OVER_TORQUE:
        # Driven past yield: the elastic slope decays and the curve flattens.
        yield_ratio = 1.0 - 0.55 * sev
        final = spec.target_torque_nm + 9.0 * sev
        truth["past_yield"] = 1.0
        truth["bolt_deformed"] = 1.0

    elif label is CurveClass.UNDER_TORQUE:
        # Run terminates before the elastic region completes.
        final = spec.spec_lo_nm - 12.0 * sev
        truth["clamp_load_deficit_pct"] = 40.0 * sev

    return GeneratedCurve(
        curve=_emit(
            spec, rng,
            knee_angle=knee, elastic_slope=slope, final_torque=final,
            rundown_torque=rundown, rundown_jitter=rundown_jitter,
            elastic_jitter=elastic_jitter, yield_ratio=yield_ratio,
            position=position, unit_id=unit_id, tool_id=tool_id,
        ),
        label=label,
        severity=sev,
        truth=truth,
    )


def generate_wheel(
    labels: dict[int, tuple[CurveClass, float]] | None = None,
    *,
    spec: CurveSpec | None = None,
    seed: int | None = None,
    unit_id: str = "",
    fastener_count: int = 5,
) -> list[GeneratedCurve]:
    """Generate a full wheel: one curve per fastener position.

    `labels` maps a 1-based position to (class, severity). Unlisted positions
    are CLEAN, which is what a realistic defect looks like -- one bad fastener
    among four good ones, not five identical failures.
    """
    labels = labels or {}
    base = random.Random(seed)  # noqa: S311
    out: list[GeneratedCurve] = []
    for pos in range(1, fastener_count + 1):
        label, sev = labels.get(pos, (CurveClass.CLEAN, 0.0))
        out.append(
            generate(
                label, severity=sev, spec=spec,
                seed=base.randint(0, 2**31 - 1), position=pos, unit_id=unit_id,
            )
        )
    return out


def learn_baseline(
    spec: CurveSpec | None = None,
    *,
    runs: int = 120,
    sigma: float = 3.0,
    seed: int = 20260807,
):  # noqa: ANN201
    """Learn a SignatureBaseline from generated clean runs.

    This mirrors what happens in production: point the system at a known-good
    production run and it derives its own tolerances. No threshold in the
    detector is chosen by a developer -- they are all `sigma` standard
    deviations of the measured normal process. Spec limits still come from the
    engineering specification, never from the data.

    Imported lazily so callers that only want raw curves do not pay for the
    domain import.
    """
    from forge.domain.torque import SignatureBaseline, extract_features  # noqa: PLC0415

    spec = spec or CurveSpec()
    rng = random.Random(seed)  # noqa: S311
    features = [
        extract_features(
            generate(CurveClass.CLEAN, spec=spec, seed=rng.randint(0, 2**31 - 1)).curve
        )
        for _ in range(runs)
    ]
    return SignatureBaseline.from_clean_runs(
        features, spec_lo_nm=spec.spec_lo_nm, spec_hi_nm=spec.spec_hi_nm, sigma=sigma
    )


def _round_trip_demo() -> None:
    """Print the §2 case. Run: python -m data.generators.torque_curve"""
    from forge.domain.torque import extract_features, score_signature  # noqa: PLC0415

    baseline = learn_baseline()
    print(
        f"\nBaseline learned from {baseline.derived_from_runs} clean runs at "
        f"{baseline.sigma_multiplier:.0f} sigma:\n"
        f"  knee  {baseline.knee_angle_deg:6.2f} deg  +/- {baseline.knee_angle_tolerance_deg:.2f}\n"
        f"  slope {baseline.elastic_slope_nm_per_deg:6.2f} Nm/deg "
        f"+/- {baseline.elastic_slope_tolerance:.2f}\n"
        f"  spec  {baseline.spec_lo_nm:.0f}-{baseline.spec_hi_nm:.0f} Nm "
        f"(from the engineering specification, not from data)\n"
    )
    header = (f"{'class':<22} {'sev':>4} {'final Nm':>9} {'knee':>6} {'slope':>6} "
              f"{'score':>6} {'in spec':>8} {'FUSION-ONLY':>12}  top class")
    print(header)
    print("-" * len(header))
    for label in CurveClass:
        for sev in (0.4, 0.8):
            g = generate(label, severity=sev, seed=42)
            v = score_signature(extract_features(g.curve), baseline)
            f = v.features
            top = v.ranked_classes[0][0] if v.ranked_classes else "-"
            flag = "** YES **" if v.fusion_only else "no"
            print(
                f"{label.value:<22} {sev:>4.1f} {f.final_torque_nm:>9.1f} "
                f"{f.knee_angle_deg:>6.1f} {f.elastic_slope_nm_per_deg:>6.2f} "
                f"{v.anomaly_score:>6.2f} {str(v.endpoint_in_spec):>8} {flag:>12}  {top}"
            )


if __name__ == "__main__":
    _round_trip_demo()
