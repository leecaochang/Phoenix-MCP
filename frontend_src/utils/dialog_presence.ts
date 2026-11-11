/**
 * Which Phoenix dialogs are open, for the floating Agent Chat window to step aside from.
 *
 * The panel, the injected MESA editor and Agent Chat are separate bundles sharing one
 * browser Window, so the set lives on the Window under a shared symbol, and each change
 * is announced with a DOM event, as approval review already is.
 */

export const DIALOG_OPENED_EVENT = "phx-dialog-opened";
export const DIALOG_CLOSED_EVENT = "phx-dialog-closed";

const REGISTRY = Symbol.for("phoenix-mcp.open-dialogs");

/** The dialogs open on this page right now, whichever bundle opened them. */
export function openDialogs(): Set<HTMLElement> {
  const holder = window as unknown as Record<symbol, Set<HTMLElement> | undefined>;
  return (holder[REGISTRY] ??= new Set<HTMLElement>());
}

/** Record a dialog opening or closing, and announce it. */
export function announceDialog(node: HTMLElement, open: boolean): void {
  if (open) openDialogs().add(node);
  else openDialogs().delete(node);
  window.dispatchEvent(new CustomEvent(open ? DIALOG_OPENED_EVENT : DIALOG_CLOSED_EVENT, { detail: { node } }));
}

/** The dialog element an announcement names, or null for anything else. */
export function announcedDialog(event: Event): HTMLElement | null {
  const node = (event as CustomEvent<{ node?: unknown }>).detail?.node;
  return node instanceof HTMLElement ? node : null;
}
