import { useEffect, useState } from "react";
import { useAuth } from "../auth";
import { Empty, Kpi, Panel, Provenance } from "../components/Shared";

interface Capability {
  provider: string; model: string; reachable: boolean;
  supports_vision: boolean; supports_json_mode: boolean;
  supports_streaming: boolean; measured_p50_latency_ms: number; error: string | null;
}
interface SystemInfo {
  tiers: Record<string, { chain: string[]; terminal_fallback: string; available: boolean }>;
  capabilities: Capability[];
  skipped_providers: { provider: string; reason: string }[];
  roles: Record<string, { label: string; permissions: string[]; default_page: string }>;
  health: Record<string, unknown>;
}

/**
 * Admin — platform, not quality.
 *
 * The live capability matrix is the point of this page. "What if your model
 * provider changes?" is answered by measurements taken at boot, not by a claim.
 * It is also how we found that the endpoint's designated vision model was
 * returning HTTP 410 while gpt-4o quietly had vision all along.
 */
export function Admin() {
  const { authFetch } = useAuth();
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    authFetch("/api/v1/admin/system")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setInfo).catch((e) => setError(e.message));
  }, [authFetch]);

  if (error) return <div className="login-error">Could not load system info: {error}</div>;
  if (!info) return <Empty>Loading system state…</Empty>;

  const tick = (b: boolean) => (b ? <span style={{ color: "var(--state-nominal)" }}>✓ yes</span>
                                  : <span className="faint">— no</span>);

  return (
    <>
      <h1 className="page-title">Administration</h1>
      <p className="page-sub">
        Platform configuration and health. Note this role cannot rule on quality —
        separation of duties is asserted at boot, so no single account can weaken
        a threshold and then pass the part it now permits.
      </p>

      <Panel title="Model capability matrix — measured at boot, not assumed">
        <table>
          <thead>
            <tr>
              <th>Provider</th><th>Model</th><th>Reachable</th>
              <th>Vision</th><th>JSON</th><th>Stream</th><th>p50</th>
            </tr>
          </thead>
          <tbody>
            {info.capabilities.map((c, i) => (
              <tr key={i} className={c.reachable ? "" : "flagged"}>
                <td>{c.provider}</td>
                <td style={{ fontSize: 11 }}>{c.model}</td>
                <td>{tick(c.reachable)}</td>
                <td>{tick(c.supports_vision)}</td>
                <td>{tick(c.supports_json_mode)}</td>
                <td>{tick(c.supports_streaming)}</td>
                <td>{c.measured_p50_latency_ms}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
        {info.capabilities.filter((c) => c.error).map((c, i) => (
          <div key={i} className="faint" style={{ marginTop: 6 }}>
            {c.provider}: {c.error?.slice(0, 160)}
          </div>
        ))}
        <Provenance items={[
          ["source", "MEASURED · boot probe"],
          ["vision test", "discriminating — model must name two solid colours"],
        ]} />
      </Panel>

      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <Panel title="Tier resolution">
          {Object.entries(info.tiers).map(([tier, t]) => (
            <div key={tier} style={{ marginBottom: 12 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                <strong style={{ minWidth: 84 }}>{tier}</strong>
                <span className={`chip ${t.available ? "live" : "down"}`}>
                  {t.available ? "available" : "fallback"}
                </span>
              </div>
              <div className="mono faint" style={{ marginTop: 4, lineHeight: 1.6 }}>
                {t.chain.join("  →  ") || "(no provider)"}
                <br />↳ {t.terminal_fallback}
              </div>
            </div>
          ))}
          {info.skipped_providers.length > 0 && (
            <>
              <div className="faint" style={{ marginTop: 10 }}>Skipped:</div>
              {info.skipped_providers.map((s, i) => (
                <div key={i} className="faint mono">· {s.provider} — {s.reason}</div>
              ))}
            </>
          )}
          <Provenance items={[["config", "config/models.yaml"], ["secrets", "env only"]]} />
        </Panel>

        <Panel title="Roles &amp; permissions">
          {Object.entries(info.roles).map(([role, r]) => (
            <div key={role} style={{ marginBottom: 14 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                <strong>{r.label}</strong>
                <span className="mono faint">{role}</span>
                <span className="spacer" />
                <span className="chip">{r.permissions.length} perms</span>
              </div>
              <div className="faint mono" style={{ marginTop: 4, lineHeight: 1.5 }}>
                home {r.default_page}
              </div>
            </div>
          ))}
          <Provenance items={[
            ["source", "config/rbac.yaml"],
            ["default", "deny"],
            ["separation", "asserted at boot"],
          ]} />
        </Panel>
      </div>

      <div style={{ marginTop: 16 }}>
        <Panel title="System health">
          <div className="grid cols-4">
            {Object.entries(info.health).map(([k, v]) => (
              <Kpi key={k} value={String(v)} label={k.replace(/_/g, " ")} />
            ))}
          </div>
          <Provenance items={[["source", "MEASURED"], ["data", "SYNTHETIC"]]} />
        </Panel>
      </div>
    </>
  );
}
