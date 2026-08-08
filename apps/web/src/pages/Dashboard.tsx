import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import {
  Empty, KpiCard, money, Panel, Pill, Provenance, Ring, VerdictBadge,
} from "../components/Shared";
import { usePageTitle } from "../pageHeader";
import type { Inspection, Metrics } from "../types";
import { useLiveFeed } from "../useLiveFeed";

const VERDICT_DOT: Record<string, string> = {
  pass: "var(--state-nominal)", defect: "var(--accent-molten)", escalate: "var(--state-watch)",
};

/** Small bar row shared by the latency and torque mini-charts — real numbers only. */
function Bars({ values, max, color, mutedColor }: {
  values: number[]; max: number; color: (i: number) => boolean; mutedColor?: string;
}) {
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 70 }}>
      {values.map((v, i) => (
        <div key={i} style={{
          flex: 1, borderRadius: 3, minHeight: 3,
          height: `${Math.max(4, (v / max) * 100)}%`,
          background: color(i) ? "var(--accent-molten)" : (mutedColor ?? "var(--border)"),
        }} />
      ))}
    </div>
  );
}

/** A cheap SVG sparkline — no charting library, this is one path element. */
function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <div className="faint">Not enough data yet for a trend.</div>;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = Math.max(max - min, 1);
  const w = 300, h = 60;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - min) / span) * (h - 8) - 4;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: 62 }}>
      <path d={`M${pts.join(" L")}`} fill="none" stroke="var(--accent-molten-hover)"
            strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

/** QA dashboard — quality authority over the whole line. */
export function Dashboard() {
  usePageTitle("Quality dashboard", "Authority over the line.");
  const { authFetch, user } = useAuth();
  const navigate = useNavigate();
  const feed = useLiveFeed(authFetch);
  const [metrics, setMetrics] = useState<Metrics>({ inspected: 0 });

  useEffect(() => {
    const load = () =>
      authFetch("/api/v1/metrics").then((r) => (r.ok ? r.json() : null))
        .then((d) => d && setMetrics(d)).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [authFetch, feed.length]);

  const pending = feed.filter((f) => f.requires_human);
  const latest: Inspection | null = feed[0] ?? null;

  const bySignal = useMemo(() => {
    const counts = new Map<string, number>();
    for (const f of feed) {
      if (f.verdict === "pass") continue;
      counts.set(f.primary_signal, (counts.get(f.primary_signal) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [feed]);



  const costSeries = useMemo(
    () => feed.filter((f) => f.verdict !== "pass").slice(0, 12).reverse().map((f) => f.expected_cost),
    [feed],
  );

  const fpyShare = metrics.first_pass_yield ?? (metrics.inspected
    ? 1 - (metrics.defects ?? 0) / metrics.inspected : 1);

  return (
    <>
      <div className="grid" style={{ gridTemplateColumns: "repeat(4, 1fr)", alignItems: "start" }}>
        <Panel tight>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
            <div className="mono" style={{ fontSize: 13, fontWeight: 600 }}>{latest?.unit_id ?? "—"}</div>
          </div>
          <div className="faint" style={{ marginBottom: 5 }}>
            Current verdict {latest ? `· ${latest.primary_signal}` : ""}
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 20 }}>
            <div style={{
              fontSize: 27, fontWeight: 600, letterSpacing: "-0.02em",
              color: latest?.verdict === "defect" ? "var(--accent-molten-hover)"
                : latest?.verdict === "escalate" ? "var(--state-watch)" : "var(--state-nominal-text)",
            }}>
              {latest ? latest.verdict.toUpperCase() : "IDLE"}
            </div>
            {latest && <span className="mono faint">conf {latest.confidence.toFixed(2)}</span>}
          </div>
          <div style={{ display: "flex", gap: 9 }}>
            <button className="primary" style={{ flex: 1 }}
                    onClick={() => latest && navigate(`/workbench/${latest.correlation_id}`)}
                    disabled={!latest}>
              Adjudicate
            </button>
          </div>
          <div style={{ borderTop: "1px solid var(--border-faint)", marginTop: 16, paddingTop: 14,
                        display: "flex", alignItems: "center", gap: 14 }}>
            <div style={{ flex: 1 }}>
              <div className="faint">Expected cost of action</div>
              <div className="mono" style={{ fontSize: 18, fontWeight: 600 }}>
                {latest ? money(latest.expected_cost, latest.currency) : "—"}
              </div>
            </div>
            <Link to={latest ? `/workbench/${latest.correlation_id}` : "#"}
                  style={{ display: "flex", alignItems: "center", gap: 9 }}>
              <span className="who-avatar" style={{ background: "var(--accent-molten)", color: "#fff", width: 30, height: 30, fontSize: 13 }}>₹</span>
              <span style={{ fontSize: 12, lineHeight: 1.3 }}>Cost<br />triage</span>
            </Link>
          </div>
        </Panel>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <KpiCard glyph="↓" value={metrics.inspected} label="Units inspected" />
          <div className="kpi-card">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
              <div className="kpi-icon">✗</div>
            </div>
            <div className="faint" style={{ marginBottom: 3 }}>
              Defects · <span style={{ color: "var(--accent-molten)" }}>{metrics.fusion_only ?? 0} fusion-only</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div className="mono" style={{ fontSize: 25, fontWeight: 600, color: "var(--accent-molten-hover)" }}>
                {metrics.defects ?? 0}
              </div>
              <div style={{ flex: 1 }} />
              <Link to="/agents" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span className="who-avatar" style={{ background: "var(--accent-molten)", color: "#fff", width: 26, height: 26, fontSize: 11 }}>◈</span>
                <span style={{ fontSize: 12, lineHeight: 1.3 }}>View<br />in trace</span>
              </Link>
            </div>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14, alignItems: "center" }}>
          <Panel tight>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 9 }}>
              <div className="kpi-icon" style={{ marginBottom: 0 }}>⛓</div>
              <div style={{ fontSize: 12.5, color: "var(--text-muted)", textAlign: "center" }}>
                Safety Override<br /><span className="faint">requires human approval</span>
              </div>
            </div>
          </Panel>
          <Ring
            value={metrics.first_pass_yield !== undefined
              ? `${(metrics.first_pass_yield * 100).toFixed(1)}%` : "95.2%"}
            label="First pass yield"
            share={fpyShare ?? 0.952}
          />
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="grid cols-2">
            <div className="kpi-card">
              <div className="kpi-icon">◎</div>
              <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: "-0.01em" }}>
                {feed.filter((f) => f.verdict !== "pass").length}
              </div>
              <div className="faint" style={{ marginBottom: 11 }}>defects in view</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(10,1fr)", gap: 4 }}>
                {Array.from({ length: 30 }, (_, i) => {
                  const item = feed[29 - i];
                  return (
                    <div key={i} style={{
                      aspectRatio: "1", borderRadius: "50%",
                      background: item ? VERDICT_DOT[item.verdict] ?? "var(--border)" : "var(--border-faint)",
                    }} />
                  );
                })}
              </div>
            </div>
            <div className="kpi-card">
              <div className="kpi-icon">⌸</div>
              <div className="faint">Latency</div>
              <Bars values={[metrics.p50_ms ?? 0, metrics.p95_ms ?? 0]} max={Math.max(metrics.p95_ms ?? 1, 1)}
                    color={(i) => i === 1} />
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8 }}>
                <span className="pill tone-neutral">p50 {metrics.p50_ms?.toFixed(0) ?? "—"}ms</span>
                <span className="pill tone-molten">p95 {metrics.p95_ms?.toFixed(0) ?? "—"}ms</span>
              </div>
            </div>
          </div>
          <div className="kpi-card">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div className="kpi-icon" style={{ marginBottom: 0 }}>↯</div>
              <div className="mono" style={{ fontSize: 20, fontWeight: 600, color: "var(--accent-molten)" }}>
                {metrics.cost_at_risk !== undefined ? money(metrics.cost_at_risk) : "—"}
              </div>
            </div>
            <div style={{ margin: "8px 0 4px" }}><Sparkline values={costSeries} /></div>
            <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between" }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 500 }}>Cost at risk</div>
                <div className="faint">across {costSeries.length} open defects in view</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "1.05fr 1.9fr 1.1fr", marginTop: 16, alignItems: "start" }}>
        <Panel tone="soft" title="Primary detection signals">
          {bySignal.length === 0
            ? <Empty>No defects seen yet in this session.</Empty>
            : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {bySignal.map(([signal, count]) => (
                  <div key={signal} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <div style={{ flex: 1, height: 8, borderRadius: 999, background: "var(--surface-white)", overflow: "hidden" }}>
                      <div style={{
                        height: "100%", borderRadius: 999, background: "var(--accent-molten)",
                        width: `${(count / bySignal[0][1]) * 100}%`,
                      }} />
                    </div>
                    <span className="mono" style={{ fontSize: 12.5, minWidth: 90, textAlign: "right" }}>
                      {count} · {signal}
                    </span>
                  </div>
                ))}
              </div>
            )}
        </Panel>

        <Panel tone="soft" title="Inspection feed">
          {feed.length > 0 && (
            <table style={{ marginTop: 16 }}>
              <thead>
                <tr><th>Unit</th><th>Verdict</th><th>Action</th><th>Assigned To</th><th>Cost</th><th>ms</th></tr>
              </thead>
              <tbody>
                {feed.slice(0, 8).map((f) => (
                  <tr key={f.correlation_id} className="clickable"
                      onClick={() => navigate(`/workbench/${f.correlation_id}`)}>
                    <td>{f.unit_id}</td>
                    <td><VerdictBadge verdict={f.verdict} fusionOnly={f.fusion_only} /></td>
                    <td style={{ fontFamily: "var(--sans)", color: "var(--text-muted)" }}>
                      {f.verdict === "pass" ? "—" : f.disposition}
                    </td>
                    <td style={{ fontSize: 12 }}>
                      {f.verdict === "pass" ? "—" : f.assigned_to_name ? (
                        <span style={{ color: "var(--accent-molten)", fontWeight: 600 }}>👤 {f.assigned_to_name}</span>
                      ) : (
                        <span style={{ color: "var(--text-muted-4)" }}>—</span>
                      )}
                    </td>
                    <td>{f.expected_cost ? money(f.expected_cost, f.currency) : "—"}</td>
                    <td>{f.total_ms.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Provenance items={[
            ["fpy", metrics.first_pass_yield !== undefined
              ? `${(metrics.first_pass_yield * 100).toFixed(1)}%` : "—"],
            ["p50", metrics.p50_ms !== undefined ? `${metrics.p50_ms.toFixed(1)}ms` : "—"],
            ["p95", metrics.p95_ms !== undefined ? `${metrics.p95_ms.toFixed(1)}ms` : "—"],
          ]} />
        </Panel>

        <Panel tone="soft">
          <div style={{ fontSize: 11, letterSpacing: "0.14em", color: "var(--text-faint-2)", marginBottom: 6 }}>
            AWAITING YOUR DECISION
          </div>
          <div style={{ fontSize: 20, fontWeight: 500, letterSpacing: "-0.015em", lineHeight: 1.3, marginBottom: 12 }}>
            {pending.length} units need a supervisor.
          </div>
          {pending.length === 0 && <Empty>
            Nothing pending. Units land here when confidence is ambiguous, cost
            crosses the supervisor threshold, or two options are within 10% of
            each other — the engine declines to break a near-tie silently.
          </Empty>}
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {pending.map((f) => (
              <div key={f.correlation_id} className="pending-row"
                   onClick={() => navigate(`/workbench/${f.correlation_id}`)}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="mono" style={{ fontSize: 12, fontWeight: 600 }}>{f.unit_id}</span>
                  {f.assigned_to_name && (
                    <span className="pill tone-critical" style={{ fontSize: 10 }}>👤 {f.assigned_to_name}</span>
                  )}
                  <div style={{ flex: 1 }} />
                  <Pill tone={f.verdict === "escalate" ? "watch" : "critical"}>
                    {f.verdict === "escalate" ? "⚠ escalate" : "✗ defect"}
                  </Pill>
                </div>
                <div className="faint" style={{ lineHeight: 1.5 }}>{f.human_reason.slice(0, 110)}…</div>
                <div className="mono" style={{ fontSize: 11, color: "var(--accent-molten)" }}>
                  {money(f.expected_cost, f.currency)}
                </div>
              </div>
            ))}
          </div>
          <div className="mono" style={{ fontSize: 10, color: "var(--text-faint-3)", lineHeight: 1.7, marginTop: 2 }}>
            gate confidence 0.45–0.70 · cost &gt; ₹50,000 · near-tie<br />
            source RULE · expected-cost
          </div>
        </Panel>
      </div>

      {user?.role === "ADMIN" && (
        <div className="faint" style={{ marginTop: 12 }}>
          Signed in as Admin — you can submit inspections and read the agent
          workflow for debugging, but this dashboard's disposition actions are
          QA's authority (separation of duties, asserted at boot).
        </div>
      )}
    </>
  );
}
