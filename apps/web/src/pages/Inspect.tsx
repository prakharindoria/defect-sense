import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { Empty, Panel } from "../components/Shared";
import type { Inspection, Scenario } from "../types";
import { pushLocal } from "../useLiveFeed";

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
      <h1 className="page-title">Submit inspection</h1>
      <p className="page-sub">
        Choose a production condition and run it through the agent pipeline.
        Every record is synthetic and carries its ground-truth label.
      </p>

      {error && <div className="login-error" role="alert">{error}</div>}

      <div className="grid cols-2">
        <Panel title="Production condition">
          <div className="scenario">
            {scenarios.map((s) => (
              <button key={s.id} type="button"
                      className={`scenario-btn ${scenario === s.id ? "active" : ""}`}
                      onClick={() => setScenario(s.id)}>
                <span className="name">{s.label}</span>
                <span className="note">{s.note}</span>
              </button>
            ))}
          </div>

          <label className="field" htmlFor="pos">Fastener position (1–5)</label>
          <input id="pos" type="number" min={1} max={5} value={position}
                 onChange={(e) => setPosition(Number(e.target.value))} />

          <label className="field" htmlFor="scope">
            Containment scope — units since last known-good
          </label>
          <input id="scope" type="number" min={1} value={scope}
                 onChange={(e) => setScope(Number(e.target.value))} />
          <div className="faint" style={{ marginTop: 6, lineHeight: 1.5 }}>
            Drives the cost model. One unit and two hundred units are different
            decisions, not the same decision scaled.
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button className="primary" onClick={() => run(true)} disabled={busy}
                    style={{ flex: 1 }}>
              {busy ? "Inspecting…" : "▶ Run &amp; open evidence"}
            </button>
            <button onClick={() => run(false)} disabled={busy}>Run only</button>
          </div>
        </Panel>

        <Panel title="Last result">
          {!last && <Empty>
            Nothing submitted yet. Start with <strong>Nominal run</strong> to see the
            system pass a good wheel — then run <strong>Contaminated threads</strong>,
            where the torque endpoint lands inside spec and vision passes, and FORGE
            flags it anyway.
          </Empty>}
          {last && (
            <>
              <div className={`andon state-${last.verdict}`} style={{ marginBottom: 14 }}>
                <span className="dot" />
                <div className="label">{last.verdict.toUpperCase()}</div>
                <div className="detail">{last.unit_id} · {last.total_ms.toFixed(1)}ms</div>
              </div>
              <div className="solid-backing">
                <div className="reasoning">{last.reasoning}</div>
              </div>
              <button style={{ marginTop: 14, width: "100%" }}
                      onClick={() => navigate(`/workbench/${last.correlation_id}`)}>
                Open full evidence →
              </button>
            </>
          )}
        </Panel>
      </div>
    </>
  );
}
