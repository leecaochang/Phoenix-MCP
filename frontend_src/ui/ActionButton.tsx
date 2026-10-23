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

export function ActionButton({ icon, label, help, onClick, busy = false, disabled, pressed, className = "btn btn-ghost btn-sm" }: {
  icon: IconName;
  label: string;
  /** Catalog key of the longer help shown in the Help window; otherwise Help shows the label. */
  help?: string;
  onClick: () => void;
  /** A request it started is running: it stays focusable but does nothing. */
  busy?: boolean;
  disabled?: boolean;
  /** A toggle that is currently on. */
  pressed?: boolean;
  className?: string;
}) {
  const iconsOnly = useContext(DisplayModeContext) === "icons";
  return (
    <BusyButton type="button" className={`${className} phx-action${iconsOnly ? " is-icon-only" : ""}`} busy={busy} disabled={disabled}
      aria-label={iconsOnly ? label : undefined} aria-pressed={pressed} title={label} data-help={help} onClick={onClick}>
      <Icon name={icon} size={16} />
      {!iconsOnly && <span>{label}</span>}
    </BusyButton>
  );
}
