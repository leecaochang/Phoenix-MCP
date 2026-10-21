/** Where windows sit relative to each other, read from their rendered rectangles. */

import type { DockviewApi, DockviewGroupPanel } from "dockview-react";

export type Direction = "left" | "right" | "up" | "down";
export type Side = "left" | "right" | "top" | "bottom";

/** Dockview names drop sides top/bottom; the window menu says up/down. */
export const SIDE_OF: Record<Direction, Side> = { left: "left", right: "right", up: "top", down: "bottom" };

/** The side of a neighbour a window returns to: one found to the left gets this window on its right. */
const RETURN_SIDE: Record<Direction, Side> = { left: "right", right: "left", up: "bottom", down: "top" };

const EDGE_SLACK = 2;
const OVERLAP_MIN = 4;

export function rectOf(group: DockviewGroupPanel): DOMRect {
  return group.element.getBoundingClientRect();
}

/** Windows laid out in the grid; floating windows have no neighbours. */
export function gridGroups(api: DockviewApi): DockviewGroupPanel[] {
  return api.groups.filter((g) => g.api.location.type === "grid");
}

/** The nearest grid window directly beside `group` in `dir`, or null at the workspace edge. */
export function neighbor(api: DockviewApi, group: DockviewGroupPanel, dir: Direction): DockviewGroupPanel | null {
  if (group.api.location.type !== "grid") return null;
  const r = rectOf(group);
  let best: DockviewGroupPanel | null = null;
  let bestDistance = Infinity;
  for (const other of gridGroups(api)) {
    if (other === group) continue;
    const o = rectOf(other);
    const overlapY = Math.min(r.bottom, o.bottom) - Math.max(r.top, o.top) > OVERLAP_MIN;
    const overlapX = Math.min(r.right, o.right) - Math.max(r.left, o.left) > OVERLAP_MIN;
    let distance = Infinity;
    if (dir === "left" && overlapY && o.right <= r.left + EDGE_SLACK) distance = r.left - o.right;
    if (dir === "right" && overlapY && o.left >= r.right - EDGE_SLACK) distance = o.left - r.right;
    if (dir === "up" && overlapX && o.bottom <= r.top + EDGE_SLACK) distance = r.top - o.bottom;
    if (dir === "down" && overlapX && o.top >= r.bottom - EDGE_SLACK) distance = o.top - r.bottom;
    if (distance < bestDistance) { bestDistance = distance; best = other; }
  }
  return best;
}

/**
 * The windows sharing `group`'s row: grid windows with the same top and bottom
 * edges. A window alone in its row spans the workspace width.
 */
export function rowOf(api: DockviewApi, group: DockviewGroupPanel): DockviewGroupPanel[] {
  const r = rectOf(group);
  return gridGroups(api).filter((g) => {
    const o = rectOf(g);
    return Math.abs(o.top - r.top) <= EDGE_SLACK && Math.abs(o.bottom - r.bottom) <= EDGE_SLACK;
  });
}

/** Swap places with the neighbour in `dir`; false when there is none. */
export function moveWindow(api: DockviewApi, group: DockviewGroupPanel, dir: Direction): boolean {
  const target = neighbor(api, group, dir);
  if (!target) return false;
  group.api.moveTo({ group: target, position: SIDE_OF[dir] });
  return true;
}

/** Where a window sat: a neighbouring window and the side of it to come back to. */
export interface Slot {
  neighbor: string;
  side: Side;
  /** The window's height when it was docked, so it can come back at that height. */
  height?: number;
}

/** Remember a window's place before it is closed or floated, by its first neighbour found. */
export function slotOf(api: DockviewApi, group: DockviewGroupPanel): Slot | null {
  for (const dir of ["left", "right", "up", "down"] as const) {
    const n = neighbor(api, group, dir);
    const id = n?.activePanel?.id;
    if (id) return { neighbor: id, side: RETURN_SIDE[dir] };
  }
  return null;
}

/** The largest grid window, the fallback anchor when a remembered neighbour is gone. */
export function largestGridGroup(api: DockviewApi, except?: DockviewGroupPanel): DockviewGroupPanel | undefined {
  return gridGroups(api)
    .filter((g) => g !== except)
    .sort((a, b) => b.width * b.height - a.width * a.height)[0];
}
