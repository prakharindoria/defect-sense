import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../auth";
import { Andon, Empty, Kpi, money, Panel, Provenance } from "../components/Shared";
import { TorqueChart } from "../TorqueChart";
import type { Inspection } from "../types";

/** Full evidence for one unit. QA and Admin. */
export function Workbench() {
  const { id } = useParams<{ id: string }>();
  const { authFetch, can } = useAuth();
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
      <h1 className="page-title">Defect Workbench · {data.unit_id}</h1>
      <p className="page-sub mono">{data.correlation_id}</p>

      <Andon inspection={data} idleText="" />

      <Panel title="Adjudicator reasoning">
        <div className="solid-backing">
          <div className="reasoning">{data.reasoning}</div>
        </div>
        {data.requires_human && (
          <div className="solid-backing" style={{ marginTop: 10 }}>
            <div className="faint" style={{ marginBottom: 4 }}>WHY A HUMAN DECIDES</div>
            <div className="muted" style={{ lineHeight: 1.6 }}>{data.human_reason}</div>
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

      {shown && (
        <div style={{ marginTop: 16 }}>
          <Panel title={`Torque-angle signature · position ${shown.position}`}>
            <TorqueChart fastener={shown} baseline={data.baseline} />
            {shown.deviations.length > 0 && (
              <ul className="assumptions">
                {shown.deviations.map((d, i) => <li key={i}>{d}</li>)}
              </ul>
            )}
            <Provenance items={[
              ["source", "MEASURED · torque-signature"],
              ["endpoint", `${shown.final_torque_nm.toFixed(1)} Nm ${shown.endpoint_in_spec ? "IN SPEC" : "OUT OF SPEC"}`],
              ["slope", `${shown.elastic_slope_nm_per_deg.toFixed(2)} Nm/deg`],
              ["baseline", `${data.baseline.derived_from_runs} runs @ ${data.baseline.sigma}σ`],
            ]} />
          </Panel>
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        <Panel title="Fasteners">
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
                  <td>{f.endpoint_in_spec ? "✓ yes" : "✗ no"}</td>
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
        </Panel>
      </div>

      <div style={{ marginTop: 16 }}>
        <Panel title={`Cost triage · ${data.disposition}`}>
          <div className="grid cols-4">
            <Kpi value={money(data.expected_cost, data.currency)} label="Expected cost"
                 color="var(--accent-molten)" />
            <Kpi value={<span style={{ fontSize: 15 }}>
              {money(data.cost_low, data.currency)} – {money(data.cost_high, data.currency)}
            </span>} label="Interval" />
            <Kpi value={data.disposition} label="Recommendation" />
            <Kpi value={data.requires_human ? "HUMAN" : "AUTO"} label="Authority"
                 color={data.requires_human ? "var(--state-watch)" : "var(--state-nominal)"} />
          </div>
          <ul className="assumptions">
            {data.cost_assumptions.map((a, i) => <li key={i}>{a}</li>)}
          </ul>
          <Provenance items={[
            ["source", "RULE · expected-cost"],
            ["never autonomous", "line halt"],
          ]} />
        </Panel>
      </div>

      {can("agentrun:read") && (
        <div style={{ marginTop: 16 }}>
          <Panel title="Agent workflow"
                 right={<Link to={`/agents/${data.correlation_id}`}
                              style={{ color: "var(--accent-signal)", fontSize: 12 }}>
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
        </div>
      )}
    </>
  );
}
