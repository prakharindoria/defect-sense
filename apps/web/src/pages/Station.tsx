import { useEffect, useState } from "react";
import { useAuth } from "../auth";
import { Andon, Empty, Kpi, Panel, Provenance, VerdictBadge } from "../components/Shared";
import type { Inspection, Metrics } from "../types";
import { useLiveFeed } from "../useLiveFeed";

/**
 * Shop-floor station view.
 *
 * Deliberately the narrowest page in the product. An operator needs to know
 * whether THIS unit is good and what to do next; they do not need agent
 * internals, cost models, or reasoning traces. An operator reading a reasoning
 * trace is an operator not watching the line.
 *
 * The API enforces this too — the shop-floor role cannot submit an inspection
 * or read the agent workflow. The UI hiding it is a courtesy, not the control.
 */
export function Station() {
  const { authFetch, user } = useAuth();
  const feed = useLiveFeed();
  const [metrics, setMetrics] = useState<Metrics>({ inspected: 0 });
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set());

  const latest: Inspection | null = feed[0] ?? null;

  useEffect(() => {
    const load = () =>
      authFetch("/api/v1/metrics").then((r) => (r.ok ? r.json() : null))
        .then((d) => d && setMetrics(d)).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [authFetch, feed.length]);

  const needsAck = latest && latest.verdict !== "pass" && !acknowledged.has(latest.correlation_id);

  return (
    <>
      <h1 className="page-title">Station · Wheel Assembly</h1>
      <p className="page-sub">
        Signed in as {user?.display_name}. You see the live verdict for each unit
        and acknowledge anything that is not a pass.
      </p>

      <Andon inspection={latest} idleText="Waiting for the next unit from the line." />

      {needsAck && (
        <Panel>
          <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 260 }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>
                Action required on {latest.unit_id}
              </div>
              <div className="muted" style={{ lineHeight: 1.6 }}>
                Recommended: <strong>{latest.disposition}</strong>.
                {latest.requires_human && " A supervisor decision is pending."}
              </div>
            </div>
            <button className="primary"
                    onClick={() => setAcknowledged((s) => new Set(s).add(latest.correlation_id))}>
              Acknowledge
            </button>
          </div>
        </Panel>
      )}

      <div className="grid cols-4" style={{ marginTop: 16 }}>
        <Panel><Kpi value={metrics.inspected} label="Units this shift" /></Panel>
        <Panel><Kpi value={metrics.defects ?? 0} label="Defects" color="var(--state-critical)" /></Panel>
        <Panel>
          <Kpi value={metrics.first_pass_yield !== undefined
            ? `${(metrics.first_pass_yield * 100).toFixed(1)}%` : "—"}
            label="First pass yield" color="var(--state-nominal)" />
        </Panel>
        <Panel>
          <Kpi value={metrics.p95_ms !== undefined ? `${metrics.p95_ms.toFixed(0)}ms` : "—"}
               label="p95 inspection time" />
        </Panel>
      </div>

      <div style={{ marginTop: 16 }}>
        <Panel title="Recent units">
          {feed.length === 0 && <Empty>
            No units yet. A QA user can submit an inspection from the Inspect page.
          </Empty>}
          {feed.length > 0 && (
            <table>
              <thead>
                <tr><th>Unit</th><th>Verdict</th><th>Action</th><th>Time</th></tr>
              </thead>
              <tbody>
                {feed.slice(0, 12).map((f) => (
                  <tr key={f.correlation_id}>
                    <td>{f.unit_id}</td>
                    <td><VerdictBadge verdict={f.verdict} fusionOnly={f.fusion_only} /></td>
                    <td>{f.verdict === "pass" ? "—" : f.disposition}</td>
                    <td>{f.total_ms.toFixed(1)}ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Provenance items={[
            ["source", "MEASURED · verifiers + torque signature"],
            ["pack", "wheel_assembly"],
            ["data", "SYNTHETIC"],
          ]} />
        </Panel>
      </div>
    </>
  );
}
