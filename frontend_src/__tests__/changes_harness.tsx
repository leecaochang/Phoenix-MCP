/**
 * Renders the Changes workspace as the shell does: the three windows around
 * their shared state, optionally with a notification centre so a test can read
 * what was announced. Polling is off unless a test turns it on.
 */

import { render } from "@testing-library/react";
import { ChangesProvider } from "../shell/changes";
import { Notifications, NotificationsContext } from "../ui/notify";
import { ChangesListWindow, ComparisonWindow, ItemHistoryWindow } from "../views/ChangesView";

export function renderChanges({ hass = null, center = null, active = false }: { hass?: unknown; center?: Notifications | null; active?: boolean } = {}) {
  return render(
    <NotificationsContext.Provider value={center}>
      <ChangesProvider hass={hass} active={active}>
        <section aria-label="Changes window"><ChangesListWindow /></section>
        <section aria-label="Comparison window"><ComparisonWindow /></section>
        <section aria-label="Item History window"><ItemHistoryWindow /></section>
      </ChangesProvider>
    </NotificationsContext.Provider>,
  );
}

const section = (name: string): HTMLElement => document.querySelector<HTMLElement>(`section[aria-label="${name}"]`)!;
export const listWindow = (): HTMLElement => section("Changes window");
export const comparisonWindow = (): HTMLElement => section("Comparison window");
export const historyWindow = (): HTMLElement => section("Item History window");

/** A window's table rows, in their shown order. */
export const rowsOf = (window: HTMLElement): HTMLElement[] => [...window.querySelectorAll<HTMLElement>("tbody tr[data-row]")];
