/**
 * Folding a window into its title bar, in place.
 *
 * A folded window hides its content but stays mounted. A window alone in its
 * row also shrinks to its title bar. Beside other windows it keeps the row's
 * height, so folding never makes a neighbour shorter; once every window in a
 * row is folded the whole row shrinks.
 */

import type { DockviewApi, DockviewGroupPanel } from "dockview-react";
import { rowOf } from "./geometry";

/** Title bar height, matching --dv-tabs-and-actions-container-height in dock.css. */
export const TITLE_BAR_HEIGHT = 34;
/** Smallest docked window; keeps a title readable and its content usable. */
export const MIN_WINDOW_WIDTH = 240;
export const MIN_WINDOW_HEIGHT = 100;

export interface FoldSnapshot {
  folded: string[];
  /** Window id to the height it returns to when its collapsed row expands. */
  collapsed: Record<string, number>;
}

/** Folded windows by window id, observable so title bars and bodies follow along. */
export class FoldStore {
  private folded = new Set<string>();
  private collapsed = new Map<string, number>();
  private listeners = new Set<() => void>();
  private version = 0;

  isFolded = (id: string): boolean => this.folded.has(id);

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  getVersion = (): number => this.version;

  snapshot(): FoldSnapshot {
    return { folded: [...this.folded], collapsed: Object.fromEntries(this.collapsed) };
  }

  /** Load a saved state; `known` drops windows that no longer exist. */
  restore(state: Partial<FoldSnapshot> | undefined, known: (id: string) => boolean): void {
    this.folded = new Set((Array.isArray(state?.folded) ? state.folded : []).filter((id) => typeof id === "string" && known(id)));
    this.collapsed = new Map(Object.entries(state?.collapsed ?? {})
      .filter(([id, h]) => this.folded.has(id) && typeof h === "number" && Number.isFinite(h) && h > TITLE_BAR_HEIGHT));
    this.changed();
  }

  /** @internal */
  setFolded(id: string, folded: boolean): void {
    if (folded) this.folded.add(id); else this.folded.delete(id);
    this.changed();
  }

  /** @internal */
  collapsedHeight(id: string): number | undefined { return this.collapsed.get(id); }

  /** @internal */
  setCollapsed(id: string, height: number | undefined): void {
    if (height === undefined) this.collapsed.delete(id); else this.collapsed.set(id, height);
  }

  private changed(): void {
    this.version++;
    for (const listener of this.listeners) listener();
  }
}

const idOf = (group: DockviewGroupPanel): string | undefined => group.activePanel?.id;

function collapse(store: FoldStore, group: DockviewGroupPanel): void {
  const id = idOf(group);
  if (!id || store.collapsedHeight(id) !== undefined) return;
  store.setCollapsed(id, Math.max(group.height, MIN_WINDOW_HEIGHT));
  group.api.setConstraints({ minimumHeight: TITLE_BAR_HEIGHT, maximumHeight: TITLE_BAR_HEIGHT });
  group.api.setSize({ height: TITLE_BAR_HEIGHT });
}

function expand(store: FoldStore, group: DockviewGroupPanel): void {
  const id = idOf(group);
  const height = id ? store.collapsedHeight(id) : undefined;
  if (!id || height === undefined) return;
  store.setCollapsed(id, undefined);
  group.api.setConstraints({ minimumHeight: MIN_WINDOW_HEIGHT, maximumHeight: Number.MAX_SAFE_INTEGER });
  group.api.setSize({ height });
}

export function foldWindow(api: DockviewApi, store: FoldStore, group: DockviewGroupPanel): void {
  const id = idOf(group);
  if (!id || store.isFolded(id)) return;
  store.setFolded(id, true);
  if (group.api.location.type !== "grid") return;
  const row = rowOf(api, group);
  if (row.every((g) => { const rid = idOf(g); return rid !== undefined && store.isFolded(rid); })) {
    for (const g of row) collapse(store, g);
  }
}

/**
 * Keep folded rows right after windows moved, closed or arrived: a row whose
 * windows are all folded shrinks to its title bars (a folded window pinned or
 * docked from floating, or left alone when its neighbour closed), and a
 * shrunk row that now holds an open window grows back to its own height, its
 * folded windows staying folded.
 */
export function settleFolds(api: DockviewApi, store: FoldStore): void {
  const allFolded = (row: DockviewGroupPanel[]) => row.every((g) => { const rid = idOf(g); return rid !== undefined && store.isFolded(rid); });
  for (const group of api.groups) {
    const id = idOf(group);
    if (!id || group.api.location.type !== "grid") continue;
    const collapsed = store.collapsedHeight(id) !== undefined;
    if (!collapsed && !store.isFolded(id)) continue;
    const row = rowOf(api, group);
    if (!collapsed && allFolded(row)) for (const g of row) collapse(store, g);
    else if (collapsed && !allFolded(row)) expand(store, group);
  }
}

export function unfoldWindow(api: DockviewApi, store: FoldStore, group: DockviewGroupPanel): void {
  const id = idOf(group);
  if (!id || !store.isFolded(id)) return;
  const wasCollapsed = store.collapsedHeight(id) !== undefined;
  // Measure the row before expanding: a collapsed row is identified by its shared edges.
  const row = wasCollapsed && group.api.location.type === "grid" ? rowOf(api, group) : [group];
  store.setFolded(id, false);
  if (wasCollapsed) for (const g of row) expand(store, g);
}

export function toggleFold(api: DockviewApi, store: FoldStore, group: DockviewGroupPanel): void {
  const id = idOf(group);
  if (!id) return;
  if (store.isFolded(id)) unfoldWindow(api, store, group); else foldWindow(api, store, group);
}

/** Saved layouts keep sizes but not constraints: pin collapsed rows again after loading one. */
export function reapplyCollapsed(api: DockviewApi, store: FoldStore): void {
  for (const group of api.groups) {
    const id = idOf(group);
    if (id && store.collapsedHeight(id) !== undefined) {
      group.api.setConstraints({ minimumHeight: TITLE_BAR_HEIGHT, maximumHeight: TITLE_BAR_HEIGHT });
      group.api.setSize({ height: TITLE_BAR_HEIGHT });
    }
  }
}
