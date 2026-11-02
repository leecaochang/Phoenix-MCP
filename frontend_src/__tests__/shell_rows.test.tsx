/**
 * Behaviour: windows side by side share their row by the row rules, never
 * more than four across, and each window added to My Workspace takes the next
 * place. Pins the shares for one, two even, two uneven (edge and middle) and
 * three windows before; the widths a moved window's row ends up with on a real
 * Dockview grid; the four-across refusal; and the order of places: beside,
 * a row under, splitting it, a column beside a stacked pair, splitting that,
 * and floating when only floating windows are there.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { DockviewReact, type DockviewApi, type DockviewGroupPanel } from "dockview-react";
import { WorkspaceController } from "../shell/dock/controller";
import { MAX_ACROSS, nextPlace, rowAround, rowShares, shareRow, tooManyAcross, widthsExcept } from "../shell/dock/rows";

const IDS = ["a", "b", "c", "d", "e", "f", "g", "z"];
const components = Object.fromEntries(IDS.map((id) => [id, () => <div>{id}</div>]));
const WIDTH = 1200;

function grid(): DockviewApi {
  let api: DockviewApi | null = null;
  render(<DockviewReact components={components} onReady={(e: { api: DockviewApi }) => { api = e.api; api.layout(WIDTH, 800); }} />);
  return api!;
}
const add = (api: DockviewApi, id: string, position?: { referencePanel: string; direction: "left" | "right" | "above" | "below" }) => api.addPanel({ id, component: id, title: id, position });
const width = (api: DockviewApi, id: string) => Math.round(api.getPanel(id)!.group.width);
const near = (got: number, want: number) => expect(Math.abs(got - want)).toBeLessThanOrEqual(2);

describe("row shares", () => {
  it("splits one window 50|50, makes two even ones thirds and three quarters", () => {
    expect(rowShares([], 0)).toEqual([1]);
    expect(rowShares([900], 1)).toEqual([0.5, 0.5]);
    expect(rowShares([600, 600], 2)).toEqual([1 / 3, 1 / 3, 1 / 3]);
    expect(rowShares([400, 400, 400], 0)).toEqual([0.25, 0.25, 0.25, 0.25]);
  });

  it("splits the nearer of two uneven windows at an edge, and makes thirds between them", () => {
    // Close enough to even counts as even.
    expect(rowShares([610, 590], 0)).toEqual([1 / 3, 1 / 3, 1 / 3]);
    expect(rowShares([900, 300], 0)).toEqual([0.375, 0.375, 0.25]);
    expect(rowShares([900, 300], 2)).toEqual([0.75, 0.125, 0.125]);
    expect(rowShares([900, 300], 1)).toEqual([1 / 3, 1 / 3, 1 / 3]);
  });
});

describe("a window moved into a row", () => {
  it("shares the row by the rules once Dockview has moved it", () => {
    const api = grid();
    add(api, "a");
    add(api, "b", { referencePanel: "a", direction: "right" });
    add(api, "c", { referencePanel: "a", direction: "below" });
    api.getPanel("a")!.group.api.setSize({ width: 900 });
    const moving = api.getPanel("c")!.group;
    const before = widthsExcept(api, moving);
    moving.api.moveTo({ group: api.getPanel("b")!.group, position: "right" });
    shareRow(api, "c", before);
    expect(rowAround(api, "c")).toEqual({ members: ["a", "b", "c"], index: 2 });
    near(width(api, "a"), 900);
    near(width(api, "b"), 150);
    near(width(api, "c"), 150);
  });

  it("makes four even quarters from three", () => {
    const api = grid();
    add(api, "a");
    add(api, "b", { referencePanel: "a", direction: "right" });
    add(api, "c", { referencePanel: "b", direction: "right" });
    add(api, "d", { referencePanel: "a", direction: "below" });
    const moving = api.getPanel("d")!.group;
    const before = widthsExcept(api, moving);
    moving.api.moveTo({ group: api.getPanel("a")!.group, position: "left" });
    shareRow(api, "d", before);
    for (const id of ["a", "b", "c", "d"]) near(width(api, id), WIDTH / 4);
  });
});

describe("four across", () => {
  // Rectangles stand in for a laid-out grid: jsdom lays nothing out.
  const fake = (rects: Array<[number, number, number, number]>) => {
    const groups = rects.map(([left, top, w, h], i) => ({
      id: `g${i}`,
      api: { location: { type: "grid" } },
      element: { getBoundingClientRect: () => ({ left, top, right: left + w, bottom: top + h, width: w, height: h }) },
    })) as unknown as DockviewGroupPanel[];
    return { api: { groups } as unknown as DockviewApi, groups };
  };

  it("refuses a fifth window beside a row of four, but lets one of the four move within it", () => {
    const { api, groups } = fake([[0, 0, 100, 100], [100, 0, 100, 100], [200, 0, 100, 100], [300, 0, 100, 100], [0, 100, 400, 100]]);
    expect(MAX_ACROSS).toBe(4);
    expect(tooManyAcross(api, groups[0], groups[4])).toBe(true);
    expect(tooManyAcross(api, groups[0], groups[3])).toBe(false);
    // Beside the full-width window below there is room.
    expect(tooManyAcross(api, groups[4], groups[0])).toBe(false);
    // Along a side edge of the whole workspace, the full row counts too.
    expect(tooManyAcross(api, null, groups[4])).toBe(true);
  });
});

describe("the next place in My Workspace", () => {
  function mine(floating = false) {
    const c = new WorkspaceController("my", [], () => {}, (id) => id, { collect: () => true, scrollable: true });
    let api: DockviewApi | null = null;
    render(<DockviewReact components={components} onReady={(e: { api: DockviewApi }) => { api = e.api; api.layout(WIDTH, 800); c.api = api; c.load(undefined); }} />);
    if (floating) {
      render(<DockviewReact components={components} onReady={(e: { api: DockviewApi }) => {
        c.attach("float", e.api);
        e.api.addPanel({ id: "z", component: "z", title: "z", floating: { position: { left: 80, top: 64 }, width: 400, height: 300 } });
      }} />);
    }
    // A floating window there is one the user chose too.
    const chosen: string[] = floating ? ["z"] : [];
    const choose = (id: string) => { chosen.push(id); c.setWindows([...chosen]); };
    return { c, api: api!, choose };
  }
  const shape = (api: DockviewApi) => {
    const { root, orientation } = api.toJSON().grid;
    const walk = (node: { type: string; data: unknown }): unknown => (node.type === "leaf" ? (node.data as { views: string[] }).views[0] : (node.data as Array<{ type: string; data: unknown }>).map(walk));
    return { orientation, root: walk(root as { type: string; data: unknown }) };
  };

  it("fills an empty workspace, goes beside the first, then a row at a time, splitting each row", () => {
    const { api, choose } = mine();
    choose("a");
    expect(shape(api)).toEqual({ orientation: "HORIZONTAL", root: ["a"] });
    choose("b");
    expect(shape(api).root).toEqual(["a", "b"]);
    near(width(api, "a"), WIDTH / 2);
    choose("c");
    expect(shape(api)).toEqual({ orientation: "VERTICAL", root: [["a", "b"], "c"] });
    choose("d");
    expect(shape(api).root).toEqual([["a", "b"], ["c", "d"]]);
    near(width(api, "c"), WIDTH / 2);
    choose("e");
    expect(shape(api).root).toEqual([["a", "b"], ["c", "d"], "e"]);
    choose("f");
    expect(shape(api).root).toEqual([["a", "b"], ["c", "d"], ["e", "f"]]);
  });

  it("puts a column beside two stacked windows, then splits that column", () => {
    const { c, api, choose } = mine();
    choose("a");
    // The user stacks the second window under the first.
    const b = () => api.getPanel("b")!.group;
    choose("b");
    b().api.moveTo({ group: api.getPanel("a")!.group, position: "bottom" });
    expect(nextPlace(api, false)).toEqual({ kind: "column" });
    choose("c");
    expect(shape(api)).toEqual({ orientation: "HORIZONTAL", root: [["a", "b"], "c"] });
    near(width(api, "c"), WIDTH / 2);
    choose("d");
    expect(shape(api).root).toEqual([["a", "b"], ["c", "d"]]);
    expect(c.windowOrder()).toEqual(["a", "b", "c", "d"]);
  });

  it("floats a new window when only floating windows are there", () => {
    const { c, api, choose } = mine(true);
    choose("a");
    expect(api.panels).toHaveLength(0);
    expect(c.hostOf("a")).toBe("float");
  });
});
