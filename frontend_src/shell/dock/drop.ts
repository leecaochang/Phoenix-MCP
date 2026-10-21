/**
 * Drag and drop rules: one window per slot, docked at an edge, never merged
 * into another window as a tab.
 */

import type { DockviewApi, DockviewGroupPanel, PositionResolver } from "dockview-react";

/** Width of the band along the whole workspace's outer edge that docks a full-height or full-width window. */
export const WORKSPACE_EDGE_BAND = 10;

type Root = ShadowRoot | Document;

/**
 * Dockview asks one resolver about every drop target: each window's content
 * and the workspace as a whole. A point over a window's content belongs to
 * that window and docks at its nearest edge; anything else (title bars, gaps)
 * belongs to the workspace and docks only inside its outer band. Window
 * content is drawn in Dockview's separate render layer, so that counts too.
 */
export function edgeResolver(root: () => Root | null): PositionResolver {
  const overWindowContent = (event: DragEvent | PointerEvent): boolean =>
    !!root()?.elementFromPoint(event.clientX, event.clientY)?.closest(".dv-content-container, .dv-render-overlay");
  return {
    resolve: ({ x, y, width, height, zones, event }) => {
      const inWindow = overWindowContent(event);
      const gaps = inWindow
        ? ([["left", x / width], ["right", 1 - x / width], ["top", y / height], ["bottom", 1 - y / height]] as const)
        : ([["left", x], ["right", width - x], ["top", y], ["bottom", height - y]] as const)
          .filter(([, gap]) => gap < WORKSPACE_EDGE_BAND);
      const open = gaps.filter(([side]) => zones.has(side));
      if (!open.length) return null;
      return { position: open.reduce((a, b) => (b[1] < a[1] ? b : a))[0] };
    },
  };
}

/** Refuse drops onto a title or title bar, which Dockview would turn into tabs. */
export function refuseTabDrops(api: DockviewApi): { dispose(): void } {
  return api.onWillShowOverlay((event) => {
    if (event.kind === "tab" || event.kind === "header_space") event.preventDefault();
  });
}

/** Dim the window being dragged until the drag ends, as a desktop window manager does. */
export function dimWhileDragging(api: DockviewApi): { dispose(): void } {
  const mark = (group: DockviewGroupPanel | undefined) => {
    if (!group) return;
    group.element.classList.add("phx-window-dragging");
    const clear = () => {
      group.element.classList.remove("phx-window-dragging");
      for (const type of ["pointerup", "pointercancel", "dragend"]) window.removeEventListener(type, clear, true);
    };
    for (const type of ["pointerup", "pointercancel", "dragend"]) window.addEventListener(type, clear, true);
  };
  const a = api.onWillDragGroup((e) => mark(e.group));
  const b = api.onWillDragPanel((e) => mark(e.panel.group));
  return { dispose: () => { a.dispose(); b.dispose(); } };
}

/**
 * Mouse moves of floating windows. Pointer-driven drag and drop turns every
 * drag into a docking drag, so on a press over a floating window's title bar
 * this one gesture switches to the browser strategy: Dockview cancels that
 * drag for a floating window and runs its own move, which keeps the content
 * layer aligned with the frame. Release switches back. Dockview's move handle
 * is only the empty title-bar space, so a press on the title text is handed to
 * it. Shift-drag falls through and docks; touch keeps Dockview's own handling.
 */
export function floatingMouseMove(api: DockviewApi, container: HTMLElement): () => void {
  let handing = false;
  const onPointerDown = (e: PointerEvent) => {
    if (handing || e.pointerType !== "mouse" || e.button !== 0 || e.shiftKey) return;
    const target = e.target as HTMLElement;
    if (target.closest("button, [role='menu']")) return;
    const shell = target.closest<HTMLElement>(".dv-resize-container");
    if (!shell || !target.closest(".dv-tabs-and-actions-container")) return;
    if (!api.groups.some((g) => shell.contains(g.element))) return;
    api.updateOptions({ dndStrategy: "html5" });
    const tab = target.closest<HTMLElement>(".dv-tab");
    const handle = shell.querySelector<HTMLElement>(".dv-void-container");
    if (tab && handle) {
      tab.draggable = false;
      e.stopPropagation();
      handing = true;
      try {
        handle.dispatchEvent(new PointerEvent("pointerdown", {
          bubbles: true, composed: true, cancelable: true, clientX: e.clientX, clientY: e.clientY,
          pointerId: e.pointerId, pointerType: "mouse", button: 0, buttons: 1, isPrimary: true,
        }));
      } finally {
        handing = false;
      }
    }
    const restore = () => {
      api.updateOptions({ dndStrategy: "pointer" });
      for (const type of ["pointerup", "pointercancel", "dragend"]) window.removeEventListener(type, restore, true);
    };
    for (const type of ["pointerup", "pointercancel", "dragend"]) window.addEventListener(type, restore, true);
  };
  container.addEventListener("pointerdown", onPointerDown, true);
  return () => container.removeEventListener("pointerdown", onPointerDown, true);
}
