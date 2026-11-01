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
  /** When it was raised, in epoch milliseconds. */
  time: number;
}

/** How long a toast stays on screen. */
export const TOAST_MS = 2800;
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

  notify(text: string): void {
    const notice = { id: ++this.sequence, text, time: Date.now() };
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
export function useNotify(): (text: string) => void {
  const center = useContext(NotificationsContext);
  return useCallback((text: string) => center?.notify(text), [center]);
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
    remaining.current = TOAST_MS;
  }

  // Count down while nothing holds the toast; the pointer or focus pauses it.
  useEffect(() => {
    if (!toast || held) return;
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

  return (
    <div className="phx-toasts" role="status" aria-live="polite">
      {toast && (
        <div className="phx-toast"
          onPointerEnter={() => setHovered(true)}
          onPointerLeave={() => setHovered(false)}
          onFocus={() => setFocused(true)}
          onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false); }}>
          <span className="phx-toast-text">{toast.text}</span>
          <button type="button" className="phx-toast-dismiss" aria-label={t("notifications.dismiss")} title={t("notifications.dismiss")}
            onClick={() => center.dismissToast(toast.id)}>
            <Icon name="close" size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
