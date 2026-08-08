import { useState, useEffect, useRef } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import type { KeyboardEvent, ReactNode } from "react";
import { useAuth } from "./auth";
import { Login } from "./pages/Login";
import { Station } from "./pages/Station";
import { Dashboard } from "./pages/Dashboard";
import { Inspect } from "./pages/Inspect";
import { Workbench } from "./pages/Workbench";
import { AgentWorkflow } from "./pages/AgentWorkflow";
import { Admin } from "./pages/Admin";
import { useWsConnected, useLiveFeed } from "./useLiveFeed";
import { PRODUCT_NAME, PRODUCT_LINE } from "./brand";
import { Assistant } from "./components/Assistant";
import { Avatar, initials, Panel } from "./components/Shared";
import { PageHeaderProvider, usePageHeaderValue } from "./pageHeader";
import type { Inspection } from "./types";

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
  if (!ready) return <div className="app"><div className="faint" style={{ padding: 24 }}>Loading…</div></div>;
  if (!user) return <Navigate to="/login" replace />;
  if (permission && !can(permission)) {
    return (
      <Panel title="Not available for your role">
        <p className="muted">
          Your role ({user.label}) does not include <span className="mono">{permission}</span>.
          This is enforced at the API as well as here.
        </p>
      </Panel>
    );
  }
  return <>{children}</>;
}

function UnitSearch() {
  const { can, authFetch } = useAuth();
  const navigate = useNavigate();
  const feed = useLiveFeed(authFetch);
  const [value, setValue] = useState("");
  const [results, setResults] = useState<Inspection[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [selectedIdx, setSelectedIdx] = useState(-1);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!value.trim()) {
      setResults([]);
      setShowDropdown(false);
      setSelectedIdx(-1);
      return;
    }

    const query = value.toLowerCase().trim();
    const filtered = feed.filter((f) =>
      f.unit_id.toLowerCase().includes(query) ||
      f.correlation_id.toLowerCase().includes(query)
    ).slice(0, 8);

    setResults(filtered);
    setShowDropdown(filtered.length > 0);
    setSelectedIdx(-1);
  }, [value, feed]);

  const handleSelect = (inspection: Inspection) => {
    if (!can("inspection:read")) return;
    navigate(`/workbench/${inspection.correlation_id}`);
    setValue("");
    setShowDropdown(false);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!showDropdown) {
      if (e.key === "Enter" && value.trim() && can("inspection:read")) {
        navigate(`/workbench/${value.trim()}`);
        setValue("");
      }
      return;
    }

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIdx((prev) => (prev < results.length - 1 ? prev + 1 : prev));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIdx((prev) => (prev > 0 ? prev - 1 : -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (selectedIdx >= 0 && selectedIdx < results.length) {
        handleSelect(results[selectedIdx]);
      } else if (value.trim() && can("inspection:read")) {
        navigate(`/workbench/${value.trim()}`);
        setValue("");
      }
    } else if (e.key === "Escape") {
      setShowDropdown(false);
      setSelectedIdx(-1);
    }
  };

  return (
    <div className="searchbar" ref={dropdownRef} style={{ position: "relative" }}>
      <div className="icon-btn" aria-hidden="true">⌕</div>
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        onFocus={() => value.trim() && setShowDropdown(results.length > 0)}
        placeholder="Search unit, correlation id …"
        aria-label="Search by unit or correlation id"
      />
      {showDropdown && results.length > 0 && (
        <div style={{
          position: "absolute",
          top: "100%",
          left: 0,
          right: 0,
          background: "var(--surface-white)",
          border: "1px solid var(--border)",
          borderRadius: "var(--r-sm)",
          marginTop: 4,
          maxHeight: "240px",
          overflowY: "auto",
          zIndex: 1000,
          boxShadow: "0 4px 12px rgba(0,0,0,0.1)",
        }}>
          {results.map((r, i) => (
            <div
              key={r.correlation_id}
              onClick={() => handleSelect(r)}
              style={{
                padding: "8px 12px",
                borderBottom: i < results.length - 1 ? "1px solid var(--border-faint)" : "none",
                cursor: "pointer",
                background: selectedIdx === i ? "var(--surface-soft)" : "transparent",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
              onMouseEnter={() => setSelectedIdx(i)}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 600 }}>{r.unit_id}</div>
                <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--mono)" }}>
                  {r.correlation_id}
                </div>
              </div>
              <div style={{ fontSize: 10, color: `var(--accent-${r.verdict === "pass" ? "nominal" : r.verdict === "defect" ? "molten" : "watch"})`, fontWeight: 600, marginLeft: 8 }}>
                {r.verdict.toUpperCase()}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Nav() {
  const { user, can, logout } = useAuth();
  const connected = useWsConnected();
  const navigate = useNavigate();
  const { title, sub } = usePageHeaderValue();
  if (!user) return null;

  const links: [string, string, string | undefined][] = [
    ["/station", "Station", undefined],
    ["/dashboard", "Dashboard", "inspection:read"],
    ["/inspect", "Inspect", "inspection:create"],
    ["/agents", "Agent Workflow", "agentrun:read"],
    ["/admin", "Admin", "admin:model_config"],
  ];

  return (
    <>
      <header className="top">

        <Avatar initials="D" dark />
        <div>
          <div className="brand">{PRODUCT_NAME}</div>
          <div className="tagline">{PRODUCT_LINE}</div>
        </div>

        <div className="spacer" />
        <div style={{ display: "flex", gap: 7, alignItems: "center" }}>
          <span className="chip synthetic" title="Demo environment with synthetic test data — not connected to production">📋 Demo Data</span>
          <span className={`chip ${connected ? "live" : "down"}`} title={connected ? "Connected to WebSocket feed" : "Disconnected from server"}>
            {connected ? "⌁ LIVE" : "⌁ OFFLINE"}
          </span>
        </div>
        <div className="who">
          <Avatar initials={initials(user.display_name)} />
          <div>
            <div className="who-name">{user.display_name}</div>
            <div className="who-role">{user.label}</div>
          </div>
        </div>
        <UnitSearch />
        <button type="button" className="icon-btn" title="Sign out" aria-label="Sign out"
                onClick={() => logout().then(() => navigate("/login"))}>⏻</button>
      </header>

      <div className="header-row">
        <nav className="mainnav">
          {links.filter(([, , p]) => !p || can(p)).map(([to, label]) => (
            <NavLink key={to} to={to}
                     className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="spacer" />
        {title && (
          <div className="page-header">
            <div className="page-title">{title}</div>
            {sub && <div className="page-sub">{sub}</div>}
          </div>
        )}
      </div>
    </>
  );
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="app">
      <PageHeaderProvider>
        <Nav />
        <main className="content">
          {children}
          <footer className="bottom">
            <span>{PRODUCT_NAME} v1.0.0</span>
            <span>pack: wheel_assembly</span>
            <span>ALL DATA SYNTHETIC — not real plant data</span>
            <span>verdict path is deterministic; no model call</span>
            <a href="/docs">API docs</a>
          </footer>
        </main>
        <Assistant />
      </PageHeaderProvider>
    </div>
  );
}

function Home() {
  const { user, ready } = useAuth();
  if (!ready) return <div className="app"><div className="faint" style={{ padding: 24 }}>Loading…</div></div>;
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
