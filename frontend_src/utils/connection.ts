/**
 * Whether Home Assistant is answering. A restart makes every request fail at
 * the network layer, which is neither a Phoenix error nor a model provider
 * error: the panel says so once, in its own words, instead of each window
 * showing a raw fetch failure. The API layer reports every
 * request here, so all three bundles share one answer.
 */

import { useEffect, useRef, useState } from "react";

/**
 * The panel, the injector and Agent Chat are separate bundles with separate
 * module state, and an idle window makes no requests of its own, so the answer
 * is shared through the page: one object every bundle reads, and an event that
 * tells the others when it changes.
 */
const SHARED = "__phxConnection";
const EVENT = "phx-connection";
type Shared = { reachable: boolean };
const scope = globalThis as unknown as Record<string, Shared | undefined>;
const shared: Shared = scope[SHARED] ?? (scope[SHARED] = { reachable: true });
const listeners = new Set<(up: boolean) => void>();
/** What this bundle's listeners were last told, so an echo of its own event is not repeated. */
let told = shared.reachable;

/** A reply being streamed stopped because the connection went, not because anything refused it. */
export class ConnectionLostError extends Error {}

/** Whether the last request reached Home Assistant. */
export function homeAssistantReachable(): boolean {
  return shared.reachable;
}

function tell(up: boolean): void {
  if (up === told) return;
  told = up;
  for (const listener of [...listeners]) listener(up);
}

if (typeof window !== "undefined") {
  window.addEventListener(EVENT, (event) => tell(!!(event as CustomEvent<{ up?: boolean }>).detail?.up));
}

/** Called with false the first time a request cannot reach Home Assistant, and true when one arrives again. */
export function onConnectionChange(listener: (up: boolean) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/**
 * How long a failed request waits before it counts as Home Assistant being away. Reloading or
 * leaving the page cancels the requests still running, and each one fails like a lost
 * connection; the page is gone before this passes, so that never shows the notice.
 */
const AWAY_GRACE_MS = 750;
let awayGraceMs = AWAY_GRACE_MS;
let pendingAway: ReturnType<typeof setTimeout> | undefined;
/** Set once the page starts to unload; requests failing after that are the page leaving. */
let leaving = false;

if (typeof window !== "undefined") {
  const leave = () => {
    leaving = true;
    globalThis.clearTimeout(pendingAway);
    pendingAway = undefined;
  };
  window.addEventListener("beforeunload", () => {
    leave();
    // Another handler can cancel the unload and keep the page; a real outage then counts again.
    globalThis.setTimeout(() => { leaving = false; }, 3000);
  });
  window.addEventListener("pagehide", leave);
  window.addEventListener("pageshow", () => { leaving = false; });
}

function settle(up: boolean): void {
  if (up === shared.reachable) return;
  shared.reachable = up;
  tell(up);
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(EVENT, { detail: { up } }));
}

/**
 * Reported by the API layer: a rejected fetch is the server being away, any reply means it is
 * back. A failure counts only if no reply arrives within a short grace and the page is not
 * unloading.
 */
export function noteConnection(up: boolean): void {
  if (up) {
    globalThis.clearTimeout(pendingAway);
    pendingAway = undefined;
    settle(true);
    return;
  }
  if (leaving || !shared.reachable || pendingAway !== undefined) return;
  if (awayGraceMs <= 0) { settle(false); return; }
  pendingAway = globalThis.setTimeout(() => {
    pendingAway = undefined;
    if (!leaving) settle(false);
  }, awayGraceMs);
}

/**
 * Whether Home Assistant is away, for a view that must stop accepting work
 * while it is. Each caller passes its own cheap request: a reply of any kind,
 * including an error status, means Home Assistant is answering again. Agent
 * Chat floats over every Home Assistant page and pops out into a window of its
 * own, where the panel's notice cannot reach it, so it asks for itself.
 */
export function useHomeAssistantAway(probe: () => Promise<unknown>, retryMs = 3000): boolean {
  const [away, setAway] = useState(() => !shared.reachable);
  const ask = useRef(probe);
  ask.current = probe;
  useEffect(() => onConnectionChange((up) => setAway(!up)), []);
  useEffect(() => {
    if (!away) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const again = async () => {
      try { await ask.current(); } catch { /* the watcher reads every request */ }
      if (!stop) timer = globalThis.setTimeout(() => { void again(); }, retryMs);
    };
    timer = globalThis.setTimeout(() => { void again(); }, retryMs);
    return () => { stop = true; globalThis.clearTimeout(timer); };
  }, [away, retryMs]);
  return away;
}

/** Test helper: forget the connection state and its listeners. */
export function __resetConnectionState(): void {
  globalThis.clearTimeout(pendingAway);
  pendingAway = undefined;
  leaving = false;
  shared.reachable = true;
  told = true;
  listeners.clear();
}

/** Test helper: how long a failure waits before it counts; 0 counts it at once. */
export function __setAwayGrace(ms: number = AWAY_GRACE_MS): void {
  awayGraceMs = ms;
}
