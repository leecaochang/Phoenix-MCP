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

/** Reported by the API layer: a rejected fetch is the server being away, any reply means it is back. */
export function noteConnection(up: boolean): void {
  if (up === shared.reachable) return;
  shared.reachable = up;
  tell(up);
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(EVENT, { detail: { up } }));
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
  shared.reachable = true;
  told = true;
  listeners.clear();
}
