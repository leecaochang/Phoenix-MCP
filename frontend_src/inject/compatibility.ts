/** Session-local compatibility state shared by independently loaded bundles. */
import { compareBuilds } from "./ownership";

const STATE_KEY = "__phxMesaInjectCompatibility";
const CHANGE_EVENT = "phx-mesa-inject-compatibility-changed";
type SessionState = { build: string; blocked: boolean };
const host = window as unknown as Record<string, unknown>;

export function injectSessionBlocked(): boolean {
  return (host[STATE_KEY] as SessionState | undefined)?.blocked === true;
}

export function subscribeInjectCompatibility(listener: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, listener);
  return () => window.removeEventListener(CHANGE_EVENT, listener);
}

export function startInjectSession(build: string): void {
  const current = host[STATE_KEY] as SessionState | undefined;
  if (current && compareBuilds(current.build, build) >= 0) return;
  host[STATE_KEY] = { build, blocked: false } satisfies SessionState;
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function blockInjectSession(build: string): void {
  const current = host[STATE_KEY] as SessionState | undefined;
  if (!current || current.build !== build || current.blocked) return;
  current.blocked = true;
  window.dispatchEvent(new Event(CHANGE_EVENT));
}
