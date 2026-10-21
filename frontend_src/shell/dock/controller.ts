/**
 * One workspace's window state: its Dockview instance, folded windows, and
 * where closed or floated windows came from, so they can return there.
 */

import type { AddPanelOptions, DockviewApi, DockviewGroupPanel, SerializedDockview } from "dockview-react";
import { FoldStore, reapplyCollapsed, toggleFold, unfoldWindow, type FoldSnapshot } from "./fold";
import { largestGridGroup, moveWindow, slotOf, type Direction, type Side, type Slot } from "./geometry";
import { sanitizeLayout } from "./sanitize";

/** What a workspace saves: Dockview's own layout plus Phoenix's per-window state. */
export interface WorkspaceLayout {
  dock: SerializedDockview;
  folds: FoldSnapshot;
  /** Closed windows, each with the place to reopen it. */
  closed: Record<string, Slot | null>;
}

const DIRECTION_OF: Record<Side, "left" | "right" | "above" | "below"> = { left: "left", right: "right", top: "above", bottom: "below" };

/** Height a shell-managed window opens at, so it does not take half the workspace. */
const TRANSIENT_HEIGHT = 240;

/** Size given to a window floated from the grid: its own size within these bounds. */
const FLOAT_BOUNDS = { minWidth: 360, maxWidth: 640, minHeight: 260, maxHeight: 480, left: 80, top: 64 };

export interface WindowSpec {
  id: string;
  title: string;
}

export class WorkspaceController {
  api: DockviewApi | null = null;
  readonly folds = new FoldStore();
  /** Called when the user closes a shell-managed window. */
  onTransientClose: ((id: string) => void) | null = null;
  /** The window that last had focus in this workspace. */
  activeWindow: string | null = null;
  private floatedFrom = new Map<string, Slot | null>();
  private closed = new Map<string, Slot | null>();
  private listeners = new Set<() => void>();
  private version = 0;

  constructor(
    readonly id: string,
    /** The windows this workspace may hold, in default order. */
    readonly windows: readonly string[],
    /** Lay out the default arrangement on an empty instance. */
    private readonly arrange: (api: DockviewApi, add: (id: string, position?: AddPanelOptions["position"]) => void) => void,
    /** Current title for a window id. */
    private readonly titleOf: (id: string) => string,
    /** Windows added and removed by the shell (Needs Attention); never saved. */
    private readonly transient: ReadonlySet<string> = new Set(),
  ) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getVersion = (): number => this.version;
  private changed(): void {
    this.version++;
    for (const listener of this.listeners) listener();
  }

  private add = (id: string, position?: AddPanelOptions["position"]): void => {
    this.api?.addPanel({ id, component: id, title: this.titleOf(id), position });
  };

  /** Load a saved layout, or the default arrangement when it is missing or unusable. */
  load(saved: WorkspaceLayout | undefined): void {
    const api = this.api;
    if (!api) return;
    const known = new Set(this.windows);
    const dock = saved ? sanitizeLayout(saved.dock, known) : null;
    this.closed = new Map(Object.entries(saved?.closed ?? {}).filter(([id]) => known.has(id) && !(dock && id in dock.panels)));
    if (dock) {
      try {
        api.fromJSON(dock, { reuseExistingPanels: true });
        this.folds.restore(saved?.folds, (id) => !!api.getPanel(id));
        reapplyCollapsed(api, this.folds);
        this.retitle();
        this.changed();
        return;
      } catch {
        // A layout Dockview cannot load falls through to the default arrangement.
      }
    }
    this.restoreOriginal();
  }

  /** Reset this workspace to its default arrangement. */
  restoreOriginal(): void {
    const api = this.api;
    if (!api) return;
    const keep = [...this.transient].filter((id) => api.getPanel(id));
    api.clear();
    this.folds.restore(undefined, () => false);
    this.closed.clear();
    this.floatedFrom.clear();
    this.arrange(api, this.add);
    for (const id of keep) this.add(id, { direction: "above" });
    this.changed();
  }

  /** The state to save. Transient windows are left out. */
  layout(): WorkspaceLayout | null {
    const api = this.api;
    if (!api) return null;
    const saved = new Set(this.windows.filter((id) => !this.transient.has(id)));
    const dock = sanitizeLayout(api.toJSON(), saved);
    if (!dock) return null;
    return { dock, folds: this.folds.snapshot(), closed: Object.fromEntries(this.closed) };
  }

  /** Record which window has focus; the toolbar's tools follow it. */
  setActiveWindow(id: string | null): void {
    if (id === this.activeWindow) return;
    this.activeWindow = id;
    this.changed();
  }

  /** Open windows in reading order: the grid left to right and top to bottom, then floating windows. */
  windowOrder(): string[] {
    const api = this.api;
    if (!api) return [];
    const out: string[] = [];
    const walk = (node: unknown): void => {
      const n = node as { type?: string; data?: unknown };
      if (n?.type === "leaf") out.push(...((n.data as { views?: string[] })?.views ?? []));
      else if (n?.type === "branch" && Array.isArray(n.data)) n.data.forEach(walk);
    };
    try {
      const json = api.toJSON();
      walk(json.grid.root);
      for (const f of json.floatingGroups ?? []) out.push(...(f.data?.views ?? []));
    } catch {
      return api.panels.map((p) => p.id);
    }
    return out;
  }

  /** Re-read every title, after a language change. */
  retitle(): void {
    for (const panel of this.api?.panels ?? []) panel.api.setTitle(this.titleOf(panel.id));
  }

  move(group: DockviewGroupPanel, dir: Direction): boolean {
    return !!this.api && moveWindow(this.api, group, dir);
  }

  toggleFold(group: DockviewGroupPanel): void {
    if (this.api) toggleFold(this.api, this.folds, group);
  }

  close(group: DockviewGroupPanel): void {
    const api = this.api;
    const panel = group.activePanel;
    if (!api || !panel) return;
    if (!this.transient.has(panel.id)) {
      this.closed.set(panel.id, group.api.location.type === "grid" ? slotOf(api, group) : this.floatedFrom.get(panel.id) ?? null);
    }
    this.floatedFrom.delete(panel.id);
    if (this.folds.isFolded(panel.id)) this.folds.setFolded(panel.id, false);
    panel.api.close();
    if (this.transient.has(panel.id)) this.onTransientClose?.(panel.id);
    this.changed();
  }

  /** Closed windows that can be reopened, in default order. */
  closedWindows(): string[] {
    return this.windows.filter((id) => this.closed.has(id));
  }

  reopen(id: string): void {
    const api = this.api;
    if (!api || api.getPanel(id) || !this.windows.includes(id)) return;
    const slot = this.closed.get(id) ?? null;
    this.closed.delete(id);
    const anchor = slot ? api.getPanel(slot.neighbor) : undefined;
    if (anchor && anchor.group.api.location.type === "grid") {
      this.add(id, { referencePanel: anchor, direction: DIRECTION_OF[slot!.side] });
    } else {
      // No remembered neighbour: full width along the bottom of the workspace.
      this.add(id, { direction: "below" });
    }
    this.changed();
  }

  /** Make a window visible: reopen it if closed, unfold it if folded. */
  ensureOpen(id: string): void {
    const api = this.api;
    if (!api || !this.windows.includes(id)) return;
    if (!api.getPanel(id)) this.reopen(id);
    const group = api.getPanel(id)?.group;
    if (group && this.folds.isFolded(id)) unfoldWindow(api, this.folds, group);
  }

  float(group: DockviewGroupPanel): void {
    const api = this.api;
    const panel = group.activePanel;
    if (!api || !panel || group.api.location.type !== "grid") return;
    this.floatedFrom.set(panel.id, slotOf(api, group));
    const b = FLOAT_BOUNDS;
    api.addFloatingGroup(group, {
      position: { left: b.left, top: b.top },
      width: Math.min(b.maxWidth, Math.max(b.minWidth, group.width)),
      height: Math.min(b.maxHeight, Math.max(b.minHeight, group.height)),
    });
    this.changed();
  }

  /** Return a floating window to where it was floated from, or beside the largest window. */
  dock(group: DockviewGroupPanel): void {
    const api = this.api;
    const panel = group.activePanel;
    if (!api || !panel || group.api.location.type !== "floating") return;
    const slot = this.floatedFrom.get(panel.id) ?? null;
    this.floatedFrom.delete(panel.id);
    const anchor = slot ? api.getPanel(slot.neighbor)?.group : undefined;
    if (anchor && anchor.api.location.type === "grid") {
      group.api.moveTo({ group: anchor, position: slot!.side });
    } else {
      const target = largestGridGroup(api, group);
      if (target) group.api.moveTo({ group: target, position: "right" });
    }
    this.changed();
  }

  /** Add a shell-managed window above everything, full width and compact, if it is not open. */
  showTransient(id: string): void {
    const api = this.api;
    if (!api || api.getPanel(id)) return;
    this.add(id, { direction: "above" });
    api.getPanel(id)?.group.api.setSize({ height: Math.min(TRANSIENT_HEIGHT, Math.max(api.height / 3, 140)) });
  }

  hideTransient(id: string): void {
    this.api?.getPanel(id)?.api.close();
  }
}
