/**
 * Workspace Options: Notifications is shown or hidden with a check rather than
 * reopened, a window a toolbar tool opens (Log Settings in Logs) is not
 * offered there, other closed windows are offered to reopen (Request Details
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
  it("shows Notifications with a check, and hides and shows it again", () => {
    const c = mount();
    const show = () => items(c).find((i) => i.id === "show-notifications")!;
    expect(show()).toMatchObject({ label: "Show Notification History", checked: true });
    show().onSelect!();
    expect(c.groupOf("notifications")).toBeNull();
    expect(show().checked).toBe(false);
    expect(items(c).some((i) => i.id === "reopen-notifications")).toBe(false);
    show().onSelect!();
    expect(c.groupOf("notifications")).not.toBeNull();
  });

  it("names the two resets for what they reset, and says more in their hints", () => {
    const c = mount();
    const actions = { saveMyDefault: () => {}, restoreMyDefault: () => {}, hasMyDefault: () => false, restoreOriginal: () => {},
      restorePanelDefaults: () => {}, attentionHidden: () => false, showAttention: () => {} };
    const all = workspaceMenu(c, actions).filter((e): e is Item => e !== "separator");
    expect(all.find((i) => i.id === "restore")).toMatchObject({ label: "Reset This Workspace", hint: expect.stringContaining("Other workspaces") });
    expect(all.find((i) => i.id === "panel-defaults")).toMatchObject({ label: "Reset All Workspaces", hint: expect.stringContaining("the toolbar, the Help Bar and the Guide") });
  });

  it("offers Needs Attention back, in this workspace, while items are waiting (user, 2026-10-29)", () => {
    const c = mount();
    const base = { saveMyDefault: () => {}, restoreMyDefault: () => {}, hasMyDefault: () => false, restoreOriginal: () => {}, restorePanelDefaults: () => {} };
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

  it("does not offer to reopen a window a toolbar tool opens here, and offers other closed windows", () => {
    const c = mount();
    const ids = items(c).map((i) => i.id);
    expect(ids).not.toContain("reopen-settingsLogs");
    expect(ids).toContain("reopen-attention");
  });
});
