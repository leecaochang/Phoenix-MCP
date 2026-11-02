/**
 * Rows of side-by-side windows. A window dropped beside others shares their
 * row by fixed rules (user, 2026-10-27): one window before splits 50|50, two
 * even ones become thirds, two uneven ones give half of the nearer one's
 * width (thirds when dropped between them), three become quarters. No
 * workspace ever holds more than MAX_ACROSS windows across. Also where a
 * window added to My Workspace goes (see `nextPlace`).
 */

import type { DockviewApi, DockviewGroupPanel, Position } from "dockview-react";
import { gridGroups, rectOf } from "./geometry";
import { leafOrder } from "./scroll";

/** The most windows side by side at any height of a workspace. */
export const MAX_ACROSS = 4;
/** Two windows whose shares of their row differ by no more than this count as an even split. */
const EVEN_SLACK = 0.05;

interface GridNode { type: "leaf" | "branch"; data: unknown }
const childrenOf = (node: GridNode): GridNode[] => (Array.isArray(node.data) ? (node.data as GridNode[]) : []);
const leafId = (node: GridNode): string | undefined => (node.type === "leaf" ? ((node.data as { views?: string[] })?.views ?? [])[0] : undefined);
const firstLeaf = (node: GridNode): string => leafOrder(node)[0] ?? "";

/** A window's row: the side-by-side children of the layout branch holding it, each named by its first window. */
export interface Row { members: string[]; index: number }

/** The row a docked window sits in; null when it is stacked rather than side by side, or not docked. */
export function rowAround(api: DockviewApi, id: string): Row | null {
  const grid = api.toJSON().grid;
  const find = (node: GridNode, across: boolean): Row | null | undefined => {
    const children = childrenOf(node);
    const at = children.findIndex((c) => leafId(c) === id);
    if (at >= 0) return across && children.length > 1 ? { members: children.map(firstLeaf), index: at } : null;
    for (const child of children) {
      const found = find(child, !across);
      if (found !== undefined) return found;
    }
    return undefined;
  };
  return find(grid.root as GridNode, grid.orientation === "HORIZONTAL") ?? null;
}

/**
 * The shares of a row once a window joins it at `index`, from the widths of
 * the windows there before (in order, without the new one).
 */
export function rowShares(before: readonly number[], index: number): number[] {
  const even = (count: number) => Array<number>(count).fill(1 / count);
  const total = before.reduce((a, b) => a + b, 0);
  if (before.length !== 2 || total <= 0 || before.some((w) => !(w > 0))) return even(before.length + 1);
  const [a, b] = before.map((w) => w / total);
  if (Math.abs(a - b) <= EVEN_SLACK || index === 1) return even(3);
  return index === 0 ? [a / 2, a / 2, b] : [a, b / 2, b / 2];
}

/** Give a row's members these shares of the row's width. */
export function setRowShares(api: DockviewApi, members: readonly string[], shares: readonly number[]): void {
  const groups = members.map((id) => api.getPanel(id)?.group);
  if (groups.length !== shares.length || groups.some((g) => !g)) return;
  const total = groups.reduce((sum, g) => sum + g!.width, 0);
  if (total <= 0) return;
  // Each width set takes its difference from the windows after it, so left to right leaves the earlier ones alone.
  groups.slice(0, -1).forEach((g, i) => g!.api.setSize({ width: Math.round(shares[i] * total) }));
}

/** Give every window in a docked window's row the same width. */
export function evenRow(api: DockviewApi, id: string): void {
  const row = rowAround(api, id);
  if (row) setRowShares(api, row.members, row.members.map(() => 1 / row.members.length));
}

const middle = (g: DockviewGroupPanel): number => { const r = rectOf(g); return (r.top + r.bottom) / 2; };

/** How many docked windows a horizontal line at `y` passes through, leaving out `except`. */
export function acrossAt(api: DockviewApi, y: number, except?: DockviewGroupPanel | null): number {
  return gridGroups(api).filter((g) => {
    if (g === except) return false;
    const r = rectOf(g);
    return r.top < y && y < r.bottom;
  }).length;
}

/**
 * Whether placing `moving` beside `target` (or along a side edge of the whole
 * workspace, without a target) would put more than MAX_ACROSS windows across.
 */
export function tooManyAcross(api: DockviewApi, target: DockviewGroupPanel | null | undefined, moving: DockviewGroupPanel | null | undefined): boolean {
  const lines = target ? [middle(target)] : gridGroups(api).filter((g) => g !== moving).map(middle);
  return lines.some((y) => acrossAt(api, y, moving) >= MAX_ACROSS);
}

/** Every docked window's width but `moving`'s, taken before a window moves. */
export function widthsExcept(api: DockviewApi, moving: DockviewGroupPanel | null): Map<string, number> {
  return new Map(gridGroups(api).filter((g) => g !== moving && g.activePanel).map((g) => [g.activePanel!.id, g.width]));
}

/** Share out the row a window just joined, from the widths its windows had before (`widthsExcept`). */
export function shareRow(api: DockviewApi, id: string, before: ReadonlyMap<string, number>): void {
  const row = rowAround(api, id);
  if (!row) return;
  const widths = row.members.filter((_, i) => i !== row.index).map((m) => before.get(m) ?? 0);
  setRowShares(api, row.members, rowShares(widths, row.index));
}

const isSide = (position: Position): boolean => position === "left" || position === "right";

/** The window frame a drag carries. */
function draggedGroup(api: DockviewApi, data: { groupId: string; panelId: string | null } | undefined): DockviewGroupPanel | null {
  if (!data) return null;
  if (data.panelId) return api.getPanel(data.panelId)?.group ?? null;
  return api.groups.find((g) => g.id === data.groupId) ?? null;
}

/**
 * Apply the row rules to drops beside a window or along a side edge of the
 * workspace, and refuse (showing no highlight) one that would put more than
 * MAX_ACROSS windows across. Dockview moves the window right after its
 * will-drop event, so the row is shared out once that move is done.
 */
export function rowRules(api: DockviewApi): { dispose(): void } {
  const applies = (kind: string, position: Position) => (kind === "content" || kind === "edge") && isSide(position);
  const overlay = api.onWillShowOverlay((e) => {
    if (!applies(e.kind, e.position)) return;
    if (tooManyAcross(api, e.kind === "content" ? e.group : null, draggedGroup(api, e.getData()))) e.preventDefault();
  });
  const drop = api.onWillDrop((e) => {
    if (!applies(e.kind, e.position)) return;
    const moving = draggedGroup(api, e.getData());
    const target = e.kind === "content" ? e.group : null;
    if (tooManyAcross(api, target, moving)) {
      e.preventDefault();
      return;
    }
    const id = moving?.activePanel?.id;
    // A window dropped on its own edge does not move.
    if (!id || target === moving) return;
    const widths = widthsExcept(api, moving);
    queueMicrotask(() => shareRow(api, id, widths));
  });
  return { dispose: () => { overlay.dispose(); drop.dispose(); } };
}

/** Windows side by side with only single windows beside them, as groups of window ids; windows alone are left out. */
export function leafRows(api: DockviewApi): string[][] {
  const grid = api.toJSON().grid;
  const out: string[][] = [];
  const walk = (node: GridNode, across: boolean): void => {
    const children = childrenOf(node);
    if (across && children.length > 1 && children.every((c) => c.type === "leaf")) out.push(children.map((c) => leafId(c) ?? ""));
    for (const child of children) walk(child, !across);
  };
  walk(grid.root as GridNode, grid.orientation === "HORIZONTAL");
  return out;
}

/** Where a window added to My Workspace goes. */
export type NextPlace =
  /** Floating, beside the floating windows already there. */
  | { kind: "float" }
  /** Alone in an empty workspace, filling it. */
  | { kind: "first" }
  /** To the right of `of`, sharing its row evenly. */
  | { kind: "beside"; of: string }
  /** Below `of`, splitting its column in half. */
  | { kind: "under"; of: string }
  /** A column along the right of the whole workspace, half its width. */
  | { kind: "column" }
  /** A new row across the bottom of the workspace. */
  | { kind: "row" };

/** How many windows a row of the layout holds across. */
const acrossOf = (node: GridNode, across: boolean): number => (node.type === "leaf" ? 1 : across ? childrenOf(node).length : Math.max(1, ...childrenOf(node).map((c) => acrossOf(c, true))));

/** The rightmost window of a row. */
const lastLeaf = (node: GridNode): string => { const ids = leafOrder(node); return ids[ids.length - 1] ?? ""; };

/**
 * The place for the next window added to My Workspace (user, 2026-10-27):
 * the first fills an empty workspace (or floats when only floating windows
 * are there); the second goes beside the first, 50|50; two side by side get
 * a row under them, two stacked a column beside them; the window after that
 * splits the newest row or column in half; and so on, a new row at a time.
 * `skip` leaves out windows the shell manages (Needs Attention).
 */
export function nextPlace(api: DockviewApi, floating: boolean, skip: ReadonlySet<string> = new Set()): NextPlace {
  const grid = api.toJSON().grid;
  // Leave out skipped windows, then branches left with a single child.
  const prune = (node: GridNode): GridNode | null => {
    if (node.type === "leaf") return skip.has(leafId(node) ?? "") ? null : node;
    const children = childrenOf(node).map(prune).filter((c): c is GridNode => !!c);
    if (!children.length) return null;
    return { type: "branch", data: children };
  };
  let root = prune(grid.root as GridNode);
  let across = grid.orientation === "HORIZONTAL";
  while (root && root.type === "branch" && childrenOf(root).length === 1) {
    root = childrenOf(root)[0];
    across = !across;
  }
  if (!root) return floating ? { kind: "float" } : { kind: "first" };
  if (root.type === "leaf") return { kind: "beside", of: leafId(root) ?? "" };
  const rows = childrenOf(root);
  if (across) {
    // A row of windows side by side gets a row under it; a stacked pair beside one window splits that window.
    const [left, right] = rows;
    if (rows.length === 2 && left.type === "branch" && childrenOf(left).length === 2 && childrenOf(left).every((c) => c.type === "leaf") && right.type === "leaf") {
      return { kind: "under", of: leafId(right) ?? "" };
    }
    return { kind: "row" };
  }
  if (rows.length === 2 && rows.every((r) => r.type === "leaf")) return { kind: "column" };
  const last = rows[rows.length - 1];
  const before = rows[rows.length - 2];
  const lastAcross = acrossOf(last, true);
  if (lastAcross < acrossOf(before, true) && lastAcross < MAX_ACROSS) return { kind: "beside", of: lastLeaf(last) };
  return { kind: "row" };
}
