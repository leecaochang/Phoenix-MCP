import React from "react";
import type { NodeState } from "../types";
import { t } from "../i18n";

interface Props {
  value: NodeState;
  onChange: (state: NodeState) => void;
  disabled?: boolean;
  /** Names the row it sets, such as "Permission for Kitchen light". */
  label: string;
}

// The letters are the same symbols in every language, as the Guide names them; each button's
// accessible name is its translated title.
const BUTTONS: { state: NodeState; letterKey: string; titleKey: string; helpKey: string }[] = [
  { state: "GREY", letterKey: "perms.letterGrey", titleKey: "perms.selGrey", helpKey: "help.permInherit" },
  { state: "YELLOW", letterKey: "perms.letterYellow", titleKey: "perms.selYellow", helpKey: "help.permRead" },
  { state: "GREEN", letterKey: "perms.letterGreen", titleKey: "perms.selGreen", helpKey: "help.permWrite" },
  { state: "RED", letterKey: "perms.letterRed", titleKey: "perms.selRed", helpKey: "help.permDeny" },
];

/**
 * One Tab stop per row: the arrow keys move between its four buttons, and Enter or Space sets
 * the one with focus. Moving does not set anything, since each choice saves at once and passing
 * Write on the way to Deny must not grant it.
 */
function arrowKeys(e: React.KeyboardEvent<HTMLDivElement>) {
  const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
  const buttons = [...e.currentTarget.querySelectorAll<HTMLButtonElement>("button.perm-btn")];
  const at = buttons.indexOf(e.target as HTMLButtonElement);
  if (at < 0) return;
  const next = step !== undefined ? (at + step + buttons.length) % buttons.length : e.key === "Home" ? 0 : e.key === "End" ? buttons.length - 1 : -1;
  if (next < 0) return;
  e.preventDefault();
  for (const [i, b] of buttons.entries()) b.tabIndex = i === next ? 0 : -1;
  buttons[next].focus();
}

// A paint gesture belongs to the pointer that started it: another finger
// entering a row, or releasing, neither paints nor ends it. A release the page
// never sees (outside the window, or while it lost focus) ends it too, so a
// later hover with no button held cannot paint.
let _drag: { state: NodeState; pointerId: number } | null = null;

if (typeof document !== "undefined") {
  const end = (e: PointerEvent) => { if (_drag?.pointerId === e.pointerId) _drag = null; };
  document.addEventListener("pointerup", end);
  document.addEventListener("pointercancel", end);
  window.addEventListener("blur", () => { _drag = null; });
  window.addEventListener("pagehide", () => { _drag = null; });
}

// Holds the nearest scrolling ancestor's scroll position steady for a few
// frames, as a guard against the page jumping on tap (seen on mobile only).
// Blurring the button (below) covers a focus-driven "scroll the focused
// element into view"; this covers anything else (a reflow, browser
// scroll anchoring) by undoing any scroll delta over the next few frames.
function lockScroll(container: HTMLElement | null) {
  if (!container) return;
  const el = container;
  const y = el.scrollTop;
  let frames = 0;
  function hold() {
    if (el.scrollTop !== y) el.scrollTop = y;
    frames += 1;
    if (frames < 6) requestAnimationFrame(hold);
  }
  requestAnimationFrame(hold);
}

export const PermissionSelector = React.memo(function PermissionSelector({ value, onChange, disabled, label }: Props) {
  return (
    <div
      className="perm-selector"
      role="group"
      data-guide="window.permissions#states"
      aria-label={label}
      style={{ touchAction: "none" }}
      onKeyDown={arrowKeys}
      onPointerEnter={(e) => {
        if (_drag === null || _drag.pointerId !== e.pointerId) return;
        if ((e.buttons & 1) === 0) {
          _drag = null;
          return;
        }
        if (!disabled) {
          lockScroll(e.currentTarget.closest<HTMLElement>(".phx-content"));
          onChange(_drag.state);
        }
      }}
    >
      {BUTTONS.map(({ state, letterKey, titleKey, helpKey }) => {
        const title = t(titleKey);
        return (
        <button
          key={state}
          type="button"
          title={title}
          data-help={helpKey}
          className={`perm-btn${value === state ? ` active-${state}` : ""}`}
          onPointerDown={(e) => {
            // Only the primary button of the primary contact paints: a right
            // click opening a context menu, or a second finger, must not
            // change a saved permission.
            if (disabled || e.button !== 0 || e.isPrimary === false) return;
            e.preventDefault();
            e.currentTarget.releasePointerCapture(e.pointerId);
            lockScroll(e.currentTarget.closest<HTMLElement>(".phx-content"));
            const newState: NodeState = state === value ? "GREY" : state;
            _drag = { state: newState, pointerId: e.pointerId };
            onChange(newState);
            // Unconditional, and safe for keyboard users: this only runs on
            // the pointer path (a keyboard Enter/Space activation never
            // fires onPointerDown), so it cannot blur a Tab-focused button.
            // Works alongside lockScroll above against scroll on tap.
            e.currentTarget.blur();
          }}
          onClick={(e) => {
            if (!disabled && e.detail === 0) onChange(state === value ? "GREY" : state);
          }}
          disabled={disabled}
          aria-pressed={value === state}
          aria-label={`${label}: ${title}`}
          tabIndex={value === state ? 0 : -1}
        >
          {t(letterKey)}
        </button>
        );
      })}
    </div>
  );
});
