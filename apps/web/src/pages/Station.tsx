import { useEffect, useState } from "react";
import { useAuth } from "../auth";
import { Andon, Empty, Panel, Provenance, VerdictBadge } from "../components/Shared";
import { usePageTitle } from "../pageHeader";
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
  usePageTitle("Station · Wheel Assembly", "Is this unit good?");
  const feed = useLiveFeed(authFetch);
  const [metrics, setMetrics] = useState<Metrics>({ inspected: 0 });

  const latest: Inspection | null = feed[0] ?? null;

  useEffect(() => {
    const load = () =>
      authFetch("/api/v1/metrics").then((r) => (r.ok ? r.json() : null))
        .then((d) => d && setMetrics(d)).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [authFetch, feed.length]);

  const [acknowledged, setAcknowledged] = useState<Record<string, boolean>>({});

  const handleAcknowledge = (corrId: string) => {
    setAcknowledged((prev) => ({ ...prev, [corrId]: true }));
  };

  const assignedUnits = feed.filter((f) => f.verdict !== "pass" && f.assigned_to_name);

  return (
    <>
      <Andon inspection={latest} idleText="Waiting for the next unit from the line." />

      {/* Shop Floor Rework Queue Card */}
      {assignedUnits.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <Panel tone="soft" title="📋 Shop Floor Rework Queue (Assigned Tasks)">
            <div style={{ padding: "8px 0" }}>
              <table>
                <thead>
                  <tr>
                    <th>Unit ID</th>
                    <th>Verdict</th>
                    <th>Required Disposition</th>
                    <th>Assigned To</th>
                    <th>Assigned By</th>
                    <th>Operator Action</th>
                  </tr>
                </thead>
                <tbody>
                  {assignedUnits.map((f) => {
                    const isDone = acknowledged[f.correlation_id];
                    return (
                      <tr key={f.correlation_id}>
                        <td style={{ fontWeight: 600 }}>{f.unit_id}</td>
                        <td><VerdictBadge verdict={f.verdict} fusionOnly={f.fusion_only} /></td>
                        <td style={{ fontFamily: "var(--sans)", color: "var(--accent-molten)", fontWeight: 600 }}>
                          {f.disposition.toUpperCase()}
                        </td>
                        <td>
                          <span style={{ color: "var(--accent-molten)", fontWeight: 600 }}>
                            👤 {f.assigned_to_name}
                          </span>
                        </td>
                        <td style={{ color: "var(--text-muted)", fontSize: 12 }}>
                          {f.assigned_by_name ? f.assigned_by_name : <span style={{ fontStyle: "italic", color: "var(--text-muted-2)" }}>—</span>}
                        </td>
                        <td>
                          {isDone ? (
                            <span className="pill tone-nominal" style={{ fontSize: 11, padding: "4px 10px" }}>
                              ✓ Rework In Progress
                            </span>
                          ) : (
                            <button
                              className="molten"
                              onClick={() => handleAcknowledge(f.correlation_id)}
                              style={{ padding: "6px 14px", fontSize: 12 }}
                            >
                              Acknowledge & Start Rework →
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: "1.5fr 1fr", marginTop: 16, alignItems: "start" }}>
        <Panel tone="soft" title={`Recent units (${metrics.inspected ?? 0} shift total)`}
               right={<span className="pill tone-outline">Last 5</span>}>
          <div className="panel" style={{ padding: "6px 18px 14px" }}>
            {feed.length === 0 && <Empty>
              No units yet. A QA user can submit an inspection from the Inspect page.
            </Empty>}
            {feed.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>Unit</th>
                    <th>Verdict</th>
                    <th>Action</th>
                    <th>Assigned To</th>
                    <th style={{ textAlign: "right" }}>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {feed.slice(0, 5).map((f) => (
                    <tr key={f.correlation_id}>
                      <td style={{ whiteSpace: "nowrap" }}>{f.unit_id}</td>
                      <td><VerdictBadge verdict={f.verdict} fusionOnly={f.fusion_only} /></td>
                      <td style={{ fontFamily: "var(--sans)", color: "var(--text-muted)" }}>
                        {f.verdict === "pass" ? "—" : f.disposition}
                      </td>
                      <td style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                        {f.verdict === "pass" ? "—" : f.assigned_to_name ? (
                          <span style={{ color: "var(--accent-molten)", fontWeight: 600 }}>👤 {f.assigned_to_name}</span>
                        ) : (
                          <span style={{ color: "var(--text-muted-4)" }}>—</span>
                        )}
                      </td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>{f.total_ms.toFixed(1)}ms</td>
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
          </div>
        </Panel>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {latest && (
            <Panel title="Action Required">
              <div style={{ fontSize: 18, fontWeight: 500, letterSpacing: "-0.01em", marginTop: 4 }}>
                Verdict: {latest.verdict.toUpperCase()}
              </div>
              <div className="muted" style={{ fontSize: 13, lineHeight: 1.65, marginTop: 8 }}>
                Required disposition: <strong style={{ color: "var(--ink)" }}>{latest.disposition}</strong>.
                <br />
                Supervisor approval required: {latest.requires_human ? "Yes" : "No"}
              </div>
              <div style={{marginTop: 18, fontSize: 13, color: "var(--text-muted)"}}>
                No action available for your role. See QA to proceed.
              </div>
            </Panel>
          )}
          <Panel tone="dark">
            <div style={{ fontSize: 11, letterSpacing: "0.14em", color: "#8E8A84", marginBottom: 14 }}>
              WHAT YOU SEE HERE
            </div>
            <div style={{ fontSize: 15, lineHeight: 1.65, color: "#E6E2DC" }}>
              The station view is deliberately the narrowest page in the product.
              You need to know whether <em>this</em> unit is good and what to do
              next — not agent internals or cost models.
            </div>
            <div className="mono" style={{ fontSize: 10, color: "#6E6A64", marginTop: 18, lineHeight: 1.7 }}>
              role {user?.role.toLowerCase()} · agent trace not permitted<br />
              enforced at the API, not just here
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}
