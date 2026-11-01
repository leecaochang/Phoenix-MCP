/**
 * The Help window and what feeds it. The shell follows the pointer and the
 * keyboard focus; Help shows short help for the control under the pointer, or
 * else the focused one: its `data-help` catalog key or `data-help-text` when it
 * has one, otherwise its accessible name. A settings row marked
 * `data-help-controls` lends its help to the controls in it that have none of
 * their own. Pointing at the Help window itself keeps the current text, so it
 * can be read.
 */

import { createContext, useContext, useSyncExternalStore } from "react";
import { hasMessage, t } from "../i18n";
import { tPlain } from "../i18n/rich";

/** The Help window's id in every workspace. */
export const HELP_WINDOW = "help";

/** Controls whose own text names them. */
const TEXT_NAMED = 'button, a[href], summary, [role="button"], [role="link"], [role^="menuitem"], [role="tab"], [role="option"], [role="switch"], [role="checkbox"], [role="radio"]';
/** Everything a user can point at or focus to act on. */
const CONTROL = `${TEXT_NAMED}, input, select, textarea, [role="treeitem"], [tabindex]:not([tabindex="-1"])`;
/** Help never repeats a whole paragraph of a control's text. */
const MAX_NAME = 160;

function accessibleName(el: Element): string {
  const label = el.getAttribute("aria-label")?.trim();
  if (label) return label;
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const root = el.getRootNode() as Document | ShadowRoot;
    const text = labelledBy.split(/\s+/).map((id) => root.getElementById?.(id)?.textContent ?? "").join(" ").trim();
    if (text) return text;
  }
  if (el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement) {
    const text = el.labels?.[0]?.textContent?.trim();
    if (text) return text;
    if (!(el instanceof HTMLSelectElement) && el.placeholder) return el.placeholder;
  }
  const title = el.getAttribute("title")?.trim();
  if (title) return title;
  if (!el.matches(TEXT_NAMED)) return "";
  return (el.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, MAX_NAME);
}

/** An element's own help: undefined when it has none, null when its catalog key is unknown. */
function ownHelp(el: Element): string | null | undefined {
  const text = el.getAttribute("data-help-text")?.trim();
  if (text) return text;
  const key = el.getAttribute("data-help");
  if (key) return hasMessage(key) ? tPlain(key) : null;
  return undefined;
}

/**
 * Short help for whatever an event landed on: the nearest help or control,
 * whichever is closer. A control without help of its own inside a settings row
 * shows the row's help, which says more than the name beside it.
 */
export function helpFor(target: EventTarget | null): string | null {
  if (!(target instanceof Element)) return null;
  const el = target.closest(`[data-help], [data-help-text], ${CONTROL}`);
  if (!el) return null;
  const own = ownHelp(el);
  if (own !== undefined) return own;
  const row = el.parentElement?.closest("[data-help-controls]");
  const lent = row ? ownHelp(row) : undefined;
  if (lent) return lent;
  return accessibleName(el) || null;
}

/**
 * The help to show: for the control under the pointer, else for the focused one.
 * It keeps the elements rather than their text, so the text follows the panel
 * language when it changes; `refresh` repaints after such a change.
 */
export class HelpTracker {
  private hovered: Element | null = null;
  private focused: Element | null = null;
  private version = 0;
  private listeners = new Set<() => void>();

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getVersion = (): number => this.version;

  text(): string | null { return helpFor(this.hovered) ?? helpFor(this.focused); }

  /** Repaint Help, as after the panel language changes. */
  refresh(): void {
    this.version++;
    for (const listener of this.listeners) listener();
  }

  private set(which: "hovered" | "focused", target: EventTarget | null): void {
    const el = target instanceof Element ? target : null;
    if (this[which] === el) return;
    const before = this.text();
    this[which] = el;
    if (this.text() !== before) this.refresh();
  }

  /** Follow the pointer and focus inside `root`; returns a function that stops. */
  attach(root: HTMLElement): () => void {
    const inHelp = (target: EventTarget | null) => target instanceof Element && !!target.closest(`[data-phx-window="${HELP_WINDOW}"]`);
    const over = (e: PointerEvent) => { if (!inHelp(e.target)) this.set("hovered", e.target); };
    const leave = () => this.set("hovered", null);
    const focusIn = (e: FocusEvent) => this.set("focused", e.target);
    const focusOut = (e: FocusEvent) => { if (!(e.relatedTarget instanceof Node && root.contains(e.relatedTarget))) this.set("focused", null); };
    root.addEventListener("pointerover", over);
    root.addEventListener("pointerleave", leave);
    root.addEventListener("focusin", focusIn);
    root.addEventListener("focusout", focusOut);
    return () => {
      root.removeEventListener("pointerover", over);
      root.removeEventListener("pointerleave", leave);
      root.removeEventListener("focusin", focusIn);
      root.removeEventListener("focusout", focusOut);
    };
  }
}

export const HelpContext = createContext<HelpTracker | null>(null);

export function HelpWindow() {
  const tracker = useContext(HelpContext);
  useSyncExternalStore(tracker?.subscribe ?? (() => () => {}), tracker?.getVersion ?? (() => 0));
  const text = tracker?.text();
  // With nothing to explain, Help also names the keys that move between areas.
  return <p className={`phx-help-text${text ? "" : " is-idle"}`}>{text ?? `${t("help.idle")} ${t("help.areaKeys")}`}</p>;
}
