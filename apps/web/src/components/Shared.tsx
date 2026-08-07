import type { ReactNode } from "react";
import type { Inspection } from "../types";

export const money = (n: number, c = "INR") =>
  new Intl.NumberFormat("en-IN", {
    style: "currency", currency: c, maximumFractionDigits: 0,
  }).format(n);

export function Panel({ title, children, right }: {
  title?: string; children: ReactNode; right?: ReactNode;
}) {
  return (
    <div className="panel">
      {title && (
        <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <h2 style={{ flex: 1 }}>{title}</h2>
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
        <span className="chip down">HUMAN APPROVAL REQUIRED</span>
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

export function Empty({ children }: { children: ReactNode }) {
  return <div className="faint" style={{ padding: "8px 0", lineHeight: 1.7 }}>{children}</div>;
}
