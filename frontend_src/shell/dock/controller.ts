/**
 * One workspace's window state. A workspace holds up to four Dockview
 * instances ("hosts"): the scrolling grid, a pinned band along the top and one
 * along the bottom that do not scroll, and a layer of floating windows that
 * stays fixed on screen. Window content belongs to the workspace, not to a
 * host, so moving a window between hosts keeps its drafts and scroll position.
 * The controller also remembers folded windows and where closed, floated or
 * pinned windows came from. Every change keeps the other windows' heights; a
 * host's canvas grows or shrinks instead.
 */

import type { AddPanelOptions, DockviewApi, DockviewGroupPanel, SerializedDockview } from "dockview-react";
import { FoldStore, foldWindow, reapplyCollapsed, toggleFold, type FoldSnapshot } from "./fold";
import { TableSettingsStore, type TableSettings } from "../../ui/table/settings";
import { largestGridGroup, moveWindow, rowOf, slotOf, type Direction, type Side, type Slot } from "./geometry";
import { sanitizeLayout } from "./sanitize";
import { leafOrder, type Heights, type WorkspaceCanvas } from "./scroll";

/** The Dockview instances a workspace's windows can live in. */
export type HostId = "main" | "top" | "bottom" | "float";
/** The pinned bands, which do not scroll. */
export type Band = "top" | "bottom";
const BANDS: readonly Band[] = ["top", "bottom"];
const HOST_ORDER: readonly HostId[] = ["top", "main", "bottom", "float"];
/** Bands are laid out before the grid, since their heights decide how much of the grid is visible. */
const LAYOUT_ORDER: readonly HostId[] = ["top", "bottom", "main", "float"];

/** A pinned band's saved arrangement. */
export interface BandLayout {
  dock: SerializedDockview;
  heights?: Record<string, number>;
}

/** What a workspace saves: Dockview's own layouts plus Phoenix's per-window state. */
export interface WorkspaceLayout {
  dock: SerializedDockview;
  folds: FoldSnapshot;
  /** Closed windows, each with the place to reopen it. */
  closed: Record<string, Slot | null>;
  /** Each docked window's intended height; the workspace scrolls when they need more than the screen. */
  heights?: Record<string, number>;
  /** Windows pinned along the top or bottom of the workspace. */
  bands?: Partial<Record<Band, BandLayout>>;
  /** Floating windows, fixed on screen. */
  floats?: SerializedDockview;
  /** Column order, widths, hidden columns and sort of the workspace's tables, by table id. */
  tables?: Record<string, TableSettings>;
  /** The workspace's layout version when saved; an arrangement saved under another version loads the default. */
  version?: number;
}

interface Host {
  api: DockviewApi;
  canvas: WorkspaceCanvas | null;
}

const EMPTY: ReadonlySet<string> = new Set();

const DIRECTION_OF: Record<Side, "left" | "right" | "above" | "below"> = { left: "left", right: "right", top: "above", bottom: "below" };

/** Height a shell-managed window opens at, so it does not take half the workspace. */
const TRANSIENT_HEIGHT = 240;
/** Height a reopened window gets when its earlier height is unknown. */
const REOPEN_HEIGHT = 360;
/** Height a window gets when it is pinned without one of its own, and the most a newly pinned window takes. */
const PIN_HEIGHT = 240;
const PIN_MAX_HEIGHT = 320;

const validHeight = (h: unknown): number | undefined => (typeof h === "number" && Number.isFinite(h) && h > 0 && h < 100000 ? h : undefined);
const isBand = (host: unknown): host is Band => host === "top" || host === "bottom";

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
  /** Windows the default arrangement pins along the top or bottom. */
  pinned?: Readonly<Partial<Record<Band, readonly string[]>>>;
  /** Windows the default arrangement leaves closed, with where each opens. */
  closedAt?: Readonly<Record<string, Slot>>;
  /** Raised when the default arrangement changes so much that older saved ones should not load. */
  layoutVersion?: number;
}

export class WorkspaceController {
  readonly folds = new FoldStore();
  /** The workspace's table settings, saved with its arrangement. */
  readonly tables = new TableSettingsStore();
  /** Called when the user closes a shell-managed window. */
  onTransientClose: ((id: string) => void) | null = null;
  /** Called when the user closes or reopens a window (not when the shell does). */
  onWindowToggle: ((id: string, open: boolean) => void) | null = null;
  /** The window that last had focus in this workspace. */
  activeWindow: string | null = null;
  /** Whether a conditional window is allowed now: true, false, or null while unknown. */
  availability: (id: string) => boolean | null = () => true;
  private hosts: Partial<Record<HostId, Host>> = {};
  /** Whether a window is maximized over the visible area by this workspace. */
  private maximizedHere = false;
  private floatedFrom = new Map<string, Slot | null>();
  private pinnedFrom = new Map<string, Slot | null>();
  private closed = new Map<string, Slot | null>();
  /** Windows the shell keeps open or closed whatever the arrangement says (Help follows Show Help Bar). */
  private wanted = new Map<string, boolean>();
  /** Each window's content element, which moves between hosts with the window. */
  private contents = new Map<string, HTMLElement>();
  private listeners = new Set<() => void>();
  private version = 0;
  private quiet = 0;

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

  /** The scrolling grid's Dockview instance. */
  get api(): DockviewApi | null { return this.hosts.main?.api ?? null; }
  set api(api: DockviewApi | null) { this.attach("main", api, this.hosts.main?.canvas ?? null); }
  /** The scrolling grid's canvas. */
  get canvas(): WorkspaceCanvas | null { return this.hosts.main?.canvas ?? null; }
  set canvas(canvas: WorkspaceCanvas | null) { if (this.hosts.main) this.hosts.main.canvas = canvas; }

  /** Register a host's Dockview instance and canvas, or remove it with null. */
  attach(host: HostId, api: DockviewApi | null, canvas: WorkspaceCanvas | null = null): void {
    if (api) this.hosts[host] = { api, canvas };
    else delete this.hosts[host];
  }
  apiOf(host: HostId): DockviewApi | null { return this.hosts[host]?.api ?? null; }
  canvasOf(host: HostId): WorkspaceCanvas | null { return this.hosts[host]?.canvas ?? null; }
  private attached(order: readonly HostId[] = HOST_ORDER): Array<[HostId, Host]> {
    return order.flatMap((id) => (this.hosts[id] ? [[id, this.hosts[id]!] as [HostId, Host]] : []));
  }

  /** The host holding a window, or null when it is closed. */
  hostOf(windowId: string): HostId | null {
    for (const [id, host] of this.attached()) if (host.api.getPanel(windowId)) return id;
    return null;
  }
  /** The host a window frame belongs to. */
  hostOfGroup(group: DockviewGroupPanel): HostId | null {
    for (const [id, host] of this.attached()) if (host.api.groups.includes(group)) return id;
    return null;
  }
  /** The window frame around an element, in any host. */
  groupContaining(element: Element): DockviewGroupPanel | null {
    for (const [, host] of this.attached()) {
      const group = host.api.groups.find((g) => g.element.contains(element));
      if (group) return group;
    }
    return null;
  }
  /** A window's frame, in whichever host holds it. */
  groupOf(windowId: string): DockviewGroupPanel | null {
    for (const [, host] of this.attached()) {
      const panel = host.api.getPanel(windowId);
      if (panel) return panel.group;
    }
    return null;
  }

  /** The element a window's content is drawn into; it moves between hosts with the window. */
  contentElement(windowId: string): HTMLElement {
    let element = this.contents.get(windowId);
    if (!element) {
      element = document.createElement("div");
      element.className = "phx-window-content";
      this.contents.set(windowId, element);
    }
    return element;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getVersion = (): number => this.version;
  changed(): void {
    if (this.quiet) return;
    this.version++;
    for (const listener of this.listeners) listener();
  }
  /** Run several changes as one, so listeners see only the result. */
  private batch(change: () => void): void {
    this.quiet++;
    try { change(); } finally { this.quiet--; }
    this.changed();
  }

  private panelOptions(id: string) {
    return { id, component: id, title: this.titleOf(id) };
  }
  private add = (id: string, position?: AddPanelOptions["position"]): void => {
    this.api?.addPanel({ ...this.panelOptions(id), position });
  };

  /**
   * Run a change, then lay out every host at its windows' intended heights
   * (plus any `extra` ones), so pages and bands grow or shrink instead of
   * squeezing a neighbour.
   */
  private keepHeights(change: () => void, extra: Heights = {}): void {
    const hosts = this.attached().filter(([, h]) => h.canvas);
    for (const [, h] of hosts) h.canvas!.capture();
    change();
    for (const [, h] of this.attached(LAYOUT_ORDER)) {
      const canvas = h.canvas;
      if (!canvas) continue;
      canvas.prune();
      const mine = Object.fromEntries(Object.entries(extra).filter(([id]) => h.api.getPanel(id)));
      canvas.apply(mine);
    }
  }

  /**
   * Load a saved layout, or the default arrangement when it is missing or
   * unusable. It also replaces a live arrangement (Restore My Default): the
   * bands and the floating layer are emptied first, so no window is ever in
   * two places, and each window's content stays mounted throughout.
   */
  load(saved: WorkspaceLayout | undefined): void {
    const api = this.api;
    if (!api) return;
    const known = new Set(this.windows);
    const current = (saved?.version ?? 0) === (this.extras.layoutVersion ?? 0);
    const dock = saved && current ? sanitizeLayout(saved.dock, known) : null;
    if (dock) {
      try {
        this.batch(() => {
          const keep = [...this.transient].filter((id) => api.getPanel(id));
          for (const host of ["top", "bottom", "float"] as const) {
            const other = this.apiOf(host);
            if (other?.panels.length) other.clear();
            const canvas = this.canvasOf(host);
            if (canvas) canvas.intended = {};
          }
          this.floatedFrom.clear();
          this.pinnedFrom.clear();
          this.maximizedHere = false;
          const used = new Set(Object.keys(dock.panels));
          this.restoreHeights("main", saved?.heights, known);
          api.fromJSON(dock, { reuseExistingPanels: true });
          for (const band of BANDS) this.loadHost(band, saved?.bands?.[band]?.dock, saved?.bands?.[band]?.heights, known, used);
          this.loadHost("float", saved?.floats, undefined, known, used);
          this.closed = new Map(Object.entries(saved?.closed ?? {}).filter(([id]) => known.has(id) && !used.has(id)));
          this.folds.restore(saved?.folds, (id) => used.has(id));
          this.tables.restore(saved?.tables);
          for (const [, host] of this.attached()) reapplyCollapsed(host.api, this.folds);
          this.moveFloatsToLayer();
          // Needs Attention stays when an arrangement is restored over it.
          for (const id of keep) if (!api.getPanel(id)) this.add(id, { direction: "above" });
          for (const [, host] of this.attached(LAYOUT_ORDER)) host.canvas?.apply();
          this.retitle();
          this.syncAvailable();
          this.applyWanted();
        });
        return;
      } catch {
        // A layout Dockview cannot load falls through to the default arrangement.
      }
    }
    this.restoreOriginal();
  }

  private restoreHeights(host: HostId, heights: Record<string, number> | undefined, known: ReadonlySet<string>): void {
    const canvas = this.canvasOf(host);
    if (!canvas) return;
    canvas.intended = Object.fromEntries(Object.entries(heights ?? {}).filter(([id, h]) => known.has(id) && validHeight(h) !== undefined));
    canvas.setCanvas(0);
  }

  /** Load one band or the float layer, leaving out windows another host already holds. */
  private loadHost(host: HostId, json: SerializedDockview | undefined, heights: Record<string, number> | undefined, known: ReadonlySet<string>, used: Set<string>): void {
    const api = this.apiOf(host);
    if (!api || !json) return;
    const allowed = new Set([...known].filter((id) => !used.has(id)));
    const clean = sanitizeLayout(json, allowed);
    if (!clean || !Object.keys(clean.panels).length) return;
    this.restoreHeights(host, heights, known);
    api.fromJSON(clean, { reuseExistingPanels: true });
    for (const id of Object.keys(clean.panels)) used.add(id);
  }

  /** Floating windows in the scrolling grid, from older saves, move to the fixed layer. */
  private moveFloatsToLayer(): void {
    const main = this.api;
    if (!main || !this.apiOf("float")) return;
    for (const group of main.groups.filter((g) => g.api.location.type === "floating")) {
      const id = group.activePanel?.id;
      if (id) this.transfer(id, "float");
    }
  }

  /** Reset this workspace to its default arrangement. */
  restoreOriginal(): void {
    const api = this.api;
    if (!api) return;
    this.batch(() => {
      const keep = [...this.transient].filter((id) => api.getPanel(id));
      for (const [, host] of this.attached()) host.api.clear();
      this.folds.restore(undefined, () => false);
      this.tables.restore(undefined);
      this.closed.clear();
      this.floatedFrom.clear();
      this.pinnedFrom.clear();
      this.maximizedHere = false;
      for (const [, host] of this.attached()) {
        if (!host.canvas) continue;
        host.canvas.intended = {};
        host.canvas.setCanvas(0);
      }
      this.arrange(api, this.add);
      for (const [id, slot] of Object.entries(this.extras.closedAt ?? {})) if (!api.getPanel(id)) this.closed.set(id, slot);
      this.canvas?.apply({ ...this.extras.heights });
      for (const band of BANDS) {
        for (const id of this.extras.pinned?.[band] ?? []) {
          const group = api.getPanel(id)?.group;
          if (group) this.pin(group, band);
        }
      }
      // Pinning recorded the grid's heights mid-arrangement; keep only the arrangement's own,
      // so windows without one still share the space left over.
      if (this.canvas) {
        this.canvas.intended = Object.fromEntries(Object.entries(this.extras.heights ?? {}).filter(([id]) => api.getPanel(id)));
        this.canvas.apply();
      }
      for (const id of this.extras.folded ?? []) this.foldById(id);
      for (const id of keep) this.keepHeights(() => this.add(id, { direction: "above" }), { [id]: TRANSIENT_HEIGHT });
      this.syncAvailable();
      this.applyWanted();
      // Bands emptied above are measured again, so an empty band takes no space.
      for (const band of BANDS) this.canvasOf(band)?.apply();
    });
  }

  /** Keep a window open or closed whatever the arrangement says; undefined lets the arrangement decide. */
  want(id: string, open: boolean | undefined): void {
    if (open === undefined) this.wanted.delete(id);
    else this.wanted.set(id, open);
    if (this.api) this.batch(() => this.applyWanted());
  }

  private applyWanted(): void {
    for (const [id, open] of this.wanted) {
      const group = this.groupOf(id);
      if (open && !group) this.place(id);
      else if (!open && group) this.closeGroup(group);
    }
  }

  private foldById(id: string): void {
    const group = this.groupOf(id);
    const host = group && this.hostOfGroup(group);
    const api = host && this.apiOf(host);
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
      const host = this.hostOf(id);
      const panel = host ? this.apiOf(host)?.getPanel(id) : undefined;
      if (!allowed && panel) {
        if (this.folds.isFolded(id)) this.folds.setFolded(id, false);
        this.folds.setCollapsed(id, undefined);
        this.floatedFrom.delete(id);
        this.pinnedFrom.delete(id);
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
    const tables = this.tables.snapshot();
    const version = this.extras.layoutVersion;
    const out: WorkspaceLayout = { dock, folds: this.folds.snapshot(), closed: Object.fromEntries(this.closed), ...(heights ? { heights } : {}), ...(tables ? { tables } : {}), ...(version ? { version } : {}) };
    const bands: Partial<Record<Band, BandLayout>> = {};
    for (const band of BANDS) {
      const bandApi = this.apiOf(band);
      if (!bandApi?.panels.length) continue;
      const json = sanitizeLayout(bandApi.toJSON(), saved);
      const canvas = this.canvasOf(band);
      if (json) bands[band] = { dock: json, ...(canvas ? { heights: { ...canvas.intended } } : {}) };
    }
    if (Object.keys(bands).length) out.bands = bands;
    const floatApi = this.apiOf("float");
    if (floatApi?.panels.length) {
      const json = sanitizeLayout(floatApi.toJSON(), saved);
      if (json) out.floats = json;
    }
    return out;
  }

  /** Record which window has focus; the toolbar's tools follow it. */
  setActiveWindow(id: string | null): void {
    if (id === this.activeWindow) return;
    this.activeWindow = id;
    this.changed();
  }

  /** Open windows in reading order: the top band, the grid, the bottom band, then floating windows. */
  windowOrder(): string[] {
    const out: string[] = [];
    for (const [, host] of this.attached()) {
      try {
        const json = host.api.toJSON();
        out.push(...leafOrder(json.grid.root));
        for (const f of json.floatingGroups ?? []) out.push(...(f.data?.views ?? []));
      } catch {
        out.push(...host.api.panels.map((p) => p.id));
      }
    }
    return [...new Set(out)];
  }

  /** Re-read every title, after a language change. */
  retitle(): void {
    for (const [, host] of this.attached()) {
      for (const panel of host.api.panels) panel.api.setTitle(this.titleOf(panel.id));
    }
  }

  /** Make a docked or pinned window taller or shorter from the keyboard, moving the windows below it. */
  resizeHeight(group: DockviewGroupPanel, delta: number): void {
    const host = this.hostOfGroup(group);
    const api = host && this.apiOf(host);
    if (!api || group.api.location.type !== "grid") return;
    const canvas = this.canvasOf(host!);
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

  /** Swap a window with its neighbour in the same host. */
  move(group: DockviewGroupPanel, dir: Direction): boolean {
    const host = this.hostOfGroup(group);
    const api = host && this.apiOf(host);
    if (!api) return false;
    let moved = false;
    this.keepHeights(() => { moved = moveWindow(api, group, dir); });
    return moved;
  }

  toggleFold(group: DockviewGroupPanel): void {
    const host = this.hostOfGroup(group);
    const api = host && this.apiOf(host);
    const id = group.activePanel?.id;
    if (!api || !id) return;
    // Folding gives the window's height back to the page; unfolding takes its own height again.
    const restore = this.folds.collapsedHeight(id) === undefined ? undefined : this.canvasOf(host!)?.intended[id] ?? this.folds.collapsedHeight(id);
    this.keepHeights(() => toggleFold(api, this.folds, group), restore ? { [id]: restore } : {});
  }

  /** Where a window sits now, to return it there later. */
  private placeOf(id: string, group: DockviewGroupPanel): Slot | null {
    const host = this.hostOfGroup(group);
    if (host === "float") return this.floatedFrom.get(id) ?? null;
    const api = host && this.apiOf(host);
    if (!api) return null;
    const height = this.canvasOf(host!)?.intended[id] ?? this.folds.collapsedHeight(id) ?? Math.round(group.height);
    if (isBand(host)) return { neighbor: "", side: "bottom", host, ...(height ? { height } : {}) };
    if (group.api.location.type !== "grid") return this.floatedFrom.get(id) ?? null;
    // Without a neighbour the window comes back along the bottom, still at its own height.
    const slot = slotOf(api, group) ?? { neighbor: "", side: "bottom" as const };
    return { ...slot, ...(height ? { height } : {}) };
  }

  close(group: DockviewGroupPanel): void {
    const id = this.closeGroup(group);
    if (id && !this.transient.has(id)) this.onWindowToggle?.(id, false);
  }

  /** Close a window, remembering where it was; returns its id. */
  private closeGroup(group: DockviewGroupPanel): string | null {
    const panel = group.activePanel;
    if (!panel || !this.hostOfGroup(group)) return null;
    if (!this.transient.has(panel.id)) {
      const place = this.placeOf(panel.id, group);
      const floating = this.hostOfGroup(group) === "float";
      this.closed.set(panel.id, floating ? { ...(place ?? { neighbor: "", side: "bottom" }), host: "float" } : place);
    }
    this.floatedFrom.delete(panel.id);
    this.pinnedFrom.delete(panel.id);
    if (this.folds.isFolded(panel.id)) this.folds.setFolded(panel.id, false);
    this.folds.setCollapsed(panel.id, undefined);
    this.keepHeights(() => panel.api.close());
    if (this.transient.has(panel.id)) this.onTransientClose?.(panel.id);
    this.changed();
    return panel.id;
  }

  /**
   * Closed windows that can be reopened, in default order. A window the
   * arrangement leaves out, or that an older saved arrangement did not know,
   * counts as closed; a conditional one only once the user closed it.
   */
  closedWindows(): string[] {
    const live = !!this.api;
    return this.windows.filter((id) => {
      if (this.transient.has(id) || this.availability(id) === false) return false;
      if (this.closed.has(id)) return !live || !this.hostOf(id);
      return live && !this.hostOf(id) && !(id in this.conditional);
    });
  }

  reopen(id: string): void {
    if (!this.place(id)) return;
    this.reveal(id);
    this.onWindowToggle?.(id, true);
  }

  /**
   * Put a closed window back where it was closed from; without a remembered
   * place, in the band the default arrangement pins it to, or else along the
   * bottom of the grid. False when it cannot be placed.
   */
  private place(id: string): boolean {
    const api = this.api;
    if (!api || this.hostOf(id) || !this.windows.includes(id) || this.availability(id) === false) return false;
    const slot = this.closed.get(id) ?? null;
    this.closed.delete(id);
    const height = validHeight(slot?.height) ?? this.extras.heights?.[id] ?? REOPEN_HEIGHT;
    const band = isBand(slot?.host) ? slot!.host as Band : slot ? undefined : BANDS.find((b) => this.extras.pinned?.[b]?.includes(id));
    this.batch(() => {
      if (band && this.apiOf(band)) {
        this.keepHeights(() => this.addToBand(id, band), { [id]: height });
        return;
      }
      if (slot?.host === "float" && this.apiOf("float")) {
        this.floatedFrom.set(id, slot.neighbor ? { neighbor: slot.neighbor, side: slot.side, height } : null);
        this.addFloating(id, FLOAT_BOUNDS.minWidth + 120, Math.min(FLOAT_BOUNDS.maxHeight, height));
        return;
      }
      const anchor = slot?.neighbor ? api.getPanel(slot.neighbor) : undefined;
      this.keepHeights(() => {
        if (anchor && anchor.group.api.location.type === "grid") {
          this.add(id, { referencePanel: anchor, direction: DIRECTION_OF[slot!.side] });
        } else {
          // No remembered neighbour: full width along the bottom of the workspace.
          this.add(id, { direction: "below" });
        }
      }, { [id]: height });
    });
    return true;
  }

  /** Scroll the workspace so a window just placed in the grid is in view. */
  private reveal(id: string): void {
    if (typeof requestAnimationFrame === "undefined") return;
    requestAnimationFrame(() => {
      const group = this.api?.getPanel(id)?.group;
      if (group?.api.location.type === "grid") group.element.scrollIntoView?.({ block: "nearest" });
    });
  }

  /** Make a window visible: reopen it if closed, unfold it if folded. */
  ensureOpen(id: string): void {
    if (!this.api || !this.windows.includes(id)) return;
    if (!this.hostOf(id)) this.reopen(id);
    const group = this.groupOf(id);
    if (group && this.folds.isFolded(id)) this.toggleFold(group);
  }

  /**
   * Move a window to another host, keeping its content. `place` adds it there;
   * by default it is appended to a band, floated, or added along the bottom of the grid.
   */
  private transfer(id: string, to: HostId, place?: (api: DockviewApi) => void): void {
    const from = this.hostOf(id);
    const fromApi = from && this.apiOf(from);
    const toApi = this.apiOf(to);
    const panel = fromApi?.getPanel(id);
    if (!fromApi || !toApi || !panel || from === to) return;
    this.batch(() => {
      fromApi.removePanel(panel);
      if (place) place(toApi);
      else if (isBand(to)) this.addToBand(id, to);
      else if (to === "float") this.addFloating(id, FLOAT_BOUNDS.minWidth + 120, FLOAT_BOUNDS.minHeight + 80);
      else toApi.addPanel({ ...this.panelOptions(id), position: { direction: "below" } });
    });
  }

  private addToBand(id: string, band: Band): void {
    const api = this.apiOf(band);
    if (!api) return;
    // A band stacks its windows; the bottom band's newest window sits at the bottom.
    if (api.panels.length) api.addPanel({ ...this.panelOptions(id), position: { direction: band === "top" ? "below" : "above" } });
    else api.addPanel(this.panelOptions(id));
  }

  private addFloating(id: string, width: number, height: number): void {
    const b = FLOAT_BOUNDS;
    this.apiOf("float")?.addPanel({
      ...this.panelOptions(id),
      floating: {
        position: { left: b.left, top: b.top },
        width: Math.min(b.maxWidth, Math.max(b.minWidth, Math.round(width))),
        height: Math.min(b.maxHeight, Math.max(b.minHeight, Math.round(height))),
      },
    });
  }

  float(group: DockviewGroupPanel): void {
    const panel = group.activePanel;
    const host = this.hostOfGroup(group);
    if (!panel || !host || host === "float" || group.api.location.type !== "grid") return;
    const id = panel.id;
    this.floatedFrom.set(id, this.placeOf(id, group));
    const width = group.width;
    const height = group.height;
    if (this.apiOf("float")) {
      this.keepHeights(() => this.transfer(id, "float", () => this.addFloating(id, width, height)));
    } else {
      // Without a fixed layer the window floats over the grid, scrolling with it.
      const api = this.apiOf(host)!;
      const b = FLOAT_BOUNDS;
      const top = (this.canvasOf(host)?.host.scrollTop ?? 0) + b.top;
      this.keepHeights(() => api.addFloatingGroup(group, {
        position: { left: b.left, top },
        width: Math.min(b.maxWidth, Math.max(b.minWidth, width)),
        height: Math.min(b.maxHeight, Math.max(b.minHeight, height)),
      }));
    }
    this.changed();
  }

  /** Return a floating window to where it was floated from, or beside the largest window. */
  dock(group: DockviewGroupPanel): void {
    const panel = group.activePanel;
    const host = this.hostOfGroup(group);
    const api = this.api;
    if (!api || !panel || !host || group.api.location.type !== "floating") return;
    const id = panel.id;
    const slot = this.floatedFrom.get(id) ?? null;
    this.floatedFrom.delete(id);
    const height = validHeight(slot?.height) ?? REOPEN_HEIGHT;
    if (host === "float") {
      if (isBand(slot?.host) && this.apiOf(slot!.host as Band)) {
        this.keepHeights(() => this.transfer(id, slot!.host as Band), { [id]: height });
      } else {
        const anchor = slot?.neighbor ? api.getPanel(slot.neighbor) : undefined;
        this.keepHeights(() => this.transfer(id, "main", (main) => {
          if (anchor && anchor.group.api.location.type === "grid") main.addPanel({ ...this.panelOptions(id), position: { referencePanel: anchor, direction: DIRECTION_OF[slot!.side] } });
          else main.addPanel({ ...this.panelOptions(id), position: { direction: "below" } });
        }), { [id]: height });
      }
      this.changed();
      return;
    }
    const anchor = slot ? api.getPanel(slot.neighbor)?.group : undefined;
    this.keepHeights(() => {
      if (anchor && anchor.api.location.type === "grid") {
        group.api.moveTo({ group: anchor, position: slot!.side });
      } else {
        const target = largestGridGroup(api, group);
        if (target) group.api.moveTo({ group: target, position: "right" });
      }
    }, { [id]: height });
    this.changed();
  }

  /** Pin a window along the top or bottom of the workspace, where it does not scroll. */
  pin(group: DockviewGroupPanel, band: Band): void {
    const panel = group.activePanel;
    const host = this.hostOfGroup(group);
    if (!panel || !host || host === band || !this.apiOf(band)) return;
    const id = panel.id;
    const place = this.placeOf(id, group);
    if (host === "main") this.pinnedFrom.set(id, place);
    else if (host === "float") this.pinnedFrom.set(id, this.floatedFrom.get(id) ?? null);
    else this.pinnedFrom.set(id, this.pinnedFrom.get(id) ?? null);
    this.floatedFrom.delete(id);
    const height = this.canvasOf(host)?.intended[id] ?? (host === "main" ? undefined : Math.round(group.height));
    this.keepHeights(() => this.transfer(id, band), { [id]: Math.min(height ?? PIN_HEIGHT, PIN_MAX_HEIGHT) });
    this.changed();
  }

  /** Return a pinned window to the scrolling grid, where it came from. */
  unpin(group: DockviewGroupPanel): void {
    const panel = group.activePanel;
    const host = this.hostOfGroup(group);
    const api = this.api;
    if (!api || !panel || !isBand(host)) return;
    const id = panel.id;
    const slot = this.pinnedFrom.get(id) ?? null;
    this.pinnedFrom.delete(id);
    const height = validHeight(slot?.height) ?? this.canvasOf(host)?.intended[id] ?? REOPEN_HEIGHT;
    const anchor = slot?.neighbor ? api.getPanel(slot.neighbor) : undefined;
    this.keepHeights(() => this.transfer(id, "main", (main) => {
      if (anchor && anchor.group.api.location.type === "grid") main.addPanel({ ...this.panelOptions(id), position: { referencePanel: anchor, direction: DIRECTION_OF[slot!.side] } });
      else main.addPanel({ ...this.panelOptions(id), position: { direction: host === "top" ? "above" : "below" } });
    }), { [id]: height });
    this.changed();
  }

  /** Add a shell-managed window above everything, full width and compact, if it is not open. */
  showTransient(id: string): void {
    const api = this.api;
    if (!api || this.hostOf(id)) return;
    const height = Math.min(TRANSIENT_HEIGHT, Math.max((this.canvas?.visibleHeight() || api.height) / 3, 140));
    if (this.canvas) {
      this.keepHeights(() => this.add(id, { direction: "above" }), { [id]: height });
    } else {
      this.add(id, { direction: "above" });
      api.getPanel(id)?.group.api.setSize({ height });
    }
  }

  hideTransient(id: string): void {
    const host = this.hostOf(id);
    const panel = host ? this.apiOf(host)?.getPanel(id) : undefined;
    if (panel) this.keepHeights(() => panel.api.close());
  }

  /** Maximize a window over the visible part of the workspace, remembering the canvas to return to. */
  maximize(group: DockviewGroupPanel): void {
    const api = this.api;
    if (!api || group.api.isMaximized() || this.hostOfGroup(group) !== "main") return;
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
    for (const [, host] of this.attached()) host.canvas?.capture();
  }
  /** After a drop, lay every docked window out at its intended height again. */
  layoutSettled(): void {
    if (this.api?.hasMaximizedGroup()) return;
    for (const [, host] of this.attached()) {
      if (!host.canvas) continue;
      host.canvas.prune();
      host.canvas.apply();
    }
  }
}
