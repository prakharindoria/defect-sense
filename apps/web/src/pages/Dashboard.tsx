import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { Andon, Empty, Kpi, money, Panel, Provenance, VerdictBadge } from "../components/Shared";
import type { Metrics } from "../types";
import { useLiveFeed } from "../useLiveFeed";

/** QA dashboard — quality authority over the whole line. */
export function Dashboard() {
  const { authFetch, user } = useAuth();
  const navigate = useNavigate();
  const feed = useLiveFeed();
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

  return (
    <>
      <h1 className="page-title">Quality dashboard</h1>
      <p className="page-sub">
        {user?.display_name} · full evidence, agent reasoning and disposition authority.
      </p>

      <Andon inspection={feed[0] ?? null}
             idleText="No units inspected yet. Submit one from Inspect." />

      <div className="grid cols-4">
        <Panel><Kpi value={metrics.inspected} label="Inspected" /></Panel>
        <Panel><Kpi value={metrics.defects ?? 0} label="Defects"
                    color="var(--state-critical)" /></Panel>
        <Panel><Kpi value={metrics.fusion_only ?? 0} label="Fusion-only catches"
                    color="var(--accent-molten)" /></Panel>
        <Panel><Kpi
          value={metrics.cost_at_risk ? money(metrics.cost_at_risk) : "—"}
          label="Cost at risk" /></Panel>
      </div>

      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <Panel title="Awaiting your decision"
               right={<span className="chip">{pending.length}</span>}>
          {pending.length === 0 && <Empty>
            Nothing pending. Units land here when confidence is ambiguous, cost
            crosses the supervisor threshold, or two options are within 10% of
            each other — the engine declines to break a near-tie silently.
          </Empty>}
          {pending.map((f) => (
            <div key={f.correlation_id} className="pending-row"
                 onClick={() => navigate(`/workbench/${f.correlation_id}`)}>
              <div style={{ flex: 1 }}>
                <div className="mono" style={{ marginBottom: 3 }}>{f.unit_id}</div>
                <div className="faint">{f.human_reason.slice(0, 110)}…</div>
              </div>
              <div style={{ textAlign: "right" }}>
                <VerdictBadge verdict={f.verdict} fusionOnly={f.fusion_only} />
                <div className="mono faint" style={{ marginTop: 4 }}>
                  {money(f.expected_cost, f.currency)}
                </div>
              </div>
            </div>
          ))}
          <Provenance items={[
            ["gate", "confidence 0.45–0.70 · cost > ₹50,000 · near-tie"],
            ["source", "RULE · expected-cost"],
          ]} />
        </Panel>

        <Panel title="Live inspection feed"
               right={<Link to="/inspect" style={{ color: "var(--accent-signal)", fontSize: 12 }}>
                        Submit →
                      </Link>}>
          {feed.length === 0 && <Empty>Waiting for units…</Empty>}
          {feed.length > 0 && (
            <table>
              <thead>
                <tr><th>Unit</th><th>Verdict</th><th>Action</th><th>Cost</th><th>ms</th></tr>
              </thead>
              <tbody>
                {feed.slice(0, 14).map((f) => (
                  <tr key={f.correlation_id} className="clickable"
                      onClick={() => navigate(`/workbench/${f.correlation_id}`)}>
                    <td>{f.unit_id}</td>
                    <td><VerdictBadge verdict={f.verdict} fusionOnly={f.fusion_only} /></td>
                    <td>{f.verdict === "pass" ? "—" : f.disposition}</td>
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
      </div>
    </>
  );
}
