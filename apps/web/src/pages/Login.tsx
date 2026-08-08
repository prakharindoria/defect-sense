import { useEffect, useState } from "react";
import { useAuth } from "../auth";
import { PRODUCT_NAME, PRODUCT_DESCRIPTION } from "../brand";
import { Avatar, initials } from "../components/Shared";

interface DemoAccount {
  username: string; display_name: string; role: string; label: string; description: string;
}

export function Login() {
  const { login, register } = useAuth();
  const [tab, setTab] = useState<"login" | "register">("login");

  // Sign in state
  const [username, setUsername] = useState("priya@ds.com");
  const [password, setPassword] = useState("forge2026");

  // Register state
  const [regUsername, setRegUsername] = useState("");
  const [regDisplayName, setRegDisplayName] = useState("");
  const [regRole, setRegRole] = useState("shop_floor_worker");
  const [regPassword, setRegPassword] = useState("forge2026");

  const [accounts, setAccounts] = useState<DemoAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/v1/auth/demo-accounts")
      .then((r) => r.json())
      .then((d) => setAccounts(d.accounts))
      .catch(() => setError("Cannot reach the API. Is it running on :8000?"));
  }, []);

  const handleLoginSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  };

  const handleRegisterSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!regUsername.trim() || !regDisplayName.trim()) {
      setError("Username and Full Name are required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await register(regUsername.trim(), regDisplayName.trim(), regRole, regPassword);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="login-grid">
        <div className="login-card">
          <div className="login-brand">
            <Avatar initials="D" dark />
            <div>
              <div className="brand" style={{ fontSize: 21 }}>{PRODUCT_NAME}</div>
              <div className="tagline">{PRODUCT_DESCRIPTION}</div>
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, marginBottom: 18, marginTop: 14 }}>
            <button
              type="button"
              className={`navlink ${tab === "login" ? "active" : ""}`}
              onClick={() => { setTab("login"); setError(null); }}
              style={{ flex: 1, padding: "8px 12px", textAlign: "center" }}
            >
              Sign In
            </button>
            <button
              type="button"
              className={`navlink ${tab === "register" ? "active" : ""}`}
              onClick={() => { setTab("register"); setError(null); }}
              style={{ flex: 1, padding: "8px 12px", textAlign: "center" }}
            >
              Register New User
            </button>
          </div>

          {tab === "login" ? (
            <>
              <div className="login-heading">Welcome back.</div>
              <div className="login-heading faint">Sign in to the line.</div>

              <form onSubmit={handleLoginSubmit}>
                <label className="field" htmlFor="u">DS account ID</label>
                <div className="login-field">
                  <span className="dot" />
                  <input id="u" className="mono" value={username} autoComplete="username"
                         placeholder="name@ds.com"
                         onChange={(e) => setUsername(e.target.value)} />
                </div>
                <label className="field" htmlFor="p">Password</label>
                <div className="login-field">
                  <span className="dot" style={{ background: "var(--dot-faint)" }} />
                  <input id="p" type="password" className="mono" value={password}
                         autoComplete="current-password"
                         onChange={(e) => setPassword(e.target.value)} />
                </div>
                <button className="molten" type="submit" disabled={busy}
                        style={{ width: "100%", marginTop: 12, display: "flex", alignItems: "center",
                                 justifyContent: "center", gap: 12, padding: "16px 24px", fontSize: 15 }}>
                  {busy ? "Signing in…" : <>Sign in<span style={{ fontSize: 17 }}>→</span></>}
                </button>
              </form>
            </>
          ) : (
            <>
              <div className="login-heading">New Account.</div>
              <div className="login-heading faint">Register as Shop Floor Worker or QA Analyst.</div>

              <form onSubmit={handleRegisterSubmit}>
                <label className="field" htmlFor="ru">DS account ID</label>
                <div className="login-field">
                  <span className="dot" />
                  <input id="ru" className="mono" placeholder="e.g. vikram@ds.com" value={regUsername}
                         onChange={(e) => setRegUsername(e.target.value)} />
                </div>
                <label className="field" htmlFor="rn">Full Name</label>
                <div className="login-field">
                  <span className="dot" style={{ background: "var(--dot-faint)" }} />
                  <input id="rn" placeholder="e.g. Vikram Gupta" value={regDisplayName}
                         onChange={(e) => setRegDisplayName(e.target.value)} />
                </div>
                <label className="field" htmlFor="rr">Role (Restricted)</label>
                <select id="rr" value={regRole} onChange={(e) => setRegRole(e.target.value)}
                        style={{ marginBottom: 12 }}>
                  <option value="shop_floor_worker">Shop Floor Worker</option>
                  <option value="qa">QA Analyst</option>
                </select>
                <label className="field" htmlFor="rp">Password</label>
                <div className="login-field">
                  <span className="dot" style={{ background: "var(--dot-faint)" }} />
                  <input id="rp" type="password" className="mono" value={regPassword}
                         onChange={(e) => setRegPassword(e.target.value)} />
                </div>
                <button className="molten" type="submit" disabled={busy}
                        style={{ width: "100%", marginTop: 12, display: "flex", alignItems: "center",
                                 justifyContent: "center", gap: 12, padding: "16px 24px", fontSize: 15 }}>
                  {busy ? "Registering…" : <>Register & Sign In<span style={{ fontSize: 17 }}>→</span></>}
                </button>
              </form>
            </>
          )}

          {error && <div className="login-error" role="alert" style={{ marginTop: 12 }}>{error}</div>}

          <div className="faint" style={{ marginTop: 20, lineHeight: 1.7 }}>
            Enterprise role-based authentication and governance.
            {" "}<a href="http://localhost:8000/docs#/" target="_blank" rel="noreferrer">API documentation</a>
          </div>
        </div>

        <div className="login-side">
          <div style={{ fontSize: 11, letterSpacing: "0.16em", color: "var(--text-faint-2)", marginBottom: 6 }}>
            ENTERPRISE ROLES
          </div>
          <div style={{ fontSize: 18, fontWeight: 500, marginBottom: 4 }}>
            Role-scoped enterprise security profiles.
          </div>
          <div className="faint" style={{ lineHeight: 1.6, marginBottom: 22 }}>
            Route guards and granular permissions are enforced server-side via RBAC.
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {accounts.map((a) => (
              <button key={a.username} type="button" className="scenario-btn-account"
                      onClick={() => { setTab("login"); setUsername(a.username); setPassword("forge2026"); }}>
                <Avatar initials={initials(a.display_name)} />
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                    <span className="name">{a.display_name}</span>
                    <span className="mono faint">{a.label}</span>
                  </div>
                  <div className="note">{a.description}</div>
                </div>
              </button>
            ))}
          </div>

          <div style={{ flex: 1 }} />
          <div style={{ display: "flex", gap: 8, marginTop: 24, flexWrap: "wrap" }}>
            <span className="pill tone-outline" style={{ color: "var(--accent-lilac)", borderColor: "var(--accent-lilac-border)" }}>
              ENTERPRISE READY
            </span>
            <span className="pill tone-nominal">⌁ API LIVE</span>
            <span className="pill tone-outline">pack: wheel_assembly</span>
          </div>
        </div>
      </div>
    </div>
  );
}
