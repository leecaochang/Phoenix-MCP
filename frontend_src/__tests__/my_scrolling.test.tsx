/**
 * Behaviour: My Workspace's Vertical Scrolling. Turned on, every window gets
 * room at once (its height from before the workspace was fitted, else the
 * larger of its height and its default), windows side by side take the
 * tallest of their row, and the workspace scrolls. Turned off, the windows
 * fit the screen and their heights are remembered, in the saved layout too,
 * for turning it on again.
 */
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { DockviewReact, type AddPanelOptions, type DockviewApi } from "dockview-react";
import { WorkspaceCanvas } from "../shell/dock/scroll";
import { WorkspaceController } from "../shell/dock/controller";

// jsdom has no layout: in these arrangements each stacked window is its own row.
vi.mock("../shell/dock/geometry", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../shell/dock/geometry")>()),
  rowOf: (_api: unknown, group: unknown) => [group],
}));

type Arrange = (api: DockviewApi, add: (id: string, position?: AddPanelOptions["position"]) => void) => void;
const VISIBLE = 600;

function mount(windows: string[], arrange: Arrange, heights: Record<string, number>) {
  const controller = new WorkspaceController("my", windows, arrange, (id) => id, { heights, scrollable: true });
  const components = Object.fromEntries(windows.map((id) => [id, () => <div>{id}</div>]));
  let hostEl: HTMLElement | null = null;
  render(
    <div className="phx-workspace" ref={(el) => { hostEl = el; }}>
      <DockviewReact components={components} disableAutoResizing onReady={(e) => {
        const host = hostEl!;
        Object.defineProperty(host, "clientHeight", { configurable: true, get: () => VISIBLE });
        Object.defineProperty(host.firstElementChild!, "clientWidth", { configurable: true, get: () => 1000 });
        controller.api = e.api;
        controller.canvas = new WorkspaceCanvas(e.api, host, (id) => controller.folds.collapsedHeight(id) !== undefined, undefined, controller.fits);
        controller.load(undefined);
      }} />
    </div>,
  );
  const sizes = () => Object.fromEntries(controller.api!.groups.map((g) => [g.activePanel!.id, Math.round(g.height)]));
  return { controller, canvas: controller.canvas!, sizes, host: hostEl! as HTMLElement };
}

const stacked: Arrange = (_api, add) => { add("a"); add("b", { direction: "below" }); };
const sideBySide: Arrange = (_api, add) => { add("a"); add("b", { referencePanel: "a", direction: "right" }); };

describe("Vertical Scrolling in My Workspace", () => {
  it("fits the windows to the screen until it is turned on, then gives every window its default height", () => {
    const { controller, canvas, sizes, host } = mount(["a", "b"], stacked, { a: 500, b: 700 });
    expect(controller.fits).toBe(true);
    expect(host).toHaveClass("phx-fits");
    expect(sizes().a + sizes().b).toBeLessThanOrEqual(VISIBLE);
    controller.setScrolls(true);
    expect(host).not.toHaveClass("phx-fits");
    expect(sizes()).toEqual({ a: 500, b: 700 });
    expect(canvas.height).toBe(1200);
  });

  it("remembers the heights the windows had while scrolling and gives them back", () => {
    const { controller, canvas, sizes } = mount(["a", "b"], stacked, { a: 300, b: 300 });
    controller.setScrolls(true);
    canvas.apply({ a: 800, b: 450 });
    controller.setScrolls(false);
    expect(sizes().a + sizes().b).toBeLessThanOrEqual(VISIBLE);
    expect(controller.layout()!.scrollHeights).toEqual({ a: 800, b: 450 });
    controller.setScrolls(true);
    expect(sizes()).toEqual({ a: 800, b: 450 });
    expect(controller.layout()!.scrollHeights).toBeUndefined();
  });

  it("keeps the remembered heights across a reload", () => {
    const first = mount(["a", "b"], stacked, { a: 300, b: 300 });
    first.controller.setScrolls(true);
    first.canvas.apply({ a: 900, b: 400 });
    first.controller.setScrolls(false);
    const second = mount(["a", "b"], stacked, { a: 300, b: 300 });
    second.controller.load(first.controller.layout()!);
    second.controller.setScrolls(true);
    expect(second.sizes()).toEqual({ a: 900, b: 400 });
  });

  it("never makes a window shorter, and gives side-by-side windows the tallest of their row", () => {
    const { controller, sizes } = mount(["a", "b"], sideBySide, { a: 300, b: 650 });
    controller.setScrolls(true);
    expect(sizes()).toEqual({ a: 650, b: 650 });
    const tall = mount(["a", "b"], stacked, { a: 100, b: 100 });
    const before = tall.sizes();
    tall.controller.setScrolls(true);
    expect(tall.sizes().a).toBeGreaterThanOrEqual(before.a);
    expect(tall.sizes().b).toBeGreaterThanOrEqual(before.b);
  });
});
