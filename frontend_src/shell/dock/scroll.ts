/**
 * Vertical scrolling for a workspace. Dockview fills its element, so the
 * workspace gives that element a height of its own: the visible height while
 * the windows fit, more when they need it, and the workspace scrolls. Each
 * docked window has an intended height that only the user (or the default
 * arrangement) changes; the page is as tall as those heights need, so making
 * one window taller pushes the windows below it down instead of squeezing a
 * neighbour. Width never scrolls.
 *
 * A fitted workspace (Logs, and My Workspace until set to scroll) never
 * scrolls: its windows always share the visible height, a border dragged
 * between two windows trades height between them, and windows too tall for
 * the screen are shrunk to fit.
 */

import type { DockviewApi, DockviewGroupPanel } from "dockview-react";
import { MIN_WINDOW_HEIGHT, TITLE_BAR_HEIGHT } from "./fold";

/** Window heights by window id. */
export type Heights = Record<string, number>;

interface GridNode {
  type: "leaf" | "branch";
  data: unknown;
  size: number;
}

const isGrid = (group: DockviewGroupPanel): boolean => group.api.location.type === "grid";
/** Dockview never lays a grid out shorter than this, whatever its windows' minimums. */
const DOCKVIEW_MIN_GRID = 100;
const leafId = (node: GridNode): string | undefined => ((node.data as { views?: string[] })?.views ?? [])[0];
const childrenOf = (node: GridNode): GridNode[] => (Array.isArray(node.data) ? (node.data as GridNode[]) : []);

/** Docked windows and their current heights. */
export function gridHeights(api: DockviewApi): Heights {
  const out: Heights = {};
  for (const group of api.groups) {
    const id = group.activePanel?.id;
    if (id && isGrid(group)) out[id] = Math.round(group.height);
  }
  return out;
}

/** Docked window ids in reading order: top to bottom, left to right. */
export function leafOrder(root: unknown): string[] {
  const out: string[] = [];
  const walk = (node: GridNode): void => {
    if (node.type === "leaf") {
      const id = leafId(node);
      if (id) out.push(id);
    } else childrenOf(node).forEach(walk);
  };
  walk(root as GridNode);
  return out;
}

/** A row of side-by-side windows: the first window of each part, and each part's share of the row's width. */
export interface RowShares { members: string[]; shares: number[] }

/** Give each row's parts their shares of the row's width again. */
export function applyShares(api: DockviewApi, rows: readonly RowShares[]): void {
  for (const row of rows) {
    const groups = row.members.map((id) => api.getPanel(id)?.group);
    if (groups.some((g) => !g || !isGrid(g) || !g.api.isVisible)) continue;
    const total = groups.reduce((sum, g) => sum + g!.width, 0);
    if (total <= 0) continue;
    // Each width set takes its difference from the windows after it, so left to right leaves the earlier ones alone.
    groups.slice(0, -1).forEach((g, i) => g!.api.setSize({ width: Math.round(row.shares[i] * total) }));
  }
}

/** Every row of side-by-side windows in a layout. */
export function sideBySide(grid: { root: unknown; orientation: string }): RowShares[] {
  const out: RowShares[] = [];
  const walk = (node: GridNode, stacks: boolean): void => {
    const children = childrenOf(node);
    if (!stacks && children.length > 1) {
      const total = children.reduce((sum, c) => sum + (c.size || 0), 0);
      const members = children.map((c) => leafOrder(c)[0]);
      if (total > 0 && members.every(Boolean)) out.push({ members: members as string[], shares: children.map((c) => (c.size || 0) / total) });
    }
    for (const child of children) walk(child, !stacks);
  };
  walk(grid.root as GridNode, grid.orientation === "VERTICAL");
  return out;
}

/** Height a grid node needs: stacked children add up, side-by-side children take the tallest. */
export function requiredHeight(root: unknown, rootStacks: boolean, heightOf: (id: string) => number): number {
  const walk = (node: GridNode, stacks: boolean): number => {
    if (node.type === "leaf") {
      const id = leafId(node);
      return id ? heightOf(id) : 0;
    }
    const sizes = childrenOf(node).map((child) => walk(child, !stacks));
    return stacks ? sizes.reduce((a, b) => a + b, 0) : Math.max(0, ...sizes);
  };
  return walk(root as GridNode, rootStacks);
}

export class WorkspaceCanvas {
  /** The canvas height while the windows need more than the visible height; 0 while they fit. */
  height = 0;
  /**
   * Each docked window's intended height, once the user or the default
   * arrangement has given it one. Windows without one share the space left
   * over; a window drawn taller than intended only fills space left in its column.
   */
  intended: Heights = {};
  /** The visible height at the last layout, to tell whether a layout that fitted still does. */
  private lastVisible = 0;

  constructor(
    private readonly api: DockviewApi,
    /** The scrolling element; Dockview's own element is its first child. */
    readonly host: HTMLElement,
    /** Whether a window is folded down to its title bar. */
    private readonly collapsed: (id: string) => boolean,
    /**
     * For a pinned band: the band is as tall as its windows, up to this height,
     * and scrolls only beyond it. Without it, the host keeps its own height.
     */
    private readonly maxHeight?: () => number,
    /** The windows always fit the visible height; the canvas never scrolls. */
    fit = false,
  ) {
    this.fit = fit;
    this.setFit(fit);
  }

  /** The windows always fit the visible height; the canvas never scrolls. */
  fit: boolean;

  /** Fit the windows to the visible height, or let the canvas grow and scroll; applies at the next layout. */
  setFit(fit: boolean): void {
    this.fit = fit;
    // Never a scrollbar, not even while an arrangement is being built: one would leave the windows narrower once it goes.
    this.host.classList.toggle("phx-fits", fit);
  }

  /** Whether a window is folded down to its title bar. */
  isCollapsed(id: string): boolean { return this.collapsed(id); }

  /** Windows hidden in place (Access Keys hides a key's windows while none is chosen): they take no height and keep their intended one. */
  hidden: (id: string) => boolean = () => false;

  /** The height a window takes in the layout: its title bar when folded, none when hidden. */
  private heightOf(id: string): number {
    if (this.hidden(id)) return 0;
    return this.collapsed(id) ? TITLE_BAR_HEIGHT : this.intended[id] ?? MIN_WINDOW_HEIGHT;
  }

  /** The edge whose band last grew or shrank; the window nearest it takes the space left. */
  growEdge: "top" | "bottom" = "bottom";

  visibleHeight(): number {
    return this.host.clientHeight;
  }

  /** Size the canvas, never below the visible height, and lay the windows out on it at once. */
  setCanvas(height: number): void {
    const visible = this.visibleHeight();
    // A hidden workspace has no visible height; keep the request and fit it when shown.
    this.height = visible <= 0 ? Math.max(0, Math.round(height)) : height > visible + 1 ? Math.round(height) : 0;
    this.host.style.setProperty("--phx-canvas-height", this.height ? `${this.height}px` : "100%");
    this.host.classList.toggle("phx-scrolls", this.height > 0);
    const element = this.host.firstElementChild as HTMLElement | null;
    const width = element?.clientWidth || this.api.width;
    if (visible > 0 && width > 0) this.layout(width, this.height || visible);
  }

  /**
   * Lay Dockview out at this size. When the width changes, as when a scrollbar
   * comes or goes, windows side by side keep their shares of it; Dockview
   * alone would take the whole change from one of them.
   */
  private layout(width: number, height: number): void {
    const resized = Math.abs(width - this.api.width) >= 1 && this.api.width > 0;
    const rows = resized ? sideBySide(this.api.toJSON().grid) : [];
    this.api.layout(width, height, true);
    applyShares(this.api, rows);
  }

  /**
   * Keep the workspace scrollable as far as it is now until the returned
   * function is called. While a border is dragged up near the bottom, the
   * browser would otherwise scroll back as the canvas shrinks, and the border
   * would slide away from the pointer; held, the space below stays empty
   * until the drag ends.
   */
  holdScrollHeight(): () => void {
    this.host.style.setProperty("--phx-canvas-hold", `${this.host.scrollHeight}px`);
    return () => this.host.style.removeProperty("--phx-canvas-hold");
  }

  /** Lay the windows out again when the canvas's width changed without a resize, as when a scrollbar comes and goes. */
  fitWidth(): void {
    const width = (this.host.firstElementChild as HTMLElement | null)?.clientWidth ?? 0;
    if (width > 0 && this.visibleHeight() > 0 && Math.abs(width - this.api.width) >= 1) this.layout(width, this.api.height);
  }

  /** Record the current height of every docked window that has no intended height yet; call it while the layout is settled. */
  capture(): void {
    for (const [id, h] of Object.entries(gridHeights(this.api))) {
      if (this.intended[id] === undefined && !this.collapsed(id) && !this.hidden(id)) this.intended[id] = h;
    }
  }

  /**
   * Give windows these intended heights, then lay everything out: the canvas
   * becomes as tall as the intended heights need, and each window gets its own
   * height except the last unfolded window in each stack, which takes up the
   * difference between side-by-side columns or the space left while the
   * windows fit the visible height.
   */
  apply(targets: Heights = {}): void {
    const api = this.api;
    for (const [id, h] of Object.entries(targets)) {
      if (Number.isFinite(h)) this.intended[id] = Math.max(MIN_WINDOW_HEIGHT, Math.round(h));
    }
    const grid = api.toJSON().grid;
    const rootStacks = grid.orientation === "VERTICAL";
    const heightOf = (id: string): number => this.heightOf(id);
    if (this.fit && !this.maxHeight && api.panels.length) this.shrinkToFit(grid.root, rootStacks, new Set(Object.keys(targets)));
    const required = api.panels.length ? requiredHeight(grid.root, rootStacks, heightOf) : 0;
    if (this.maxHeight) {
      const height = Math.min(required, Math.max(0, this.maxHeight()));
      this.host.style.height = `${height}px`;
      this.host.classList.toggle("phx-band-empty", required === 0);
      // A band of folded windows is shorter than Dockview's smallest grid: draw it at that size and clip it.
      this.host.classList.toggle("phx-band-clipped", required > 0 && required < DOCKVIEW_MIN_GRID);
      if (required === 0) {
        this.setCanvas(0);
        return;
      }
    }
    this.setCanvas(this.maxHeight ? Math.max(required, DOCKVIEW_MIN_GRID) : required);

    this.lastVisible = this.visibleHeight();
    const fixed = (node: GridNode): boolean => {
      if (node.type !== "leaf") return childrenOf(node).every(fixed);
      const id = leafId(node) ?? "";
      return this.collapsed(id) || this.hidden(id);
    };
    // Only windows with an intended height are sized; the others keep the share Dockview gave them.
    const sized = (node: GridNode): boolean => (node.type === "leaf"
      ? this.intended[leafId(node) ?? ""] !== undefined
      : childrenOf(node).every((c) => fixed(c) || sized(c)));
    const needs = (node: GridNode, stacks: boolean): number => requiredHeight(node, stacks, heightOf);
    // A stacked child's height is set through a window: the child itself, or a window directly in its row.
    // Side-by-side columns with no window of their own directly in the row have none.
    const settable = (node: GridNode): boolean => node.type === "leaf" || childrenOf(node).some((c) => c.type === "leaf" && !fixed(c));
    const setHeight = (node: GridNode, height: number): void => {
      const leaf = node.type === "leaf" ? node : childrenOf(node).find((c) => c.type === "leaf" && !fixed(c));
      const id = leaf && leafId(leaf);
      const group = id ? api.getPanel(id)?.group : undefined;
      if (group && isGrid(group)) group.api.setSize({ height });
    };
    const walk = (node: GridNode, stacks: boolean): void => {
      if (node.type === "leaf") return;
      const children = childrenOf(node);
      if (stacks) {
        const flexible = children.filter((c) => !fixed(c));
        // The space left goes to the window beside the band that last changed (the lowest by default),
        // so space a closed Help frees goes to its neighbour; a fitted workspace skips the window just sized.
        const untargeted = flexible.filter((c) => !leafOrder(c).some((id) => id in targets));
        const pool = this.fit && untargeted.length ? untargeted : flexible;
        // A child that cannot be sized takes the space left, or Dockview shares it out and a dragged border lags the pointer.
        const unsized = flexible.filter((c) => !settable(c));
        const takers = unsized.length ? unsized : pool;
        const absorber = this.growEdge === "top" ? takers[0] : takers[takers.length - 1];
        for (const child of children) if (child !== absorber && !fixed(child) && sized(child)) setHeight(child, needs(child, false));
      }
      for (const child of children) walk(child, !stacks);
    };
    walk(grid.root as GridNode, rootStacks);
  }

  /**
   * The tallest this window can be drawn while every other window keeps its
   * height and the windows still fit the visible height. A window that follows
   * its content asks for no more than this, so growing to fit a long record
   * never shrinks the windows around it. A canvas that scrolls has no limit.
   */
  roomFor(id: string): number {
    const visible = this.visibleHeight();
    if (!this.fit || visible <= 0) return Number.MAX_SAFE_INTEGER;
    const grid = this.api.toJSON().grid;
    const rootStacks = grid.orientation === "VERTICAL";
    const withHeight = (h: number) => (other: string): number => (other === id ? h : this.heightOf(other));
    if (requiredHeight(grid.root, rootStacks, withHeight(visible)) <= visible) return visible;
    let lo = MIN_WINDOW_HEIGHT, hi = visible;
    for (let i = 0; i < 16 && hi - lo > 1; i++) {
      const mid = Math.floor((lo + hi) / 2);
      if (requiredHeight(grid.root, rootStacks, withHeight(mid)) <= visible) lo = mid; else hi = mid;
    }
    return lo;
  }

  /**
   * In a fitted workspace, make the intended heights fit the visible height:
   * windows not just given a height shrink in proportion first, and the ones
   * just sized only if that is not enough.
   */
  private shrinkToFit(root: unknown, rootStacks: boolean, keep: ReadonlySet<string>): void {
    // Side-by-side columns each fit the screen on their own: one that already fits keeps its heights.
    if (!rootStacks) {
      for (const column of childrenOf(root as GridNode)) this.shrinkToFit(column, true, keep);
      return;
    }
    const visible = this.visibleHeight();
    if (visible <= 0) return;
    const mine = new Set(leafOrder(root));
    const base = Object.fromEntries(Object.entries(this.intended).filter(([id]) => mine.has(id)));
    const scaled = (f: number, all: boolean) => (id: string): number => {
      if (this.hidden(id)) return 0;
      if (this.collapsed(id)) return TITLE_BAR_HEIGHT;
      const h = base[id] ?? MIN_WINDOW_HEIGHT;
      return all || !keep.has(id) ? Math.max(MIN_WINDOW_HEIGHT, Math.floor(h * f)) : h;
    };
    if (requiredHeight(root, rootStacks, scaled(1, false)) <= visible) return;
    for (const all of [false, true]) {
      let lo = 0, hi = 1;
      for (let i = 0; i < 20; i++) {
        const mid = (lo + hi) / 2;
        if (requiredHeight(root, rootStacks, scaled(mid, all)) <= visible) lo = mid; else hi = mid;
      }
      if (requiredHeight(root, rootStacks, scaled(lo, all)) <= visible || all) {
        const size = scaled(lo, all);
        for (const id of Object.keys(base)) if (!this.collapsed(id)) this.intended[id] = size(id);
        return;
      }
    }
  }

  /**
   * After the pinned bands changed, keep every window's height (`before`)
   * except along each edge: the windows nearest a band that grew by `top` or
   * `bottom` pixels give that much up, and those nearest one that shrank
   * (a negative change) take the space it left.
   */
  shiftEdges(before: Heights, top: number, bottom: number): void {
    const grid = this.api.toJSON().grid;
    // The windows along an edge: in a stack, its first (or last) child's; side by side, every column's.
    const edge = (node: GridNode, stacks: boolean, first: boolean): string[] => {
      if (node.type === "leaf") return leafOrder(node);
      const children = childrenOf(node);
      if (!children.length) return [];
      return stacks ? edge(children[first ? 0 : children.length - 1], false, first) : children.flatMap((c) => edge(c, true, first));
    };
    const targets: Heights = {};
    const shift = (ids: string[], change: number): void => {
      for (const id of ids) {
        if (this.collapsed(id) || before[id] === undefined) continue;
        targets[id] = Math.max(MIN_WINDOW_HEIGHT, (targets[id] ?? before[id]) - change);
      }
    };
    const root = grid.root as GridNode;
    const stacks = grid.orientation === "VERTICAL";
    // A window spanning the full height lies on both edges, and the two changes cancel out.
    if (top) shift(edge(root, stacks, true), top);
    if (bottom) shift(edge(root, stacks, false), bottom);
    for (const [id, h] of Object.entries(before)) if (!this.collapsed(id)) this.intended[id] = h;
    this.apply(targets);
  }

  /** Record every docked window's current height as intended, after the user dragged a border; returns the windows that changed. */
  adopt(): string[] {
    const changed: string[] = [];
    for (const [id, h] of Object.entries(gridHeights(this.api))) {
      if (this.collapsed(id) || this.hidden(id)) continue;
      if (Math.abs((this.intended[id] ?? 0) - h) > 1) changed.push(id);
      this.intended[id] = h;
    }
    return changed;
  }

  /** Forget the windows that are no longer docked. */
  prune(): void {
    const docked = gridHeights(this.api);
    for (const id of Object.keys(this.intended)) if (!(id in docked)) delete this.intended[id];
  }

  /**
   * Follow a change in the visible height. A layout that fitted the screen
   * keeps fitting it, its intended heights scaled to the new height; one that
   * scrolled keeps its heights. A maximized window keeps filling the visible area.
   */
  refit(): void {
    const visible = this.visibleHeight();
    if (visible <= 0) return;
    if (this.api.hasMaximizedGroup()) {
      this.setCanvas(0);
      return;
    }
    if (this.maxHeight) {
      this.apply();
      return;
    }
    const grid = this.api.toJSON().grid;
    const required = requiredHeight(grid.root, grid.orientation === "VERTICAL", (id) => this.heightOf(id));
    if (this.height === 0 && this.lastVisible > 0 && required > visible && required <= this.lastVisible + 1) {
      const scale = visible / required;
      for (const id of Object.keys(this.intended)) {
        if (!this.collapsed(id)) this.intended[id] = Math.max(MIN_WINDOW_HEIGHT, Math.floor(this.intended[id] * scale));
      }
    }
    this.apply();
  }
}

/** How far a border or the bottom handle must be dragged down before holding it below the visible area scrolls. */
const ARM_DISTANCE = 8;
/** Distance from the visible edge at which a drag starts scrolling, and the fastest scroll per frame. */
const EDGE_ZONE = 32;
const EDGE_STEP = 18;

/** Scroll speed for a pointer near the top or bottom of the visible area; 0 elsewhere. */
export function edgeScrollStep(clientY: number, top: number, bottom: number): number {
  if (clientY > bottom - EDGE_ZONE) return Math.ceil(EDGE_STEP * Math.min(1, (clientY - (bottom - EDGE_ZONE)) / EDGE_ZONE));
  if (clientY < top + EDGE_ZONE) return -Math.ceil(EDGE_STEP * Math.min(1, (top + EDGE_ZONE - clientY) / EDGE_ZONE));
  return 0;
}

/**
 * Dragging the border under a window makes that window taller or shorter and
 * moves everything below with it, instead of trading space with the window
 * underneath. Only borders between stacked windows; borders between
 * side-by-side windows keep Dockview's own behaviour, since width never
 * scrolls. Near the top or bottom of the visible area the workspace scrolls.
 */
export function pushDownResizing(api: DockviewApi, canvas: WorkspaceCanvas, onEnd: () => void, onResized?: (ids: string[]) => void): () => void {
  const host = canvas.host;
  const onPointerDown = (e: PointerEvent) => {
    if (e.button !== 0) return;
    const sash = (e.target as HTMLElement).closest<HTMLElement>(".dv-sash");
    if (!sash || sash.closest(".dv-resize-container") || sash.classList.contains("dv-disabled")) return;
    if (canvas.fit) {
      // A fitted workspace keeps Dockview's own border: the windows on both sides trade height.
      // While it is dragged nothing else sizes the windows, and the ones it changed are the user's.
      const before = gridHeights(api);
      host.classList.add("phx-resizing");
      const done = () => {
        window.removeEventListener("pointerup", done, true);
        window.removeEventListener("pointercancel", done, true);
        requestAnimationFrame(() => {
          host.classList.remove("phx-resizing");
          canvas.adopt();
          const after = gridHeights(api);
          const ids = Object.keys(after).filter((id) => Math.abs((before[id] ?? after[id]) - after[id]) > 1);
          if (ids.length) onResized?.(ids);
          onEnd();
        });
      };
      window.addEventListener("pointerup", done, true);
      window.addEventListener("pointercancel", done, true);
      return;
    }
    const line = sash.getBoundingClientRect();
    if (line.width <= line.height) return;
    const y = line.top + line.height / 2;
    const above = api.groups.filter((g) => {
      if (!isGrid(g) || !g.activePanel) return false;
      const r = g.element.getBoundingClientRect();
      return Math.abs(r.bottom - y) <= 6 && r.right > line.left + 1 && r.left < line.right - 1;
    });
    if (!above.length) return;
    e.preventDefault();
    e.stopPropagation();
    // Every window keeps its height except the ones being resized.
    canvas.capture();
    const start = gridHeights(api);
    const startIntended = { ...canvas.intended };
    const ids = above.map((g) => g.activePanel!.id);
    const minDelta = Math.max(...ids.map((id) => MIN_WINDOW_HEIGHT - start[id]));
    const startY = e.clientY;
    // Only the scrolling this drag does counts, in case the browser also scrolls back when the workspace gets shorter.
    let scrolled = 0;
    let lastY = e.clientY;
    let frame = 0;
    // A border pressed near the bottom of the visible area grows the workspace only once it is dragged down.
    let armed = false;
    const release = canvas.holdScrollHeight();
    host.classList.add("phx-resizing");
    const apply = () => {
      const delta = Math.max(minDelta, lastY - startY + scrolled);
      canvas.intended = { ...startIntended };
      const targets: Heights = {};
      for (const id of ids) targets[id] = start[id] + delta;
      canvas.apply(targets);
    };
    const tick = () => {
      const r = host.getBoundingClientRect();
      const step = edgeScrollStep(lastY, r.top, r.bottom);
      if (step > 0 && armed) {
        // Scrolling down always moves the border: the canvas grows first to make room.
        scrolled += step;
        apply();
        host.scrollTop += step;
      } else if (step < 0) {
        // Up only as far as the page really went.
        const before = host.scrollTop;
        host.scrollTop += step;
        if (host.scrollTop !== before) {
          scrolled += host.scrollTop - before;
          apply();
        }
      }
      frame = requestAnimationFrame(tick);
    };
    const move = (ev: PointerEvent) => {
      lastY = ev.clientY;
      if (lastY > startY + ARM_DISTANCE) armed = true;
      apply();
    };
    const end = () => {
      cancelAnimationFrame(frame);
      release();
      host.classList.remove("phx-resizing");
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", end, true);
      window.removeEventListener("pointercancel", end, true);
      onResized?.(ids);
      onEnd();
    };
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", end, true);
    window.addEventListener("pointercancel", end, true);
    frame = requestAnimationFrame(tick);
  };
  host.addEventListener("pointerdown", onPointerDown, true);
  return () => host.removeEventListener("pointerdown", onPointerDown, true);
}

/**
 * While a window is being dragged to a new place, holding the pointer near the
 * top or bottom of the visible area scrolls the workspace, and the drop
 * highlight follows what is now under the pointer.
 */
export function scrollWhileDragging(api: DockviewApi, host: HTMLElement): () => void {
  let frame = 0;
  let last: PointerEvent | null = null;
  const track = (e: PointerEvent) => { if (!e.isTrusted) return; last = e; };
  const tick = () => {
    if (last) {
      const r = host.getBoundingClientRect();
      const step = edgeScrollStep(last.clientY, r.top, r.bottom);
      const before = host.scrollTop;
      if (step) host.scrollTop += step;
      if (host.scrollTop !== before) {
        // Dockview finds the drop target on pointer moves; replay the last one over the new content.
        last.target?.dispatchEvent(new PointerEvent("pointermove", last));
      }
    }
    frame = requestAnimationFrame(tick);
  };
  const stop = () => {
    cancelAnimationFrame(frame);
    frame = 0;
    last = null;
    window.removeEventListener("pointermove", track, true);
    for (const type of ["pointerup", "pointercancel", "dragend"]) window.removeEventListener(type, stop, true);
  };
  const begin = () => {
    if (frame) return;
    window.addEventListener("pointermove", track, true);
    for (const type of ["pointerup", "pointercancel", "dragend"]) window.addEventListener(type, stop, true);
    frame = requestAnimationFrame(tick);
  };
  const a = api.onWillDragGroup(begin);
  const b = api.onWillDragPanel(begin);
  return () => { a.dispose(); b.dispose(); stop(); };
}

/**
 * The handle along the bottom of a scrolling grid: dragging it makes the
 * windows along the bottom taller or shorter, and the workspace grows or
 * shrinks with them, so the last row can be stretched as any other. Held
 * below the visible area the workspace scrolls and keeps growing; the handle
 * itself sits at the bottom of that area once scrolled down, so the scrolling
 * starts only after the pointer has moved down. A fitted workspace hides the
 * handle (CSS) and ignores it.
 */
export function bottomEdgeResizing(api: DockviewApi, canvas: WorkspaceCanvas, edge: HTMLElement, onEnd: () => void, onResized?: (ids: string[]) => void): () => void {
  const host = canvas.host;
  const onPointerDown = (e: PointerEvent) => {
    if (e.button !== 0 || canvas.fit || api.hasMaximizedGroup()) return;
    const docked = api.groups.filter((g) => isGrid(g) && g.activePanel).map((g) => ({ id: g.activePanel!.id, rect: g.element.getBoundingClientRect() }));
    if (!docked.length) return;
    const bottom = Math.max(...docked.map((d) => d.rect.bottom));
    const ids = docked.filter((d) => bottom - d.rect.bottom <= 6 && !canvas.isCollapsed(d.id)).map((d) => d.id);
    if (!ids.length) return;
    e.preventDefault();
    e.stopPropagation();
    canvas.capture();
    const start = gridHeights(api);
    const startIntended = { ...canvas.intended };
    const minDelta = Math.max(...ids.map((id) => MIN_WINDOW_HEIGHT - start[id]));
    const startY = e.clientY;
    // Only the scrolling this drag does counts, in case the browser also scrolls back when the workspace gets shorter.
    let scrolled = 0;
    let lastY = e.clientY;
    let frame = 0;
    let armed = false;
    const release = canvas.holdScrollHeight();
    host.classList.add("phx-resizing");
    edge.classList.add("is-dragging");
    const apply = () => {
      const delta = Math.max(minDelta, lastY - startY + scrolled);
      canvas.intended = { ...startIntended };
      const targets: Heights = {};
      for (const id of ids) targets[id] = start[id] + delta;
      canvas.apply(targets);
    };
    const tick = () => {
      const r = host.getBoundingClientRect();
      const step = edgeScrollStep(lastY, r.top, r.bottom);
      if (armed && step > 0) {
        scrolled += step;
        apply();
        host.scrollTop += step;
      }
      frame = requestAnimationFrame(tick);
    };
    const move = (ev: PointerEvent) => {
      lastY = ev.clientY;
      if (lastY > startY + ARM_DISTANCE) armed = true;
      apply();
    };
    const end = () => {
      cancelAnimationFrame(frame);
      release();
      host.classList.remove("phx-resizing");
      edge.classList.remove("is-dragging");
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", end, true);
      window.removeEventListener("pointercancel", end, true);
      onResized?.(ids);
      onEnd();
    };
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", end, true);
    window.addEventListener("pointercancel", end, true);
    frame = requestAnimationFrame(tick);
  };
  edge.addEventListener("pointerdown", onPointerDown);
  return () => edge.removeEventListener("pointerdown", onPointerDown);
}

/**
 * Dragging the edge between a pinned band and the scrolling grid makes the
 * band's windows along that edge taller or shorter: the bottom row of the top
 * band, the top row of the bottom band.
 */
export function bandEdgeResizing(api: DockviewApi, canvas: WorkspaceCanvas, edge: HTMLElement, band: "top" | "bottom", onEnd: () => void): () => void {
  const onPointerDown = (e: PointerEvent) => {
    if (e.button !== 0 || !api.panels.length) return;
    const rows = api.groups.filter((g) => isGrid(g) && g.activePanel).map((g) => ({ id: g.activePanel!.id, rect: g.element.getBoundingClientRect() }));
    if (!rows.length) return;
    e.preventDefault();
    canvas.capture();
    const start = gridHeights(api);
    const startIntended = { ...canvas.intended };
    const edgeY = band === "top" ? Math.max(...rows.map((r) => r.rect.bottom)) : Math.min(...rows.map((r) => r.rect.top));
    const ids = rows.filter((r) => Math.abs((band === "top" ? r.rect.bottom : r.rect.top) - edgeY) <= 6).map((r) => r.id);
    const minDelta = Math.max(...ids.map((id) => MIN_WINDOW_HEIGHT - start[id]));
    const startY = e.clientY;
    edge.classList.add("is-dragging");
    const move = (ev: PointerEvent) => {
      const raw = ev.clientY - startY;
      const delta = Math.max(minDelta, band === "top" ? raw : -raw);
      canvas.intended = { ...startIntended };
      const targets: Heights = {};
      for (const id of ids) targets[id] = start[id] + delta;
      canvas.apply(targets);
    };
    const end = () => {
      edge.classList.remove("is-dragging");
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", end, true);
      window.removeEventListener("pointercancel", end, true);
      onEnd();
    };
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", end, true);
    window.addEventListener("pointercancel", end, true);
  };
  edge.addEventListener("pointerdown", onPointerDown);
  return () => edge.removeEventListener("pointerdown", onPointerDown);
}
