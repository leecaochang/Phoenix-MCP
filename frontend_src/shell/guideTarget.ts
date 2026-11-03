/**
 * What the Guide shows follows what the reader works with (user, 2026-11-01):
 * selecting or focusing a control opens its window's page and the section
 * about that control. Hovering does not move the Guide, so it stays put while
 * the pointer crosses the screen.
 *
 * A control, or a part of a window around it, names its section with
 * `data-guide="<topic id>#<anchor>"` (or `data-guide="<topic id>"` for a
 * page). Windows whose controls are already indexed, such as Settings, map a
 * control to its section through a resolver instead. Without either, the
 * Guide shows the window's own page.
 */

import { HELP_WINDOW } from "./help";

export interface GuideTarget {
  /** The window the reader works in; null outside any window. */
  windowId: string | null;
  /** The page to show; null for the window's own page. */
  topic: string | null;
  /** The section to show and highlight; null for the top of the page. */
  anchor: string | null;
}

/** Find the section for a control that carries no `data-guide` of its own. */
export type GuideResolver = (el: Element, windowId: string) => string | null;

/** Read `<topic id>#<anchor>`, `<topic id>` or `#<anchor>`. */
export function parseGuideRef(ref: string): { topic: string | null; anchor: string | null } {
  const at = ref.indexOf("#");
  const topic = (at < 0 ? ref : ref.slice(0, at)).trim() || null;
  const anchor = at < 0 ? null : ref.slice(at + 1).trim() || null;
  return { topic, anchor };
}

/** The target for whatever an event landed on, or null when it is not in a window. */
export function guideTargetFor(el: Element, resolvers: readonly GuideResolver[] = []): GuideTarget | null {
  const body = el.closest("[data-phx-window]");
  const windowId = body?.getAttribute("data-phx-window") ?? null;
  if (!windowId || windowId === HELP_WINDOW) return null;
  const tagged = el.closest("[data-guide]");
  if (tagged && body!.contains(tagged)) {
    const { topic, anchor } = parseGuideRef(tagged.getAttribute("data-guide") ?? "");
    return { windowId, topic, anchor };
  }
  for (const resolve of resolvers) {
    const anchor = resolve(el, windowId);
    if (anchor) return { windowId, topic: null, anchor };
  }
  return { windowId, topic: null, anchor: null };
}

export class GuideTargetTracker {
  private target: GuideTarget | null = null;
  private version = 0;
  private listeners = new Set<() => void>();
  private resolvers: GuideResolver[] = [];

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getVersion = (): number => this.version;
  current(): GuideTarget | null { return this.target; }

  /** Add a way to map a control to its section; returns a function that removes it. */
  addResolver(resolve: GuideResolver): () => void {
    this.resolvers.push(resolve);
    return () => { this.resolvers = this.resolvers.filter((r) => r !== resolve); };
  }

  /** Follow selection and focus inside `root`; returns a function that stops. */
  attach(root: HTMLElement): () => void {
    const note = (e: Event) => {
      const el = e.target;
      if (!(el instanceof Element) || el.closest(".phx-guide, .phx-guide-phone")) return;
      const next = guideTargetFor(el, this.resolvers);
      // A click on a window's empty space keeps the section the reader had chosen there.
      if (!next || (next.anchor === null && next.topic === null && this.target?.windowId === next.windowId)) return;
      const same = this.target && this.target.windowId === next.windowId && this.target.topic === next.topic && this.target.anchor === next.anchor;
      // Selecting the same control again counts, so the Guide returns to it from another page (user, 2026-11-03);
      // focus coming back to it does not.
      if (same && e.type !== "pointerdown") return;
      if (!same) this.target = next;
      this.version++;
      for (const listener of this.listeners) listener();
    };
    root.addEventListener("pointerdown", note);
    root.addEventListener("focusin", note);
    return () => {
      root.removeEventListener("pointerdown", note);
      root.removeEventListener("focusin", note);
    };
  }
}
