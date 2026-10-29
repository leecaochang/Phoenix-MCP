/**
 * The scrolling workspace: windows keep intended heights on a canvas that
 * grows past the screen, and every change to the docked windows pushes the
 * others down instead of squeezing them. Dockview computes window sizes from
 * the layout it is given, so jsdom can check heights with a stubbed screen.
 */

import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { DockviewReact, type AddPanelOptions, type DockviewApi } from "dockview-react";
import { WorkspaceCanvas, edgeScrollStep, leafOrder, requiredHeight } from "../shell/dock/scroll";
import { WorkspaceController } from "../shell/dock/controller";
import { TITLE_BAR_HEIGHT } from "../shell/dock/fold";

// jsdom has no layout, so every window would share one zero-sized row; in these
// stacked arrangements each window is its own row.
vi.mock("../shell/dock/geometry", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../shell/dock/geometry")>()),
  rowOf: (_api: unknown, group: unknown) => [group],
}));

const leaf = (id: string, size = 0) => ({ type: "leaf", data: { views: [id] }, size });
const branch = (data: unknown[], size = 0) => ({ type: "branch", data, size });

describe("canvas geometry", () => {
  const tree = branch([leaf("list"), branch([branch([leaf("persona"), leaf("caps")]), branch([leaf("tree"), leaf("summary")])])]);

  it("reads windows top to bottom, left to right", () => {
    expect(leafOrder(tree)).toEqual(["list", "persona", "caps", "tree", "summary"]);
  });

  it("adds stacked heights and takes the tallest of side-by-side columns", () => {
    const h: Record<string, number> = { list: 300, persona: 400, caps: 600, tree: 700, summary: 34 };
    expect(requiredHeight(tree, true, (id) => h[id])).toBe(300 + Math.max(400 + 600, 700 + 34));
    h.tree = 1200;
    expect(requiredHeight(tree, true, (id) => h[id])).toBe(300 + 1234);
  });

  it("scrolls faster the closer a drag gets to the edge, and not at all in the middle", () => {
    expect(edgeScrollStep(500, 100, 900)).toBe(0);
    expect(edgeScrollStep(899, 100, 900)).toBeGreaterThan(edgeScrollStep(875, 100, 900));
    expect(edgeScrollStep(875, 100, 900)).toBeGreaterThan(0);
    expect(edgeScrollStep(101, 100, 900)).toBeLessThan(0);
  });
});

type Arrange = (api: DockviewApi, add: (id: string, position?: AddPanelOptions["position"]) => void) => void;

/** A workspace with a stubbed visible height, the way WorkspaceDock wires it. */
function mount(windows: string[], arrange: Arrange, opts: { visible?: number; heights?: Record<string, number>; folded?: string[]; fit?: boolean; contentHeights?: boolean } = {}) {
  let visible = opts.visible ?? 600;
  const controller = new WorkspaceController("test", windows, arrange, (id) => id, { heights: opts.heights, folded: opts.folded, fit: opts.fit, contentHeights: opts.contentHeights });
  const components = Object.fromEntries(windows.map((id) => [id, () => <div>{id}</div>]));
  let hostEl: HTMLElement | null = null;
  render(
    <div className="phx-workspace" ref={(el) => { hostEl = el; }}>
      <DockviewReact components={components} disableAutoResizing onReady={(e) => {
        const host = hostEl!;
        Object.defineProperty(host, "clientHeight", { configurable: true, get: () => visible });
        Object.defineProperty(host.firstElementChild!, "clientWidth", { configurable: true, get: () => 1000 });
        controller.api = e.api;
        controller.canvas = new WorkspaceCanvas(e.api, host, (id) => controller.folds.collapsedHeight(id) !== undefined, undefined, !!opts.fit);
        controller.load(undefined);
      }} />
    </div>,
  );
  const heights = () => Object.fromEntries(controller.api!.groups.filter((g) => g.api.location.type === "grid").map((g) => [g.activePanel!.id, Math.round(g.height)]));
  const host = hostEl! as HTMLElement;
  return { controller, canvas: controller.canvas!, heights, host, setVisible: (v: number) => { visible = v; } };
}

const stacked: Arrange = (_, add) => {
  add("a");
  add("b", { referencePanel: "a", direction: "below" });
};

describe("scrolling workspace", () => {
  it("gives windows their comfortable heights and grows the canvas past the screen", () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 400, b: 500 } });
    expect(w.heights()).toEqual({ a: 400, b: 500 });
    expect(w.canvas.height).toBe(900);
    expect(w.host.style.getPropertyValue("--phx-canvas-height")).toBe("900px");
    expect(w.host.classList.contains("phx-scrolls")).toBe(true);
  });

  it("fits the screen when the windows need less, the last window taking the rest", () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 200, b: 200 } });
    expect(w.canvas.height).toBe(0);
    expect(w.host.style.getPropertyValue("--phx-canvas-height")).toBe("100%");
    expect(w.heights()).toEqual({ a: 200, b: 400 });
  });

  it("leaves windows without an intended height to share the space", () => {
    const w = mount(["a", "b"], stacked);
    const h = w.heights();
    expect(w.canvas.height).toBe(0);
    expect(h.a + h.b).toBe(600);
    expect(w.canvas.intended).toEqual({});
  });

  it("making a window taller pushes the one below down instead of squeezing it", () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 400, b: 500 } });
    w.canvas.apply({ a: 550 });
    expect(w.heights()).toEqual({ a: 550, b: 500 });
    expect(w.canvas.height).toBe(1050);
    w.canvas.apply({ a: 300 });
    expect(w.heights()).toEqual({ a: 300, b: 500 });
    expect(w.canvas.height).toBe(800);
  });

  it("keeps each column's windows at their heights, the shorter column's last window filling the difference", () => {
    const w = mount(["list", "p", "c", "t"], (_, add) => {
      add("list");
      add("p", { direction: "below" });
      add("t", { referencePanel: "p", direction: "right" });
      add("c", { referencePanel: "p", direction: "below" });
    }, { heights: { list: 200, p: 300, c: 500, t: 400 } });
    expect(w.canvas.height).toBe(200 + 800);
    expect(w.heights()).toEqual({ list: 200, p: 300, c: 500, t: 800 });
    expect(w.canvas.intended.t).toBe(400);
  });

  it("moves the border under two columns as far as the pointer, with a full-width window below keeping its height", () => {
    const w = mount(["a", "b", "c", "d", "e"], (_, add) => {
      add("a");
      add("c", { referencePanel: "a", direction: "right" });
      add("b", { referencePanel: "a", direction: "below" });
      add("d", { referencePanel: "c", direction: "below" });
      add("e", { direction: "below" });
    }, { heights: { a: 300, b: 300, c: 300, d: 300, e: 300 } });
    expect(w.heights()).toEqual({ a: 300, b: 300, c: 300, d: 300, e: 300 });
    // Dragging the top of the bottom window down grows the columns' lowest windows.
    w.canvas.apply({ b: 400, d: 400 });
    expect(w.heights()).toEqual({ a: 300, b: 400, c: 300, d: 400, e: 300 });
    expect(w.canvas.height).toBe(1000);
    // The bottom edge grows the bottom window alone.
    w.canvas.apply({ e: 500 });
    expect(w.heights()).toEqual({ a: 300, b: 400, c: 300, d: 400, e: 500 });
  });

  it("keeps the workspace scrollable as far as it was while a border is dragged, and lets it go after", () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 400, b: 500 } });
    Object.defineProperty(w.host, "scrollHeight", { configurable: true, get: () => w.canvas.height });
    const release = w.canvas.holdScrollHeight();
    w.canvas.apply({ b: 300 });
    expect(w.canvas.height).toBe(700);
    expect(w.host.style.getPropertyValue("--phx-canvas-hold")).toBe("900px");
    release();
    expect(w.host.style.getPropertyValue("--phx-canvas-hold")).toBe("");
  });

  it("folding gives the height back to the page and unfolding takes it again", () => {
    const w = mount(["a", "b", "c"], (_, add) => {
      add("a");
      add("b", { referencePanel: "a", direction: "below" });
      add("c", { referencePanel: "b", direction: "below" });
    }, { heights: { a: 400, b: 500, c: 300 } });
    expect(w.canvas.height).toBe(1200);
    w.controller.toggleFold(w.controller.api!.getPanel("b")!.group);
    expect(w.heights()).toEqual({ a: 400, b: TITLE_BAR_HEIGHT, c: 300 });
    expect(w.canvas.height).toBe(400 + TITLE_BAR_HEIGHT + 300);
    w.controller.toggleFold(w.controller.api!.getPanel("b")!.group);
    expect(w.heights()).toEqual({ a: 400, b: 500, c: 300 });
    expect(w.canvas.height).toBe(1200);
  });

  it("closing a window shortens the page; reopening brings it back at its height", () => {
    const w = mount(["a", "b", "c"], (_, add) => {
      add("a");
      add("b", { referencePanel: "a", direction: "below" });
      add("c", { referencePanel: "b", direction: "below" });
    }, { heights: { a: 400, b: 500, c: 300 } });
    w.controller.close(w.controller.api!.getPanel("b")!.group);
    expect(w.heights()).toEqual({ a: 400, c: 300 });
    expect(w.canvas.height).toBe(700);
    w.controller.reopen("b");
    expect(w.heights()).toEqual({ a: 400, b: 500, c: 300 });
    expect(w.canvas.height).toBe(1200);
  });

  it("keyboard resizing pushes the windows below down too", () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 400, b: 500 } });
    w.controller.resizeHeight(w.controller.api!.getPanel("a")!.group, 40);
    expect(w.heights()).toEqual({ a: 440, b: 500 });
    expect(w.canvas.height).toBe(940);
  });

  it("maximizing fits the screen; restoring brings the canvas back", () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 400, b: 500 } });
    const a = w.controller.api!.getPanel("a")!.group;
    w.controller.maximize(a);
    expect(w.canvas.height).toBe(0);
    expect(Math.round(a.height)).toBe(600);
    w.controller.restoreMaximized(a);
    w.controller.maximizedChanged();
    expect(w.canvas.height).toBe(900);
    expect(w.heights()).toEqual({ a: 400, b: 500 });
  });

  it("a layout that fitted keeps fitting when the screen gets shorter; a scrolling one keeps its heights", () => {
    const fits = mount(["a", "b"], stacked, { heights: { a: 300, b: 300 } });
    expect(fits.canvas.height).toBe(0);
    fits.setVisible(540);
    fits.canvas.refit();
    expect(fits.canvas.height).toBe(0);
    const h = fits.heights();
    expect(h.a + h.b).toBe(540);

    const scrolls = mount(["a", "b"], stacked, { heights: { a: 400, b: 500 } });
    scrolls.setVisible(700);
    scrolls.canvas.refit();
    expect(scrolls.canvas.height).toBe(900);
    expect(scrolls.heights()).toEqual({ a: 400, b: 500 });
  });

  it("saves the intended heights with the arrangement and loads them back", () => {
    const first = mount(["a", "b"], stacked, { heights: { a: 400, b: 500 } });
    first.canvas.apply({ a: 650 });
    const saved = first.controller.layout()!;
    expect(saved.heights).toEqual({ a: 650, b: 500 });
    const second = mount(["a", "b"], stacked);
    second.controller.load(saved);
    expect(second.heights()).toEqual({ a: 650, b: 500 });
    expect(second.canvas.height).toBe(1150);
  });

  it("ignores saved heights that are not usable numbers", () => {
    const first = mount(["a", "b"], stacked, { heights: { a: 400, b: 500 } });
    const saved = { ...first.controller.layout()!, heights: { a: Number.NaN, b: -5, ghost: 300 } as Record<string, number> };
    const second = mount(["a", "b"], stacked);
    second.controller.load(saved);
    expect(second.canvas.intended).toEqual({});
    expect(second.canvas.height).toBe(0);
  });
});

describe("a fitted workspace (Logs)", () => {
  it("never scrolls: windows taller than the screen shrink to share it", () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 400, b: 500 }, fit: true });
    const h = w.heights();
    expect(w.host).not.toHaveClass("phx-scrolls");
    expect(h.a + h.b).toBeLessThanOrEqual(600);
    expect(h.a + h.b).toBeGreaterThan(590);
    // In proportion: the taller window stays taller.
    expect(h.b).toBeGreaterThan(h.a);
  });

  it("sizes a window to its content and gives the space left to the other window", () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 400, b: 200 }, fit: true });
    w.controller.sizeToContent("b", 120);
    expect(w.heights()).toEqual({ a: 480, b: 120 });
    w.controller.sizeToContent("b", 260);
    expect(w.heights()).toEqual({ a: 340, b: 260 });
  });

  it("only ever makes a window the user resized or moved smaller", () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 400, b: 200 }, fit: true });
    w.controller.markUserSized(["b"]);
    w.controller.sizeToContent("b", 320);
    expect(w.heights().b).toBe(200);
    w.controller.sizeToContent("b", 150);
    expect(w.heights()).toEqual({ a: 450, b: 150 });
  });

  it("gives height freed below the workspace to its lowest window, as when Help closes", () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 400, b: 200 }, fit: true });
    expect(w.heights()).toEqual({ a: 400, b: 200 });
    w.setVisible(700);
    w.canvas.refit();
    expect(w.heights()).toEqual({ a: 400, b: 300 });
    expect(w.host).toHaveClass("phx-fits");
  });

  it("keeps a window just sized and shrinks the others when the content would not fit", () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 500, b: 100 }, fit: true });
    w.controller.sizeToContent("b", 360);
    expect(w.heights()).toEqual({ a: 240, b: 360 });
    expect(w.host).not.toHaveClass("phx-scrolls");
  });
});

const nextFrame = () => new Promise((resolve) => requestAnimationFrame(() => resolve(null)));

describe("windows hidden in place (Access Keys with no key chosen)", () => {
  const keyLayout: Arrange = (_, add) => {
    add("list");
    add("persona", { direction: "below" });
    add("tree", { referencePanel: "persona", direction: "right" });
    add("caps", { referencePanel: "persona", direction: "below" });
  };

  it("take no space, keep their places and heights, and come back when shown", () => {
    const w = mount(["list", "persona", "tree", "caps"], keyLayout, { heights: { list: 300, persona: 400, tree: 700, caps: 500 } });
    expect(w.canvas.height).toBe(300 + 900);
    w.controller.setHiddenWindows(["persona", "tree", "caps"]);
    expect(w.controller.isHidden("tree")).toBe(true);
    // Only the list is left; it fills the screen.
    expect(w.canvas.height).toBe(0);
    expect(w.heights()).toEqual({ list: 600, persona: 0, tree: 0, caps: 0 });
    expect(w.canvas.intended).toMatchObject({ persona: 400, tree: 700, caps: 500 });
    w.controller.setHiddenWindows([]);
    expect(w.heights()).toEqual({ list: 300, persona: 400, tree: 900, caps: 500 });
    expect(w.canvas.height).toBe(1200);
  });

  it("stay hidden after a window is maximized and restored, and are never saved hidden", () => {
    const w = mount(["list", "persona", "tree", "caps"], keyLayout, { heights: { list: 300, persona: 400, tree: 700, caps: 500 } });
    w.controller.setHiddenWindows(["persona", "tree", "caps"]);
    const list = w.controller.api!.getPanel("list")!.group;
    w.controller.maximize(list);
    w.controller.restoreMaximized(list);
    w.controller.maximizedChanged();
    expect(w.heights()).toEqual({ list: 600, persona: 0, tree: 0, caps: 0 });
    // A saved arrangement loads with every window shown; what is hidden follows the selection.
    const saved = w.controller.layout()!;
    const again = mount(["list", "persona", "tree", "caps"], keyLayout, { heights: { list: 300, persona: 400, tree: 700, caps: 500 } });
    again.controller.load(saved);
    expect(again.heights().tree).toBeGreaterThan(0);
  });
});

describe("windows that follow their content (Access Keys)", () => {
  it("take the height their content reports, and keyboard resizing leaves them alone", async () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 300, b: 300 }, contentHeights: true });
    expect(w.controller.contentSized).toBe(true);
    w.controller.contentResized("a", 520);
    w.controller.contentResized("b", 940);
    await nextFrame();
    expect(w.heights()).toEqual({ a: 520, b: 940 });
    expect(w.canvas.height).toBe(1460);
    w.controller.resizeHeight(w.controller.api!.getPanel("a")!.group, 40);
    expect(w.heights().a).toBe(520);
    // Content that shrinks makes the window shorter too.
    w.controller.contentResized("b", 400);
    await nextFrame();
    expect(w.heights()).toEqual({ a: 520, b: 400 });
  });

  it("leave a folded window folded, and other workspaces ignore content sizes", async () => {
    const w = mount(["a", "b"], stacked, { heights: { a: 300, b: 300 }, folded: ["a"], contentHeights: true });
    w.controller.contentResized("a", 800);
    await nextFrame();
    expect(w.heights().a).toBe(TITLE_BAR_HEIGHT);
    const other = mount(["a", "b"], stacked, { heights: { a: 300, b: 400 } });
    other.controller.contentResized("a", 800);
    await nextFrame();
    expect(other.heights()).toEqual({ a: 300, b: 400 });
  });

  it("a maximized window fills the screen though its frame ended at its content before (the wizard's second run)", async () => {
    const sideBySide: Arrange = (_, add) => {
      add("a");
      add("b", { referencePanel: "a", direction: "right" });
    };
    const w = mount(["a", "b"], sideBySide, { heights: { a: 300, b: 300 }, contentHeights: true });
    w.controller.contentResized("a", 200);
    w.controller.contentResized("b", 500);
    await nextFrame();
    const a = w.controller.api!.getPanel("a")!.group;
    const ended = a.element.style.getPropertyValue("--phx-slack");
    expect(ended).not.toBe("");
    // Dockview reports the maximize; nothing else lays the windows out again.
    w.controller.maximize(a);
    w.controller.maximizedChanged();
    expect(a.element.style.getPropertyValue("--phx-slack")).toBe("");
    w.controller.restoreMaximized(a);
    w.controller.maximizedChanged();
    expect(a.element.style.getPropertyValue("--phx-slack")).toBe(ended);
  });
});
