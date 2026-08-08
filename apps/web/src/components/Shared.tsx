import type { CSSProperties, ReactNode } from "react";
import type { Inspection } from "../types";

export const money = (n: number, c = "INR") =>
  new Intl.NumberFormat("en-IN", {
    style: "currency", currency: c, maximumFractionDigits: 0,
  }).format(n);

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

type Tone = "white" | "soft" | "dark";

export function Panel({ title, children, right, tone = "white", tight }: {
  title?: string; children: ReactNode; right?: ReactNode; tone?: Tone; tight?: boolean;
}) {
  return (
    <div className={`panel tone-${tone}${tight ? " tight" : ""}`}>
      {title && (
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 14 }}>
          <h2 style={{ flex: 1, margin: 0 }}>{title}</h2>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

/**
 * The provenance strip. Every panel showing a derived value carries one.
 * In a quality-control product, *where did this number come from* is the job —
 * an inspector who cannot tell a measured reading from a model's estimate
 * cannot do theirs.
 */
export function Provenance({ items }: { items: [string, string][] }) {
  return (
    <div className="provenance">
      {items.map(([k, v]) => (
        <span key={k}><span className="k">{k}</span> {v}</span>
      ))}
    </div>
  );
}

type PillTone = "neutral" | "nominal" | "critical" | "watch" | "molten" | "ink" | "outline";

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: PillTone }) {
  return <span className={`pill tone-${tone}`}>{children}</span>;
}

/** Circular initials badge — the user avatar and the brand mark share this shape. */
export function Avatar({ initials, dark }: { initials: string; dark?: boolean }) {
  return (
    <div className={dark ? "brand-mark" : "who-avatar"}>{initials}</div>
  );
}

/** Status is never colour alone — always icon + label. */
export function VerdictBadge({ verdict, fusionOnly }: {
  verdict: string; fusionOnly?: boolean;
}) {
  const icon = verdict === "pass" ? "✓" : verdict === "defect" ? "✗" : "⚠";
  return (
    <>
      <span className={`badge ${verdict}`}>{icon} {verdict}</span>
      {fusionOnly && <span className="badge fusion" style={{ marginLeft: 6 }}>⚠ FUSION-ONLY</span>}
    </>
  );
}

export function Andon({ inspection, idleText }: {
  inspection: Inspection | null; idleText: string;
}) {
  const cls = inspection ? `state-${inspection.verdict}` : "state-idle";
  return (
    <div className={`andon ${cls}`} aria-live="assertive" role="status">
      <span className="dot" />
      <div className="label">
        {inspection ? inspection.verdict.toUpperCase() : "IDLE"}
        {inspection?.fusion_only && (
          <span className="badge fusion" style={{ marginLeft: 10 }}>⚠ FUSION-ONLY</span>
        )}
      </div>
      <div className="detail">
        {inspection
          ? `${inspection.unit_id} · ${inspection.severity} · confidence ${inspection.confidence.toFixed(2)} · ${inspection.total_ms.toFixed(1)}ms`
          : idleText}
      </div>
      <div className="spacer" />
      {inspection?.requires_human && (
        <Pill tone="critical">HUMAN APPROVAL REQUIRED</Pill>
      )}
    </div>
  );
}

export function Kpi({ value, label, color }: {
  value: ReactNode; label: string; color?: string;
}) {
  return (
    <div className="kpi">
      <div className="value" style={color ? { color } : undefined}>{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}

/** A KPI presented as its own card, with a circular icon glyph up top. */
export function KpiCard({ glyph, value, label, color, right }: {
  glyph: ReactNode; value: ReactNode; label: string; color?: string; right?: ReactNode;
}) {
  return (
    <div className="kpi-card">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div className="kpi-icon">{glyph}</div>
        {right}
      </div>
      <Kpi value={value} label={label} color={color} />
    </div>
  );
}

/** The donut used for first-pass yield: dynamic threshold coloring. */
export function Ring({ value, label, sublabel, share, color }: {
  value: string; label: string; sublabel?: string; share: number; color?: string;
}) {
  const pct = Math.max(0, Math.min(1, share)) * 100;

  // Dynamic threshold coloring rules:
  // >= 75%: Green (#2E9E6B)
  // 30% to 75%: Pale Yellow (#EAB308)
  // < 30%: Red (#DC2626)
  let autoColor = "var(--state-nominal)"; // Green >= 75%
  if (pct < 30) {
    autoColor = "#DC2626"; // Red < 30%
  } else if (pct < 75) {
    autoColor = "#EAB308"; // Pale Yellow (30% to 75%)
  }

  const ringColor = color ?? autoColor;
  const fill = `conic-gradient(${ringColor} 0% ${pct}%, #2E2B27 ${pct}% 100%)`;
  const ringStyle = { "--ring-fill": fill } as CSSProperties;
  return (
    <div className="ring" style={ringStyle}>
      <div className="ring-value">
        <div className="n">{value}</div>
        <div className="l">{label}{sublabel ? <><br />{sublabel}</> : null}</div>
      </div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="faint" style={{ padding: "10px 0", lineHeight: 1.75 }}>{children}</div>;
}
