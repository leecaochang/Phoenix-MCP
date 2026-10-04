import React from "react";
import type { NodeState } from "../types";
import { t } from "../i18n";

interface Props {
  value: NodeState;
  onChange: (state: NodeState) => void;
  disabled?: boolean;
  label?: string;
}

const BUTTONS: { state: NodeState; label: string; titleKey: string }[] = [
  { state: "GREY", label: "N", titleKey: "perms.selGrey" },
  { state: "YELLOW", label: "R", titleKey: "perms.selYellow" },
  { state: "GREEN", label: "W", titleKey: "perms.selGreen" },
  { state: "RED", label: "D", titleKey: "perms.selRed" },
];

let _dragState: NodeState | null = null;

if (typeof document !== "undefined") {
  document.addEventListener("pointerup", () => { _dragState = null; });
  document.addEventListener("pointercancel", () => { _dragState = null; });
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

export const PermissionSelector = React.memo(function PermissionSelector({ value, onChange, disabled, label = "Permission" }: Props) {
  return (
    <div
      className="perm-selector"
      role="group"
      aria-label={label}
      style={{ touchAction: "none" }}
      onPointerEnter={(e) => {
        if (_dragState !== null && !disabled) {
          lockScroll(e.currentTarget.closest<HTMLElement>(".phx-content"));
          onChange(_dragState);
        }
      }}
    >
      {BUTTONS.map(({ state, label, titleKey }) => {
        const title = t(titleKey);
        return (
        <button
          key={state}
          type="button"
          title={title}
          className={`perm-btn${value === state ? ` active-${state}` : ""}`}
          onPointerDown={(e) => {
            if (disabled) return;
            e.preventDefault();
            e.currentTarget.releasePointerCapture(e.pointerId);
            lockScroll(e.currentTarget.closest<HTMLElement>(".phx-content"));
            const newState: NodeState = state === value ? "GREY" : state;
            _dragState = newState;
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
          aria-label={`${label}: ${value === state ? t("perms.selSelected", { title }) : title}`}
        >
          {label}
        </button>
        );
      })}
    </div>
  );
});
