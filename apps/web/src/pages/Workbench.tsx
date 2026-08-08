import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../auth";
import { Empty, Kpi, money, Panel, Pill, Provenance } from "../components/Shared";
import { usePageTitle } from "../pageHeader";
import { TorqueChart } from "../TorqueChart";
import type { Inspection } from "../types";
import { useNarrative } from "../useLiveFeed";

/**
 * The LLM explanation, streamed in after the verdict.
 *
 * Three states, all of them honest:
 *   pending   — the model has not answered yet; the verdict is already usable
 *   generated — a model wrote this; the strip names it
 *   degraded  — no model answered; this is the deterministic text, and it says so
 */
function NarrativePanel({ correlationId }: { correlationId: string }) {
  const { text, provenance } = useNarrative(correlationId);
  const degraded = (provenance?.degradations?.length ?? 0) > 0;

  if (!text) {
    return (
      <Panel title="AI explanation">
        <div className="faint" style={{ lineHeight: 1.7 }}>
          Waiting for the reasoning model… The verdict above is already final and
          does not depend on this.
        </div>
      </Panel>
    );
  }

  return (
    <Panel
      title="AI explanation"
      right={degraded
        ? <Pill tone="watch">DEGRADED — no model answered</Pill>
        : <Pill tone="nominal">generated</Pill>}
    >
      <div className="solid-backing">
        <div className="reasoning">{text}</div>
      </div>
      {degraded && (
        <div className="faint" style={{ marginTop: 8, lineHeight: 1.6 }}>
          Every configured model provider was unreachable, so this is the
          deterministic explanation rather than a generated one. The verdict,
          evidence and cost are unaffected.
        </div>
      )}
      <Provenance items={[
        ["source", degraded ? "RULE · fallback" : `LLM · ${provenance?.model ?? "unknown"}`],
        ["prompt", `v${provenance?.prompt_version ?? "?"}`],
        ["latency", `${provenance?.latency_ms ?? 0}ms`],
        ...(degraded
          ? ([["degraded", (provenance?.degradations ?? []).join(", ")]] as [string, string][])
          : []),
      ]} />
    </Panel>
  );
}

/** Full evidence for one unit. QA and Admin. */
export function Workbench() {
  const { id } = useParams<{ id: string }>();
  const { authFetch, can } = useAuth();
  usePageTitle("Defect workbench", "Everything behind one verdict.");
  const [data, setData] = useState<Inspection | null>(null);
  const [pos, setPos] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    authFetch(`/api/v1/inspections/${id}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: Inspection) => {
        setData(d);
        setPos(d.fasteners.find((f) => f.signature_anomalous)?.position
               ?? d.fasteners[0]?.position ?? null);
      })
      .catch((e) => setError(e.message));
  }, [id, authFetch]);

  if (error) return <div className="login-error">Could not load inspection: {error}</div>;
  if (!data) return <Empty>Loading…</Empty>;

  const shown = data.fasteners.find((f) => f.position === pos) ?? null;

  return (
    <>
      <div className="mono faint" style={{ marginBottom: 14 }}>
        {data.unit_id} · {data.correlation_id}
      </div>

      <div className="grid" style={{ gridTemplateColumns: "1.35fr 1fr", alignItems: "start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
          {shown && (
            <Panel title={`Torque–angle signature · position ${shown.position}`}
                   right={data.fasteners.length > 1 && (
                     <div style={{ display: "flex", gap: 6 }}>
                       {data.fasteners.map((f) => (
                         <button key={f.position} type="button"
                                 onClick={() => setPos(f.position)}
                                 style={{
                                   width: 34, height: 34, borderRadius: "50%", padding: 0,
                                   fontFamily: "var(--mono)", fontSize: 12,
                                   background: f.position === pos ? "var(--ink-black)"
                                     : f.signature_anomalous ? "var(--accent-molten-bg)" : "var(--surface-soft)",
                                   color: f.position === pos ? "#fff"
                                     : f.signature_anomalous ? "var(--accent-molten-hover)" : "var(--text-muted-2)",
                                 }}>
                           {f.position}
                         </button>
                       ))}
                     </div>
                   )}>
              <div className="solid-backing">
                <TorqueChart fastener={shown} baseline={data.baseline} />
              </div>
              {shown.deviations.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 14, alignItems: "flex-start" }}>
                  {shown.deviations.map((d, i) => (
                    <span key={i} className="pill tone-critical" style={{ fontSize: 12, whiteSpace: "normal", display: "inline-block", maxWidth: "100%" }}>{d}</span>
                  ))}
                </div>
              )}
              <Provenance items={[
                ["source", "MEASURED · torque-signature"],
                ["endpoint", `${shown.final_torque_nm.toFixed(1)} Nm ${shown.endpoint_in_spec ? "IN SPEC" : "OUT OF SPEC"}`],
                ["slope", `${shown.elastic_slope_nm_per_deg.toFixed(2)} Nm/deg`],
                ["baseline", `${data.baseline.derived_from_runs} runs @ ${data.baseline.sigma}σ`],
              ]} />
            </Panel>
          )}

          {data.image_uri && (
            <Panel title="Uploaded Vision Frame (VLM Input)">
              <div className="solid-backing" style={{ textAlign: "center", padding: 14 }}>
                <img src={data.image_uri} alt="Inspection Frame"
                     style={{ maxWidth: "100%", maxHeight: "280px", borderRadius: 8, display: "block", margin: "0 auto" }} />
              </div>
              <Provenance items={[
                ["source", "MEASURED · VLM camera frame"],
                ["vision status", "evaluated"],
              ]} />
            </Panel>
          )}

          <Panel tone="soft" title="Fasteners">
            <div className="panel" style={{ padding: "6px 18px 16px" }}>
              <table>
                <thead>
                  <tr>
                    <th>Pos</th><th>Final Nm</th><th>Knee</th><th>Slope</th>
                    <th>Score</th><th>In spec</th><th>Likely class</th>
                  </tr>
                </thead>
                <tbody>
                  {data.fasteners.map((f) => (
                    <tr key={f.position}
                        className={`clickable ${f.fusion_only ? "fusion" : f.signature_anomalous ? "flagged" : ""}`}
                        onClick={() => setPos(f.position)}>
                      <td>{f.position}</td>
                      <td>{f.final_torque_nm.toFixed(1)}</td>
                      <td>{f.knee_angle_deg.toFixed(1)}°</td>
                      <td>{f.elastic_slope_nm_per_deg.toFixed(2)}</td>
                      <td>{f.anomaly_score.toFixed(2)}</td>
                      <td style={{ color: f.endpoint_in_spec ? "var(--state-nominal-text)" : "var(--accent-molten-hover)" }}>
                        {f.endpoint_in_spec ? "✓ yes" : "✗ no"}
                      </td>
                      <td>{f.fusion_only
                        ? <span className="badge fusion">{f.likely_class}</span>
                        : (f.likely_class ?? "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Provenance items={[
                ["source", "MEASURED · verifiers + signature"],
                ["quality", data.data_quality],
              ]} />
            </div>
          </Panel>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
          <Panel tone="dark">
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
              <span className="who-avatar" style={{ background: "var(--accent-molten)", color: "#fff" }}>◈</span>
              <span style={{ fontSize: 11, letterSpacing: "0.14em", color: "#8E8A84" }}>DETERMINISTIC DECISION BASIS</span>
            </div>
            <div className="reasoning">{data.reasoning}</div>
            {data.requires_human && (
              <div style={{ borderTop: "1px solid #2A2724", marginTop: 18, paddingTop: 14 }}>
                <div style={{ fontSize: 10, letterSpacing: "0.14em", color: "#6E6A64", marginBottom: 6 }}>
                  WHY A HUMAN DECIDES
                </div>
                <div style={{ fontSize: 13, lineHeight: 1.65, color: "#B7B2AB" }}>{data.human_reason}</div>
              </div>
            )}
            <Provenance items={[
              ["source", "RULE · adjudicator"],
              ["signal", data.primary_signal],
              ["conf", data.confidence.toFixed(2)],
              ["latency", `${data.total_ms.toFixed(1)}ms`],
              ["deterministic", "no model call"],
            ]} />
          </Panel>

          <NarrativePanel correlationId={data.correlation_id} />

          <Panel title="Cost triage"
                 right={<span className="pill tone-molten">{data.disposition}</span>}>
            <div className="grid cols-2">
              <div className="solid-backing">
                <Kpi value={money(data.expected_cost, data.currency)} label="Expected cost"
                     color="var(--accent-molten)" />
              </div>
              <div className="solid-backing">
                <Kpi value={<span style={{ fontSize: 15 }}>
                  {money(data.cost_low, data.currency)} – {money(data.cost_high, data.currency)}
                </span>} label="Interval" />
              </div>
              <div className="solid-backing">
                <Kpi value={data.disposition} label="Recommendation" />
              </div>
              <div className="solid-backing">
                <Kpi value={data.requires_human ? "HUMAN" : "AUTO"} label="Authority"
                     color={data.requires_human ? "var(--state-watch)" : "var(--state-nominal-text)"} />
              </div>
            </div>
            <ul className="assumptions">
              {data.cost_assumptions.map((a, i) => <li key={i}>{a}</li>)}
            </ul>
            <div style={{ marginTop: 16, padding: 14, border: "1px solid var(--border)", borderRadius: "var(--r-sm)", background: "var(--surface-white)" }}>
              <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>Action Status & Governance</div>
              <div className="muted" style={{ fontSize: 12, lineHeight: 1.5, marginBottom: 12 }}>
                Recommendation: <strong style={{ color: "var(--ink)" }}>{data.disposition}</strong><br />
                {data.requires_human ? "Pending QA/Supervisor decision." : "Automated action applied."}<br />
                {data.requires_human && <span className="faint">Safety requirement: production line halt requires human approval before execution.</span>}
              </div>

              <div style={{
                padding: "10px 12px",
                borderRadius: 6,
                border: "1px solid rgba(224, 86, 36, 0.3)",
                background: data.assigned_to_name ? "rgba(224, 86, 36, 0.08)" : "var(--surface-faint)",
                marginBottom: 12,
              }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div style={{ fontSize: 10, letterSpacing: "0.1em", fontWeight: 700, color: "var(--accent-molten)", textTransform: "uppercase" }}>
                    👤 DEFECT CUSTODY / CURRENTLY WITH
                  </div>
                  <span className={`pill ${data.assigned_to_name ? "tone-critical" : "tone-watch"}`} style={{ fontSize: 10 }}>
                    {data.assigned_to_name ? "WITH WORKER" : "UNASSIGNED"}
                  </span>
                </div>
                <div style={{ fontSize: 14, fontWeight: 700, marginTop: 4, color: "var(--text-main)" }}>
                  {data.assigned_to_name ? (
                    <>
                      {data.assigned_to_name} <span style={{ fontSize: 11, fontWeight: 400, opacity: 0.75 }}>({data.assigned_to_username})</span>
                    </>
                  ) : (
                    <span style={{ color: "var(--text-muted)", fontStyle: "italic", fontSize: 12.5 }}>Unassigned — Pending QA / Shop Floor Rework Assignment</span>
                  )}
                </div>
                {data.assigned_by_name && (
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 3 }}>
                    Assigned by {data.assigned_by_name} • Email & Slack Alert Dispatched
                  </div>
                )}
              </div>

              {(can("inspection:create") || can("inspection:acknowledge")) && data.verdict !== "pass" && (
                <ReworkAssignmentBox
                  correlationId={data.correlation_id}
                  authFetch={authFetch}
                />
              )}
            </div>
            <Provenance items={[
              ["source", "RULE · expected-cost"],
              ["safety", "production halt requires human approval"],
              ["sender", "admin.defect.sense@gmail.com"],
            ]} />
          </Panel>

          {can("agentrun:read") && (
            <Panel tone="soft" title="Agent workflow"
                   right={<Link to={`/agents/${data.correlation_id}`} style={{ fontSize: 12 }}>
                            Open full trace →
                          </Link>}>
              {data.spans.map((s, i) => (
                <div className="agent-span" key={i}>
                  <span className="name">{s.agent}</span>
                  <span className="ms">{s.duration_ms.toFixed(2)}ms</span>
                  <span className="sum">{s.summary}</span>
                </div>
              ))}
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}

function ReworkAssignmentBox({
  correlationId,
  authFetch,
}: {
  correlationId: string;
  authFetch: (url: string, init?: RequestInit) => Promise<Response>;
}) {
  const [worker, setWorker] = useState("ravi");
  const [email, setEmail] = useState("prakhar181999@gmail.com");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useState<{ text: string; error?: boolean } | null>(null);
  const [workers, setWorkers] = useState<{ username: string; display_name: string }[]>([
    { username: "ravi", display_name: "Ravi Verma" },
    { username: "aarav", display_name: "Aarav Singh" },
  ]);

  useEffect(() => {
    authFetch("/api/v1/auth/demo-accounts")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && Array.isArray(d.accounts)) {
          const sf = d.accounts
            .filter((a: any) => a.role === "SHOP_FLOOR_WORKER")
            .map((a: any) => ({ username: a.username, display_name: a.display_name }));
          if (sf.length > 0) setWorkers(sf);
        }
      })
      .catch(() => {});
  }, [authFetch]);

  const handleAssign = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setStatusMsg(null);
    try {
      const res = await authFetch("/api/v1/defects/assign", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          correlation_id: correlationId,
          assigned_to_username: worker,
          recipient_email: email.trim(),
          note: note.trim(),
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setStatusMsg({
        text: `Assigned to ${data.assigned_to_name}! Email & Slack alerts dispatched to ${data.recipient_email}`,
      });
    } catch (err) {
      setStatusMsg({
        text: err instanceof Error ? err.message : "Assignment failed",
        error: true,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={handleAssign} style={{ paddingTop: 10, borderTop: "1px solid var(--border-faint)" }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--accent-molten)", marginBottom: 8 }}>
        ASSIGN REWORK TO WORKER
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div>
          <label className="field" style={{ margin: "0 0 4px" }}>Shop Floor Worker</label>
          <select value={worker} onChange={(e) => setWorker(e.target.value)} style={{ padding: "8px 14px", fontSize: 13 }}>
            {workers.map((w) => (
              <option key={w.username} value={w.username}>
                {w.display_name} ({w.username})
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="field" style={{ margin: "0 0 4px" }}>Alert Email Address</label>
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{ padding: "8px 14px", fontSize: 12.5 }}
            className="mono"
          />
        </div>
        <div>
          <label className="field" style={{ margin: "0 0 4px" }}>Rework Note / Instructions (Optional)</label>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. Inspect fastener thread contamination before re-torque"
            style={{ padding: "8px 14px", fontSize: 12.5 }}
          />
        </div>
        <button type="submit" className="molten" disabled={busy} style={{ marginTop: 4, width: "100%", padding: "10px 16px" }}>
          {busy ? "Assigning & Emailing…" : "Assign Rework & Send Email →"}
        </button>
      </div>
      {statusMsg && (
        <div className={`pill ${statusMsg.error ? "tone-critical" : "tone-nominal"}`} style={{ marginTop: 10, fontSize: 11 }}>
          {statusMsg.text}
        </div>
      )}
    </form>
  );
}

