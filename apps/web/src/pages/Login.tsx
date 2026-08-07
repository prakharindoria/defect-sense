import { useEffect, useState } from "react";
import { useAuth } from "../auth";

interface DemoAccount {
  username: string; display_name: string; role: string; label: string; description: string;
}

export function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("priya");
  const [password, setPassword] = useState("forge2026");
  const [accounts, setAccounts] = useState<DemoAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/v1/auth/demo-accounts")
      .then((r) => r.json())
      .then((d) => setAccounts(d.accounts))
      .catch(() => setError("Cannot reach the API. Is it running on :8000?"));
  }, []);

  const submit = async (e: React.FormEvent) => {
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

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="brand" style={{ fontSize: 30 }}>FORGE</div>
        <div className="tagline" style={{ marginBottom: 22 }}>
          Factory Operations Reasoning &amp; Governance Engine
        </div>

        <form onSubmit={submit}>
          <label className="field" htmlFor="u">Username</label>
          <input id="u" value={username} autoComplete="username"
                 onChange={(e) => setUsername(e.target.value)} />
          <label className="field" htmlFor="p">Password</label>
          <input id="p" type="password" value={password} autoComplete="current-password"
                 onChange={(e) => setPassword(e.target.value)} />
          <button className="primary" type="submit" disabled={busy}
                  style={{ width: "100%", marginTop: 16 }}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        {error && <div className="login-error" role="alert">{error}</div>}

        {accounts.length > 0 && (
          <>
            <div className="faint" style={{ margin: "22px 0 8px" }}>
              Demo accounts — each role sees a genuinely different product.
              Password <span className="mono">forge2026</span>.
            </div>
            {accounts.map((a) => (
              <button key={a.username} type="button" className="scenario-btn"
                      style={{ width: "100%", marginBottom: 6 }}
                      onClick={() => { setUsername(a.username); setPassword("forge2026"); }}>
                <span className="name">{a.display_name} · {a.label}</span>
                <span className="note">{a.description}</span>
              </button>
            ))}
          </>
        )}

        <div className="faint" style={{ marginTop: 20, lineHeight: 1.6 }}>
          All data in this system is synthetic. Credentials above are demo
          accounts against generated data, not secrets.
        </div>
      </div>
    </div>
  );
}
