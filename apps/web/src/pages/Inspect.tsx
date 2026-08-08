import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { Empty, money, Panel, Provenance } from "../components/Shared";
import { usePageTitle } from "../pageHeader";
import type { Inspection, Scenario } from "../types";
import { pushLocal } from "../useLiveFeed";
import { PRODUCT_NAME } from "../brand";

/**
 * Input page — QA and Admin only.
 *
 * This is where a unit enters the system. In production the inputs arrive from
 * the station camera and the fastening controller; here they are generated with
 * recorded ground truth so detection can be scored against what was actually
 * injected rather than against a later human opinion.
 */
export function Inspect() {
  const { authFetch } = useAuth();
  const navigate = useNavigate();
  usePageTitle("Submit inspection", "Run a production condition.");
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenario, setScenario] = useState("clean");
  const [position, setPosition] = useState(3);
  const [scope, setScope] = useState(12);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<Inspection | null>(null);



  useEffect(() => {
    authFetch("/api/v1/scenarios").then((r) => r.json())
      .then((d) => setScenarios(d.scenarios)).catch(() => setError("Cannot reach the API."));
  }, [authFetch]);

  const run = async (goToWorkbench: boolean) => {
    setBusy(true); setError(null);
    try {
      const res = await authFetch("/api/v1/inspections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario, position, containment_scope: scope }),
      });
      if (res.status === 403) throw new Error("Your role cannot submit inspections.");
      if (!res.ok) throw new Error(`API returned ${res.status}`);
      const data: Inspection = await res.json();
      pushLocal(data);
      setLast(data);
      if (goToWorkbench) navigate(`/workbench/${data.correlation_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Inspection failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {error && <div className="login-error" role="alert">{error}</div>}

      <div className="grid cols-2">
        <Panel tone="soft">
          <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 4 }}>Demo controls — synthetic input</div>
          <div className="faint" style={{ lineHeight: 1.6, marginBottom: 16 }}>
            Select a scenario to simulate a unit arriving on the line. 
            This is a demo control, not a production workflow.
          </div>

          <div className="scenario">
            {scenarios.map((s) => (
              <button key={s.id} type="button"
                      className={`scenario-btn ${scenario === s.id ? "active" : ""}`}
                      onClick={() => setScenario(s.id)}>
                <span className="radio" />
                <span>
                  <span className="name">{s.label}</span>
                  <span className="note">{s.note}</span>
                </span>
              </button>
            ))}
          </div>

          <div className="grid cols-2" style={{ marginTop: 18 }}>
            <div className="stepper">
              <div className="stepper-label">FASTENER POSITION</div>
              <div className="stepper-row">
                <button type="button" className="step"
                        onClick={() => setPosition((p) => Math.max(1, p - 1))}>−</button>
                <div className="stepper-value">{position}</div>
                <button type="button" className="step up"
                        onClick={() => setPosition((p) => Math.min(5, p + 1))}>+</button>
              </div>
            </div>
            <div className="stepper">
              <div className="stepper-label">CONTAINMENT SCOPE</div>
              <div className="stepper-row">
                <button type="button" className="step"
                        onClick={() => setScope((p) => Math.max(1, p - 1))}>−</button>
                <div className="stepper-value">{scope}</div>
                <button type="button" className="step up"
                        onClick={() => setScope((p) => p + 1)}>+</button>
              </div>
            </div>
          </div>
          <div className="faint" style={{ marginTop: 12, lineHeight: 1.6 }}>
            Units since last known-good drives the cost model. One unit and two
            hundred units are different decisions, not the same decision scaled.
          </div>

          <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
            <button className="molten" onClick={() => run(true)} disabled={busy} style={{ flex: 1 }}>
              {busy ? "Inspecting…" : "Run & open evidence →"}
            </button>
          </div>
        </Panel>

        <Panel title="Last result"
               right={last && <span className="mono faint">{last.correlation_id}</span>}>
          {!last && <Empty>
            Nothing submitted yet. Start with <strong>Nominal run</strong> to see the
            system pass a good wheel — then run <strong>Contaminated threads</strong>,
            where the torque endpoint lands inside spec and vision passes, and {PRODUCT_NAME}
            flags it anyway.
          </Empty>}
          {last && (
            <>
              <div className={`andon state-${last.verdict}`}>
                <span className="dot" />
                <div className="label">{last.verdict.toUpperCase()}</div>
                <div className="detail">{last.unit_id} · {last.total_ms.toFixed(1)}ms</div>
              </div>
              <div className="solid-backing" style={{ marginTop: 14 }}>
                <div className="reasoning">{last.reasoning}</div>
              </div>
              <div className="grid cols-2" style={{ marginTop: 14 }}>
                <div className="solid-backing">
                  <div className="mono" style={{ fontSize: 19, fontWeight: 600, color: "var(--accent-molten)" }}>
                    {money(last.expected_cost, last.currency)}
                  </div>
                  <div style={{ fontSize: 11, letterSpacing: "0.1em", color: "var(--text-faint-2)", marginTop: 5 }}>
                    EXPECTED COST
                  </div>
                </div>
                <div className="solid-backing">
                  <div className="mono" style={{ fontSize: 19, fontWeight: 600 }}>
                    {last.total_ms.toFixed(1)}ms
                  </div>
                  <div style={{ fontSize: 11, letterSpacing: "0.1em", color: "var(--text-faint-2)", marginTop: 5 }}>
                    TOTAL LATENCY
                  </div>
                </div>
              </div>
              <button className="primary" style={{ marginTop: 14, width: "100%" }}
                      onClick={() => navigate(`/workbench/${last.correlation_id}`)}>
                Open full evidence →
              </button>
              <div className="mono" style={{ fontSize: 10, color: "var(--text-faint-3)", marginTop: 14, lineHeight: 1.7 }}>
                source RULE · adjudicator · deterministic, no model call<br />
                scenario {scenario} @ position {position}
              </div>
            </>
          )}
        </Panel>
      </div>

      <div style={{ marginTop: 20 }}>
        <RuntimeImageIngestPanel authFetch={authFetch} />
      </div>
    </>
  );
}

function RuntimeImageIngestPanel({ authFetch }: { authFetch: (url: string, init?: RequestInit) => Promise<Response> }) {
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [componentType, setComponentType] = useState("wheel_assembly");
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [latestCreated, setLatestCreated] = useState<Inspection | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const selected = e.target.files[0];
      setFile(selected);
      setPreview(URL.createObjectURL(selected));
      setResult(null);
      setLatestCreated(null);
      setError(null);
    }
  };

  const handleAnalyze = async () => {
    if (!file) return;
    setAnalyzing(true); setError(null);
    try {
      // Convert image file to base64 data URI for pipeline
      const reader = new FileReader();
      const base64Promise = new Promise<string>((resolve) => {
        reader.onload = (e) => resolve(e.target?.result as string);
        reader.readAsDataURL(file);
      });
      const dataUri = await base64Promise;

      // 1. Submit to VLM Endpoint for standalone vision breakdown
      const formData = new FormData();
      formData.append("image", file);
      formData.append("component_type", componentType);

      const res = await authFetch("/api/v1/vision/analyze", {
        method: "POST",
        body: formData,
      });

      if (res.ok) {
        const visionData = await res.json();
        setResult(visionData);
      }

      // 2. Submit to Multi-Agent Inspection Pipeline (POST /api/v1/inspections)
      const inspRes = await authFetch("/api/v1/inspections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenario: "thread_contamination",
          position: 3,
          containment_scope: 12,
          image_data_uri: dataUri,
          component_type: componentType,
        }),
      });

      if (inspRes.ok) {
        const inspData = await inspRes.json();
        pushLocal(inspData);
        setLatestCreated(inspData);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Vision analysis failed");
    } finally {
      setAnalyzing(false);
    }
  };

  return (
    <Panel tone="soft" title="Live Ingest — Runtime Image Analysis (VLM)">
      <div className="faint" style={{ lineHeight: 1.6, marginBottom: 16 }}>
        Upload an actual component image at runtime. Analyzed live by <strong>Llama-3.2-90B-Vision / GPT-4o</strong> to detect anomalies, missing fasteners, or surface defects.
      </div>

      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <div>
          <label className="field" style={{ margin: "0 0 6px" }}>Component Type Hint</label>
          <select value={componentType} onChange={(e) => setComponentType(e.target.value)}>
            <option value="wheel_assembly">Wheel Assembly</option>
            <option value="brake_assembly">Brake Assembly</option>
            <option value="pcb_board">PCB Board</option>
            <option value="motor_bearing">Motor Bearing</option>
            <option value="">Auto-Detect Component</option>
          </select>

          <div style={{ marginTop: 14 }}>
            <label className="field" style={{ margin: "0 0 6px" }}>Select Image File</label>
            <input type="file" accept="image/jpeg,image/png" onChange={handleFileChange} style={{ padding: "10px" }} />
          </div>

          {preview && (
            <div style={{ marginTop: 14, background: "#fff", padding: 10, borderRadius: "var(--r-sm)", border: "1px solid var(--border)", textAlign: "center" }}>
              <img src={preview} alt="Inspection Preview" style={{ maxWidth: "100%", maxHeight: "220px", borderRadius: 8, display: "block", margin: "0 auto" }} />
              <div className="mono faint" style={{ marginTop: 6, fontSize: 11 }}>{file?.name} ({Math.round((file?.size || 0) / 1024)} KB)</div>
            </div>
          )}

          <button
            className="molten"
            onClick={handleAnalyze}
            disabled={!file || analyzing}
            style={{ width: "100%", marginTop: 16 }}
          >
            {analyzing ? "Analyzing with VLM…" : "Upload & Analyze Image →"}
          </button>
          {error && <div className="login-error" style={{ marginTop: 10 }}>{error}</div>}
        </div>

        <div>
          {!result && (
            <Empty>
              Select an image file and click <strong>Upload & Analyze Image</strong> to run real-time computer vision defect detection.
            </Empty>
          )}

          {result && (
            <div className="solid-backing" style={{ background: "var(--surface-white)" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                <span className={`pill ${result.defects_found ? "tone-critical" : "tone-nominal"}`}>
                  {result.overall_condition.toUpperCase()}
                </span>
                <span className="mono faint">{result.image_dimensions}</span>
              </div>

              <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 8 }}>
                Identified: <strong style={{ color: "var(--ink)" }}>{result.component_identified}</strong>
              </div>

              <div className="faint" style={{ marginBottom: 12 }}>
                {result.defects_found ? `Found ${result.defect_count} defect(s)` : "No visible defects detected."}
              </div>

              {result.defects.map((d: any, i: number) => (
                <div key={i} style={{ border: "1px solid var(--border)", borderRadius: "var(--r-sm)", padding: 12, marginBottom: 10, background: "var(--surface-soft)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{d.defect_type}</span>
                    <span className={`pill ${d.severity === "critical" || d.severity === "high" ? "tone-critical" : "tone-watch"}`} style={{ fontSize: 10 }}>
                      {d.severity}
                    </span>
                  </div>
                  <div className="faint" style={{ fontSize: 12, lineHeight: 1.5 }}>
                    Location: <strong>{d.location}</strong><br />
                    {d.description}
                  </div>
                  <div className="mono faint" style={{ marginTop: 6, fontSize: 10 }}>
                    Confidence: {(d.confidence * 100).toFixed(0)}%
                  </div>
                </div>
              ))}

              {result.recommendations && result.recommendations.length > 0 && (
                <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid var(--border-faint)" }}>
                  <div className="faint" style={{ fontWeight: 600, fontSize: 11, marginBottom: 4 }}>RECOMMENDATIONS</div>
                  <ul className="assumptions">
                    {result.recommendations.map((r: string, i: number) => <li key={i}>{r}</li>)}
                  </ul>
                </div>
              )}

              <Provenance items={[
                ["source", result.provenance.source],
                ["model", result.provenance.model_id || "vision"],
                ["latency", `${result.provenance.latency_ms}ms`],
              ]} />

              {latestCreated && (
                <button
                  className="molten"
                  onClick={() => navigate(`/workbench/${latestCreated.correlation_id}`)}
                  style={{ width: "100%", marginTop: 14, display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}
                >
                  Open Full Multi-Agent Evidence →
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

