/**
 * A confirmation shown in place, inside the window whose action asked for it,
 * instead of a dialog over the panel. It says what the action will do and can
 * require an acknowledgement before Confirm becomes available. Focus moves
 * into it when it opens, so a confirmation started from a menu is reached
 * without hunting for it, and the whole box scrolls into view; Escape, Cancel or a click anywhere else closes it
 * unless the action is running. It is a plain UI2 box (user, 2026-10-28): the window's own surface
 * and a hairline with a heading, no coloured accents.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { ActionButton } from "./ActionButton";
import type { IconName } from "./Icon";
import { useClickAway } from "./useClickAway";

export function InlineConfirm({
  label, title, tone = "warning", children, help, acknowledge, confirmLabel, confirmIcon = "check", confirmClassName = "btn btn-primary btn-sm",
  acknowledgeLabel, cancelLabel, busy = false, error = null, onConfirm, onCancel, opener,
}: {
  /** Accessible name of the confirmation, usually the action's name. */
  label: string;
  /** The heading; the label when not given. */
  title?: string;
  tone?: "warning" | "danger";
  /** What the action will do; left out when it reads in the Help window instead. */
  children?: ReactNode;
  /**
   * What the action will do, read in the Help window rather than shown in the box. It also
   * reads for the box's own buttons and acknowledgement: focus lands on them, and their names
   * alone would hide the consequence.
   */
  help?: string;
  /** The control that opened the box: a click on it is not a click elsewhere, so it can close the box itself. */
  opener?: RefObject<HTMLElement | null>;
  /** An acknowledgement to tick before Confirm is available. */
  acknowledge?: ReactNode;
  /** The acknowledgement as plain text, for its switch's name; needed when `acknowledge` has markup. */
  acknowledgeLabel?: string;
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
  const ids = useId();
  const bodyId = `${ids}-body`;
  const helpId = `${ids}-help`;
  // The body says what it will do when there is one; otherwise the Help text, read aloud too.
  const spoken = !!help && !children;
  const described = (children ? bodyId : spoken ? helpId : undefined);
  useClickAway(root, onCancel, !busy, opener);
  // When the box closes with focus inside it, focus goes back to what opened it, while the box
  // is still in the page; otherwise focus would fall to the page with the box.
  useLayoutEffect(() => {
    const box = root.current;
    const scope = box?.getRootNode() as Document | ShadowRoot | undefined;
    const back = opener?.current ?? scope?.activeElement ?? null;
    return () => {
      const active = scope?.activeElement;
      if (!box || !active || !box.contains(active)) return;
      if (back instanceof HTMLElement && back.isConnected && !box.contains(back)) back.focus({ preventScroll: true });
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    // Focus alone does not bring the box into view on an iPhone, and a box opened from a
    // toolbar can start below the screen (user, 2026-11-02): show the whole box first.
    root.current?.scrollIntoView?.({ block: "nearest" });
    root.current?.querySelector<HTMLElement>("input, button:not([disabled])")?.focus({ preventScroll: true });
  }, []);
  return (
    <div ref={root} className={`phx-inline-confirm is-${tone}`} role="group" aria-label={label} aria-describedby={described} data-help-text={help}
      data-help-controls={help ? "" : undefined}
      onKeyDown={(e) => {
        if (e.key !== "Escape" || busy) return;
        e.preventDefault();
        e.stopPropagation();
        onCancel();
      }}>
      <div className="phx-inline-confirm-title">{title ?? label}</div>
      {children && <div id={bodyId} className="phx-inline-confirm-body">{children}</div>}
      {/* What it will do, for a screen reader too: Help is not read aloud. */}
      {spoken && <p id={helpId} className="sr-only">{help}</p>}
      {acknowledge && (
        <div className="toggle-row phx-inline-confirm-ack">
          <div className="toggle-label"><span>{acknowledge}</span></div>
          <label className="toggle-switch">
            <input type="checkbox" aria-label={acknowledgeLabel ?? (typeof acknowledge === "string" ? acknowledge : undefined)} checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
            <span className="toggle-switch-track" />
          </label>
        </div>
      )}
      {error && <div className="banner banner-error" role="alert">{error}</div>}
      <div className="phx-inline-confirm-actions">
        <ActionButton icon={confirmIcon} label={confirmLabel} busy={busy} disabled={!!acknowledge && !acknowledged} describedBy={described}
          onClick={onConfirm} className={confirmClassName} />
        <ActionButton icon="undo" label={cancelLabel} busy={busy} onClick={onCancel} className="btn btn-outline btn-sm" />
      </div>
    </div>
  );
}
