/**
 * Pinned bands and the fixed floating layer: a window moves between the
 * scrolling grid, a band along the top or bottom, and the floating layer, and
 * keeps its content, its height and its way back. Layouts save and reload all
 * four places.
 */

import { describe, it, expect, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { useState } from "react";
import { DockviewReact, type AddPanelOptions, type DockviewApi } from "dockview-react";
import { WorkspaceController, type HostId } from "../shell/dock/controller";
import { WorkspaceCanvas } from "../shell/dock/scroll";
import { WorkspaceDock } from "../shell/dock/WorkspaceDock";
import { DOCK_COMPONENTS } from "../shell/workspaces";

// jsdom has no layout; in these stacked arrangements each window is its own row.
vi.mock("../shell/dock/geometry", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../shell/dock/geometry")>()),
  rowOf: (_api: unknown, group: unknown) => [group],
}));

type Arrange = (api: DockviewApi, add: (id: string, position?: AddPanelOptions["position"]) => void) => void;
const WINDOWS = ["a", "b", "c"];
const stacked: Arrange = (_, add) => {
  add("a");
  add("b", { referencePanel: "a", direction: "below" });
  add("c", { referencePanel: "b", direction: "below" });
};

/** A controller with all four hosts, each a real Dockview instance with a stubbed size. */
function mount(opts: { heights?: Record<string, number>; pinned?: Partial<Record<"top" | "bottom", string[]>>; frame?: number; pairs?: Record<string, string>; closedAt?: Record<string, { neighbor: string; side: "bottom"; host?: "float" }>; arrange?: Arrange; floatWidths?: Record<string, number> } = {}) {
  const controller = new WorkspaceController("test", WINDOWS, opts.arrange ?? stacked, (id) => `Title ${id}`, { heights: opts.heights, pinned: opts.pinned, pairs: opts.pairs, closedAt: opts.closedAt, floatWidths: opts.floatWidths });
  const components = Object.fromEntries(WINDOWS.map((id) => [id, () => <div>{id}</div>]));
  const hosts: Partial<Record<HostId, HTMLElement>> = {};
  const apis: Partial<Record<HostId, DockviewApi>> = {};
  const sized = (id: HostId, visible: number) => (el: HTMLElement | null) => {
    if (!el) return;
    hosts[id] = el;
    // With a frame height, the grid sees what the bands leave of it, as in the browser.
    const band = (h: HostId) => Number.parseInt(hosts[h]?.style.height || "0", 10);
    const grid = () => (opts.frame ? opts.frame - band("top") - band("bottom") : visible);
    Object.defineProperty(el, "clientHeight", { configurable: true, get: () => (id === "main" ? grid() : Number.parseInt(el.style.height || "0", 10)) });
  };
  const ready = (id: HostId) => (e: { api: DockviewApi }) => {
    apis[id] = e.api;
    if ((["main", "top", "bottom", "float"] as HostId[]).every((h) => apis[h])) {
      for (const h of ["main", "top", "bottom"] as HostId[]) {
        Object.defineProperty(hosts[h]!.firstElementChild!, "clientWidth", { configurable: true, get: () => 1000 });
      }
      const collapsed = (w: string) => controller.folds.collapsedHeight(w) !== undefined;
      controller.attach("main", apis.main!, new WorkspaceCanvas(apis.main!, hosts.main!, collapsed));
      controller.attach("top", apis.top!, new WorkspaceCanvas(apis.top!, hosts.top!, collapsed, () => 300));
      controller.attach("bottom", apis.bottom!, new WorkspaceCanvas(apis.bottom!, hosts.bottom!, collapsed, () => 300));
      controller.attach("float", apis.float!);
      controller.load(undefined);
    }
  };
  render(
    <div>
      {(["top", "main", "bottom"] as HostId[]).map((h) => (
        <div key={h} ref={sized(h, 600)}><DockviewReact components={components} disableAutoResizing onReady={ready(h)} /></div>
      ))}
      <div><DockviewReact components={components} onReady={ready("float")} /></div>
    </div>,
  );
  const heights = (host: HostId) => Object.fromEntries((apis[host]?.groups ?? []).filter((g) => g.api.location.type === "grid").map((g) => [g.activePanel!.id, Math.round(g.height)]));
  const group = (id: string) => controller.groupOf(id)!;
  return { controller, apis, hosts, heights, group };
}

describe("pinned bands and the floating layer", () => {
  it("reopens a paired window beside its partner in the band, alone at the partner's height, and the partner beside it", () => {
    // c is paired to the right of a, as Notifications is to Help.
    const w = mount({ heights: { a: 200, b: 300, c: 300 }, pinned: { bottom: ["a"] }, pairs: { c: "a" } });
    w.controller.close(w.group("c"));
    w.controller.reopen("c");
    expect(w.controller.hostOf("c")).toBe("bottom");
    expect(w.group("c")).not.toBe(w.group("a"));
    // Left to right, in the order the band draws its windows.
    const sideBySide = () => [...w.hosts.bottom!.querySelectorAll<HTMLElement>(".dv-groupview")].map((el) => w.apis.bottom!.groups.find((g) => g.element === el)!.activePanel!.id);
    expect(sideBySide()).toEqual(["a", "c"]);
    expect(w.heights("bottom")).toEqual({ a: 200, c: 200 });

    // Without its partner it takes the band alone, at the partner's height.
    w.controller.close(w.group("a"));
    w.controller.close(w.group("c"));
    w.controller.reopen("c");
    expect(w.controller.hostOf("c")).toBe("bottom");
    expect(w.hosts.bottom!.style.height).toBe("200px");
    // The partner comes back to its left.
    w.controller.reopen("a");
    expect(w.controller.hostOf("a")).toBe("bottom");
    expect(sideBySide()).toEqual(["a", "c"]);
  });

  it("reopens a paired window in the top band it was pinned to, not the bottom", () => {
    const w = mount({ heights: { a: 200, b: 300, c: 300 }, pinned: { bottom: ["a"] }, pairs: { c: "a" } });
    w.controller.pin(w.group("a"), "top");
    w.controller.close(w.group("a"));
    w.controller.reopen("a");
    expect(w.controller.hostOf("a")).toBe("top");
    // Its partner, never shown yet, joins it there.
    w.controller.reopen("c");
    expect(w.controller.hostOf("c")).toBe("top");
    w.controller.close(w.group("c"));
    w.controller.close(w.group("a"));
    // The partner closed too: each still comes back to the top band.
    w.controller.reopen("c");
    expect(w.controller.hostOf("c")).toBe("top");
  });

  it("reopens a paired window that was floating as floating, not beside its partner in the band", () => {
    const w = mount({ heights: { a: 200, b: 300, c: 300 }, pinned: { bottom: ["a"] }, pairs: { c: "a" } });
    w.controller.reopen("c");
    w.controller.float(w.group("a"));
    expect(w.controller.hostOf("a")).toBe("float");
    w.controller.close(w.group("a"));
    w.controller.reopen("a");
    expect(w.controller.hostOf("a")).toBe("float");
  });

  it("pins a floating window at the height it had before it floated, not its floating size", () => {
    const w = mount({ heights: { a: 120, b: 300, c: 300 }, pinned: { bottom: ["a"] } });
    expect(w.heights("bottom")).toEqual({ a: 120 });
    w.controller.float(w.group("a"));
    expect(w.controller.hostOf("a")).toBe("float");
    // Hidden and shown again while floating, it still remembers the band it floated from.
    w.controller.close(w.group("a"));
    w.controller.reopen("a");
    expect(w.controller.hostOf("a")).toBe("float");
    w.controller.pin(w.group("a"), "top");
    expect(w.controller.hostOf("a")).toBe("top");
    expect(w.heights("top")).toEqual({ a: 120 });
    w.controller.float(w.group("a"));
    w.controller.dock(w.group("a"));
    expect(w.controller.hostOf("a")).toBe("top");
  });

  it("pins a floating paired window beside its partner, bringing the partner along", () => {
    const w = mount({ heights: { a: 120, b: 300, c: 300 }, pinned: { bottom: ["a"] }, pairs: { c: "a" } });
    w.controller.reopen("c");
    w.controller.float(w.group("c"));
    w.controller.pin(w.group("a"), "top");
    w.controller.pin(w.group("c"), "bottom");
    expect(w.controller.hostOf("a")).toBe("bottom");
    expect(w.controller.hostOf("c")).toBe("bottom");
    expect(w.heights("bottom")).toEqual({ a: 120, c: 120 });
  });

  it("pins or docks a folded floating window as just its title bar, and unfolds it to its own height", () => {
    const w = mount({ heights: { a: 120, b: 300, c: 300 }, pinned: { bottom: ["a"] } });
    w.controller.toggleFold(w.group("a"));
    w.controller.float(w.group("a"));
    w.controller.pin(w.group("a"), "top");
    expect(w.controller.folds.isFolded("a")).toBe(true);
    // The band shows only the title bar (it draws its grid at Dockview's smallest size and clips it).
    expect(w.hosts.top!.style.height).toBe("34px");
    w.controller.toggleFold(w.group("a"));
    expect(w.heights("top")).toEqual({ a: 120 });
    expect(w.hosts.top!.style.height).toBe("120px");
    // Folded while floating, then docked into the grid.
    w.controller.float(w.group("b"));
    w.controller.toggleFold(w.group("b"));
    w.controller.dock(w.group("b"));
    expect(w.controller.hostOf("b")).toBe("main");
    expect(w.heights("main").b).toBe(34);
  });

  it("shrinks a folded window to its title bar once its open neighbour leaves", () => {
    const w = mount({ heights: { a: 120, b: 300, c: 300 }, pinned: { bottom: ["a"] }, pairs: { c: "a" } });
    w.controller.reopen("c");
    w.controller.toggleFold(w.group("a"));
    // Beside an open window, a folded one keeps the row's height.
    expect(w.hosts.bottom!.style.height).toBe("120px");
    w.controller.close(w.group("c"));
    expect(w.hosts.bottom!.style.height).toBe("34px");
    // Growing the row back needs window geometry (rows here are stubbed one window each); the live checks cover it.
    w.controller.reopen("c");
    expect(w.controller.folds.isFolded("a")).toBe(true);
  });

  it("remembers a closed window's place by the window it shares its column with", () => {
    // a over b on the left, c on the right: c's column holds only c, b's sibling is a.
    const columns: Arrange = (_, add) => { add("a"); add("c", { referencePanel: "a", direction: "right" }); add("b", { referencePanel: "a", direction: "below" }); };
    const w = mount({ arrange: columns });
    w.controller.close(w.group("b"));
    expect(w.controller.layout()!.closed.b).toMatchObject({ neighbor: "a", side: "bottom" });
    w.controller.reopen("b");
    w.controller.close(w.group("a"));
    expect(w.controller.layout()!.closed.a).toMatchObject({ neighbor: "b", side: "top" });
  });

  it("docks a window back along the grid edge it floated from, beside the whole column", () => {
    // As in Logs: a over b on the left, c down the right half.
    const columns: Arrange = (_, add) => { add("a"); add("c", { referencePanel: "a", direction: "right" }); add("b", { referencePanel: "a", direction: "below" }); };
    const w = mount({ arrange: columns });
    const shape = () => { const g = w.apis.main!.toJSON().grid; const kids = (g.root.data as Array<{ type: string; data: unknown }>); return { orientation: g.orientation, kinds: kids.map((k) => k.type), last: (kids[kids.length - 1].data as { views?: string[] }).views }; };
    expect(shape()).toEqual({ orientation: "HORIZONTAL", kinds: ["branch", "leaf"], last: ["c"] });
    w.controller.float(w.group("c"));
    w.controller.dock(w.group("c"));
    expect(shape()).toEqual({ orientation: "HORIZONTAL", kinds: ["branch", "leaf"], last: ["c"] });
  });

  it("floats a window the arrangement floats at its own width, wider than other floating windows (Archived Access Keys)", () => {
    const twoOpen: Arrange = (_, add) => { add("a"); add("b", { referencePanel: "a", direction: "below" }); };
    const w = mount({ arrange: twoOpen, closedAt: { c: { neighbor: "a", side: "bottom", host: "float" } }, floatWidths: { c: 960 } });
    w.controller.want("c", true);
    expect(w.controller.hostOf("c")).toBe("float");
    expect(w.group("c").element.closest<HTMLElement>(".dv-resize-container")?.style.width).toBe("960px");
  });

  it("opens a window by itself until the user closes it, then only after a reopen or a reset", () => {
    const twoOpen: Arrange = (_, add) => { add("a"); add("b", { referencePanel: "a", direction: "below" }); };
    const w = mount({ arrange: twoOpen, closedAt: { c: { neighbor: "a", side: "bottom" } } });
    w.controller.autoOpen("c");
    expect(w.controller.hostOf("c")).toBe("main");
    w.controller.close(w.group("c"));
    w.controller.autoOpen("c");
    expect(w.controller.hostOf("c")).toBeNull();
    // The choice survives a reload of the arrangement.
    const saved = w.controller.layout()!;
    expect(saved.dismissed).toEqual(["c"]);
    w.controller.load(saved);
    w.controller.autoOpen("c");
    expect(w.controller.hostOf("c")).toBeNull();
    w.controller.reopen("c");
    w.controller.close(w.group("c"));
    w.controller.restoreOriginal();
    w.controller.autoOpen("c");
    expect(w.controller.hostOf("c")).toBe("main");
  });

  it("pins both paired windows together, side by side in the new band", () => {
    const w = mount({ heights: { a: 200, b: 300, c: 300 }, pinned: { bottom: ["a"] }, pairs: { c: "a" } });
    w.controller.reopen("c");
    expect(w.controller.hostOf("c")).toBe("bottom");
    w.controller.pin(w.group("c"), "top");
    expect(w.controller.hostOf("a")).toBe("top");
    expect(w.controller.hostOf("c")).toBe("top");
    const order = [...w.hosts.top!.querySelectorAll<HTMLElement>(".dv-groupview")].map((el) => w.apis.top!.groups.find((g) => g.element === el)!.activePanel!.id);
    expect(order).toEqual(["a", "c"]);
    expect(w.heights("top")).toEqual({ a: 200, c: 200 });
    expect(w.apis.bottom!.panels).toHaveLength(0);
  });

  it("gives height freed by a top band to the window just below it", () => {
    const w = mount({ heights: { a: 200, b: 200, c: 100 }, frame: 600 });
    w.controller.pin(w.group("c"), "top");
    const before = w.heights("main");
    w.controller.close(w.group("c"));
    const after = w.heights("main");
    expect(after.a).toBeGreaterThan(before.a);
    expect(after.b).toBe(before.b);
  });

  it("floats a closed window over the workspace from a tool, and closes it from the same tool", () => {
    const w = mount();
    w.controller.close(w.group("b"));
    w.controller.toggleFloating("b", 680, 640);
    expect(w.controller.hostOf("b")).toBe("float");
    expect(w.group("b").api.location.type).toBe("floating");
    w.controller.toggleFloating("b", 680, 640);
    expect(w.controller.hostOf("b")).toBeNull();
    expect(w.controller.closedWindows()).toContain("b");
  });

  it("pins a window to a band, keeps its height there, and returns it to its place on Unpin", () => {
    const w = mount({ heights: { a: 300, b: 250, c: 300 } });
    w.controller.pin(w.group("b"), "bottom");
    expect(w.controller.hostOf("b")).toBe("bottom");
    expect(w.heights("bottom")).toEqual({ b: 250 });
    expect(w.hosts.bottom!.style.height).toBe("250px");
    expect(w.heights("main")).toEqual({ a: 300, c: 300 });
    w.controller.unpin(w.group("b"));
    expect(w.controller.hostOf("b")).toBe("main");
    // Its place beside a neighbour needs window geometry, which jsdom lacks; the live checks cover it.
    expect(w.heights("main")).toEqual({ a: 300, b: 250, c: 300 });
    expect(w.hosts.bottom!.style.height).toBe("0px");
  });

  it("caps a band at its share of the workspace; the band scrolls beyond it", () => {
    const w = mount({ heights: { a: 300, b: 300, c: 300 } });
    w.controller.pin(w.group("a"), "top");
    w.controller.pin(w.group("b"), "top");
    expect(w.controller.hostOf("a")).toBe("top");
    expect(w.hosts.top!.style.height).toBe("300px");
    expect(w.controller.canvasOf("top")!.height).toBe(600);
  });

  it("lists the top band first, then the grid, the bottom band and floating windows", () => {
    const w = mount();
    w.controller.pin(w.group("c"), "top");
    w.controller.pin(w.group("a"), "bottom");
    expect(w.controller.windowOrder()).toEqual(["c", "b", "a"]);
    w.controller.float(w.group("b"));
    expect(w.controller.hostOf("b")).toBe("float");
    expect(w.controller.windowOrder()).toEqual(["c", "a", "b"]);
  });

  it("floats a window into the fixed layer and docks it back to its slot at its height", () => {
    const w = mount({ heights: { a: 300, b: 250, c: 300 } });
    w.controller.float(w.group("b"));
    expect(w.controller.hostOf("b")).toBe("float");
    expect(w.group("b").api.location.type).toBe("floating");
    expect(w.heights("main")).toEqual({ a: 300, c: 300 });
    w.controller.dock(w.group("b"));
    expect(w.controller.hostOf("b")).toBe("main");
    expect(w.heights("main")).toEqual({ a: 300, b: 250, c: 300 });
  });

  it("reopens a window closed from a band into that band", () => {
    const w = mount();
    w.controller.pin(w.group("b"), "top");
    w.controller.close(w.group("b"));
    expect(w.controller.closedWindows()).toEqual(["b"]);
    w.controller.reopen("b");
    expect(w.controller.hostOf("b")).toBe("top");
  });

  it("keeps only the arrangement's own heights in the grid when the default arrangement pins a window", () => {
    // Otherwise the last window, drawn taller to fill the screen, would snap back on the first push-down resize.
    const w = mount({ heights: { a: 300 }, pinned: { bottom: ["c"] } });
    expect(w.controller.hostOf("c")).toBe("bottom");
    expect(w.controller.canvasOf("main")!.intended).toEqual({ a: 300 });
  });

  it("lays out the bands before the grid when loading, so a saved arrangement taller than the rest of the screen still scrolls", () => {
    const first = mount({ heights: { a: 330, b: 330, c: 100 }, pinned: { bottom: ["c"] } });
    const saved = first.controller.layout()!;
    expect(saved.heights).toMatchObject({ a: 330, b: 330 });
    // 700 px of workspace: the band takes 100, leaving 600 for 660 px of windows.
    const second = mount({ frame: 700 });
    second.controller.load(saved);
    expect(second.controller.hostOf("c")).toBe("bottom");
    expect(second.controller.canvasOf("main")!.height).toBe(660);
  });

  it("reopens a window an older save did not know into the band the default arrangement pins it to", () => {
    const first = mount({ pinned: { bottom: ["c"] } });
    first.controller.close(first.group("c"));
    const saved = { ...first.controller.layout()!, closed: {} };
    const second = mount({ pinned: { bottom: ["c"] } });
    second.controller.load(saved);
    expect(second.controller.closedWindows()).toEqual(["c"]);
    second.controller.reopen("c");
    expect(second.controller.hostOf("c")).toBe("bottom");
  });

  it("saves bands and floating windows and loads them back, each window once", () => {
    const first = mount({ heights: { a: 300, b: 250, c: 300 } });
    first.controller.pin(first.group("a"), "top");
    first.controller.float(first.group("c"));
    const saved = first.controller.layout()!;
    expect(Object.keys(saved.bands?.top?.dock.panels ?? {})).toEqual(["a"]);
    expect(Object.keys(saved.floats?.panels ?? {})).toEqual(["c"]);
    const second = mount();
    second.controller.load(saved);
    expect(second.controller.hostOf("a")).toBe("top");
    expect(second.controller.hostOf("b")).toBe("main");
    expect(second.controller.hostOf("c")).toBe("float");
    expect(second.controller.windowOrder()).toEqual(["a", "b", "c"]);
  });

  it("moves floating windows saved in the grid by an older build into the fixed layer", () => {
    const first = mount();
    // Float inside the grid itself, as before the floating layer existed.
    first.apis.main!.addFloatingGroup(first.group("b"), { position: { left: 10, top: 10 }, width: 400, height: 300 });
    const saved = first.controller.layout()!;
    expect(saved.floats).toBeUndefined();
    const second = mount();
    second.controller.load(saved);
    expect(second.controller.hostOf("b")).toBe("float");
  });

  it("Reset This Workspace empties the bands and the floating layer and pins the defaults", () => {
    const w = mount({ pinned: { bottom: ["c"] } });
    expect(w.controller.hostOf("c")).toBe("bottom");
    w.controller.unpin(w.group("c"));
    w.controller.pin(w.group("a"), "top");
    w.controller.float(w.group("b"));
    w.controller.restoreOriginal();
    expect(w.controller.hostOf("a")).toBe("main");
    expect(w.controller.hostOf("b")).toBe("main");
    expect(w.controller.hostOf("c")).toBe("bottom");
    expect(w.apis.float!.panels).toHaveLength(0);
    expect(w.hosts.top!.style.height).toBe("0px");
  });

  it("Reset This Workspace forgets where floating windows were moved", () => {
    const first = mount();
    const saved = { ...first.controller.layout()!, floatBoxes: { b: { left: 300, top: 200, width: 420, height: 320 } } };
    const w = mount();
    w.controller.load(saved);
    expect(w.controller.layout()!.floatBoxes).toEqual(saved.floatBoxes);
    w.controller.restoreOriginal();
    expect(w.controller.layout()!.floatBoxes).toBeUndefined();
  });

  // jsdom measures every frame as 0 by 0, as a browser does a panel removed from the page.
  it("never saves a floating window at the size of a frame that is not on screen", () => {
    const w = mount();
    w.controller.float(w.group("b"));
    const position = w.controller.layout()!.floats!.floatingGroups![0].position as { width: number; height: number };
    expect(position.width).toBeGreaterThan(0);
    expect(position.height).toBeGreaterThan(0);
  });

  it("restores a floating window saved at 0 by 0 at its last measured box", () => {
    const first = mount();
    first.controller.float(first.group("b"));
    const broken = first.controller.layout()!;
    const floats = { ...broken.floats!, floatingGroups: broken.floats!.floatingGroups!.map((g) => ({ ...g, position: { top: 402, right: 55, width: 0, height: 0 } })) };
    const w = mount();
    w.controller.load({ ...broken, floats, floatBoxes: { b: { left: 300, top: 200, width: 420, height: 320 } } });
    expect(w.controller.hostOf("b")).toBe("float");
    const frame = w.apis.float!.getPanel("b")!.group.element.closest<HTMLElement>(".dv-resize-container")!;
    expect(frame.style.width).toBe("420px");
    expect(frame.style.height).toBe("320px");
  });
});

describe("window content across moves", () => {
  it("keeps a window's content mounted, with its state, when it is pinned, floated, docked and unpinned", () => {
    let mounts = 0;
    function Counter({ id }: { id: string }) {
      const [count, setCount] = useState(() => { mounts++; return 0; });
      return <button type="button" data-testid={`count-${id}`} onClick={() => setCount(count + 1)}>{count}</button>;
    }
    const controller = new WorkspaceController("test", WINDOWS, stacked, (id) => id);
    const slots = Object.fromEntries(WINDOWS.map((id) => [id, DOCK_COMPONENTS.accessKeys]));
    const { getByTestId } = render(
      <WorkspaceDock controller={controller} components={slots} renderWindow={(id) => <Counter id={id} />}
        watermark={() => null} saved={undefined} visible onLayoutChange={() => {}} workspaceMenu={() => []} />,
    );
    expect(mounts).toBe(3);
    act(() => getByTestId("count-b").click());
    act(() => getByTestId("count-b").click());
    expect(getByTestId("count-b").textContent).toBe("2");
    act(() => controller.pin(controller.groupOf("b")!, "bottom"));
    act(() => controller.float(controller.groupOf("b")!));
    act(() => controller.dock(controller.groupOf("b")!));
    // Docking returns it to the band it was floated from; Unpin returns it to the grid.
    expect(controller.hostOf("b")).toBe("bottom");
    act(() => controller.unpin(controller.groupOf("b")!));
    expect(controller.hostOf("b")).toBe("main");
    expect(getByTestId("count-b").textContent).toBe("2");
    expect(mounts).toBe(3);
  });
});
