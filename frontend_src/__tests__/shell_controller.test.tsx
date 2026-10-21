import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { DockviewReact, type AddPanelOptions, type DockviewApi } from "dockview-react";
import { WorkspaceController } from "../shell/dock/controller";

// The controller drives a real Dockview instance in jsdom. jsdom has no layout,
// so geometry (neighbours, rows) is covered against fixed rectangles in
// dock_engine.test.ts; this covers loading, saving, closing and reopening.
const WINDOWS = ["auditLog", "conversationLog", "attention"] as const;
const arrange = (_: DockviewApi, add: (id: string, position?: AddPanelOptions["position"]) => void) => {
  add("auditLog");
  add("conversationLog", { referencePanel: "auditLog", direction: "right" });
};
const components = Object.fromEntries(WINDOWS.map((id) => [id, () => <div data-window={id}>{id}</div>]));

function mount(saved?: Parameters<WorkspaceController["load"]>[0]) {
  const controller = new WorkspaceController("logs", WINDOWS, arrange, (id) => `Title ${id}`, { transient: new Set(["attention"]) });
  render(<DockviewReact components={components} onReady={(e) => { controller.api = e.api; controller.load(saved); }} />);
  return controller;
}
const ids = (c: WorkspaceController) => c.api!.panels.map((p) => p.id);
/** Windows in layout order, read from the saved grid (api.panels lists them in creation order). */
const order = (c: WorkspaceController) =>
  (c.layout()!.dock.grid.root as unknown as { data: Array<{ data: { views: string[] } }> }).data.map((leaf) => leaf.data.views[0]);

describe("workspace controller", () => {
  it("lays out the default arrangement when nothing is saved", () => {
    const c = mount();
    expect(ids(c)).toEqual(["auditLog", "conversationLog"]);
    expect(c.api!.getPanel("auditLog")!.title).toBe("Title auditLog");
  });

  it("saves its arrangement and loads it back in a new instance", () => {
    const first = mount();
    first.api!.getPanel("auditLog")!.group.api.moveTo({ group: first.api!.getPanel("conversationLog")!.group, position: "right" });
    expect(order(first)).toEqual(["conversationLog", "auditLog"]);
    const second = mount(first.layout()!);
    expect(order(second)).toEqual(["conversationLog", "auditLog"]);
  });

  it("falls back to the default arrangement for an unusable saved layout", () => {
    const c = mount({ dock: { grid: {}, panels: {} } as never, folds: { folded: [], collapsed: {} }, closed: {} });
    expect(ids(c)).toEqual(["auditLog", "conversationLog"]);
  });

  it("never saves the shell-managed Needs Attention window", () => {
    const c = mount();
    c.showTransient("attention");
    expect(ids(c)).toContain("attention");
    const saved = c.layout()!;
    expect(Object.keys(saved.dock.panels).sort()).toEqual(["auditLog", "conversationLog"]);
    c.hideTransient("attention");
    expect(ids(c)).not.toContain("attention");
  });

  it("remembers a closed window, offers it again and reopens it", () => {
    const c = mount();
    const listener = vi.fn();
    c.subscribe(listener);
    c.close(c.api!.getPanel("conversationLog")!.group);
    expect(ids(c)).toEqual(["auditLog"]);
    expect(c.closedWindows()).toEqual(["conversationLog"]);
    expect(listener).toHaveBeenCalled();
    // A closed window survives a save and load.
    const reloaded = mount(c.layout()!);
    expect(reloaded.closedWindows()).toEqual(["conversationLog"]);
    reloaded.reopen("conversationLog");
    expect(ids(reloaded).sort()).toEqual(["auditLog", "conversationLog"]);
    expect(reloaded.closedWindows()).toEqual([]);
  });

  it("tells the shell when the user closes its managed window, and does not offer it again", () => {
    const c = mount();
    const closed = vi.fn();
    c.onTransientClose = closed;
    c.showTransient("attention");
    c.close(c.api!.getPanel("attention")!.group);
    expect(closed).toHaveBeenCalledWith("attention");
    expect(c.closedWindows()).toEqual([]);
  });

  it("floats a window and docks it back", () => {
    const c = mount();
    const group = c.api!.getPanel("auditLog")!.group;
    c.float(group);
    expect(c.api!.getPanel("auditLog")!.group.api.location.type).toBe("floating");
    c.dock(c.api!.getPanel("auditLog")!.group);
    expect(c.api!.getPanel("auditLog")!.group.api.location.type).toBe("grid");
  });

  it("restores the original arrangement and forgets closed windows", () => {
    const c = mount();
    c.close(c.api!.getPanel("auditLog")!.group);
    c.restoreOriginal();
    expect(ids(c)).toEqual(["auditLog", "conversationLog"]);
    expect(c.closedWindows()).toEqual([]);
  });

  it("re-reads titles after a language change", () => {
    let language = "en";
    const c = new WorkspaceController("logs", WINDOWS, arrange, (id) => `${language}:${id}`);
    render(<DockviewReact components={components} onReady={(e) => { c.api = e.api; c.load(undefined); }} />);
    language = "de";
    c.retitle();
    expect(c.api!.getPanel("auditLog")!.title).toBe("de:auditLog");
  });

  describe("windows shown only while a setting allows", () => {
    const KEYS = ["accessKeys", "keySettings", "presets"] as const;
    const keyComponents = Object.fromEntries(KEYS.map((id) => [id, () => <div>{id}</div>]));
    const keysArrange = (_: DockviewApi, add: (id: string, position?: AddPanelOptions["position"]) => void) => {
      add("accessKeys");
      add("keySettings", { referencePanel: "accessKeys", direction: "below" });
    };
    function mountKeys(allowed: () => boolean | null, saved?: Parameters<WorkspaceController["load"]>[0]) {
      const c = new WorkspaceController("accessKeys", KEYS, keysArrange, (id) => id, {
        folded: ["keySettings", "presets"],
        conditional: { presets: { neighbor: "keySettings", side: "bottom" } },
      });
      c.availability = (id) => (id === "presets" ? allowed() : true);
      render(<DockviewReact components={keyComponents} onReady={(e) => { c.api = e.api; c.load(saved); }} />);
      return c;
    }

    it("starts the listed windows folded", () => {
      const c = mountKeys(() => true);
      expect(c.folds.isFolded("keySettings")).toBe(true);
      expect(c.folds.isFolded("accessKeys")).toBe(false);
    });

    it("adds the window in its place, folded, once the setting is known to allow it", () => {
      let allowed: boolean | null = null;
      const c = mountKeys(() => allowed);
      expect(ids(c)).not.toContain("presets");
      allowed = true;
      c.syncAvailable();
      expect(ids(c)).toContain("presets");
      expect(c.folds.isFolded("presets")).toBe(true);
      expect(c.windowOrder()).toEqual(["accessKeys", "keySettings", "presets"]);
    });

    it("removes it without remembering it as closed, and never saves or offers it while off", () => {
      let allowed = true;
      const c = mountKeys(() => allowed);
      allowed = false;
      c.syncAvailable();
      expect(ids(c)).not.toContain("presets");
      expect(c.closedWindows()).toEqual([]);
      expect(Object.keys(c.layout()!.dock.panels)).not.toContain("presets");
      c.reopen("presets");
      expect(ids(c)).not.toContain("presets");
      allowed = true;
      c.syncAvailable();
      expect(ids(c)).toContain("presets");
    });

    it("keeps it closed when the user closed it, offering it again only while allowed", () => {
      let allowed = true;
      const c = mountKeys(() => allowed);
      c.close(c.api!.getPanel("presets")!.group);
      c.syncAvailable();
      expect(ids(c)).not.toContain("presets");
      expect(c.closedWindows()).toEqual(["presets"]);
      allowed = false;
      expect(c.closedWindows()).toEqual([]);
      allowed = true;
      c.syncAvailable();
      expect(ids(c)).not.toContain("presets");
      c.reopen("presets");
      expect(ids(c)).toContain("presets");
    });

    it("brings it back after a reload of a layout saved while it was off", () => {
      let allowed = false;
      const first = mountKeys(() => allowed);
      const saved = first.layout()!;
      allowed = true;
      const second = mountKeys(() => allowed, saved);
      expect(ids(second)).toContain("presets");
    });
  });
});
