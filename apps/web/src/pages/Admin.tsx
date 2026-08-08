import { useEffect, useState } from "react";
import { useAuth } from "../auth";
import { Empty, Kpi, Panel, Pill, Provenance } from "../components/Shared";
import { usePageTitle } from "../pageHeader";

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
  usePageTitle("Administration", "Platform, not quality.");
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [validating, setValidating] = useState(false);

  const loadSystemInfo = async () => {
    setValidating(true);
    try {
      const r = await authFetch("/api/v1/admin/system");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setInfo(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load system info");
    } finally {
      setValidating(false);
    }
  };

  useEffect(() => {
    loadSystemInfo();
  }, [authFetch]);

  if (error) return <div className="login-error">Could not load system info: {error}</div>;
  if (!info) return <Empty>Loading system state…</Empty>;

  const tick = (b: boolean) => (b ? <span style={{ color: "var(--state-nominal-text)" }}>✓ yes</span>
                                  : <span className="faint">— no</span>);

  return (
    <>
      <div className="faint" style={{ lineHeight: 1.6, marginBottom: 16 }}>
        Note this role cannot rule on quality — separation of duties is
        asserted at boot, so no single account can weaken a threshold and then
        pass the part it now permits.
      </div>

      <Panel title="Model capability matrix"
             right={<div style={{ display: "flex", alignItems: "center", gap: 12 }}>
               <button
                 onClick={() => loadSystemInfo()}
                 disabled={validating}
                 style={{
                   padding: "4px 12px",
                   fontSize: 11,
                   background: "var(--accent-molten-bg)",
                   color: "var(--accent-molten)",
                   border: "1px solid var(--accent-molten-border)",
                   borderRadius: "var(--r-sm)",
                   cursor: validating ? "not-allowed" : "pointer",
                   opacity: validating ? 0.6 : 1,
                 }}
               >
                 {validating ? "Validating..." : "Validate now"}
               </button>
               <span className="mono faint">{validating ? "checking" : "MEASURED"}</span>
             </div>}>
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
                <td style={{ fontSize: 11, color: "var(--text-muted-3)" }}>{c.model}</td>
                <td>{tick(c.reachable)}</td>
                <td>{tick(c.supports_vision)}</td>
                <td>{tick(c.supports_json_mode)}</td>
                <td>{tick(c.supports_streaming)}</td>
                <td style={{ textAlign: "right", color: "var(--text-muted-4)" }}>{c.measured_p50_latency_ms}ms</td>
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
        <Panel tone="soft" title="Tier resolution">
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {Object.entries(info.tiers).map(([tier, t]) => (
              <div key={tier} className="panel" style={{ padding: "16px 18px" }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <span style={{ fontSize: 13.5, fontWeight: 500, minWidth: 84 }}>{tier}</span>
                  <Pill tone={t.available ? "nominal" : "critical"}>
                    {t.available ? "available" : "fallback"}
                  </Pill>
                </div>
                <div className="mono faint" style={{ marginTop: 9, lineHeight: 1.7 }}>
                  {t.chain.join("  →  ") || "(no provider)"}
                  <br />↳ {t.terminal_fallback}
                </div>
              </div>
            ))}
          </div>
          {info.skipped_providers.length > 0 && (
            <>
              <div className="faint" style={{ marginTop: 12 }}>Skipped:</div>
              {info.skipped_providers.map((s, i) => (
                <div key={i} className="faint mono">· {s.provider} — {s.reason}</div>
              ))}
            </>
          )}
          <Provenance items={[["config", "config/models.yaml"], ["secrets", "env only"]]} />
        </Panel>

        <Panel tone="soft" title="Roles & permissions">
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {Object.entries(info.roles).map(([role, r]) => (
              <div key={role} className="panel" style={{ padding: "16px 18px" }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <span style={{ fontSize: 13.5, fontWeight: 500 }}>{r.label}</span>
                  <span className="mono faint">{role}</span>
                  <span className="spacer" />
                  <span className="chip">{r.permissions.length} perms</span>
                </div>
                <div className="faint mono" style={{ marginTop: 8 }}>
                  home {r.default_page}
                </div>
              </div>
            ))}
          </div>
          <Provenance items={[
            ["source", "config/rbac.yaml"],
            ["default", "deny"],
            ["separation", "asserted at boot"],
          ]} />
        </Panel>
      </div>

      <div style={{ marginTop: 16 }}>
        <UserRegistrationPanel authFetch={authFetch} />
      </div>

      <div style={{ marginTop: 16 }}>
        <Panel title="System health">
          <div className="grid cols-4">
            {Object.entries(info.health).map(([k, v]) => (
              <div key={k} className="solid-backing">
                <Kpi value={typeof v === "object" && v !== null ? ((v as any).detail || (v as any).backend || JSON.stringify(v)) : String(v)} label={k.replace(/_/g, " ")} />
              </div>
            ))}
          </div>
          <Provenance items={[["source", "MEASURED"], ["data", "SYNTHETIC"]]} />
        </Panel>
      </div>
    </>
  );
}

function UserRegistrationPanel({ authFetch }: { authFetch: (url: string, init?: RequestInit) => Promise<Response> }) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState("shop_floor_worker");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !displayName.trim()) {
      setMsg({ text: "Username and display name are required.", error: true });
      return;
    }
    setBusy(true); setMsg(null);
    try {
      const res = await authFetch("/api/v1/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: username.trim(),
          display_name: displayName.trim(),
          role,
          password: "forge2026",
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const newIdentity = await res.json();
      setMsg({ text: `User ${newIdentity.display_name} (${newIdentity.role}) registered successfully!` });
      setUsername(""); setDisplayName("");
    } catch (err) {
      setMsg({ text: err instanceof Error ? err.message : "Registration failed", error: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel tone="soft" title="Register New User">
      <form onSubmit={handleRegister} className="grid cols-4" style={{ alignItems: "flex-end" }}>
        <div>
          <label className="field" style={{ margin: "0 0 6px" }}>Username</label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="e.g. vikram"
            className="mono"
          />
        </div>
        <div>
          <label className="field" style={{ margin: "0 0 6px" }}>Full Name</label>
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="e.g. Vikram Gupta"
          />
        </div>
        <div>
          <label className="field" style={{ margin: "0 0 6px" }}>Role</label>
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="shop_floor_worker">Shop Floor Worker</option>
            <option value="qa">QA Analyst</option>
            <option value="admin">Administrator</option>
          </select>
        </div>
        <div>
          <button type="submit" className="molten" disabled={busy} style={{ width: "100%" }}>
            {busy ? "Registering…" : "Register User →"}
          </button>
        </div>
      </form>
      {msg && (
        <div
          className={`pill ${msg.error ? "tone-critical" : "tone-nominal"}`}
          style={{ marginTop: 14, display: "inline-block" }}
        >
          {msg.text}
        </div>
      )}
    </Panel>
  );
}

