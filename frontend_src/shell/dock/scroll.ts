/**
 * Vertical scrolling for a workspace. Dockview fills its element, so the
 * workspace gives that element a height of its own: the visible height while
 * the windows fit, more when they need it, and the workspace scrolls. Each
 * docked window has an intended height that only the user (or the default
 * arrangement) changes; the page is as tall as those heights need, so making
 * one window taller pushes the windows below it down instead of squeezing a
 * neighbour. Width never scrolls.
 *
 * A fitted workspace (Logs) never scrolls: its windows always share the
 * visible height, a border dragged between two windows trades height between
 * them, and windows too tall for the screen are shrunk to fit.
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
    readonly fit = false,
  ) {
    // Never a scrollbar, not even while an arrangement is being built: one would leave the windows narrower once it goes.
    if (fit) host.classList.add("phx-fits");
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
    if (visible > 0 && width > 0) this.api.layout(width, this.height || visible, true);
  }

  /** Lay the windows out again when the canvas's width changed without a resize, as when a scrollbar comes and goes. */
  fitWidth(): void {
    const width = (this.host.firstElementChild as HTMLElement | null)?.clientWidth ?? 0;
    if (width > 0 && this.visibleHeight() > 0 && Math.abs(width - this.api.width) >= 1) this.api.layout(width, this.api.height, true);
  }

  /** Record the current height of every docked window that has no intended height yet; call it while the layout is settled. */
  capture(): void {
    for (const [id, h] of Object.entries(gridHeights(this.api))) {
      if (this.intended[id] === undefined && !this.collapsed(id)) this.intended[id] = h;
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
    const heightOf = (id: string): number => (this.collapsed(id) ? TITLE_BAR_HEIGHT : this.intended[id] ?? MIN_WINDOW_HEIGHT);
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
    const fixed = (node: GridNode): boolean => (node.type === "leaf" ? this.collapsed(leafId(node) ?? "") : childrenOf(node).every(fixed));
    // Only windows with an intended height are sized; the others keep the share Dockview gave them.
    const sized = (node: GridNode): boolean => (node.type === "leaf"
      ? this.intended[leafId(node) ?? ""] !== undefined
      : childrenOf(node).every((c) => fixed(c) || sized(c)));
    const needs = (node: GridNode, stacks: boolean): number => requiredHeight(node, stacks, heightOf);
    // A stacked child's height is set through a window: the child itself, or a window directly in its row.
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
        const absorber = this.growEdge === "top" ? pool[0] : pool[pool.length - 1];
        for (const child of children) if (child !== absorber && !fixed(child) && sized(child)) setHeight(child, needs(child, false));
      }
      for (const child of children) walk(child, !stacks);
    };
    walk(grid.root as GridNode, rootStacks);
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
      if (this.collapsed(id)) continue;
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
    const heightOf = (id: string): number => (this.collapsed(id) ? TITLE_BAR_HEIGHT : this.intended[id] ?? MIN_WINDOW_HEIGHT);
    const required = requiredHeight(grid.root, grid.orientation === "VERTICAL", heightOf);
    if (this.height === 0 && this.lastVisible > 0 && required > visible && required <= this.lastVisible + 1) {
      const scale = visible / required;
      for (const id of Object.keys(this.intended)) {
        if (!this.collapsed(id)) this.intended[id] = Math.max(MIN_WINDOW_HEIGHT, Math.floor(this.intended[id] * scale));
      }
    }
    this.apply();
  }
}

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
    const startScroll = host.scrollTop;
    let lastY = e.clientY;
    let frame = 0;
    host.classList.add("phx-resizing");
    const apply = () => {
      const delta = Math.max(minDelta, lastY - startY + host.scrollTop - startScroll);
      canvas.intended = { ...startIntended };
      const targets: Heights = {};
      for (const id of ids) targets[id] = start[id] + delta;
      canvas.apply(targets);
    };
    const tick = () => {
      const r = host.getBoundingClientRect();
      const step = edgeScrollStep(lastY, r.top, r.bottom);
      if (step) {
        const before = host.scrollTop;
        host.scrollTop += step;
        if (host.scrollTop !== before || step > 0) apply();
      }
      frame = requestAnimationFrame(tick);
    };
    const move = (ev: PointerEvent) => { lastY = ev.clientY; apply(); };
    const end = () => {
      cancelAnimationFrame(frame);
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
