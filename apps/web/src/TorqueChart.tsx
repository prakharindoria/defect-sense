import type { Baseline, Fastener } from "./types";

/**
 * Torque-angle trace against the learned baseline.
 *
 * Hand-drawn SVG rather than a charting library: this is the single most
 * important visual in the product and it needs exact control over the spec
 * band, the knee marker and the baseline overlay. A generic chart component
 * would fight us on all three.
 *
 * The story the reader must get in two seconds: the curve ENDS inside the green
 * spec band (so a torque gun passes it) while its SHAPE departs from the dashed
 * baseline (so we do not).
 */
export function TorqueChart({ fastener, baseline }: { fastener: Fastener; baseline: Baseline }) {
  const w = 620;
  const h = 300;
  const pad = { l: 46, r: 14, t: 14, b: 34 };

  const pts = fastener.curve;
  if (pts.length === 0) return <div className="faint">No curve data.</div>;

  const maxAngle = Math.max(...pts.map((p) => p[0]), baseline.knee_angle_deg * 2);
  const maxTorque = Math.max(...pts.map((p) => p[1]), baseline.spec_hi_nm) * 1.08;

  const x = (a: number) => pad.l + (a / maxAngle) * (w - pad.l - pad.r);
  const y = (t: number) => h - pad.b - (t / maxTorque) * (h - pad.t - pad.b);

  const path = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");

  // Baseline: an ideal run at the learned knee and slope, drawn to the same
  // final torque so the shape difference is what stands out, not the endpoint.
  const seat = 6;
  const bKnee = baseline.knee_angle_deg;
  const bSlope = baseline.elastic_slope_nm_per_deg;
  const bEndAngle = bKnee + (fastener.final_torque_nm - seat) / bSlope;
  const basePath =
    `M${x(0)},${y(2.4)} L${x(bKnee)},${y(seat)} L${x(bEndAngle)},${y(fastener.final_torque_nm)}`;

  const anomalous = fastener.signature_anomalous;

  return (
    <div className="torque-chart-wrapper" style={{ width: "100%", display: "flex", justifyContent: "center", alignItems: "center" }}>
      <svg viewBox={`0 0 ${w} ${h}`}
           style={{ width: "100%", maxWidth: "620px", height: "auto", maxHeight: "280px", display: "block", margin: "0 auto" }}
           role="img"
           aria-label={`Torque angle curve for position ${fastener.position}`}>
        {/* spec band */}
        <rect x={pad.l} y={y(baseline.spec_hi_nm)} width={w - pad.l - pad.r}
              height={Math.max(y(baseline.spec_lo_nm) - y(baseline.spec_hi_nm), 1)}
              fill="rgba(46,158,107,0.10)" />
        <line x1={pad.l} x2={w - pad.r} y1={y(baseline.spec_lo_nm)} y2={y(baseline.spec_lo_nm)}
              stroke="rgba(46,158,107,0.45)" strokeWidth="1" />
        <line x1={pad.l} x2={w - pad.r} y1={y(baseline.spec_hi_nm)} y2={y(baseline.spec_hi_nm)}
              stroke="rgba(46,158,107,0.45)" strokeWidth="1" />
        <text x={w - pad.r} y={y(baseline.spec_hi_nm) - 5} textAnchor="end"
              fill="#237A53" fontSize="9" fontFamily="var(--mono)">
          SPEC {baseline.spec_lo_nm}–{baseline.spec_hi_nm} Nm
        </text>

        {/* axes */}
        <line x1={pad.l} x2={w - pad.r} y1={h - pad.b} y2={h - pad.b} stroke="#E0DCD6" />
        <line x1={pad.l} x2={pad.l} y1={pad.t} y2={h - pad.b} stroke="#E0DCD6" />
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <text key={f} x={pad.l - 6} y={y(maxTorque * f) + 3} textAnchor="end"
                fill="#A5A099" fontSize="9" fontFamily="var(--mono)">
            {Math.round(maxTorque * f)}
          </text>
        ))}
        <text x={(w + pad.l) / 2} y={h - 6} textAnchor="middle" fill="#A5A099"
              fontSize="9" fontFamily="var(--mono)">ROTATION ANGLE (deg)</text>

        {/* learned baseline */}
        <path d={basePath} fill="none" stroke="#5B9BD5" strokeWidth="2.2"
              strokeDasharray="5 4" opacity="0.85" />

        {/* knee markers */}
        <line x1={x(bKnee)} x2={x(bKnee)} y1={pad.t} y2={h - pad.b}
              stroke="rgba(91,155,213,0.4)" strokeDasharray="2 3" />
        <line x1={x(fastener.knee_angle_deg)} x2={x(fastener.knee_angle_deg)}
              y1={pad.t} y2={h - pad.b}
              stroke={anomalous ? "rgba(221,95,66,0.65)" : "rgba(124,119,111,0.45)"}
              strokeDasharray="2 3" />
        <text x={x(fastener.knee_angle_deg) + 4} y={pad.t + 11}
              fill={anomalous ? "#DD5F42" : "#7C776F"} fontSize="9" fontFamily="var(--mono)">
          knee {fastener.knee_angle_deg.toFixed(1)}°
        </text>

        {/* measured curve */}
        <path d={path} fill="none" stroke={anomalous ? "#DD5F42" : "#7C776F"} strokeWidth="2.4" strokeLinecap="round" />

        <g fontFamily="var(--mono)" fontSize="9">
          <line x1={w - 150} x2={w - 132} y1={pad.t + 6} y2={pad.t + 6}
                stroke="#5B9BD5" strokeDasharray="5 4" strokeWidth="2.2" />
          <text x={w - 128} y={pad.t + 9} fill="#5B9BD5">baseline ({baseline.derived_from_runs} runs)</text>
          <line x1={w - 150} x2={w - 132} y1={pad.t + 20} y2={pad.t + 20}
                stroke={anomalous ? "#DD5F42" : "#7C776F"} strokeWidth="2.4" />
          <text x={w - 128} y={pad.t + 23} fill={anomalous ? "#DD5F42" : "#7C776F"}>measured</text>
        </g>
      </svg>
    </div>
  );
}
