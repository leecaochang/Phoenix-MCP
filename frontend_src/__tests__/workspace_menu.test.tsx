/**
 * Workspace Options: Notification History is offered only while its window is
 * closed, a window a toolbar tool opens (Log Settings in Logs) is offered
 * while hidden and opens as its tool would, other closed windows are offered to reopen (Request Details
 * among them), and the two resets say what they reset.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { DockviewReact, type DockviewApi } from "dockview-react";
import { WorkspaceController } from "../shell/dock/controller";
import { workspaceMenu } from "../shell/Toolbar";
import type { MenuEntry } from "../ui/Menu";

type Item = Exclude<MenuEntry, "separator">;
const WINDOWS = ["auditLog", "settingsLogs", "notifications", "attention"];
const components = Object.fromEntries(WINDOWS.map((id) => [id, () => <div>{id}</div>]));

function mount() {
  const c = new WorkspaceController("logs", WINDOWS, (_, add) => { add("auditLog"); add("notifications", { referencePanel: "auditLog", direction: "below" }); }, (id) => id);
  render(<DockviewReact components={components} onReady={(e: { api: DockviewApi }) => { c.api = e.api; c.load(undefined); }} />);
  return c;
}
const items = (c: WorkspaceController) => workspaceMenu(c, null).filter((e): e is Item => e !== "separator");

describe("Workspace Options", () => {
  it("offers Notification History only while it is closed (user, 2026-10-29)", () => {
    const c = mount();
    const show = () => items(c).find((i) => i.id === "show-notifications");
    // Open: nothing to offer, and no second entry for it among the reopenable windows.
    expect(show()).toBeUndefined();
    expect(items(c).some((i) => i.id === "reopen-notifications")).toBe(false);
    c.close(c.groupOf("notifications")!);
    expect(show()).toMatchObject({ label: "Show Notification History" });
    expect(show()!.checked).toBeUndefined();
    expect(items(c).some((i) => i.id === "reopen-notifications")).toBe(false);
    show()!.onSelect!();
    expect(c.groupOf("notifications")).not.toBeNull();
    expect(show()).toBeUndefined();
  });

  it("names the two resets for what they reset, and says more in their hints", () => {
    const c = mount();
    const actions = { saveMyDefault: () => {}, restoreMyDefault: () => {}, hasMyDefault: () => false, restoreOriginal: () => {},
      restorePanelDefaults: () => {}, attentionHidden: () => false, showAttention: () => {}, openToolWindow: () => {} };
    const all = workspaceMenu(c, actions).filter((e): e is Item => e !== "separator");
    expect(all.find((i) => i.id === "restore")).toMatchObject({ label: "Reset This Workspace", hint: expect.stringContaining("Other workspaces") });
    expect(all.find((i) => i.id === "panel-defaults")).toMatchObject({ label: "Reset All Workspaces", hint: expect.stringContaining("the toolbar, the Help Bar and the Guide") });
  });

  it("offers Needs Attention back, in this workspace, while items are waiting (user, 2026-10-29)", () => {
    const c = mount();
    const base = { saveMyDefault: () => {}, restoreMyDefault: () => {}, hasMyDefault: () => false, restoreOriginal: () => {}, restorePanelDefaults: () => {}, openToolWindow: () => {} };
    const entry = (hidden: boolean, show = () => {}) =>
      workspaceMenu(c, { ...base, attentionHidden: () => hidden, showAttention: show })
        .filter((e): e is Item => e !== "separator").find((i) => i.id === "show-attention");
    expect(entry(false)).toBeUndefined();
    const shown: string[] = [];
    const item = entry(true, () => shown.push(c.id))!;
    expect(item.label).toBe("Show Needs Attention");
    item.onSelect!();
    expect(shown).toEqual([c.id]);
  });

  it("offers Request Details back in Approvals, which no tool opens any more (user, 2026-10-29)", () => {
    const windows = ["approvals", "review", "requestDetails"];
    const parts = Object.fromEntries(windows.map((id) => [id, () => <div>{id}</div>]));
    const c = new WorkspaceController("approvals", windows, (_, add) => {
      add("approvals");
      add("review", { referencePanel: "approvals", direction: "right" });
      add("requestDetails", { referencePanel: "review", direction: "below" });
    }, (id) => id);
    render(<DockviewReact components={parts} onReady={(e: { api: DockviewApi }) => { c.api = e.api; c.load(undefined); }} />);
    expect(items(c).some((i) => i.id === "reopen-requestDetails")).toBe(false);
    c.close(c.groupOf("requestDetails")!);
    expect(items(c).map((i) => i.id)).toContain("reopen-requestDetails");
  });

  it("offers a hidden window a toolbar tool opens, once, opening it as the tool would (user, 2026-10-31)", () => {
    const c = mount();
    const opened: string[] = [];
    const actions = { saveMyDefault: () => {}, restoreMyDefault: () => {}, hasMyDefault: () => false, restoreOriginal: () => {},
      restorePanelDefaults: () => {}, attentionHidden: () => false, showAttention: () => {}, openToolWindow: (_: WorkspaceController, id: string) => { opened.push(id); } };
    const all = workspaceMenu(c, actions).filter((e): e is Item => e !== "separator");
    const entries = all.filter((i) => i.id === "reopen-settingsLogs");
    expect(entries).toHaveLength(1);
    expect(entries[0].label).toBe("Show Logs and Notifications");
    entries[0].onSelect!();
    expect(opened).toEqual(["settingsLogs"]);
    expect(all.map((i) => i.id)).toContain("reopen-attention");
    // Open, it is not offered.
    c.reopen("settingsLogs");
    expect(items(c).some((i) => i.id === "reopen-settingsLogs")).toBe(false);
  });
});
