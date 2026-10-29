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
import { FoldStore, MIN_WINDOW_HEIGHT, foldWindow, reapplyCollapsed, settleFolds, toggleFold, type FoldSnapshot } from "./fold";
import { TableSettingsStore, type TableSettings } from "../../ui/table/settings";
import { largestGridGroup, moveWindow, neighbor, rowOf, slotOf, type Direction, type Side, type Slot } from "./geometry";
import { sanitizeLayout } from "./sanitize";
import { applyShares, gridHeights, leafOrder, sideBySide, type Heights, type RowShares, type WorkspaceCanvas } from "./scroll";
import { DisplayStore, type DisplaySnapshot } from "./display";
import { evenRow, leafRows, nextPlace } from "./rows";

/** The first window of each child of a grid's root, in order. */
const leafOrderByChild = (root: unknown): string[] =>
  (Array.isArray((root as { data?: unknown }).data) ? ((root as { data: unknown[] }).data) : []).map((child) => leafOrder(child)[0]).filter((id): id is string => !!id);

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
export interface WorkspaceLayout extends DisplaySnapshot {
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
  /** Where each window last floated, and its size there, for the next time it floats. */
  floatBoxes?: Record<string, FloatBox>;
  /** Windows the user closed, which do not open by themselves until reopened or the workspace is reset. */
  dismissed?: string[];
}

/** A floating window's place and size, relative to the floating layer. */
export interface FloatBox { left: number; top: number; width: number; height: number }

interface Host {
  api: DockviewApi;
  canvas: WorkspaceCanvas | null;
}

const EMPTY: ReadonlySet<string> = new Set();

const DIRECTION_OF: Record<Side, "left" | "right" | "above" | "below"> = { left: "left", right: "right", top: "above", bottom: "below" };

/** Height a shell-managed window opens at, so it does not take half the workspace. */
const TRANSIENT_HEIGHT = 240;
/**
 * How far a measured content height may drift before a window that cannot grow freely
 * follows it. Large enough that settling noise never moves the windows under the
 * pointer, small enough that opening a confirmation inside the window still shows it.
 */
const CONTENT_TOLERANCE = 24;
/** Height a reopened window gets when its earlier height is unknown. */
const REOPEN_HEIGHT = 360;
/** Height a window gets when it is pinned without one of its own, and the most a newly pinned window takes. */
const PIN_HEIGHT = 240;
const PIN_MAX_HEIGHT = 320;

const validHeight = (h: unknown): number | undefined => (typeof h === "number" && Number.isFinite(h) && h > 0 && h < 100000 ? h : undefined);
const isBand = (host: unknown): host is Band => host === "top" || host === "bottom";

/** Size given to a window floated from the grid: its own size within these bounds. */
const FLOAT_BOUNDS = { minWidth: 360, maxWidth: 640, minHeight: 260, maxHeight: 480, left: 80, top: 64 };
/** A window floating onto another's corner moves down and right by this much, so neither hides the other. */
const FLOAT_CASCADE = 28;

const validBox = (b: unknown): b is FloatBox => !!b && typeof b === "object"
  && (["left", "top", "width", "height"] as const).every((k) => Number.isFinite((b as Record<string, unknown>)[k]))
  && (b as FloatBox).width > 0 && (b as FloatBox).height > 0;

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
  /**
   * Windows the user chose for this workspace (My Workspace): one the
   * arrangement leaves out was added since it was saved, so it takes the next
   * place (see `nextPlace`) instead of counting as closed. Other windows
   * (Notifications) keep the usual rule.
   */
  collect?: (id: string) => boolean;
  /** The windows always share the visible height and the workspace never scrolls (Logs). */
  fit?: boolean;
  /** The user chooses whether the workspace scrolls; it fits its windows to the screen until they do (My Workspace). */
  scrollable?: boolean;
  /**
   * Docked windows are as tall as their content, so none scrolls inside; the
   * workspace scrolls instead, and the borders between stacked windows do not
   * drag (Access Keys). `heights` are the sizes before the content is measured.
   */
  contentHeights?: boolean;
  /**
   * Windows that open beside a partner in its pinned band, at its height, rather
   * than along the bottom: { notifications: "help" } puts Notifications to the
   * right of Help, and Help back to its left. Without the partner open, the
   * window takes the bottom band at the partner's height.
   */
  pairs?: Readonly<Record<string, string>>;
}

export class WorkspaceController {
  readonly folds = new FoldStore();
  /** Windows that always show icons and text, and whether the workspace scrolls. */
  readonly display = new DisplayStore();
  /** Where each window last floated, for the next time it floats. */
  private floatBoxes = new Map<string, FloatBox>();
  /** Windows the user closed: autoOpen leaves them closed until they are reopened or the workspace is reset. */
  private dismissed = new Set<string>();
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

  private allowed: readonly string[];
  /** Whether the workspace is on screen. */
  private shown = true;
  /** Windows the user resized or moved this session; sizing them to their content may only make them smaller. */
  private userSized = new Set<string>();
  /** Windows hidden in place, keeping their spot in the arrangement (see `setHiddenWindows`). */
  private hiddenIds: ReadonlySet<string> = new Set();
  /** Each window's content height, while windows follow their content. */
  private natural = new Map<string, number>();
  /** Each host's side-by-side shares from before windows were hidden, given back when they show again. */
  private hiddenShares = new Map<HostId, RowShares[]>();
  private naturalFrame = 0;
  /** The windows changed while the workspace was hidden or not yet laid out. */
  private windowsChanged = false;

  constructor(
    readonly id: string,
    /** The windows this workspace may hold, in default order. */
    windows: readonly string[],
    /** Lay out the default arrangement on an empty instance. */
    private readonly arrange: (api: DockviewApi, add: (id: string, position?: AddPanelOptions["position"]) => void) => void,
    /** Current title for a window id. */
    private readonly titleOf: (id: string) => string,
    private readonly extras: WorkspaceExtras = {},
  ) {
    this.allowed = [...windows];
    this.folds.subscribe(() => this.markFloatingFolds());
  }

  /**
   * A floating window folds to its title bar too. Dockview offers no call to
   * resize a floating window, so its frame is marked and the stylesheet clips
   * it; its own size is kept for when it unfolds.
   */
  private markFloatingFolds(): void {
    for (const group of this.apiOf("float")?.groups ?? []) {
      const id = group.activePanel?.id;
      group.element.closest(".dv-resize-container")?.classList.toggle("phx-float-folded", !!id && this.folds.isFolded(id));
    }
  }

  /** The windows this workspace may hold, in default order. */
  get windows(): readonly string[] { return this.allowed; }

  /** Whether the windows always fit the visible height (the workspace never scrolls). */
  get fits(): boolean { return this.extras.scrollable ? !this.display.scrolls : !!this.extras.fit; }

  /** Whether the user chooses if this workspace scrolls (My Workspace). */
  get scrollable(): boolean { return !!this.extras.scrollable; }

  /** Whether docked windows are as tall as their content (see `WorkspaceExtras.contentHeights`). */
  get contentSized(): boolean { return !!this.extras.contentHeights; }

  /**
   * A window's content now needs `height` pixels, title bar included. While
   * windows follow their content, the docked one gets that height at the next
   * frame; folded, hidden, floating and pinned windows keep theirs.
   */
  contentResized(id: string, height: number): void {
    // Needs Attention is as tall as what it shows in every workspace, not only in one
    // that sizes every window to its content (user, 2026-10-29).
    if (!(this.contentSized || this.transient.has(id)) || !(height > 0)) return;
    const rounded = Math.round(height);
    const known = this.natural.get(id);
    if (known === rounded) return;
    // A workspace that fits the screen cannot always give a window the height it asks
    // for, and the height it does give is measured again: without a tolerance the two
    // chase each other and the workspace never settles.
    if (known !== undefined && !this.contentSized && Math.abs(known - rounded) < CONTENT_TOLERANCE) return;
    this.natural.set(id, rounded);
    if (!this.naturalFrame && typeof requestAnimationFrame === "function") {
      this.naturalFrame = requestAnimationFrame(() => { this.naturalFrame = 0; this.followContent(); });
    }
  }

  private followContent(): void {
    const api = this.api;
    const canvas = this.canvas;
    if (!api || !canvas || !this.shown || api.hasMaximizedGroup() || canvas.host.classList.contains("phx-resizing")) return;
    if (!this.contentSized && !this.natural.size) return;
    const targets: Heights = {};
    for (const [id, h] of this.natural) {
      if (!this.contentSized && !this.transient.has(id)) continue;
      const group = api.getPanel(id)?.group;
      if (!group || this.folds.isFolded(id) || this.hiddenIds.has(id)) continue;
      const target = Math.max(MIN_WINDOW_HEIGHT, h);
      if (Math.abs((canvas.intended[id] ?? 0) - target) >= 2) targets[id] = target;
    }
    if (Object.keys(targets).length) this.batch(() => canvas.apply(targets));
    this.markSlack();
  }

  /**
   * A window taller than its content (the last of the shorter column) ends its
   * frame at the content, so the space left looks like empty workspace rather
   * than an empty window. Sets --phx-slack on each docked window's frame.
   */
  markSlack(): void {
    const api = this.api;
    if (!api || !this.contentSized) return;
    const maximized = api.hasMaximizedGroup();
    for (const group of api.groups) {
      const id = group.activePanel?.id;
      const natural = id ? this.natural.get(id) : undefined;
      const slack = !maximized && natural && id && !this.folds.isFolded(id) && !this.hiddenIds.has(id) ? Math.round(group.height - natural) : 0;
      if (slack > 2) group.element.style.setProperty("--phx-slack", `${slack}px`);
      else group.element.style.removeProperty("--phx-slack");
    }
  }

  /**
   * Hide these windows where they are, or show them again, keeping the
   * arrangement: a hidden window takes no space, and comes back in its place at
   * its height. Access Keys hides the selected key's windows while no key is
   * chosen. A maximized window keeps the screen until it is restored.
   */
  setHiddenWindows(ids: Iterable<string>): void {
    const next = new Set(ids);
    if (next.size === this.hiddenIds.size && [...next].every((id) => this.hiddenIds.has(id))) return;
    this.hiddenIds = next;
    if (this.api) this.batch(() => this.applyHidden());
  }

  /** Whether a window is hidden in place. */
  isHidden(id: string): boolean { return this.hiddenIds.has(id); }

  private applyHidden(): void {
    // Changing a window's visibility would end a maximize; it applies once the window is restored.
    if (this.api?.hasMaximizedGroup()) return;
    for (const [host, h] of this.attached()) {
      let changed = false;
      // Hidden windows side by side would come back at stale widths: remember their shares while they show.
      const showing = host === "float" ? [] : sideBySide(h.api.toJSON().grid).filter((row) => row.members.every((id) => h.api.getPanel(id)?.group.api.isVisible));
      for (const row of showing) this.hiddenShares.set(host, [...(this.hiddenShares.get(host) ?? []).filter((r) => r.members.join() !== row.members.join()), row]);
      for (const group of h.api.groups) {
        const id = group.activePanel?.id;
        if (!id) continue;
        const hide = this.hiddenIds.has(id);
        if (host === "float") {
          group.element.closest(".dv-resize-container")?.classList.toggle("phx-float-hidden", hide);
        } else if (group.api.isVisible === hide) {
          group.api.setVisible(!hide);
          changed = true;
        }
      }
      if (changed) {
        applyShares(h.api, this.hiddenShares.get(host) ?? []);
        h.canvas?.apply();
      }
    }
  }

  /**
   * Let the workspace scroll vertically, or fit every window to the screen at
   * once, shrinking them in proportion. Fitting remembers each window's height
   * first; scrolling gives those heights back, and a window without one its
   * default height (never less than it has), so the workspace grows at once.
   */
  setScrolls(on: boolean): void {
    if (!this.extras.scrollable || on === this.display.scrolls) return;
    const api = this.api;
    const canvas = this.canvas;
    if (!on && api && canvas) {
      canvas.capture();
      const docked = gridHeights(api);
      this.display.setScrollHeights(Object.fromEntries(Object.entries(canvas.intended).filter(([id]) => id in docked && !this.folds.isFolded(id))));
    }
    const roomy = on && api ? this.roomyHeights() : {};
    this.display.setScrolls(on);
    canvas?.setFit(this.fits);
    if (on) this.display.setScrollHeights({});
    if (api) this.batch(() => this.keepHeights(() => {}, roomy));
  }

  /**
   * Heights for a workspace that starts scrolling: each docked window's height
   * from before it was fitted, else the larger of its height now and its
   * default; windows side by side all take the tallest of the row.
   */
  private roomyHeights(): Heights {
    const api = this.api;
    const canvas = this.canvas;
    if (!api || !canvas) return {};
    canvas.capture();
    const saved = this.display.scrollHeights;
    const out: Heights = {};
    for (const [id, now] of Object.entries(gridHeights(api))) {
      if (this.folds.isFolded(id)) continue;
      const own = validHeight(saved[id]);
      out[id] = own ?? Math.max(canvas.intended[id] ?? now, validHeight(this.extras.heights?.[id]) ?? REOPEN_HEIGHT);
    }
    for (const row of leafRows(api)) {
      const ids = row.filter((id) => id in out);
      const tallest = Math.max(0, ...ids.map((id) => out[id]));
      for (const id of ids) out[id] = tallest;
    }
    return out;
  }

  /** Whether the user resized or moved this window this session. */
  isUserSized(id: string): boolean {
    return this.userSized.has(id);
  }

  /** Record that the user resized or moved these windows. */
  markUserSized(ids: Iterable<string>): void {
    for (const id of ids) this.noteUserSized(id);
  }

  /** Called once the user resized or moved a window and its new size is laid out. */
  onUserSized: ((id: string) => void) | null = null;

  private noteUserSized(id: string): void {
    this.userSized.add(id);
    if (this.onUserSized && typeof requestAnimationFrame === "function") requestAnimationFrame(() => this.onUserSized?.(id));
  }

  /** Whether a docked window has another window directly above or below it in its host. */
  isStacked(id: string): boolean {
    const group = this.groupOf(id);
    const host = group && this.hostOfGroup(group);
    const api = host && this.apiOf(host);
    if (!group || !api || group.api.location.type !== "grid") return false;
    return !!(neighbor(api, group, "up") || neighbor(api, group, "down"));
  }

  /** The height the scrolling grid shows; 0 before it is laid out. */
  visibleHeight(): number {
    return this.canvas?.visibleHeight() ?? 0;
  }

  /**
   * Give a docked window the height its content needs. A window the user
   * resized or moved keeps its height unless the content needs less, unless
   * `chosen`: the caller already limited the height to the one the user chose.
   */
  sizeToContent(id: string, height: number, chosen = false): void {
    const group = this.groupOf(id);
    if (!group || group.api.location.type !== "grid" || this.folds.isFolded(id)) return;
    const host = this.hostOfGroup(group);
    const canvas = host ? this.canvasOf(host) : null;
    if (!canvas) return;
    // The user is dragging a border: their size wins.
    if (canvas.host.classList.contains("phx-resizing")) return;
    const current = Math.round(group.height);
    const target = Math.max(MIN_WINDOW_HEIGHT, Math.round(height));
    if (Math.abs(target - current) < 4 || (!chosen && this.userSized.has(id) && target > current)) return;
    this.batch(() => canvas.apply({ [id]: target }));
  }

  /**
   * Change the windows this workspace may hold (My Workspace). A window taken
   * away closes without being remembered as closed; with `collect`, one added
   * opens along the bottom. Both wait until the workspace is on screen: a
   * hidden workspace measures nothing, and laying it out then would collapse
   * its pinned bands.
   */
  setWindows(ids: readonly string[]): void {
    const changed = ids.length !== this.allowed.length || ids.some((id, i) => id !== this.allowed[i]);
    this.allowed = [...ids];
    if (!changed) return;
    if (!this.api || !this.shown) {
      this.windowsChanged = true;
      this.changed();
      return;
    }
    this.batch(() => this.applyWindows());
  }

  /** Whether the workspace is on screen; changes to its windows made while it was hidden apply now. */
  setShown(shown: boolean): void {
    this.shown = shown;
    if (shown && this.contentSized) this.followContent();
    if (!shown || !this.windowsChanged || !this.api) return;
    this.windowsChanged = false;
    this.batch(() => this.applyWindows());
  }

  /** Close the windows no longer held and open the chosen ones that are missing. */
  private applyWindows(): void {
    for (const [, host] of this.attached()) {
      for (const panel of [...host.api.panels]) {
        if (!this.allowed.includes(panel.id) && !this.transient.has(panel.id)) this.forget(panel.id);
      }
    }
    for (const id of [...this.closed.keys()]) if (!this.allowed.includes(id)) this.closed.delete(id);
    for (const id of this.allowed) this.collectOne(id);
  }

  /** Close a window that may no longer be here, without remembering where it was. */
  private forget(id: string): void {
    const host = this.hostOf(id);
    const panel = host ? this.apiOf(host)?.getPanel(id) : undefined;
    if (this.folds.isFolded(id)) this.folds.setFolded(id, false);
    this.folds.setCollapsed(id, undefined);
    this.floatedFrom.delete(id);
    this.pinnedFrom.delete(id);
    if (panel) this.keepHeights(() => panel.api.close());
  }

  /** Open a chosen window the arrangement does not show and the user did not close. */
  private collectOne(id: string): void {
    if (!this.extras.collect?.(id)) return;
    if (this.transient.has(id) || this.wanted.has(id) || this.closed.has(id) || this.hostOf(id)) return;
    if (this.availability(id) !== true) return;
    this.placeChosen(id);
  }

  /**
   * Put a window the user just chose in the next place (see `nextPlace`):
   * floating beside the floating windows when nothing is docked, otherwise
   * beside, under or below the docked windows. A window sharing a row takes
   * its height; a new row takes the comfortable height of the window while the
   * workspace scrolls, and an equal share of the screen while it fits.
   */
  private placeChosen(id: string): void {
    const api = this.api;
    if (!api || this.hostOf(id)) return;
    const float = this.apiOf("float");
    const where = nextPlace(api, !!float?.panels.length, this.transient);
    if (where.kind === "float") {
      if (!float) return;
      const last = float.groups[float.groups.length - 1];
      const box = last ? this.floatBox(last) : null;
      if (box && !this.floatBoxes.has(id)) this.floatBoxes.set(id, { ...box, left: box.left + FLOAT_CASCADE, top: box.top + FLOAT_CASCADE });
      this.batch(() => {
        this.floatedFrom.set(id, null);
        this.addFloating(id, FLOAT_BOUNDS.minWidth + 120, FLOAT_BOUNDS.minHeight + 80);
      });
      return;
    }
    const canvas = this.canvas;
    const comfortable = validHeight(this.extras.heights?.[id]) ?? REOPEN_HEIGHT;
    const heightOf = (other: string) => canvas?.intended[other] ?? (Math.round(api.getPanel(other)?.group.height ?? 0) || comfortable);
    const rows = (() => {
      const grid = api.toJSON().grid;
      return grid.orientation === "VERTICAL" ? Math.max(1, (grid.root.data as unknown[]).length) : 1;
    })();
    this.batch(() => this.keepHeights(() => {
      const sizes: Heights = {};
      switch (where.kind) {
        case "first":
          this.add(id);
          break;
        case "beside":
          this.add(id, { referencePanel: where.of, direction: "right" });
          evenRow(api, id);
          sizes[id] = heightOf(where.of);
          break;
        case "under": {
          const half = Math.max(MIN_WINDOW_HEIGHT, Math.round(heightOf(where.of) / 2));
          this.add(id, { referencePanel: where.of, direction: "below" });
          sizes[where.of] = half;
          sizes[id] = half;
          break;
        }
        case "column":
          sizes[id] = Math.round(api.height) || comfortable;
          this.add(id, { direction: "right" });
          evenRow(api, id);
          break;
        case "row":
          sizes[id] = this.fits ? Math.max(MIN_WINDOW_HEIGHT, Math.round(api.height / rows)) : comfortable;
          this.add(id, { direction: "below" });
          break;
      }
      // Set directly rather than as a fixed height, so a workspace that fits shrinks every window alike.
      if (canvas) Object.assign(canvas.intended, sizes);
    }));
  }

  /** With `collect`, open every chosen window the arrangement left out. */
  private collectAll(): void {
    for (const id of this.allowed) this.collectOne(id);
  }

  private get transient(): ReadonlySet<string> { return this.extras.transient ?? EMPTY; }
  private get conditional(): Readonly<Record<string, Slot>> { return this.extras.conditional ?? {}; }

  /** The scrolling grid's Dockview instance. */
  get api(): DockviewApi | null { return this.hosts.main?.api ?? null; }
  set api(api: DockviewApi | null) { this.attach("main", api, this.hosts.main?.canvas ?? null); }
  /** The scrolling grid's canvas. */
  get canvas(): WorkspaceCanvas | null { return this.hosts.main?.canvas ?? null; }
  set canvas(canvas: WorkspaceCanvas | null) {
    if (canvas) canvas.hidden = (id) => this.hiddenIds.has(id);
    if (this.hosts.main) this.hosts.main.canvas = canvas;
  }

  /** Register a host's Dockview instance and canvas, or remove it with null. */
  attach(host: HostId, api: DockviewApi | null, canvas: WorkspaceCanvas | null = null): void {
    if (canvas) canvas.hidden = (id) => this.hiddenIds.has(id);
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
    // A window floated while folded, or folded in a saved layout, keeps its folded frame.
    this.markFloatingFolds();
    this.version++;
    for (const listener of this.listeners) listener();
  }
  /** Run several changes as one, so listeners see only the result. */
  private batch(change: () => void): void {
    this.quiet++;
    try { change(); } finally { this.quiet--; }
    this.changed();
    // A host can need a scrollbar only part way through a change; once it settles, use the width it leaves.
    if (!this.quiet && typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => { for (const [, host] of this.attached()) host.canvas?.fitWidth(); });
    }
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
    this.applyHidden();
    this.settleAllFolds();
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
          this.display.restore(saved, (id) => known.has(id));
          this.canvas?.setFit(this.fits);
          const used = new Set(Object.keys(dock.panels));
          this.restoreHeights("main", saved?.heights, known);
          api.fromJSON(dock, { reuseExistingPanels: true });
          for (const band of BANDS) this.loadHost(band, saved?.bands?.[band]?.dock, saved?.bands?.[band]?.heights, known, used);
          this.loadHost("float", saved?.floats, undefined, known, used);
          this.closed = new Map(Object.entries(saved?.closed ?? {}).filter(([id]) => known.has(id) && !used.has(id)));
          this.floatBoxes = new Map(Object.entries(saved?.floatBoxes ?? {}).filter(([id, box]) => known.has(id) && validBox(box)));
          this.dismissed = new Set((Array.isArray(saved?.dismissed) ? saved.dismissed : []).filter((id) => typeof id === "string" && known.has(id)));
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
          this.collectAll();
          this.applyHidden();
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
      // The default arrangement's pairs come back in their default order and shares.
      this.pairLeft.clear();
      this.pairShare.clear();
      this.dismissed.clear();
      this.maximizedHere = false;
      this.display.restore(undefined, () => false);
      this.canvas?.setFit(this.fits);
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
      // With the pinned windows out of the grid, its columns share the width equally.
      this.equalizeColumns(api);
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
      this.collectAll();
      this.applyHidden();
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
      if (!this.allowed.includes(id)) continue;
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
    for (const id of this.apiOf("float")?.panels.map((p) => p.id) ?? []) this.rememberFloat(id);
    const floatBoxes = this.floatBoxes.size ? Object.fromEntries([...this.floatBoxes].filter(([id]) => saved.has(id))) : undefined;
    const dismissed = [...this.dismissed].filter((id) => saved.has(id));
    const out: WorkspaceLayout = { dock, folds: this.folds.snapshot(), ...this.display.snapshot(saved), closed: Object.fromEntries(this.closed), ...(heights ? { heights } : {}), ...(tables ? { tables } : {}), ...(version ? { version } : {}), ...(floatBoxes ? { floatBoxes } : {}), ...(dismissed.length ? { dismissed } : {}) };
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
    // Windows that follow their content keep its height.
    if (this.contentSized && this.hostOfGroup(group) === "main") return;
    if (group.activePanel) this.noteUserSized(group.activePanel.id);
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
    if (group.activePanel) this.noteUserSized(group.activePanel.id);
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
    // Without a neighbour the window comes back along the bottom, still at its own height; one along a side
    // edge comes back at its own width too.
    const slot = slotOf(api, group) ?? { neighbor: "", side: "bottom" as const };
    const width = !slot.neighbor && (slot.side === "left" || slot.side === "right") ? Math.round(group.width) : undefined;
    return { ...slot, ...(height ? { height } : {}), ...(width ? { width } : {}) };
  }

  close(group: DockviewGroupPanel): void {
    const id = this.closeGroup(group);
    if (id && !this.transient.has(id)) {
      this.dismissed.add(id);
      this.onWindowToggle?.(id, false);
    }
  }

  /** Open a window the workspace offers when it has something to show, unless the user closed it. */
  autoOpen(id: string): void {
    if (this.dismissed.has(id) || this.hostOf(id) || !this.api) return;
    this.ensureOpen(id);
  }

  /** Close a window, remembering where it was; returns its id. */
  private closeGroup(group: DockviewGroupPanel): string | null {
    const panel = group.activePanel;
    if (!panel || !this.hostOfGroup(group)) return null;
    this.notePairOrder(panel.id);
    if (!this.transient.has(panel.id)) {
      const place = this.placeOf(panel.id, group);
      const floating = this.hostOfGroup(group) === "float";
      if (floating) this.rememberFloat(panel.id);
      this.closed.set(panel.id, floating ? { ...(place ?? { neighbor: "", side: "bottom" }), host: "float", from: place } : place);
    }
    this.bandChanging(this.hostOfGroup(group));
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
      return live && !this.hostOf(id) && !(id in this.conditional) && !this.extras.collect?.(id);
    });
  }

  reopen(id: string): void {
    this.dismissed.delete(id);
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
    if (this.placePaired(id, slot)) return true;
    this.batch(() => {
      if (band && this.apiOf(band)) {
        this.bandChanging(band);
        this.keepHeights(() => this.addToBand(id, band), { [id]: height });
        return;
      }
      if (slot?.host === "float" && this.apiOf("float")) {
        this.floatedFrom.set(id, slot.from !== undefined ? slot.from : slot.neighbor ? { neighbor: slot.neighbor, side: slot.side, height } : null);
        this.addFloating(id, FLOAT_BOUNDS.minWidth + 120, Math.min(FLOAT_BOUNDS.maxHeight, height));
        return;
      }
      const anchor = slot?.neighbor ? api.getPanel(slot.neighbor) : undefined;
      this.keepHeights(() => {
        if (anchor && anchor.group.api.location.type === "grid") {
          this.add(id, { referencePanel: anchor, direction: DIRECTION_OF[slot!.side] });
        } else {
          // No remembered neighbour: along the edge it was on (the bottom by default), across the workspace.
          this.add(id, { direction: slot && !slot.neighbor ? DIRECTION_OF[slot.side] : "below" });
          this.restoreWidth(id, slot);
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

  /**
   * Open a window under `neighbour`, sharing its height equally, unless the
   * user has since given it a place of its own. Request Details opens as the
   * bottom half of Review's column this way (user, 2026-10-29).
   */
  ensureOpenSplitting(id: string, neighbour: string): void {
    const share = this.api && !this.hostOf(id) && !this.closed.get(id)?.height
      ? Math.round((this.groupOf(neighbour)?.height ?? 0) / 2) : 0;
    if (share >= MIN_WINDOW_HEIGHT) {
      this.closed.set(id, { ...(this.closed.get(id) ?? { neighbor: neighbour, side: "bottom" }), height: share });
    }
    this.ensureOpen(id);
    // Both halves are given the same height: in a workspace that fits the screen, sizing
    // only the new one leaves the other with whatever is left, which is not half.
    if (share >= MIN_WINDOW_HEIGHT && this.canvas && this.hostOf(id) === "main" && this.hostOf(neighbour) === "main") {
      this.batch(() => this.canvas?.apply({ [id]: share, [neighbour]: share }));
    }
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
    if (from === "float") this.rememberFloat(id);
    this.batch(() => {
      fromApi.removePanel(panel);
      if (place) place(toApi);
      else if (isBand(to)) this.addToBand(id, to);
      else if (to === "float") this.addFloating(id, FLOAT_BOUNDS.minWidth + 120, FLOAT_BOUNDS.minHeight + 80);
      else toApi.addPanel({ ...this.panelOptions(id), position: { direction: "below" } });
      // Floating, a folded window only hides its content; in a grid its row is settled with the others.
      if (to === "float") this.folds.setCollapsed(id, undefined);
    });
  }

  /** Shrink rows that are now all folded and grow rows that now hold an open window, in every docked host. */
  private settleAllFolds(): void {
    for (const [id, host] of this.attached()) if (id !== "float") settleFolds(host.api, this.folds);
  }

  /**
   * Side-by-side columns of the default arrangement share the width equally: Dockview splits by the
   * proportions it last had, so an arrangement rebuilt over a rearranged one would otherwise come back uneven.
   */
  private equalizeColumns(api: DockviewApi): void {
    const grid = api.toJSON().grid;
    // Dockview may keep a root from the arrangement before, holding the real split one level down.
    let node = grid.root as { type: string; data: unknown };
    let horizontal = grid.orientation === "HORIZONTAL";
    while (Array.isArray(node.data) && node.data.length === 1 && (node.data[0] as { type: string }).type === "branch") {
      node = node.data[0] as typeof node;
      horizontal = !horizontal;
    }
    if (!horizontal) return;
    const columns = leafOrderByChild(node);
    if (columns.length < 2 || api.width <= 0) return;
    const width = Math.floor(api.width / columns.length);
    for (const first of columns.slice(0, -1)) api.getPanel(first)?.group.api.setSize({ width });
  }

  /** A window back along a side edge of the grid takes the width it had there. */
  private restoreWidth(id: string, slot: Slot | null): void {
    const width = slot && !slot.neighbor ? slot.width : undefined;
    const group = width ? this.api?.getPanel(id)?.group : undefined;
    if (group && width && width > 0 && width < 100000) group.api.setSize({ width: Math.round(width) });
  }

  /** A band is about to change height: the grid window beside it takes or gives the difference. */
  private bandChanging(band: HostId | null): void {
    if (this.canvas && isBand(band)) this.canvas.growEdge = band;
  }

  /** For each pair, the window the user last had on the left when the two shared a row. */
  private pairLeft = new Map<string, string>();
  /** For each pair, the share of their row the left window had then, so a width the user set survives a move. */
  private pairShare = new Map<string, number>();

  /** Note which window of a pair is on the left while both share a band row. */
  private notePairOrder(id: string): void {
    const pair = this.partnerOf(id);
    const mine = this.groupOf(id), theirs = pair ? this.groupOf(pair.partner) : null;
    if (!pair || !mine || !theirs || !isBand(this.hostOfGroup(mine)) || this.hostOfGroup(mine) !== this.hostOfGroup(theirs)) return;
    const a = mine.element.getBoundingClientRect(), b = theirs.element.getBoundingClientRect();
    if (Math.abs(a.top - b.top) > 2 || a.left === b.left) return;
    const leader = pair.right ? pair.partner : id;
    this.pairLeft.set(leader, a.left < b.left ? id : pair.partner);
    const total = a.width + b.width;
    if (total > 0) this.pairShare.set(leader, (a.left < b.left ? a.width : b.width) / total);
  }

  /** Give a pair side by side in a band the shares of the row they last had. */
  private restorePairShare(id: string): void {
    const pair = this.partnerOf(id);
    if (!pair) return;
    const leader = pair.right ? pair.partner : id;
    const share = this.pairShare.get(leader);
    const left = this.pairLeft.get(leader) ?? leader;
    const right = left === id ? pair.partner : id;
    const lg = this.groupOf(left), rg = this.groupOf(right);
    if (share === undefined || !lg || !rg || !isBand(this.hostOfGroup(lg)) || this.hostOfGroup(lg) !== this.hostOfGroup(rg)) return;
    const total = lg.width + rg.width;
    if (total > 0) lg.api.setSize({ width: Math.round(share * total) });
  }

  /** The side of its partner a paired window goes on: as the user last had them, else the follower on the right. */
  private sideOfPartner(id: string, pair: { partner: string; right: boolean }): "left" | "right" {
    const leader = pair.right ? pair.partner : id;
    const left = this.pairLeft.get(leader);
    if (left === id) return "left";
    if (left === pair.partner) return "right";
    return pair.right ? "right" : "left";
  }

  /** A paired window's partner, and whether this window sits to the partner's right. */
  private partnerOf(id: string): { partner: string; right: boolean } | null {
    const pairs = this.extras.pairs ?? {};
    if (pairs[id]) return { partner: pairs[id], right: true };
    const partner = Object.keys(pairs).find((k) => pairs[k] === id);
    return partner ? { partner, right: false } : null;
  }

  /**
   * Place a paired window beside its partner in the partner's band. With the
   * partner closed it takes a band row of its own: the band it was last in,
   * else the partner's, else the bottom. False when it has no partner, or its
   * partner or its own last place is outside the bands.
   */
  private placePaired(id: string, slot: Slot | null): boolean {
    const pair = this.partnerOf(id);
    if (!pair) return false;
    const { partner, right } = pair;
    // A window last floating, or in the grid, goes back there rather than to its partner's band.
    if (slot && !isBand(slot.host)) return false;
    const group = this.groupOf(partner);
    const host = group ? this.hostOfGroup(group) : null;
    if (group && isBand(host)) {
      const api = this.apiOf(host)!;
      this.bandChanging(host);
      this.batch(() => this.keepHeights(() => api.addPanel({ ...this.panelOptions(id), position: { referenceGroup: group, direction: this.sideOfPartner(id, pair) } })));
      this.restorePairShare(id);
      return true;
    }
    if (group) return false;
    const partnerSlot = this.closed.get(partner);
    const band: Band = isBand(slot?.host) ? slot!.host as Band
      : isBand(partnerSlot?.host) ? partnerSlot!.host as Band
      : BANDS.find((b) => this.extras.pinned?.[b]?.includes(partner) || this.extras.pinned?.[b]?.includes(id)) ?? "bottom";
    if (!this.apiOf(band)) return false;
    const leader = right ? partner : id;
    const height = validHeight(slot?.height) ?? this.extras.heights?.[leader] ?? REOPEN_HEIGHT;
    this.bandChanging(band);
    this.batch(() => this.keepHeights(() => this.addToBand(id, band), { [id]: height }));
    return true;
  }

  /** Open a closed window as a floating window over the workspace; close it again when it is open. */
  toggleFloating(id: string, width: number, height: number): void {
    const group = this.groupOf(id);
    if (group) { this.close(group); return; }
    const float = this.apiOf("float");
    if (!float || !this.windows.includes(id)) return;
    this.closed.delete(id);
    const visible = this.visibleHeight() || height;
    const room = (this.canvas?.host.clientWidth ?? 0) - 2 * FLOAT_BOUNDS.left;
    const w = Math.max(FLOAT_BOUNDS.minWidth, Math.round(room > 0 ? Math.min(width, room) : width));
    const h = Math.max(FLOAT_BOUNDS.minHeight, Math.min(Math.round(height), Math.round(visible * 0.85)));
    this.batch(() => {
      this.floatedFrom.set(id, null);
      this.addFloating(id, w, h, true);
    });
    this.onWindowToggle?.(id, true);
  }

  private addToBand(id: string, band: Band): void {
    const api = this.apiOf(band);
    if (!api) return;
    // A band stacks its windows; the bottom band's newest window sits at the bottom.
    if (api.panels.length) api.addPanel({ ...this.panelOptions(id), position: { direction: band === "top" ? "below" : "above" } });
    else api.addPanel(this.panelOptions(id));
  }

  /**
   * Float a window where it last floated, at its size there; a window floating
   * for the first time takes this size, within FLOAT_BOUNDS unless `free`.
   * Either way it moves down and right while it would land on another
   * floating window's corner.
   */
  private addFloating(id: string, width: number, height: number, free = false): void {
    const float = this.apiOf("float");
    if (!float) return;
    const b = FLOAT_BOUNDS;
    const box = this.floatBoxes.get(id);
    const w = Math.max(b.minWidth, Math.round(box?.width ?? (free ? width : Math.min(b.maxWidth, width))));
    const h = Math.max(MIN_WINDOW_HEIGHT, Math.round(box?.height ?? (free ? height : Math.min(b.maxHeight, Math.max(b.minHeight, height)))));
    let left = Math.round(box?.left ?? b.left);
    let top = Math.round(box?.top ?? b.top);
    const taken = float.groups.map((g) => this.floatBox(g)).filter((x): x is FloatBox => !!x);
    for (let i = 0; i < 12 && taken.some((t) => Math.abs(t.left - left) < FLOAT_CASCADE / 2 && Math.abs(t.top - top) < FLOAT_CASCADE / 2); i++) {
      left += FLOAT_CASCADE;
      top += FLOAT_CASCADE;
    }
    float.addPanel({ ...this.panelOptions(id), floating: { position: { left, top }, width: w, height: h } });
  }

  /** A floating window's place and size now, relative to the floating layer; a folded one keeps its unfolded height. */
  private floatBox(group: DockviewGroupPanel): FloatBox | null {
    const frame = group.element.closest<HTMLElement>(".dv-resize-container");
    const layer = this.apiOf("float") && frame?.offsetParent;
    if (!frame || !layer) return null;
    const r = frame.getBoundingClientRect();
    const l = layer.getBoundingClientRect();
    const height = frame.classList.contains("phx-float-folded") ? parseFloat(frame.style.height) : r.height;
    const box = { left: Math.round(r.left - l.left), top: Math.round(r.top - l.top), width: Math.round(r.width), height: Math.round(height) };
    return validBox(box) ? box : null;
  }

  /** Note where a floating window is, before it leaves the floating layer. */
  private rememberFloat(id: string): void {
    const group = this.apiOf("float")?.getPanel(id)?.group;
    const box = group && this.floatBox(group);
    if (box) this.floatBoxes.set(id, box);
  }

  float(group: DockviewGroupPanel): void {
    if (group.activePanel) this.noteUserSized(group.activePanel.id);
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
    if (group.activePanel) this.noteUserSized(group.activePanel.id);
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
          else main.addPanel({ ...this.panelOptions(id), position: { direction: slot && !slot.neighbor ? DIRECTION_OF[slot.side] : "below" } });
          this.restoreWidth(id, slot);
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
    this.notePairOrder(id);
    // A paired window shares its partner's row in the band, and pinning either moves both;
    // the row keeps the leading window's height, so the leader moves first and brings this one.
    const pair = this.partnerOf(id);
    const partner = pair ? this.groupOf(pair.partner) : null;
    const partnerHost = partner ? this.hostOfGroup(partner) : null;
    const together = !!partner && partnerHost !== band && partnerHost !== "float";
    if (pair?.right && together) {
      this.pin(partner!, band);
      // The leader brings this window only when it is not floating; then it follows on its own.
      const self = this.groupOf(id);
      if (self && this.hostOfGroup(self) !== band) this.pin(self, band);
      return;
    }
    const place = this.placeOf(id, group);
    // A floating window is pinned at the height it had before it floated, not its floating size.
    const floatedFrom = host === "float" ? this.floatedFrom.get(id) ?? null : null;
    if (host === "main") this.pinnedFrom.set(id, place);
    else if (host === "float") this.pinnedFrom.set(id, floatedFrom);
    else this.pinnedFrom.set(id, this.pinnedFrom.get(id) ?? null);
    this.floatedFrom.delete(id);
    const height = host === "float"
      ? validHeight(floatedFrom?.height) ?? this.extras.heights?.[id]
      : this.canvasOf(host)?.intended[id] ?? (host === "main" ? undefined : Math.round(group.height));
    const beside = partner && partnerHost === band
      ? (api: DockviewApi) => api.addPanel({ ...this.panelOptions(id), position: { referenceGroup: partner, direction: this.sideOfPartner(id, pair!) } })
      : undefined;
    this.bandChanging(band);
    // Beside its partner it takes the row's height.
    const rowHeight = beside ? this.canvasOf(band)?.intended[pair!.partner] ?? Math.round(partner!.height) : Math.min(height ?? PIN_HEIGHT, PIN_MAX_HEIGHT);
    // From another band or the floating layer, both bands may change: note the grid's edges and heights first.
    const edges = host !== "main" && this.api && this.canvas ? { box: this.canvas.host.getBoundingClientRect(), heights: gridHeights(this.api) } : null;
    this.keepHeights(() => this.transfer(id, band, beside), { [id]: rowHeight });
    if (beside) this.restorePairShare(id);
    if (edges && this.canvas) {
      const now = this.canvas.host.getBoundingClientRect();
      const top = Math.round(now.top - edges.box.top);
      const bottom = Math.round(edges.box.bottom - now.bottom);
      // The windows beside a band that grew give the space up; those beside one that shrank take it.
      if (top || bottom) this.canvas.shiftEdges(edges.heights, top, bottom);
    }
    if (together) this.pin(partner!, band);
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
    this.bandChanging(host);
    this.keepHeights(() => this.transfer(id, "main", (main) => {
      if (anchor && anchor.group.api.location.type === "grid") main.addPanel({ ...this.panelOptions(id), position: { referencePanel: anchor, direction: DIRECTION_OF[slot!.side] } });
      else main.addPanel({ ...this.panelOptions(id), position: { direction: slot && !slot.neighbor ? DIRECTION_OF[slot.side] : host === "top" ? "above" : "below" } });
      this.restoreWidth(id, slot);
    }), { [id]: height });
    this.changed();
  }

  /** Add a shell-managed window above everything, full width and compact, if it is not open. */
  showTransient(id: string): void {
    const api = this.api;
    if (!api || this.hostOf(id)) return;
    // Its own measured height when it has one, so nothing it shows is clipped.
    const measured = this.natural.get(id);
    const height = measured && measured > 0
      ? measured
      : Math.min(TRANSIENT_HEIGHT, Math.max((this.canvas?.visibleHeight() || api.height) / 3, 140));
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
    if (!api) return;
    // A maximized window fills the visible area: no frame may still end at its content, as one
    // marked before a second maximize did when nothing laid the windows out again.
    if (api.hasMaximizedGroup()) {
      this.markSlack();
      return;
    }
    // Windows hidden while one was maximized, as the Access Keys wizard does, are hidden now.
    this.applyHidden();
    if (!this.maximizedHere) return;
    this.maximizedHere = false;
    this.canvas?.apply();
    if (this.contentSized) this.followContent();
  }

  /** Before a window starts moving, record every docked window's height. */
  dragStarted(): void {
    for (const [, host] of this.attached()) host.canvas?.capture();
  }
  /** After a drop, lay every docked window out at its intended height again. */
  layoutSettled(): void {
    if (this.api?.hasMaximizedGroup()) return;
    this.settleAllFolds();
    for (const [, host] of this.attached()) {
      if (!host.canvas) continue;
      host.canvas.prune();
      host.canvas.apply();
    }
  }
}
