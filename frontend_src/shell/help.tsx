/**
 * The Help window and what feeds it. The shell follows the pointer and the
 * keyboard focus; Help shows short help for the control under the pointer, or
 * else the focused one: its `data-help` catalog key when it has one, otherwise
 * its accessible name. Pointing at the Help window itself keeps the current
 * text, so it can be read.
 */

import { createContext, useContext, useSyncExternalStore } from "react";
import { hasMessage, t } from "../i18n";

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

/** Short help for whatever an event landed on: the nearest `data-help` or control, whichever is closer. */
export function helpFor(target: EventTarget | null): string | null {
  if (!(target instanceof Element)) return null;
  const el = target.closest(`[data-help], ${CONTROL}`);
  if (!el) return null;
  const key = el.getAttribute("data-help");
  if (key) return hasMessage(key) ? t(key) : null;
  return accessibleName(el) || null;
}

/** The help to show: for the control under the pointer, else for the focused one. */
export class HelpTracker {
  private hovered: string | null = null;
  private focused: string | null = null;
  private version = 0;
  private listeners = new Set<() => void>();

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getVersion = (): number => this.version;

  text(): string | null { return this.hovered ?? this.focused; }

  private set(which: "hovered" | "focused", text: string | null): void {
    if (this[which] === text) return;
    const before = this.text();
    this[which] = text;
    if (this.text() === before) return;
    this.version++;
    for (const listener of this.listeners) listener();
  }

  /** Follow the pointer and focus inside `root`; returns a function that stops. */
  attach(root: HTMLElement): () => void {
    const inHelp = (target: EventTarget | null) => target instanceof Element && !!target.closest(`[data-phx-window="${HELP_WINDOW}"]`);
    const over = (e: PointerEvent) => { if (!inHelp(e.target)) this.set("hovered", helpFor(e.target)); };
    const leave = () => this.set("hovered", null);
    const focusIn = (e: FocusEvent) => this.set("focused", helpFor(e.target));
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
  return <p className={`phx-help-text${text ? "" : " is-idle"}`}>{text ?? t("help.idle")}</p>;
}
