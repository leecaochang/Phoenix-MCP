/** Whether the window a view is drawn in can be seen; background polling pauses when it cannot. */

import { createContext, useContext } from "react";

/** True outside the workspace shell, so a view rendered on its own behaves as before. */
export const WindowVisibleContext = createContext(true);

/** Whether this workspace is on screen; windows in a hidden workspace stay mounted but pause. */
export const WorkspaceVisibleContext = createContext(true);

export function useWindowVisible(): boolean {
  return useContext(WindowVisibleContext);
}

/**
 * A window's content in the workspace on screen. A window added to My
 * Workspace exists in two workspaces; the hidden one's copy is used only when
 * no visible one exists.
 */
export function visibleWindowEl(root: ParentNode | null | undefined, id: string): HTMLElement | null {
  const all = [...(root?.querySelectorAll<HTMLElement>(`[data-phx-window="${id}"]`) ?? [])];
  return all.find((el) => !el.closest(".phx-workspace-frame[hidden]")) ?? all[0] ?? null;
}
