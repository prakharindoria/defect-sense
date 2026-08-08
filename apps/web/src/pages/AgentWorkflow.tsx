import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useAuth } from "../auth";
import { Panel, Provenance } from "../components/Shared";
import { usePageTitle } from "../pageHeader";
import type { Inspection } from "../types";
import { useLiveFeed } from "../useLiveFeed";

const AGENT_LABELS: Record<string, string> = {
  ingestion: "Ingestion",
  vision_inspector: "Vision Inspector",
  process_sentinel: "Process Sentinel",
  adjudicator: "Decision Arbiter",
  cost_triage: "Cost Triage",
  context: "Context",
};

const DEFAULT_DEMO_INSPECTION: Inspection = {
  correlation_id: "corr-demo-5node-agent-flow",
  unit_id: "VIN-SYN-00018",
  created_at: new Date().toISOString(),
  pack_id: "wheel_assembly",
  verdict: "defect",
  disposition: "quarantine",
  confidence: 0.94,
  fusion_only: true,
  severity: "medium",
  primary_signal: "process_sentinel",
  reasoning:
    "Vision passed geometric verification (5/5 bolts). Process Sentinel detected yield slope degradation on Fastener 3 (1.4 Nm/deg vs 2.1 Nm/deg baseline), indicating thread particle contamination.",
  expected_cost: 34838,
  cost_low: 28000,
  cost_high: 42000,
  currency: "INR",
  cost_assumptions: ["Quarantine teardown cost"],
  requires_human: true,
  human_reason: "Fusion disagreement between vision pass and torque defect",
  data_quality: "PASS",
  data_quality_reasons: [],
  is_synthetic: true,
  baseline: {
    knee_angle_deg: 45.0,
    knee_tolerance_deg: 2.0,
    elastic_slope_nm_per_deg: 2.1,
    elastic_slope_tolerance: 0.2,
    spec_lo_nm: 115.0,
    spec_hi_nm: 125.0,
    derived_from_runs: 500,
    sigma: 3.0,
  },
  fasteners: [
    { position: 1, final_torque_nm: 118.4, knee_angle_deg: 44.2, elastic_slope_nm_per_deg: 2.1, anomaly_score: 0.1, endpoint_in_spec: true, signature_anomalous: false, fusion_only: false, likely_class: null, deviations: [], curve: [] },
    { position: 2, final_torque_nm: 119.1, knee_angle_deg: 45.0, elastic_slope_nm_per_deg: 2.2, anomaly_score: 0.1, endpoint_in_spec: true, signature_anomalous: false, fusion_only: false, likely_class: null, deviations: [], curve: [] },
    { position: 3, final_torque_nm: 114.2, knee_angle_deg: 41.8, elastic_slope_nm_per_deg: 1.4, anomaly_score: 0.88, endpoint_in_spec: true, signature_anomalous: true, fusion_only: true, likely_class: "thread_contamination", deviations: ["Yield slope 1.4 Nm/deg below baseline 2.1 Nm/deg"], curve: [] },
    { position: 4, final_torque_nm: 120.0, knee_angle_deg: 45.2, elastic_slope_nm_per_deg: 2.15, anomaly_score: 0.1, endpoint_in_spec: true, signature_anomalous: false, fusion_only: false, likely_class: null, deviations: [], curve: [] },
    { position: 5, final_torque_nm: 119.5, knee_angle_deg: 44.8, elastic_slope_nm_per_deg: 2.1, anomaly_score: 0.1, endpoint_in_spec: true, signature_anomalous: false, fusion_only: false, likely_class: null, deviations: [], curve: [] },
  ],
  total_ms: 7.42,
  spans: [
    { agent: "ingestion", duration_ms: 0.84, ok: true, summary: "Input payload validated and ingested." },
    { agent: "vision_inspector", duration_ms: 2.15, ok: true, summary: "Visual geometry check: passed (5/5 fasteners visible, within tolerance)." },
    { agent: "process_sentinel", duration_ms: 1.95, ok: false, summary: "Torque signature analysis: detected yield slope anomaly on Fastener 3." },
    { agent: "adjudicator", duration_ms: 1.48, ok: true, summary: "Reconciled signal conflict: Vision pass vs. Torque defect → elevated to defect (anomaly significance outweighs endpoint spec)." },
    { agent: "cost_triage", duration_ms: 1.00, ok: true, summary: "Cost analysis complete: Quarantine recommended (lowest risk disposition)." },
  ],
};

export function AgentWorkflow() {
  const { id } = useParams<{ id?: string }>();
  const { authFetch } = useAuth();
  usePageTitle("Agent workflow", "Five specialists, one state.");
  const feed = useLiveFeed();
  const [data, setData] = useState<Inspection | null>(null);
  const [activeStep, setActiveStep] = useState<number | null>(null);

  useEffect(() => {
    const target = id ?? feed[0]?.correlation_id;
    if (target) {
      authFetch(`/api/v1/inspections/${target}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => setData(d || DEFAULT_DEMO_INSPECTION))
        .catch(() => setData(DEFAULT_DEMO_INSPECTION));
    } else {
      authFetch("/api/v1/inspections?limit=1")
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (Array.isArray(d) && d.length > 0) {
            setData(d[0]);
          } else {
            setData(DEFAULT_DEMO_INSPECTION);
          }
        })
        .catch(() => setData(DEFAULT_DEMO_INSPECTION));
    }
  }, [id, feed[0]?.correlation_id, authFetch]);

  const activeData = data || DEFAULT_DEMO_INSPECTION;
  const fusionCatches = activeData.fasteners.filter((f) => f.fusion_only).length;

  return (
    <>
      <style>{`
        .clean-node {
          position: relative;
          padding: 12px 14px;
          border-radius: var(--r-md);
          border: 1.5px solid var(--border);
          background: var(--surface-white);
          transition: all 0.2s ease;
        }
        .clean-node.parallel {
          margin-left: 28px;
          border-color: var(--accent-molten-border);
        }
        .clean-node:hover, .clean-node.active {
          border-color: var(--accent-molten);
          box-shadow: 0 2px 10px rgba(224, 86, 36, 0.15);
        }
      `}</style>

      <div className="grid" style={{ gridTemplateColumns: "1.2fr 1fr", alignItems: "start", gap: 16 }}>
        {/* Minimal Left Trace Column */}
        <Panel tone="soft" title={`Trace · ${activeData.unit_id}`} right={<span className="mono faint">{activeData.correlation_id}</span>}>
          <div className="graph" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {activeData.spans.map((span, i) => {
              const label = AGENT_LABELS[span.agent] || span.agent;
              const parallel = span.agent === "vision_inspector" || span.agent === "process_sentinel";
              const isActive = activeStep === i;

              return (
                <div
                  key={`${span.agent}-${i}`}
                  className={`clean-node ${parallel ? "parallel" : ""} ${isActive ? "active" : ""}`}
                  onClick={() => setActiveStep(i)}
                  style={{ cursor: "pointer" }}
                >
                  <div className="node-head" style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span
                      className="node-index"
                      style={{
                        color: "#fff",
                        background: parallel ? "var(--accent-molten)" : "var(--ink-black)",
                        fontSize: 11,
                        fontFamily: "var(--mono)",
                        width: 24,
                        height: 24,
                        borderRadius: "50%",
                        display: "inline-flex",
                        alignItems: "center",
                        justifyContent: "center",
                        flex: "none",
                      }}
                    >
                      {i + 1}
                    </span>
                    <span className="node-label" style={{ fontWeight: 600, fontSize: 13.5 }}>
                      {label}
                    </span>
                    {parallel && <span className="parallel-tag" style={{ fontSize: 9, padding: "2px 8px" }}>PARALLEL</span>}
                    <div style={{ flex: 1 }} />
                    <span className="node-ms mono" style={{ fontSize: 11, color: "var(--accent-molten)", fontWeight: 600 }}>
                      {span.duration_ms.toFixed(2)}ms
                    </span>
                  </div>

                  <div className="node-summary" style={{ color: "var(--text-muted-3)", fontSize: 12, marginTop: 4, marginLeft: 34, lineHeight: 1.45 }}>
                    {span.summary}
                  </div>
                </div>
              );
            })}
          </div>

          <Provenance
            items={[
              ["total execution", `${activeData.total_ms.toFixed(2)}ms`],
              ["specialists", String(activeData.spans.length)],
            ]}
          />
        </Panel>

        {/* Minimal Right Metrics Column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="grid cols-2">
            <Panel tone="dark">
              <div className="mono" style={{ fontSize: 24, fontWeight: 600 }}>
                {activeData.total_ms.toFixed(1)}ms
              </div>
              <div style={{ fontSize: 10.5, letterSpacing: "0.13em", color: "#8E8A84", marginTop: 6 }}>
                END-TO-END LATENCY
              </div>
            </Panel>
            <Panel>
              <div className="mono" style={{ fontSize: 24, fontWeight: 600, color: "var(--accent-molten)" }}>
                {fusionCatches}
              </div>
              <div style={{ fontSize: 10.5, letterSpacing: "0.13em", color: "var(--text-faint-2)", marginTop: 6 }}>
                FUSION-ONLY CATCHES
              </div>
            </Panel>
          </div>

          {activeStep !== null && (
            <Panel tone="soft" title={`Step ${activeStep + 1} · ${AGENT_LABELS[activeData.spans[activeStep]?.agent] || activeData.spans[activeStep]?.agent}`}>
              <div style={{ fontSize: 12.5, color: "var(--text-muted)", lineHeight: 1.5 }}>
                {activeData.spans[activeStep]?.summary}
              </div>
              <div style={{ display: "flex", gap: 12, marginTop: 8, fontSize: 11 }} className="mono">
                <span>Duration: <strong>{activeData.spans[activeStep]?.duration_ms.toFixed(2)}ms</strong></span>
                <span>Status: <strong style={{ color: "var(--state-nominal-text)" }}>OK ✓</strong></span>
              </div>
            </Panel>
          )}

          <Panel title="Handoffs">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Agent</th>
                  <th>Duration</th>
                  <th>Output Summary</th>
                  <th>OK</th>
                </tr>
              </thead>
              <tbody>
                {activeData.spans.map((s, i) => (
                  <tr
                    key={i}
                    style={{
                      background: activeStep === i ? "rgba(224, 86, 36, 0.08)" : "transparent",
                      cursor: "pointer",
                    }}
                    onClick={() => setActiveStep(i)}
                  >
                    <td>{i + 1}</td>
                    <td style={{ color: "var(--accent-molten-hover)", fontWeight: 600 }}>{AGENT_LABELS[s.agent] || s.agent}</td>
                    <td className="mono">{s.duration_ms.toFixed(2)}ms</td>
                    <td style={{ fontFamily: "var(--sans)", color: "var(--text-muted-3)" }}>{s.summary}</td>
                    <td style={{ color: s.ok ? "var(--state-nominal-text)" : "var(--accent-molten-hover)" }}>
                      {s.ok ? "✓" : "✗"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>

          {fusionCatches !== null && fusionCatches > 0 && (
            <Panel tone="soft">
              <div style={{ fontSize: 11, letterSpacing: "0.14em", color: "var(--text-faint-2)", marginBottom: 6 }}>
                THE DISAGREEMENT
              </div>
              <div style={{ fontSize: 16, fontWeight: 500, letterSpacing: "-0.015em", lineHeight: 1.35 }}>
                Vision passed. The torque signature did not.
              </div>
              <div className="faint" style={{ lineHeight: 1.6, marginTop: 8 }}>
                The endpoint landed inside spec, so an endpoint-only check would have shipped this wheel. The defect lives in the disagreement between the two agents.
              </div>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}



