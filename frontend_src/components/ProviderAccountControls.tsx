import { BusyButton } from "./BusyButton";
import { Icon } from "../ui/Icon";

/** A square icon action. Icon-only, so the accessible name and the tooltip are
 *  the SAME localized sentence, never a slug. */
export function IconButton({ label, busyLabel, danger, disabled, busy, blocked, onClick, children, expanded }: {
  expanded?: boolean; label: string; busyLabel?: string; danger?: boolean; disabled?: boolean; busy?: boolean; blocked?: boolean;
  onClick: () => void; children: React.ReactNode;
}) {
  // While running, the NAME changes rather than only the styling: an icon that
  // merely spins tells a screen reader nothing.
  const name = busy && busyLabel ? busyLabel : label;
  return (
    <BusyButton busy={!!busy || !!blocked} type="button" onClick={onClick} disabled={disabled}
            className={`agentcli-account-btn${danger ? " is-danger" : ""}${busy ? " is-busy" : ""}`}
            aria-label={name} title={name} aria-busy={busy || undefined} aria-expanded={expanded}>
      {children}
    </BusyButton>
  );
}

export const EditIcon = () => <Icon name="edit" size={15} style={{ display: "block" }} />;
export const RefreshIcon = () => <Icon name="refresh" size={15} style={{ display: "block" }} />;
export const ProbeIcon = () => <Icon name="probe" size={15} style={{ display: "block" }} />;
export const TrashIcon = () => <Icon name="delete" size={15} style={{ display: "block" }} />;
