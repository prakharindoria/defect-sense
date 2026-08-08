import { useEffect, useState } from "react";
import { getAccessToken } from "./auth";
import type { Inspection, NarrativeProvenance } from "./types";

let socket: WebSocket | null = null;
const listeners = new Set<(i: Inspection) => void>();
const buffer: Inspection[] = [];
let connectedListeners = new Set<(c: boolean) => void>();
type NarrativeFn = (id: string, text: string, prov: NarrativeProvenance) => void;
const narrativeListeners = new Set<NarrativeFn>();
let isConnected = false;

/**
 * One shared WebSocket for the whole app.
 *
 * Every page wants the live feed, but a socket per mounted component means a
 * page with three panels opens three connections and the server fans the same
 * message out three times. A module-level singleton with a subscriber set keeps
 * it to one, and the buffer means a page mounted after an event still shows it.
 */
function ensureSocket() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const token = getAccessToken();
  const query = token ? `?token=${encodeURIComponent(token)}` : "";

  socket = new WebSocket(`${proto}//${location.host}/ws/live${query}`);

  socket.onopen = () => {
    isConnected = true;
    connectedListeners.forEach((fn) => fn(true));
    if (token && socket && socket.readyState === WebSocket.OPEN) {
      try {
        socket.send(JSON.stringify({ type: "auth", token }));
      } catch {
        // ignore send error on open
      }
    }
  };
  const drop = () => {
    isConnected = false;
    connectedListeners.forEach((fn) => fn(false));
    // Reconnect after a pause. Without this the feed silently dies on any
    // backend restart and the UI shows a stale board with no indication.
    setTimeout(ensureSocket, 2000);
  };
  socket.onclose = drop;
  socket.onerror = drop;
  socket.onmessage = (e) => {
    const msg = JSON.parse(e.data);

    if (msg.type === "inspection") {
      const item = msg.data as Inspection;
      buffer.unshift(item);
      if (buffer.length > 50) buffer.pop();
      listeners.forEach((fn) => fn(item));
      return;
    }

    // The narrative arrives separately, after the verdict has already been
    // shown. We patch it onto the existing record rather than replacing it, so
    // a slow model can never roll back a verdict the operator already acted on.
    if (msg.type === "narrative") {
      const { correlation_id, narrative, provenance } = msg.data;
      const idx = buffer.findIndex((b) => b.correlation_id === correlation_id);
      if (idx >= 0) {
        buffer[idx] = { ...buffer[idx], narrative, narrative_provenance: provenance };
        listeners.forEach((fn) => fn(buffer[idx]));
      }
      narrativeListeners.forEach((fn) => fn(correlation_id, narrative, provenance));
    }
  };
}

let hydrated = false;

/**
 * Seed the feed from the server on first mount.
 *
 * The WebSocket only carries events that happen while you are connected, so
 * without this a page reload shows an empty board even though the backend has
 * the history — metrics would read ₹34,838 next to "waiting for units".
 * Runs once per page load; live events take over from there.
 */
async function hydrate(fetcher: (u: string) => Promise<Response>) {
  if (hydrated) return;
  hydrated = true;
  try {
    const res = await fetcher("/api/v1/inspections?limit=25");
    if (!res.ok) return;
    const { items } = await res.json();
    for (const item of (items as Inspection[]).reverse()) {
      if (!buffer.some((b) => b.correlation_id === item.correlation_id)) {
        buffer.unshift(item);
      }
    }
    listeners.forEach((fn) => buffer[0] && fn(buffer[0]));
  } catch {
    hydrated = false; // let a later mount retry
  }
}

export function useLiveFeed(
  fetcher?: (u: string, init?: RequestInit) => Promise<Response>,
): Inspection[] {
  const [feed, setFeed] = useState<Inspection[]>([...buffer]);
  useEffect(() => {
    ensureSocket();
    const fn = (i: Inspection) => {
      setFeed((f) => [i, ...f.filter((x) => x.correlation_id !== i.correlation_id)].slice(0, 50));
    };
    listeners.add(fn);
    if (fetcher) hydrate(fetcher).then(() => setFeed([...buffer]));
    return () => { listeners.delete(fn); };
  }, [fetcher]);
  return feed;
}

export function useWsConnected(): boolean {
  const [connected, setConnected] = useState(isConnected);
  useEffect(() => {
    ensureSocket();
    connectedListeners.add(setConnected);
    return () => { connectedListeners.delete(setConnected); };
  }, []);
  return connected;
}

/** Lets a page push its own result into the shared feed immediately. */
export function pushLocal(item: Inspection) {
  if (buffer.some((b) => b.correlation_id === item.correlation_id)) return;
  buffer.unshift(item);
  listeners.forEach((fn) => fn(item));
}

/** Subscribe to narratives arriving for any inspection. */
export function useNarrative(
  correlationId: string | undefined,
): { text: string | null; provenance: NarrativeProvenance | null } {
  const [text, setText] = useState<string | null>(null);
  const [provenance, setProvenance] = useState<NarrativeProvenance | null>(null);

  useEffect(() => {
    setText(null);
    setProvenance(null);
    if (!correlationId) return;
    const existing = buffer.find((b) => b.correlation_id === correlationId);
    if (existing?.narrative) {
      setText(existing.narrative);
      setProvenance(existing.narrative_provenance ?? null);
    }
    const fn: NarrativeFn = (id, t, p) => {
      if (id !== correlationId) return;
      setText(t);
      setProvenance(p);
    };
    narrativeListeners.add(fn);
    return () => { narrativeListeners.delete(fn); };
  }, [correlationId]);

  return { text, provenance };
}
