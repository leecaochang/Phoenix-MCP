/**
 * A confirmation shown in place, inside the window whose action asked for it,
 * instead of a dialog over the panel. It says what the action will do and can
 * require an acknowledgement before Confirm becomes available. Focus moves
 * into it when it opens, so a confirmation started from a menu is reached
 * without hunting for it; Escape or Cancel closes it unless the action is
 * running.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { ActionButton } from "./ActionButton";
import type { IconName } from "./Icon";

export function InlineConfirm({
  label, tone = "warning", children, acknowledge, confirmLabel, confirmIcon = "check", confirmClassName = "btn btn-primary btn-sm",
  cancelLabel, busy = false, error = null, onConfirm, onCancel,
}: {
  /** Accessible name of the confirmation, usually the action's name. */
  label: string;
  tone?: "warning" | "danger";
  /** What the action will do. */
  children: ReactNode;
  /** An acknowledgement to tick before Confirm is available. */
  acknowledge?: string;
  confirmLabel: string;
  confirmIcon?: IconName;
  confirmClassName?: string;
  cancelLabel: string;
  /** The action is running: both buttons stay focusable but do nothing. */
  busy?: boolean;
  error?: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [acknowledged, setAcknowledged] = useState(false);
  const root = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    root.current?.querySelector<HTMLElement>("input, button:not([disabled])")?.focus();
  }, []);
  return (
    <div ref={root} className={`phx-inline-confirm is-${tone}`} role="group" aria-label={label}
      onKeyDown={(e) => {
        if (e.key !== "Escape" || busy) return;
        e.preventDefault();
        e.stopPropagation();
        onCancel();
      }}>
      <div className="phx-inline-confirm-body">{children}</div>
      {acknowledge && (
        <div className="toggle-row phx-inline-confirm-ack">
          <div className="toggle-label"><span>{acknowledge}</span></div>
          <label className="toggle-switch">
            <input type="checkbox" aria-label={acknowledge} checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
            <span className="toggle-switch-track" />
          </label>
        </div>
      )}
      {error && <div className="banner banner-error" role="alert">{error}</div>}
      <div className="phx-inline-confirm-actions">
        <ActionButton icon={confirmIcon} label={confirmLabel} busy={busy} disabled={!!acknowledge && !acknowledged}
          onClick={onConfirm} className={confirmClassName} />
        <ActionButton icon="undo" label={cancelLabel} busy={busy} onClick={onCancel} className="btn btn-outline btn-sm" />
      </div>
    </div>
  );
}
