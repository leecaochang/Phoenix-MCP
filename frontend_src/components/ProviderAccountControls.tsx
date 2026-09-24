/** A square icon action. Icon-only, so the accessible name and the tooltip are
 *  the SAME localized sentence: a screen reader and a hovering mouse must not be
 *  told different things, and neither may be told a slug. */
export function IconButton({ label, busyLabel, danger, disabled, busy, onClick, children, expanded }: {
  expanded?: boolean; label: string; busyLabel?: string; danger?: boolean; disabled?: boolean; busy?: boolean;
  onClick: () => void; children: React.ReactNode;
}) {
  // While running, the NAME changes rather than only the styling. A text button
  // said "Refreshing..."; an icon that merely spins tells a screen reader
  // nothing, so the running state has to live somewhere a name can carry it.
  const name = busy && busyLabel ? busyLabel : label;
  return (
    <button type="button" onClick={onClick} disabled={disabled}
            className={`agentcli-account-btn${danger ? " is-danger" : ""}${busy ? " is-busy" : ""}`}
            aria-label={name} title={name} aria-busy={busy || undefined} aria-expanded={expanded}>
      {children}
    </button>
  );
}

const ICON_PROPS = {
  width: 15, height: 15, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
  "aria-hidden": true, style: { display: "block" },
};

export const EditIcon = () => (
  <svg {...ICON_PROPS}><path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17v3Z" /><path d="M14.5 6.5l3 3" /></svg>
);
export const RefreshIcon = () => (
  <svg {...ICON_PROPS}><path d="M20 11a8 8 0 1 0-.6 4" /><path d="M20 4v7h-7" /></svg>
);
export const ProbeIcon = () => (
  <svg {...ICON_PROPS}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.6-3.6" /><path d="M8.5 11l2 2 4-4" /></svg>
);
export const TrashIcon = () => (
  <svg {...ICON_PROPS}><path d="M4 7h16" /><path d="M9 7V5h6v2" /><path d="M6 7l1 13h10l1-13" /></svg>
);
