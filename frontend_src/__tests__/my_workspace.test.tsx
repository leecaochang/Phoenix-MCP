/**
 * My Workspace's window rules, on a real Dockview instance: the windows the
 * user chose open along the bottom (now, or when the workspace is first
 * shown), a removed one closes without being offered again, a closed one
 * stays closed across a reload, and a conditional one shows only while it is
 * both chosen and allowed. Also the stored list of chosen windows and how it
 * merges with Home Assistant's copy.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { DockviewReact, type DockviewApi } from "dockview-react";
import { WorkspaceController, type WorkspaceLayout } from "../shell/dock/controller";
import { parseRecord, readMyWindows, type ShellRecord } from "../shell/storage";
import { mergeRecords } from "../shell/sync";
import { SOURCE_OF, canAddToMy, myWorkspaceWindows } from "../shell/workspaces";
import { visibleWindowEl } from "../shell/visibility";

const ALL = ["a", "b", "c", "p", "help", "n"];
const components = Object.fromEntries(ALL.map((id) => [id, () => <div data-window={id}>{id}</div>]));

function mount(windows: string[], saved?: WorkspaceLayout, extras: Partial<ConstructorParameters<typeof WorkspaceController>[4]> = {}, before?: (c: WorkspaceController) => void) {
  const c = new WorkspaceController("my", windows, () => {}, (id) => id, { collect: (id) => id !== "help" && id !== "n", ...extras });
  before?.(c);
  render(<DockviewReact components={components} onReady={(e: { api: DockviewApi }) => { c.api = e.api; c.load(saved); }} />);
  return c;
}
const open = (c: WorkspaceController) => c.api!.panels.map((p) => p.id).sort();

describe("a workspace of chosen windows", () => {
  it("opens the windows chosen before it was first shown", () => {
    const c = mount(["a"], undefined, {}, (ctl) => ctl.setWindows(["a", "b"]));
    expect(open(c)).toEqual(["a", "b"]);
    expect(c.closedWindows()).toEqual([]);
  });

  it("opens a window chosen while it is shown, and closes a removed one without offering it again", () => {
    const c = mount(["a"]);
    c.setWindows(["a", "b"]);
    expect(open(c)).toEqual(["a", "b"]);
    c.setWindows(["b"]);
    expect(open(c)).toEqual(["b"]);
    expect(c.closedWindows()).toEqual([]);
    expect(Object.keys(c.layout()!.dock.panels)).toEqual(["b"]);
  });

  it("waits until it is shown to open or close windows chosen while it was hidden", () => {
    const c = mount(["a", "b"]);
    c.setShown(false);
    c.setWindows(["a", "c"]);
    // Hidden: nothing is measured or moved yet.
    expect(open(c)).toEqual(["a", "b"]);
    c.setShown(true);
    expect(open(c)).toEqual(["a", "c"]);
    expect(c.closedWindows()).toEqual([]);
  });

  it("keeps a closed window closed after a reload, and opens one chosen since", () => {
    const first = mount(["a", "b"]);
    first.close(first.api!.getPanel("b")!.group);
    expect(first.closedWindows()).toEqual(["b"]);
    const second = mount(["a", "b", "c"], first.layout()!);
    expect(open(second)).toEqual(["a", "c"]);
    expect(second.closedWindows()).toEqual(["b"]);
    // Taking a closed window away forgets it.
    second.setWindows(["a", "c"]);
    expect(second.closedWindows()).toEqual([]);
  });

  it("shows a conditional window only while it is chosen and allowed", () => {
    let allowed = false;
    const conditional = { p: { neighbor: "", side: "bottom" as const } };
    const c = mount(["a"], undefined, { conditional }, (ctl) => { ctl.availability = (id) => (id === "p" ? allowed : true); });
    allowed = true;
    c.syncAvailable();
    // Allowed but not chosen: My Workspace does not show it.
    expect(open(c)).toEqual(["a"]);
    c.setWindows(["a", "p"]);
    expect(open(c)).toEqual(["a", "p"]);
    allowed = false;
    c.syncAvailable();
    expect(open(c)).toEqual(["a"]);
    expect(c.closedWindows()).toEqual([]);
  });

  it("leaves Quick Help to the Help menu, and a shared window closed, rather than opening them as chosen windows", () => {
    const c = mount(["a", "help", "n"], undefined, {}, (ctl) => ctl.want("help", false));
    expect(open(c)).toEqual(["a"]);
    // Not chosen: each counts as closed, as in every workspace, and reopens from Workspace Options.
    expect(c.closedWindows()).toEqual(["help", "n"]);
  });
});

describe("which windows My Workspace can hold", () => {
  it("offers every workspace window, and neither Help, Notifications nor Needs Attention", () => {
    expect(canAddToMy("accessKeys")).toBe(true);
    expect(canAddToMy("settingsSafety")).toBe(true);
    expect(SOURCE_OF.review).toBe("approvals");
    for (const id of ["help", "notifications", "attention", "agentChat"]) expect(canAddToMy(id)).toBe(false);
  });

  it("keeps the chosen windows in order, Agent Chat included, before the shared ones", () => {
    expect(myWorkspaceWindows(["review", "agentChat", "help", "nonsense"])).toEqual(["review", "agentChat", "attention", "help", "notifications"]);
  });
});

describe("the stored list of My Workspace windows", () => {
  it("keeps unique window ids and drops anything else", () => {
    expect(readMyWindows(["accessKeys", "accessKeys", "bad id!", 3, null, "agentChat"])).toEqual(["accessKeys", "agentChat"]);
    expect(readMyWindows("accessKeys")).toEqual([]);
    const parsed = parseRecord({ schema: 1, savedAt: 1, workspaces: {}, myWindows: ["review", "<script>"] }) as ShellRecord;
    expect(parsed.myWindows).toEqual(["review"]);
    expect("myWindows" in (parseRecord({ schema: 1, savedAt: 1, workspaces: {} }) as ShellRecord)).toBe(false);
  });

  it("merges as its own section, so another computer's arrangements survive", () => {
    const stored: ShellRecord = { schema: 1, savedAt: 1, rev: 3, workspaces: { logs: { dock: {}, folds: { folded: [], collapsed: {} }, closed: {} } as never }, myWindows: ["review"] };
    const local: ShellRecord = { schema: 1, savedAt: 2, rev: 3, workspaces: {}, myWindows: ["review", "agentChat"] };
    const merged = mergeRecords(stored, local, new Set(["myWindows"]), "here");
    expect(merged.myWindows).toEqual(["review", "agentChat"]);
    expect(merged.workspaces.logs).toBeDefined();
    expect(mergeRecords(stored, local, new Set(["toolbar"]), "here").myWindows).toEqual(["review"]);
  });
});

describe("finding a window that exists in two workspaces", () => {
  it("prefers the copy in the workspace on screen, and falls back to the only one", () => {
    const root = document.createElement("div");
    root.innerHTML = `
      <div class="phx-workspace-frame" hidden data-workspace="logs"><div data-phx-window="auditLog" id="hidden-copy"></div></div>
      <div class="phx-workspace-frame" data-workspace="my"><div data-phx-window="auditLog" id="shown-copy"></div></div>
      <div class="phx-workspace-frame" hidden data-workspace="approvals"><div data-phx-window="review" id="only-copy"></div></div>`;
    expect(visibleWindowEl(root, "auditLog")?.id).toBe("shown-copy");
    expect(visibleWindowEl(root, "review")?.id).toBe("only-copy");
    expect(visibleWindowEl(root, "missing")).toBeNull();
  });
});
