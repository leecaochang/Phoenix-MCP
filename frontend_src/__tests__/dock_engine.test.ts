import { describe, it, expect, vi } from "vitest";
import type { DockviewApi, DockviewGroupPanel } from "dockview-react";
import { largestGridGroup, moveWindow, neighbor, rowOf, slotOf } from "../shell/dock/geometry";
import { FoldStore, MIN_WINDOW_HEIGHT, TITLE_BAR_HEIGHT, foldWindow, reapplyCollapsed, unfoldWindow } from "../shell/dock/fold";
import { sanitizeLayout } from "../shell/dock/sanitize";
import { edgeResolver } from "../shell/dock/drop";
import { DOCK_OPTIONS } from "../shell/dock/WorkspaceDock";

// A stand-in for Dockview's group API: geometry comes from fixed rectangles, and
// every call the helpers make is recorded so tests assert the public API used.
type Box = [left: number, top: number, width: number, height: number];
function fakeGroup(id: string, [left, top, width, height]: Box, location: "grid" | "floating" = "grid") {
  const group = {
    id: `g-${id}`,
    width,
    height,
    activePanel: { id },
    element: { getBoundingClientRect: () => ({ left, top, width, height, right: left + width, bottom: top + height }) },
    api: {
      location: { type: location },
      setConstraints: vi.fn(),
      setSize: vi.fn((size: { width?: number; height?: number }) => { if (size.height !== undefined) group.height = size.height; }),
      moveTo: vi.fn(),
    },
  };
  return group;
}
type FakeGroup = ReturnType<typeof fakeGroup>;
const asGroup = (g: FakeGroup) => g as unknown as DockviewGroupPanel;
const fakeApi = (...groups: FakeGroup[]) => ({ groups }) as unknown as DockviewApi;

// Two windows side by side on top, one full-width window below.
//  +-------+-------+
//  |   a   |   b   |
//  +-------+-------+
//  |       c       |
//  +---------------+
const layout = () => {
  const a = fakeGroup("a", [0, 0, 400, 300]);
  const b = fakeGroup("b", [400, 0, 400, 300]);
  const c = fakeGroup("c", [0, 300, 800, 300]);
  return { a, b, c, api: fakeApi(a, b, c) };
};

describe("window geometry", () => {
  it("finds the neighbour in each direction and none past the workspace edge", () => {
    const { a, b, c, api } = layout();
    expect(neighbor(api, asGroup(a), "right")).toBe(b);
    expect(neighbor(api, asGroup(b), "left")).toBe(a);
    expect(neighbor(api, asGroup(a), "down")).toBe(c);
    expect(neighbor(api, asGroup(c), "up")).toBeTruthy();
    expect(neighbor(api, asGroup(a), "left")).toBeNull();
    expect(neighbor(api, asGroup(c), "down")).toBeNull();
  });

  it("gives floating windows no neighbours and ignores them as neighbours", () => {
    const { a, b, c } = layout();
    const f = fakeGroup("f", [420, 20, 200, 200], "floating");
    const api = fakeApi(a, b, c, f);
    expect(neighbor(api, asGroup(f), "left")).toBeNull();
    expect(neighbor(api, asGroup(a), "right")).toBe(b);
  });

  it("moves a window to its neighbour's side through the public group API", () => {
    const { a, b, api } = layout();
    expect(moveWindow(api, asGroup(a), "right")).toBe(true);
    expect(a.api.moveTo).toHaveBeenCalledWith({ group: b, position: "right" });
    expect(moveWindow(api, asGroup(a), "left")).toBe(false);
  });

  it("remembers a window's place as a neighbour and the side to return to", () => {
    const { b, api } = layout();
    expect(slotOf(api, asGroup(b))).toEqual({ neighbor: "a", side: "right" });
  });

  it("reads a row from shared top and bottom edges", () => {
    const { a, b, c, api } = layout();
    expect(rowOf(api, asGroup(a))).toEqual([a, b]);
    expect(rowOf(api, asGroup(c))).toEqual([c]);
    expect(largestGridGroup(api)).toBe(c);
  });
});

describe("folding", () => {
  it("shrinks a full-width window to its title bar and restores its height", () => {
    const { c, api } = layout();
    const store = new FoldStore();
    foldWindow(api, store, asGroup(c));
    expect(store.isFolded("c")).toBe(true);
    expect(c.api.setConstraints).toHaveBeenCalledWith({ minimumHeight: TITLE_BAR_HEIGHT, maximumHeight: TITLE_BAR_HEIGHT });
    expect(c.api.setSize).toHaveBeenLastCalledWith({ height: TITLE_BAR_HEIGHT });
    unfoldWindow(api, store, asGroup(c));
    expect(store.isFolded("c")).toBe(false);
    expect(c.api.setConstraints).toHaveBeenLastCalledWith({ minimumHeight: MIN_WINDOW_HEIGHT, maximumHeight: Number.MAX_SAFE_INTEGER });
    expect(c.api.setSize).toHaveBeenLastCalledWith({ height: 300 });
  });

  it("hides a window beside another without shrinking the row, so the neighbour keeps its height", () => {
    const { a, b, api } = layout();
    const store = new FoldStore();
    foldWindow(api, store, asGroup(a));
    expect(store.isFolded("a")).toBe(true);
    expect(a.api.setSize).not.toHaveBeenCalled();
    expect(b.api.setSize).not.toHaveBeenCalled();
  });

  it("shrinks the row once every window in it is folded, and unfolding one expands the row", () => {
    const { a, b, api } = layout();
    const store = new FoldStore();
    foldWindow(api, store, asGroup(a));
    foldWindow(api, store, asGroup(b));
    expect(a.api.setSize).toHaveBeenLastCalledWith({ height: TITLE_BAR_HEIGHT });
    expect(b.api.setSize).toHaveBeenLastCalledWith({ height: TITLE_BAR_HEIGHT });
    // The row now measures as title bars; rowOf still finds both by shared edges.
    unfoldWindow(api, store, asGroup(a));
    expect(store.isFolded("a")).toBe(false);
    expect(store.isFolded("b")).toBe(true);
    expect(a.api.setSize).toHaveBeenLastCalledWith({ height: 300 });
    expect(b.api.setSize).toHaveBeenLastCalledWith({ height: 300 });
  });

  it("restores saved fold state only for windows that still exist, and pins collapsed rows again", () => {
    const { c, api } = layout();
    const store = new FoldStore();
    store.restore({ folded: ["c", "gone"], collapsed: { c: 280, gone: 200 } }, (id) => id === "c");
    expect(store.snapshot()).toEqual({ folded: ["c"], collapsed: { c: 280 } });
    reapplyCollapsed(api, store);
    expect(c.api.setSize).toHaveBeenLastCalledWith({ height: TITLE_BAR_HEIGHT });
  });

  it("notifies subscribers when fold state changes", () => {
    const { c, api } = layout();
    const store = new FoldStore();
    const listener = vi.fn();
    store.subscribe(listener);
    foldWindow(api, store, asGroup(c));
    expect(listener).toHaveBeenCalled();
  });
});

describe("saved layout sanitizer", () => {
  const panel = (id: string) => ({ id, contentComponent: id, title: id });
  const leaf = (id: string, views: string[]) => ({ type: "leaf", data: { id, views, activeView: views[0] }, size: 100 });
  const saved = (root: unknown, panels: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
    ({ grid: { root, width: 800, height: 600, orientation: "HORIZONTAL" }, panels, ...extra });
  const known = new Set(["audit", "conversations"]);

  it("keeps a valid layout", () => {
    const layout = saved({ type: "branch", data: [leaf("1", ["audit"]), leaf("2", ["conversations"])], size: 600 },
      { audit: panel("audit"), conversations: panel("conversations") }, { activeGroup: "2" });
    const clean = sanitizeLayout(layout, known)!;
    expect(Object.keys(clean.panels)).toEqual(["audit", "conversations"]);
    expect(clean.activeGroup).toBe("2");
  });

  it("drops a retired window and the empty slot it leaves", () => {
    const layout = saved({ type: "branch", data: [leaf("1", ["audit"]), leaf("2", ["retired"])], size: 600 },
      { audit: panel("audit"), retired: panel("retired") }, { activeGroup: "2" });
    const clean = sanitizeLayout(layout, known)!;
    expect(Object.keys(clean.panels)).toEqual(["audit"]);
    expect((clean.grid.root as { data: unknown[] }).data).toHaveLength(1);
    expect(clean.activeGroup).toBeUndefined();
  });

  it("keeps one window per slot and never places a window twice", () => {
    const layout = saved({ type: "branch", data: [leaf("1", ["audit", "conversations"]), leaf("2", ["audit"])], size: 600 },
      { audit: panel("audit"), conversations: panel("conversations") });
    const clean = sanitizeLayout(layout, known)!;
    expect(Object.keys(clean.panels)).toEqual(["audit"]);
    const leaves = (clean.grid.root as { data: Array<{ data: { views: string[] } }> }).data;
    expect(leaves.map((l) => l.data.views)).toEqual([["audit"]]);
  });

  it("refuses layouts it cannot read, so the default arrangement is used", () => {
    expect(sanitizeLayout(null, known)).toBeNull();
    expect(sanitizeLayout("{}", known)).toBeNull();
    expect(sanitizeLayout({ grid: {}, panels: {} }, known)).toBeNull();
    expect(sanitizeLayout(saved(leaf("1", ["nope"]), {}), known)).toBeNull();
    // A panel whose component does not match its window id is not trusted.
    expect(sanitizeLayout(saved(leaf("1", ["audit"]), { audit: { id: "audit", contentComponent: "conversations" } }), known)).toBeNull();
  });

  it("keeps floating windows with a position and drops the rest", () => {
    const layout = saved({ type: "branch", data: [leaf("1", ["audit"])], size: 600 },
      { audit: panel("audit"), conversations: panel("conversations") },
      { floatingGroups: [{ data: { id: "9", views: ["conversations"] }, position: { left: 10, top: 10, width: 300, height: 200 } }, { data: { id: "8", views: ["x"] } }] });
    const clean = sanitizeLayout(layout, known)!;
    expect(clean.floatingGroups).toHaveLength(1);
    expect(Object.keys(clean.panels).sort()).toEqual(["audit", "conversations"]);
  });
});

describe("drop resolver", () => {
  const event = { clientX: 0, clientY: 0 } as PointerEvent;
  const zones = new Set(["left", "right", "top", "bottom", "center"] as const);
  const rootWith = (hit: Element | null) => ({ elementFromPoint: () => hit }) as unknown as Document;
  const inContent = (() => { const outer = document.createElement("div"); outer.className = "dv-render-overlay"; const inner = document.createElement("span"); outer.append(inner); return inner; })();

  it("docks at the nearest edge anywhere over a window's content, never in its centre", () => {
    const resolve = edgeResolver(() => rootWith(inContent)).resolve;
    expect(resolve({ x: 390, y: 100, width: 400, height: 300, zones, event })?.position).toBe("right");
    expect(resolve({ x: 200, y: 290, width: 400, height: 300, zones, event })?.position).toBe("bottom");
    expect(resolve({ x: 199, y: 150, width: 400, height: 300, zones, event })?.position).not.toBe("center");
  });

  it("docks to the workspace only inside its outer band, not over a title bar", () => {
    const resolve = edgeResolver(() => rootWith(document.createElement("div"))).resolve;
    expect(resolve({ x: 4, y: 300, width: 1200, height: 800, zones, event })?.position).toBe("left");
    expect(resolve({ x: 600, y: 20, width: 1200, height: 800, zones, event })).toBeNull();
  });
});

describe("Dockview options", () => {
  it("uses no paid Dockview feature", () => {
    const paid = ["keyboardNavigation", "keyboardDocking", "layoutHistory", "dockToEdgeGroups", "autoHideEdgeGroups", "pinnedTabs", "smartGuides", "dndCompass", "license"];
    for (const key of paid) expect(DOCK_OPTIONS).not.toHaveProperty(key);
  });

  it("drags with pointer events and keeps hidden windows mounted", () => {
    expect(DOCK_OPTIONS.dndStrategy).toBe("pointer");
    expect(DOCK_OPTIONS.defaultRenderer).toBe("always");
    expect(DOCK_OPTIONS.announcements).toBe(false);
  });
});
