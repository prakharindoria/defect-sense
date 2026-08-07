import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useAuth } from "../auth";
import { Empty, Panel, Provenance } from "../components/Shared";
import type { Inspection } from "../types";
import { useLiveFeed } from "../useLiveFeed";

const NODES = [
  { id: "ingestion", label: "Ingestion", note: "validate · DQ gate · correlation id" },
  { id: "vision_inspector", label: "Vision", note: "geometric verifiers (exact)" },
  { id: "process_sentinel", label: "Process Sentinel", note: "torque-angle signature" },
  { id: "adjudicator", label: "Adjudicator", note: "reconciles disagreement" },
  { id: "cost_triage", label: "Cost Triage", note: "expected cost per action" },
];

/**
 * The multi-agent workflow, visible to QA and Admin.
 *
 * Vision and Process Sentinel run in parallel and their outputs only meet at
 * the Adjudicator — which is the whole point. The defect this system exists to
 * catch lives in the disagreement between them, so the graph is drawn to make
 * that convergence obvious rather than as a straight line.
 */
export function AgentWorkflow() {
  const { id } = useParams<{ id?: string }>();
  const { authFetch } = useAuth();
  const feed = useLiveFeed();
  const [data, setData] = useState<Inspection | null>(null);

  useEffect(() => {
    const target = id ?? feed[0]?.correlation_id;
    if (!target) return;
    authFetch(`/api/v1/inspections/${target}`)
      .then((r) => (r.ok ? r.json() : null)).then(setData).catch(() => {});
  }, [id, feed, authFetch]);

  const spanFor = (nodeId: string) => data?.spans.find((s) => s.agent === nodeId);

  return (
    <>
      <h1 className="page-title">Agent workflow</h1>
      <p className="page-sub">
        Five specialists behind an orchestrator. Vision and Process Sentinel run
        in parallel; their outputs meet only at the Adjudicator, which is where
        a fusion-only defect becomes visible.
      </p>

      {!data && <Empty>
        No inspection selected. Submit one from the Inspect page, or open a unit
        from the dashboard.
      </Empty>}

      {data && (
        <>
          <Panel title={`Trace · ${data.unit_id}`}
                 right={<span className="mono faint">{data.correlation_id}</span>}>
            <div className="graph">
              {NODES.map((n, i) => {
                const span = spanFor(n.id);
                const parallel = n.id === "vision_inspector" || n.id === "process_sentinel";
                return (
                  <div key={n.id} className={`node ${span ? "ran" : "idle"} ${parallel ? "parallel" : ""}`}>
                    <div className="node-head">
                      <span className="node-index mono">{i + 1}</span>
                      <span className="node-label">{n.label}</span>
                      {span && <span className="node-ms mono">{span.duration_ms.toFixed(2)}ms</span>}
                    </div>
                    <div className="node-note">{n.note}</div>
                    {span && <div className="node-summary">{span.summary}</div>}
                    {parallel && <span className="parallel-tag mono">parallel</span>}
                  </div>
                );
              })}
            </div>
            <Provenance items={[
              ["total", `${data.total_ms.toFixed(2)}ms`],
              ["nodes", String(data.spans.length)],
              ["verdict path", "deterministic — no model call"],
              ["pack", data.pack_id],
            ]} />
          </Panel>

          <div style={{ marginTop: 16 }}>
            <Panel title="Handoffs">
              <table>
                <thead><tr><th>#</th><th>Agent</th><th>Duration</th><th>Output</th><th>OK</th></tr></thead>
                <tbody>
                  {data.spans.map((s, i) => (
                    <tr key={i}>
                      <td>{i + 1}</td>
                      <td style={{ color: "var(--accent-signal)" }}>{s.agent}</td>
                      <td>{s.duration_ms.toFixed(2)}ms</td>
                      <td style={{ fontFamily: "var(--sans)" }}>{s.summary}</td>
                      <td>{s.ok ? "✓" : "✗"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Provenance items={[
                ["state", "QCState — every agent reads and writes the same object"],
                ["escalation", data.requires_human ? "raised to human" : "not raised"],
              ]} />
            </Panel>
          </div>
        </>
      )}
    </>
  );
}
