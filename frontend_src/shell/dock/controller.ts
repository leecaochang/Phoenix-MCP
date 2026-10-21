/**
 * One workspace's window state: its Dockview instance, folded windows, and
 * where closed or floated windows came from, so they can return there. Every
 * change that adds, removes, folds or moves a docked window keeps the other
 * windows' heights; the workspace's canvas grows or shrinks instead.
 */

import type { AddPanelOptions, DockviewApi, DockviewGroupPanel, SerializedDockview } from "dockview-react";
import { FoldStore, foldWindow, reapplyCollapsed, toggleFold, type FoldSnapshot } from "./fold";
import { largestGridGroup, moveWindow, rowOf, slotOf, type Direction, type Side, type Slot } from "./geometry";
import { sanitizeLayout } from "./sanitize";
import type { Heights, WorkspaceCanvas } from "./scroll";

/** What a workspace saves: Dockview's own layout plus Phoenix's per-window state. */
export interface WorkspaceLayout {
  dock: SerializedDockview;
  folds: FoldSnapshot;
  /** Closed windows, each with the place to reopen it. */
  closed: Record<string, Slot | null>;
  /** Each docked window's intended height; the workspace scrolls when they need more than the screen. */
  heights?: Record<string, number>;
}

const EMPTY: ReadonlySet<string> = new Set();

const DIRECTION_OF: Record<Side, "left" | "right" | "above" | "below"> = { left: "left", right: "right", top: "above", bottom: "below" };

/** Height a shell-managed window opens at, so it does not take half the workspace. */
const TRANSIENT_HEIGHT = 240;
/** Height a reopened window gets when its earlier height is unknown. */
const REOPEN_HEIGHT = 360;

const validHeight = (h: unknown): number | undefined => (typeof h === "number" && Number.isFinite(h) && h > 0 && h < 100000 ? h : undefined);

/** Size given to a window floated from the grid: its own size within these bounds. */
const FLOAT_BOUNDS = { minWidth: 360, maxWidth: 640, minHeight: 260, maxHeight: 480, left: 80, top: 64 };

export interface WindowSpec {
  id: string;
  title: string;
}

/** Optional parts of a workspace definition. */
export interface WorkspaceExtras {
  /** Windows added and removed by the shell (Needs Attention); never saved. */
  transient?: ReadonlySet<string>;
  /** Windows that start folded into their title bars. */
  folded?: readonly string[];
  /** Windows shown only while a setting allows, with where each appears when it does. */
  conditional?: Readonly<Record<string, Slot>>;
  /** Comfortable heights the default arrangement gives windows; the workspace scrolls when they need it. */
  heights?: Readonly<Record<string, number>>;
}

export class WorkspaceController {
  api: DockviewApi | null = null;
  readonly folds = new FoldStore();
  /** Called when the user closes a shell-managed window. */
  onTransientClose: ((id: string) => void) | null = null;
  /** The window that last had focus in this workspace. */
  activeWindow: string | null = null;
  /** Whether a conditional window is allowed now: true, false, or null while unknown. */
  availability: (id: string) => boolean | null = () => true;
  /** The scrolling canvas the windows are laid out on; absent until the workspace is drawn. */
  canvas: WorkspaceCanvas | null = null;
  /** Whether a window is maximized over the visible area by this workspace. */
  private maximizedHere = false;
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
    private readonly extras: WorkspaceExtras = {},
  ) {}

  private get transient(): ReadonlySet<string> { return this.extras.transient ?? EMPTY; }
  private get conditional(): Readonly<Record<string, Slot>> { return this.extras.conditional ?? {}; }

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

  /**
   * Run a change to the docked windows, then lay them out at their intended
   * heights (plus any `extra` ones), so the page grows or shrinks instead of
   * squeezing a neighbour.
   */
  private keepHeights(change: () => void, extra: Heights = {}): void {
    const canvas = this.canvas;
    if (!canvas) {
      change();
      return;
    }
    canvas.capture();
    change();
    canvas.prune();
    canvas.apply(extra);
  }

  /** Load a saved layout, or the default arrangement when it is missing or unusable. */
  load(saved: WorkspaceLayout | undefined): void {
    const api = this.api;
    if (!api) return;
    const known = new Set(this.windows);
    const dock = saved ? sanitizeLayout(saved.dock, known) : null;
    this.closed = new Map(Object.entries(saved?.closed ?? {}).filter(([id]) => known.has(id) && !(dock && id in dock.panels)));
    if (dock) {
      try {
        if (this.canvas) {
          this.canvas.intended = Object.fromEntries(Object.entries(saved?.heights ?? {})
            .filter(([id, h]) => known.has(id) && validHeight(h) !== undefined));
          this.canvas.setCanvas(0);
        }
        api.fromJSON(dock, { reuseExistingPanels: true });
        this.folds.restore(saved?.folds, (id) => !!api.getPanel(id));
        reapplyCollapsed(api, this.folds);
        this.canvas?.apply();
        this.retitle();
        this.syncAvailable();
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
    this.maximizedHere = false;
    if (this.canvas) {
      this.canvas.intended = {};
      this.canvas.setCanvas(0);
    }
    this.arrange(api, this.add);
    this.canvas?.apply({ ...this.extras.heights });
    for (const id of this.extras.folded ?? []) this.foldById(id);
    for (const id of keep) this.keepHeights(() => this.add(id, { direction: "above" }), { [id]: TRANSIENT_HEIGHT });
    this.syncAvailable();
    this.changed();
  }

  private foldById(id: string): void {
    const api = this.api;
    const group = api?.getPanel(id)?.group;
    if (api && group) this.keepHeights(() => foldWindow(api, this.folds, group));
  }

  /**
   * Show or hide conditional windows to match `availability`. A window that is
   * no longer allowed closes without being remembered as closed; one that becomes
   * allowed opens in its place unless the user closed it.
   */
  syncAvailable(): void {
    const api = this.api;
    if (!api) return;
    let changed = false;
    for (const [id, home] of Object.entries(this.conditional)) {
      const allowed = this.availability(id);
      if (allowed === null) continue;
      const panel = api.getPanel(id);
      if (!allowed && panel) {
        if (this.folds.isFolded(id)) this.folds.setFolded(id, false);
        this.folds.setCollapsed(id, undefined);
        this.floatedFrom.delete(id);
        this.keepHeights(() => panel.api.close());
        changed = true;
      } else if (allowed && !panel && !this.closed.has(id)) {
        const anchor = api.getPanel(home.neighbor);
        this.keepHeights(() => {
          if (anchor && anchor.group.api.location.type === "grid") this.add(id, { referencePanel: anchor, direction: DIRECTION_OF[home.side] });
          else this.add(id, { direction: "below" });
        }, { [id]: this.extras.heights?.[id] ?? REOPEN_HEIGHT });
        if (this.extras.folded?.includes(id)) this.foldById(id);
        changed = true;
      }
    }
    if (changed) this.changed();
  }

  /** The state to save. Transient windows are left out. */
  layout(): WorkspaceLayout | null {
    const api = this.api;
    if (!api) return null;
    const saved = new Set(this.windows.filter((id) => !this.transient.has(id)));
    const dock = sanitizeLayout(api.toJSON(), saved);
    if (!dock) return null;
    const heights = this.canvas ? { ...this.canvas.intended } : undefined;
    return { dock, folds: this.folds.snapshot(), closed: Object.fromEntries(this.closed), ...(heights ? { heights } : {}) };
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

  /** Make a docked window taller or shorter from the keyboard, moving the windows below it. */
  resizeHeight(group: DockviewGroupPanel, delta: number): void {
    const api = this.api;
    if (!api || group.api.location.type !== "grid") return;
    const canvas = this.canvas;
    if (!canvas) {
      group.api.setSize({ height: group.height + delta });
      return;
    }
    canvas.capture();
    const targets: Heights = {};
    for (const g of rowOf(api, group)) {
      const id = g.activePanel?.id;
      if (id) targets[id] = Math.round(g.height) + delta;
    }
    canvas.apply(targets);
  }

  move(group: DockviewGroupPanel, dir: Direction): boolean {
    const api = this.api;
    if (!api) return false;
    let moved = false;
    this.keepHeights(() => { moved = moveWindow(api, group, dir); });
    return moved;
  }

  toggleFold(group: DockviewGroupPanel): void {
    const api = this.api;
    const id = group.activePanel?.id;
    if (!api || !id) return;
    // Folding gives the window's height back to the page; unfolding takes its own height again.
    const restore = this.folds.collapsedHeight(id) === undefined ? undefined : this.canvas?.intended[id] ?? this.folds.collapsedHeight(id);
    this.keepHeights(() => toggleFold(api, this.folds, group), restore ? { [id]: restore } : {});
  }

  close(group: DockviewGroupPanel): void {
    const api = this.api;
    const panel = group.activePanel;
    if (!api || !panel) return;
    if (!this.transient.has(panel.id)) {
      const docked = group.api.location.type === "grid";
      const slot = docked ? slotOf(api, group) : this.floatedFrom.get(panel.id) ?? null;
      const height = docked ? (this.canvas?.intended[panel.id] ?? this.folds.collapsedHeight(panel.id) ?? Math.round(group.height)) : slot?.height;
      this.closed.set(panel.id, slot ? { ...slot, ...(height ? { height } : {}) } : null);
    }
    this.floatedFrom.delete(panel.id);
    if (this.folds.isFolded(panel.id)) this.folds.setFolded(panel.id, false);
    this.folds.setCollapsed(panel.id, undefined);
    this.keepHeights(() => panel.api.close());
    if (this.transient.has(panel.id)) this.onTransientClose?.(panel.id);
    this.changed();
  }

  /** Closed windows that can be reopened, in default order. */
  closedWindows(): string[] {
    return this.windows.filter((id) => this.closed.has(id) && this.availability(id) !== false);
  }

  reopen(id: string): void {
    const api = this.api;
    if (!api || api.getPanel(id) || !this.windows.includes(id) || this.availability(id) === false) return;
    const slot = this.closed.get(id) ?? null;
    this.closed.delete(id);
    const anchor = slot ? api.getPanel(slot.neighbor) : undefined;
    this.keepHeights(() => {
      if (anchor && anchor.group.api.location.type === "grid") {
        this.add(id, { referencePanel: anchor, direction: DIRECTION_OF[slot!.side] });
      } else {
        // No remembered neighbour: full width along the bottom of the workspace.
        this.add(id, { direction: "below" });
      }
    }, { [id]: validHeight(slot?.height) ?? this.extras.heights?.[id] ?? REOPEN_HEIGHT });
    this.changed();
  }

  /** Make a window visible: reopen it if closed, unfold it if folded. */
  ensureOpen(id: string): void {
    const api = this.api;
    if (!api || !this.windows.includes(id)) return;
    if (!api.getPanel(id)) this.reopen(id);
    const group = api.getPanel(id)?.group;
    if (group && this.folds.isFolded(id)) this.toggleFold(group);
  }

  float(group: DockviewGroupPanel): void {
    const api = this.api;
    const panel = group.activePanel;
    if (!api || !panel || group.api.location.type !== "grid") return;
    const slot = slotOf(api, group);
    this.floatedFrom.set(panel.id, slot ? { ...slot, height: this.canvas?.intended[panel.id] ?? Math.round(group.height) } : null);
    const b = FLOAT_BOUNDS;
    // Floating windows sit on the canvas, so open this one where the workspace is scrolled to.
    const top = (this.canvas?.host.scrollTop ?? 0) + b.top;
    const width = Math.min(b.maxWidth, Math.max(b.minWidth, group.width));
    const height = Math.min(b.maxHeight, Math.max(b.minHeight, group.height));
    this.keepHeights(() => api.addFloatingGroup(group, { position: { left: b.left, top }, width, height }));
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
    this.keepHeights(() => {
      if (anchor && anchor.api.location.type === "grid") {
        group.api.moveTo({ group: anchor, position: slot!.side });
      } else {
        const target = largestGridGroup(api, group);
        if (target) group.api.moveTo({ group: target, position: "right" });
      }
    }, { [panel.id]: validHeight(slot?.height) ?? REOPEN_HEIGHT });
    this.changed();
  }

  /** Add a shell-managed window above everything, full width and compact, if it is not open. */
  showTransient(id: string): void {
    const api = this.api;
    if (!api || api.getPanel(id)) return;
    const height = Math.min(TRANSIENT_HEIGHT, Math.max((this.canvas?.visibleHeight() || api.height) / 3, 140));
    if (this.canvas) {
      this.keepHeights(() => this.add(id, { direction: "above" }), { [id]: height });
    } else {
      this.add(id, { direction: "above" });
      api.getPanel(id)?.group.api.setSize({ height });
    }
  }

  hideTransient(id: string): void {
    const panel = this.api?.getPanel(id);
    if (panel) this.keepHeights(() => panel.api.close());
  }

  /** Maximize a window over the visible part of the workspace, remembering the canvas to return to. */
  maximize(group: DockviewGroupPanel): void {
    const api = this.api;
    if (!api || group.api.isMaximized()) return;
    this.canvas?.capture();
    this.maximizedHere = true;
    group.api.maximize();
    if (this.canvas) {
      this.canvas.setCanvas(0);
      this.canvas.host.scrollTop = 0;
    }
  }

  restoreMaximized(group?: DockviewGroupPanel): void {
    if (group) group.api.exitMaximized();
    else this.api?.exitMaximizedGroup();
  }

  /** After Dockview leaves maximized mode, however that happened, return to the remembered canvas. */
  maximizedChanged(): void {
    const api = this.api;
    if (!api || api.hasMaximizedGroup() || !this.maximizedHere) return;
    this.maximizedHere = false;
    this.canvas?.apply();
  }

  /** Before a window starts moving, record every docked window's height. */
  dragStarted(): void {
    this.canvas?.capture();
  }
  /** After a drop, lay every docked window out at its intended height again. */
  layoutSettled(): void {
    if (!this.canvas || this.api?.hasMaximizedGroup()) return;
    this.canvas.prune();
    this.canvas.apply();
  }
}
