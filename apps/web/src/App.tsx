import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./auth";
import { Login } from "./pages/Login";
import { Station } from "./pages/Station";
import { Dashboard } from "./pages/Dashboard";
import { Inspect } from "./pages/Inspect";
import { Workbench } from "./pages/Workbench";
import { AgentWorkflow } from "./pages/AgentWorkflow";
import { Admin } from "./pages/Admin";
import { useWsConnected } from "./useLiveFeed";

/**
 * Route guard.
 *
 * This is convenience, not security — it stops a user navigating somewhere
 * useless. The real control is server-side: every endpoint carries its own
 * permission dependency, so hiding a link and blocking a call are separate
 * mechanisms and only the second one matters.
 */
function Guard({ permission, children }: { permission?: string; children: ReactNode }) {
  const { user, ready, can } = useAuth();
  if (!ready) return <div className="app"><div className="faint">Loading…</div></div>;
  if (!user) return <Navigate to="/login" replace />;
  if (permission && !can(permission)) {
    return (
      <div className="panel">
        <h2>Not available for your role</h2>
        <p className="muted">
          Your role ({user.label}) does not include <span className="mono">{permission}</span>.
          This is enforced at the API as well as here.
        </p>
      </div>
    );
  }
  return <>{children}</>;
}

function Nav() {
  const { user, can, logout } = useAuth();
  const connected = useWsConnected();
  const navigate = useNavigate();
  if (!user) return null;

  const links: [string, string, string | undefined][] = [
    ["/station", "Station", undefined],
    ["/dashboard", "Dashboard", "inspection:read"],
    ["/inspect", "Inspect", "inspection:create"],
    ["/agents", "Agent Workflow", "agentrun:read"],
    ["/admin", "Admin", "admin:model_config"],
  ];

  return (
    <header className="top">
      <div>
        <div className="brand">FORGE</div>
        <div className="tagline">Wheel Assembly Quality Control</div>
      </div>

      <nav className="mainnav">
        {links.filter(([, , p]) => !p || can(p)).map(([to, label]) => (
          <NavLink key={to} to={to}
                   className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="spacer" />
      <span className="chip synthetic">SYNTHETIC DATA</span>
      <span className={`chip ${connected ? "live" : "down"}`}>
        {connected ? "⌁ LIVE" : "⌁ OFFLINE"}
      </span>
      <div className="who">
        <div className="who-name">{user.display_name}</div>
        <div className="who-role mono">{user.label}</div>
      </div>
      <button onClick={() => logout().then(() => navigate("/login"))}>Sign out</button>
    </header>
  );
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="app">
      <Nav />
      {children}
      <footer className="bottom">
        <span>FORGE v1.0.0</span>
        <span>pack: wheel_assembly</span>
        <span>ALL DATA SYNTHETIC — not real plant data</span>
        <span>verdict path is deterministic; no model call</span>
        <a href="/docs" style={{ color: "var(--accent-signal)" }}>API docs</a>
      </footer>
    </div>
  );
}

function Home() {
  const { user, ready } = useAuth();
  if (!ready) return <div className="app"><div className="faint">Loading…</div></div>;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={user.default_page} replace />;
}

export default function App() {
  const { user, ready } = useAuth();

  if (ready && !user) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={<Home />} />
      <Route path="/" element={<Home />} />
      <Route path="/station" element={<Shell><Guard><Station /></Guard></Shell>} />
      <Route path="/dashboard" element={
        <Shell><Guard permission="inspection:read"><Dashboard /></Guard></Shell>} />
      <Route path="/inspect" element={
        <Shell><Guard permission="inspection:create"><Inspect /></Guard></Shell>} />
      <Route path="/workbench/:id" element={
        <Shell><Guard permission="inspection:read"><Workbench /></Guard></Shell>} />
      <Route path="/agents" element={
        <Shell><Guard permission="agentrun:read"><AgentWorkflow /></Guard></Shell>} />
      <Route path="/agents/:id" element={
        <Shell><Guard permission="agentrun:read"><AgentWorkflow /></Guard></Shell>} />
      <Route path="/admin" element={
        <Shell><Guard permission="admin:model_config"><Admin /></Guard></Shell>} />
      <Route path="*" element={<Home />} />
    </Routes>
  );
}
