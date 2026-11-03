/**
 * An action button inside a window that follows the toolbar's display mode:
 * an icon with its label in icons-and-text mode, the icon alone in icons-only
 * mode (the label then names it and shows as its tooltip). Outside the shell
 * the label is shown.
 */

import { createContext, useContext } from "react";
import { BusyButton } from "../components/BusyButton";
import { Icon, type IconName } from "./Icon";

export type DisplayMode = "icons" | "icon-text";

/** The toolbar's display mode, provided by the shell. */
export const DisplayModeContext = createContext<DisplayMode>("icon-text");

export function ActionButton({ icon, label, name, title, help, helpText, guide, onClick, busy = false, inactive, disabled, pressed, describedBy, className = "btn btn-ghost btn-sm" }: {
  icon: IconName;
  label: string;
  /** A fuller accessible name than the label, such as one naming the item it acts on. */
  name?: string;
  /** A longer tooltip than the label, such as when the action last ran. */
  title?: string;
  /** Catalog key of the longer help shown in the Help window; otherwise Help shows the label. */
  help?: string;
  /** The longer help itself, for text built at render time; it wins over `help`. */
  helpText?: string;
  /** The Guide section about this action, `<topic id>#<anchor>` (see shell/guideTarget.ts). */
  guide?: string;
  onClick: () => void;
  /** A request it started is running: it stays focusable but does nothing. */
  busy?: boolean;
  /** Unavailable for now but kept focusable, so focus is not lost when it changes under the user. */
  inactive?: boolean;
  disabled?: boolean;
  /** A toggle that is currently on. */
  pressed?: boolean;
  /** Ids of text that says more about what it does, such as a confirmation's consequence. */
  describedBy?: string;
  className?: string;
}) {
  const iconsOnly = useContext(DisplayModeContext) === "icons";
  return (
    <BusyButton type="button" className={`${className} phx-action${iconsOnly ? " is-icon-only" : ""}`} busy={busy} inactive={inactive} disabled={disabled}
      aria-label={name ?? (iconsOnly ? label : undefined)} aria-pressed={pressed} aria-describedby={describedBy} title={title ?? label} data-help={help} data-help-text={helpText} data-guide={guide} onClick={onClick}>
      <Icon name={icon} size={16} />
      {!iconsOnly && <span>{label}</span>}
    </BusyButton>
  );
}
