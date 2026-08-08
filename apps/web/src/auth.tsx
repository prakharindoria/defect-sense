import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import type { ReactNode } from "react";

export interface Identity {
  username: string;
  display_name: string;
  role: "SHOP_FLOOR_WORKER" | "QA" | "ADMIN";
  label: string;
  persona: string;
  default_page: string;
  explanation_depth: string;
  permissions: string[];
}

interface AuthState {
  user: Identity | null;
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, display_name: string, role: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
  authFetch: (input: string, init?: RequestInit) => Promise<Response>;
}

const Ctx = createContext<AuthState | null>(null);

let activeToken: string | null = null;

export function getAccessToken(): string | null {
  return activeToken;
}

export function setAccessToken(token: string | null) {
  activeToken = token;
}

/**
 * The access token is held in a ref — memory only, never localStorage, where
 * any injected script could read it. The refresh token is an httpOnly cookie
 * the JS side cannot touch at all; on boot we call /refresh to see whether a
 * session survives, which is what makes a page reload keep you logged in
 * without persisting anything readable.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const token = useRef<string | null>(null);
  const [user, setUser] = useState<Identity | null>(null);
  const [ready, setReady] = useState(false);

  const apply = (data: { access_token: string; user: Identity }) => {
    token.current = data.access_token;
    setAccessToken(data.access_token);
    setUser(data.user);
  };

  useEffect(() => {
    fetch("/api/v1/auth/refresh", { method: "POST", credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && apply(d))
      .catch(() => {})
      .finally(() => setReady(true));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? "Sign in failed");
    }
    const data = await res.json();
    apply(data);

    // Sync all actors with database after successful login
    try {
      await fetch("/api/v1/auth/sync", {
        method: "POST",
        credentials: "include",
        headers: {
          Authorization: `Bearer ${data.access_token}`,
        },
      }).catch(() => {
        // Non-fatal: sync failure should not block login
      });
    } catch {
      // Silently handle sync errors
    }
  }, []);

  const register = useCallback(async (username: string, display_name: string, role: string, password: string) => {
    const res = await fetch("/api/v1/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ username, display_name, role, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? "Registration failed");
    }
    apply(await res.json());
  }, []);

  const logout = useCallback(async () => {
    await fetch("/api/v1/auth/logout", { method: "POST", credentials: "include" })
      .catch(() => {});
    token.current = null;
    setUser(null);
  }, []);

  /** Fetch with the bearer token, retrying once through /refresh on a 401. */
  const authFetch = useCallback(async (input: string, init: RequestInit = {}) => {
    const call = () =>
      fetch(input, {
        ...init,
        credentials: "include",
        headers: {
          ...(init.headers ?? {}),
          ...(token.current ? { Authorization: `Bearer ${token.current}` } : {}),
        },
      });

    let res = await call();
    if (res.status === 401) {
      const refreshed = await fetch("/api/v1/auth/refresh", {
        method: "POST", credentials: "include",
      });
      if (refreshed.ok) {
        apply(await refreshed.json());
        res = await call();
      } else {
        token.current = null;
        setUser(null);
      }
    }
    return res;
  }, []);

  const can = useCallback(
    (permission: string) => user?.permissions.includes(permission) ?? false,
    [user],
  );

  const value = useMemo(
    () => ({ user, ready, login, register, logout, can, authFetch }),
    [user, ready, login, register, logout, can, authFetch],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
