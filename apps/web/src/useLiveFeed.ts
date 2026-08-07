import { useEffect, useState } from "react";
import type { Inspection } from "./types";

let socket: WebSocket | null = null;
const listeners = new Set<(i: Inspection) => void>();
const buffer: Inspection[] = [];
let connectedListeners = new Set<(c: boolean) => void>();
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
  socket = new WebSocket(`${proto}//${location.host}/ws/live`);

  socket.onopen = () => {
    isConnected = true;
    connectedListeners.forEach((fn) => fn(true));
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
    if (msg.type !== "inspection") return;
    const item = msg.data as Inspection;
    buffer.unshift(item);
    if (buffer.length > 50) buffer.pop();
    listeners.forEach((fn) => fn(item));
  };
}

export function useLiveFeed(): Inspection[] {
  const [feed, setFeed] = useState<Inspection[]>([...buffer]);
  useEffect(() => {
    ensureSocket();
    const fn = (i: Inspection) => setFeed((f) => [i, ...f].slice(0, 50));
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  }, []);
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
