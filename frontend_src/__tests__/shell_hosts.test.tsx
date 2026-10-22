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
function mount(opts: { heights?: Record<string, number>; pinned?: Partial<Record<"top" | "bottom", string[]>>; frame?: number } = {}) {
  const controller = new WorkspaceController("test", WINDOWS, stacked, (id) => `Title ${id}`, { heights: opts.heights, pinned: opts.pinned });
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

  it("Restore Original Layout empties the bands and the floating layer and pins the defaults", () => {
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
