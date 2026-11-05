/**
 * Toasts and this session's notification history. A notice shows as a toast
 * near the bottom of the panel for a few seconds, longer while the pointer or
 * keyboard focus is on it, and stays in the history (newest first, capped)
 * until it is cleared or the page reloads. Screen readers hear each notice
 * through the toast's live region.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { t } from "../i18n";
import { Icon } from "./Icon";

export interface Notice {
  id: number;
  text: string;
  /** A failure stays until it is dismissed and is spoken at once; other notices go by themselves. */
  tone?: "error";
  /** When it was raised, in epoch milliseconds. */
  time: number;
}

/** How long a short toast stays on screen; a longer one stays long enough to read. */
export const TOAST_MS = 2800;
const TOAST_MS_PER_CHAR = 60;
const TOAST_MAX_MS = 10000;
/** Han, kana and Hangul say in one character about what three Latin letters do. */
const WIDE_CHAR = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/gu;
const WIDE_CHAR_WEIGHT = 3;
export function toastMs(text: string): number {
  const wide = text.match(WIDE_CHAR)?.length ?? 0;
  const reading = text.length + wide * (WIDE_CHAR_WEIGHT - 1);
  return Math.min(TOAST_MAX_MS, Math.max(TOAST_MS, reading * TOAST_MS_PER_CHAR));
}
/** A toast left by the pointer or focus gets at least this long before it goes. */
const TOAST_RESUME_MS = 1200;
/** Notices kept in the history. */
export const HISTORY_LIMIT = 30;

export class Notifications {
  /** Newest first. */
  history: Notice[] = [];
  /** The notice shown as a toast, if any. */
  toast: Notice | null = null;
  private sequence = 0;
  private version = 0;
  private listeners = new Set<() => void>();

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getVersion = (): number => this.version;
  private changed(): void {
    this.version++;
    for (const listener of this.listeners) listener();
  }

  notify(text: string, tone?: "error"): void {
    const notice: Notice = { id: ++this.sequence, text, time: Date.now(), ...(tone ? { tone } : {}) };
    this.history = [notice, ...this.history].slice(0, HISTORY_LIMIT);
    this.toast = notice;
    this.changed();
  }

  /** Hide the toast; with an id, only while that notice is the one shown. */
  dismissToast(id?: number): void {
    if (!this.toast || (id !== undefined && this.toast.id !== id)) return;
    this.toast = null;
    this.changed();
  }

  clear(id: number): void {
    this.history = this.history.filter((n) => n.id !== id);
    this.changed();
  }

}

export const NotificationsContext = createContext<Notifications | null>(null);

/** Follow a notification store; renders again on every change. */
export function useNotifications(): Notifications | null {
  const center = useContext(NotificationsContext);
  useSyncExternalStore(center?.subscribe ?? (() => () => {}), center?.getVersion ?? (() => 0));
  return center;
}

/** Raise a notice from anywhere inside the shell. Outside it, notices are dropped. */
export function useNotify(): (text: string, tone?: "error") => void {
  const center = useContext(NotificationsContext);
  return useCallback((text: string, tone?: "error") => center?.notify(text, tone), [center]);
}

/** The toast, near the bottom of the panel. The live region stays mounted so each new notice is announced. */
export function Toasts({ center }: { center: Notifications }) {
  useSyncExternalStore(center.subscribe, center.getVersion);
  const toast = center.toast;
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const held = hovered || focused;
  const shown = useRef<number | null>(null);
  const remaining = useRef(TOAST_MS);
  if (toast && shown.current !== toast.id) {
    shown.current = toast.id;
    remaining.current = toastMs(toast.text);
  }

  // Count down while nothing holds the toast; the pointer or focus pauses it.
  useEffect(() => {
    if (!toast || held || toast.tone === "error") return;
    const id = toast.id;
    const started = Date.now();
    const timer = window.setTimeout(() => center.dismissToast(id), remaining.current);
    return () => {
      window.clearTimeout(timer);
      if (shown.current === id) remaining.current = Math.max(TOAST_RESUME_MS, remaining.current - (Date.now() - started));
    };
  }, [center, toast, held]);
  useEffect(() => {
    if (toast) return;
    setHovered(false);
    setFocused(false);
  }, [toast]);

  const error = toast?.tone === "error";
  return (
    <>
    {/* A failure is spoken at once from its own region; other notices wait their turn. */}
    <div className="sr-only" role="alert">{error ? toast!.text : ""}</div>
    <div className="phx-toasts" role="status" aria-live="polite">
      {toast && (
        <div className={`phx-toast${error ? " is-error" : ""}`}
          onPointerEnter={() => setHovered(true)}
          onPointerLeave={() => setHovered(false)}
          onFocus={() => setFocused(true)}
          onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false); }}>
          {error && <Icon name="warning" size={16} />}
          <span className="phx-toast-text" aria-hidden={error || undefined}>{toast.text}</span>
          <button type="button" className="phx-toast-dismiss" data-help="help.dismissToast" aria-label={t("notifications.dismiss")} title={t("notifications.dismiss")}
            onClick={() => center.dismissToast(toast.id)}>
            <Icon name="close" size={14} />
          </button>
        </div>
      )}
    </div>
    </>
  );
}
