import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { api } from "../api";
import type { VersionListResponse, VersionRecord, VersionSummary } from "../types";
import { Notifications } from "../ui/notify";
import { comparisonWindow, historyWindow, listWindow, renderChanges, rowsOf } from "./changes_harness";

vi.mock("../api", () => ({
  api: {
    listVersions: vi.fn(),
    getVersion: vi.fn(),
    restoreVersion: vi.fn(),
    listAccessKeys: vi.fn().mockResolvedValue([]),
    listArchivedAccessKeys: vi.fn().mockResolvedValue([]),
  },
}));

function version(over: Partial<VersionSummary> = {}): VersionSummary {
  return {
    id: "v1",
    resource_type: "automation",
    resource_id: "aid1",
    alias: "My automation",
    action: "create",
    access_key_id: "t1",
    access_key_name: "my_access_key",
    approved_by_user_id: null,
    timestamp: "2026-01-01T00:00:00Z",
    has_before: false,
    has_after: true,
    ...over,
  };
}

function record(summary: VersionSummary, sides: Partial<Pick<VersionRecord, "before" | "after">> = {}): VersionRecord {
  return { ...summary, request_id: "r1", before: { alias: "Before" }, after: { alias: "After" }, ...sides };
}

const page = (versions: VersionSummary[], total = versions.length): VersionListResponse => ({ resource_type: null, resource_id: null, versions, total });

/** Answer every read from this history, as the server pages it. */
function pagedHistory(all: VersionSummary[], firstPage?: number) {
  vi.mocked(api.listVersions).mockImplementation(async ({ limit = 100, offset = 0 } = {}) => page(all.slice(offset, offset + (offset ? limit : firstPage ?? limit)), all.length));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

function stubMatchMedia() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

/** Show a change in Comparison by choosing it in the list. */
async function choose(text: string) {
  fireEvent.click(await within(listWindow()).findByText(text));
  await waitFor(() => expect(comparisonWindow().querySelector(".change-comparison")).toBeTruthy());
}

/** Open the Restore menu and return its items. */
function openRestore(): HTMLElement[] {
  fireEvent.click(within(comparisonWindow()).getByRole("button", { name: "Restore" }));
  return screen.getAllByRole("menuitem");
}

describe("the Changes list", () => {
  beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); });

  it("filters changes by whole words, sorts names, and finds a match on a later page", async () => {
    const all = [version({ id: "v1", alias: "Zebra lamp" }), version({ id: "v2", alias: "Alpha lamp", action: "edit" }), version({ id: "v3", alias: "Older socket" })];
    pagedHistory(all, 2);
    const view = renderChanges();
    await view.findByText("Zebra lamp");
    fireEvent.click(view.getByRole("button", { name: "Item" }));
    expect(rowsOf(listWindow())[0]).toHaveTextContent("Alpha lamp");
    // Letters in order are not a match; each word has to appear as it was typed (user, 2026-10-29).
    fireEvent.change(view.getByRole("searchbox"), { target: { value: "zbr lmp" } });
    expect(view.queryByText("Zebra lamp")).toBeNull();
    fireEvent.change(view.getByRole("searchbox"), { target: { value: "lamp zebra" } });
    expect(view.queryByText("Alpha lamp")).toBeNull();
    expect(view.getByText("Zebra lamp")).toBeVisible();
    fireEvent.change(view.getByRole("searchbox"), { target: { value: "socket" } });
    expect(await view.findByText("Older socket")).toBeVisible();
    fireEvent.change(view.getByRole("searchbox"), { target: { value: "lamp" } });
    fireEvent.change(view.getByLabelText("Action"), { target: { value: "delete" } });
    expect(await view.findByText("No changes match these filters.")).toBeVisible();
  });

  it("gives a filter a full page at a time, however few changes each page of the history holds", async () => {
    pagedHistory(Array.from({ length: 600 }, (_, i) => version({ id: `v${i}`, resource_type: i % 2 ? "dashboard" : "automation", alias: `Item ${i}` })));
    const view = renderChanges();
    await view.findByText("Item 0");
    expect(view.getByText("Showing 100 of 600")).toBeTruthy();

    fireEvent.change(view.getByLabelText("Type"), { target: { value: "dashboard" } });
    expect(await view.findByText("Showing 100 of 300")).toBeTruthy();
    expect(api.listVersions).toHaveBeenLastCalledWith({ limit: 500, offset: 100 });
    expect(rowsOf(listWindow())).toHaveLength(100);

    // The rest of the filtered list is loaded already, so Load More only shows it.
    const reads = vi.mocked(api.listVersions).mock.calls.length;
    fireEvent.click(view.getByText("Load More"));
    expect(rowsOf(listWindow())).toHaveLength(200);
    fireEvent.click(view.getByText("Load More"));
    expect(view.getByText("Showing 300 of 300")).toBeTruthy();
    expect(view.queryByText("Load More")).toBeNull();
    expect(api.listVersions).toHaveBeenCalledTimes(reads);

    // Clearing the filter goes back to one page, of everything.
    fireEvent.change(view.getByLabelText("Type"), { target: { value: "" } });
    expect(rowsOf(listWindow())).toHaveLength(100);
    expect(view.getByText("Showing 100 of 600")).toBeTruthy();
  });

  it("keeps a filter short of a page when that is all there is", async () => {
    pagedHistory(Array.from({ length: 250 }, (_, i) => version({ id: `v${i}`, resource_type: i < 21 ? "dashboard" : "automation", alias: `Item ${i}` })));
    const view = renderChanges();
    await view.findByText("Item 0");
    fireEvent.change(view.getByLabelText("Type"), { target: { value: "dashboard" } });
    expect(await view.findByText("Showing 21 of 21")).toBeTruthy();
    expect(view.queryByText("Load More")).toBeNull();
  });

  it("stops reading for a filter when a read fails, and Load More tries again", async () => {
    const all = Array.from({ length: 150 }, (_, i) => version({ id: `v${i}`, alias: `Item ${i}` }));
    pagedHistory(all);
    const view = renderChanges();
    await view.findByText("Item 0");
    vi.mocked(api.listVersions).mockRejectedValueOnce(new Error("Read failed"));
    fireEvent.change(view.getByLabelText("Type"), { target: { value: "automation" } });
    expect(await view.findByText("Read failed")).toBeVisible();
    await new Promise((r) => setTimeout(r, 20));
    expect(api.listVersions).toHaveBeenCalledTimes(2);
    fireEvent.click(view.getByText("Load More"));
    expect(await view.findByText("Showing 150 of 150")).toBeTruthy();
    expect(view.queryByText("Read failed")).toBeNull();
  });

  it("names a restore from this panel by the localized Admin, not the English name stored for agents", async () => {
    vi.mocked(api.listVersions).mockResolvedValue(page([
      version({ id: "v1", alias: "Kitchen", access_key_id: "__restore__:u1", access_key_name: "(admin restore)", approved_by_user_id: "u1" }),
    ]));
    const { findByText, queryByText } = renderChanges();
    await findByText("Kitchen");
    expect(await findByText("Admin")).toBeInTheDocument();
    expect(queryByText("(admin restore)")).toBeNull();
  });

  it("loads the first page and shows Load More only while more rows remain", async () => {
    vi.mocked(api.listVersions).mockResolvedValue(page([version({ id: "v1", alias: "First change" })], 2));
    const { findByText, queryByText } = renderChanges();
    await findByText("First change");
    expect(api.listVersions).toHaveBeenCalledWith({ limit: 100, offset: 0 });
    expect(queryByText("Load More")).toBeTruthy();
  });

  it("Load More appends the next page at the accumulated offset", async () => {
    vi.mocked(api.listVersions)
      .mockResolvedValueOnce(page([version({ id: "v1", alias: "First change" })], 2))
      .mockResolvedValueOnce(page([version({ id: "v2", alias: "Second change" })], 2));
    const { findByText, getByText, queryByText } = renderChanges();
    await findByText("First change");
    fireEvent.click(getByText("Load More"));
    await findByText("Second change");
    expect(api.listVersions).toHaveBeenLastCalledWith({ limit: 100, offset: 1 });
    expect(queryByText("Load More")).toBeNull();
  });

  it("offers no Refresh: a configuration change reloads the first page, replacing the loaded feed", async () => {
    let configChanged: (() => void) | undefined;
    const hass = { connection: { subscribeEvents: vi.fn(async (cb: () => void) => { configChanged = cb; return () => {}; }) } };
    vi.mocked(api.listVersions)
      .mockResolvedValueOnce(page([version({ id: "v1", alias: "First change" })]))
      .mockResolvedValue(page([version({ id: "v2", alias: "Newer change" })]));
    const view = renderChanges({ hass });
    await view.findByText("First change");
    expect(view.queryByRole("button", { name: "Refresh Changes" })).toBeNull();
    await waitFor(() => expect(configChanged).toBeTypeOf("function"));
    configChanged!();
    await view.findByText("Newer change");
    expect(api.listVersions).toHaveBeenLastCalledWith({ limit: 100, offset: 0 });
    expect(view.queryByText("First change")).toBeNull();
  });

  it("shows how many changes are loaded of all recorded", async () => {
    vi.mocked(api.listVersions).mockResolvedValue(page([version({ id: "v1", alias: "First change" })], 42));
    const view = renderChanges();
    await view.findByText("First change");
    expect(view.getByText("Showing 1 of 42")).toBeTruthy();
  });

  it("lets a newer config-change refresh release an older foreground load", async () => {
    const foreground = deferred<VersionListResponse>();
    const background = deferred<VersionListResponse>();
    let configChanged: (() => void) | undefined;
    const hass = { connection: { subscribeEvents: vi.fn(async (cb: () => void) => { configChanged = cb; return () => {}; }) } };
    vi.mocked(api.listVersions)
      .mockReturnValueOnce(foreground.promise)
      .mockReturnValueOnce(background.promise);
    // The first load is the foreground one; a configuration change starts a newer one before it answers.
    const view = renderChanges({ hass });
    await waitFor(() => expect(configChanged).toBeTypeOf("function"));
    configChanged!();
    await waitFor(() => expect(api.listVersions).toHaveBeenCalledTimes(2));
    // The newer, event-driven reload answers first with nothing: its answer stands, and loading ends.
    background.resolve(page([]));
    expect(await view.findByText(/No configuration changes yet/)).toBeTruthy();
    foreground.resolve(page([version({ id: "v9", alias: "Stale foreground" })]));
    await new Promise((r) => setTimeout(r, 20));
    expect(view.queryByText("Stale foreground")).toBeNull();
  });
});

describe("Comparison and Restore", () => {
  beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); stubMatchMedia(); });

  it("empties Comparison and Item History when the list is told to show other changes", async () => {
    const edit = version({ action: "edit", has_before: true });
    vi.mocked(api.listVersions).mockResolvedValue(page([edit]));
    vi.mocked(api.getVersion).mockResolvedValue(record(edit));
    const view = renderChanges();
    await choose("My automation");
    // Showing another set of changes leaves nothing chosen in the list (user, 2026-10-29).
    fireEvent.change(view.getByLabelText("Action"), { target: { value: "delete" } });
    await waitFor(() => expect(comparisonWindow().querySelector(".change-comparison")).toBeNull());
    expect(within(comparisonWindow()).getByText("Select a change to compare.")).toBeVisible();
    expect(within(historyWindow()).getByText("Select a change to see the item's other recorded changes.")).toBeVisible();
  });

  it("typing a search empties Comparison as the other filters do", async () => {
    const edit = version({ action: "edit", has_before: true });
    vi.mocked(api.listVersions).mockResolvedValue(page([edit]));
    vi.mocked(api.getVersion).mockResolvedValue(record(edit));
    const view = renderChanges();
    await choose("My automation");
    fireEvent.change(view.getByLabelText("Search changes..."), { target: { value: "automation" } });
    await waitFor(() => expect(comparisonWindow().querySelector(".change-comparison")).toBeNull());
  });

  it("choosing a change shows it in Comparison and keeps focus in the list", async () => {
    const edit = version({ action: "edit", has_before: true });
    vi.mocked(api.listVersions).mockResolvedValueOnce(page([edit])).mockResolvedValue(page([edit]));
    vi.mocked(api.getVersion).mockResolvedValue(record(edit));
    renderChanges();
    const row = await within(listWindow()).findByText("My automation");
    row.closest("tr")!.focus();
    fireEvent.click(row);
    await waitFor(() => expect(within(comparisonWindow()).getByRole("heading", { name: "My automation" })).toBeTruthy());
    expect(listWindow().contains(document.activeElement)).toBe(true);
    expect(comparisonWindow().textContent).toContain("Before");
  });

  it("offers each side in one Restore menu and explains the ones it cannot restore", async () => {
    const created = version({ id: "v1", action: "create" });
    vi.mocked(api.listVersions).mockResolvedValueOnce(page([created])).mockResolvedValue(page([created]));
    vi.mocked(api.getVersion).mockResolvedValue(record(created, { before: null }));
    renderChanges();
    await choose("My automation");
    const [before, after] = openRestore();
    // A creation has no Before; the latest change's After is the item's current state.
    expect(before).toHaveAttribute("aria-disabled", "true");
    expect(before).toHaveAccessibleDescription("There is no saved Before state for this change, for example because it created the item.");
    expect(after).toHaveAttribute("aria-disabled", "true");
    expect(after).toHaveAccessibleDescription("This is the newest recorded change for this item, so its After state is current unless the item was changed outside Phoenix MCP.");
  });

  it("refuses a side whose snapshot was too large to store", async () => {
    const blueprint = version({ id: "v1", resource_type: "blueprint", resource_id: "automation/motion.yaml", alias: "motion.yaml", action: "edit", has_before: true });
    const newer = version({ id: "v2", resource_type: "blueprint", resource_id: "automation/motion.yaml", alias: "motion.yaml", action: "edit" });
    vi.mocked(api.listVersions).mockResolvedValueOnce(page([blueprint])).mockResolvedValue(page([newer, blueprint]));
    vi.mocked(api.getVersion).mockResolvedValue(record(blueprint, { before: { content: null, bytes: 900000 }, after: { content: "a: 1\n" } }));
    renderChanges();
    await choose("motion.yaml");
    const [before, after] = openRestore();
    expect(before).toHaveAccessibleDescription("This snapshot was too large to store, so it cannot be restored.");
    expect(after).not.toHaveAttribute("aria-disabled", "true");
  });

  it("confirms inline, restores, announces it, and shows the new record", async () => {
    const center = new Notifications();
    const original = version({ action: "edit", has_before: true });
    const rollback = version({ id: "v2", action: "rollback", has_before: true });
    vi.mocked(api.listVersions)
      .mockResolvedValueOnce(page([original]))          // feed
      .mockResolvedValueOnce(page([original]))          // item history
      .mockResolvedValueOnce(page([original]))          // the stale check
      .mockResolvedValueOnce(page([rollback, original])) // feed after the restore
      .mockResolvedValue(page([rollback, original]));    // item history after the restore
    vi.mocked(api.getVersion).mockImplementation(async (id: string) => record(id === "v2" ? rollback : original));
    vi.mocked(api.restoreVersion).mockResolvedValue({ restored: true, version_id: "v1", resource_type: "automation", resource_id: "aid1" });
    renderChanges({ center });
    await choose("My automation");
    fireEvent.click(openRestore()[0]);
    const confirm = within(comparisonWindow()).getByRole("group", { name: "Restore" });
    expect(confirm.textContent).toMatch(/Restore My automation to its Before state from .+\?/);
    expect(confirm.textContent).toContain("later edits to the same settings are lost");
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm Restore" }));
    // The newest change seen goes too, so the server refuses if another lands first.
    await waitFor(() => expect(api.restoreVersion).toHaveBeenCalledWith("v1", "before", "v1"));
    await waitFor(() => expect(api.getVersion).toHaveBeenCalledWith("v2"));
    expect(center.history[0].text).toMatch(/^Restored My automation to its Before state from /);
    expect(within(comparisonWindow()).queryByRole("group", { name: "Restore" })).toBeNull();
    // The restore's own change joins the order, so Next steps back to the change restored.
    await waitFor(() => expect(within(comparisonWindow()).getByRole("button", { name: "Next Record" })).toBeEnabled());
  });

  it("names the reason the server gives for a side it would refuse", async () => {
    const edit = version({ action: "edit", has_before: true, resource_type: "entity", resource_id: "light.gone" });
    const newer = version({ id: "v2", action: "edit", resource_type: "entity", resource_id: "light.gone" });
    vi.mocked(api.listVersions).mockResolvedValueOnce(page([edit])).mockResolvedValue(page([newer, edit]));
    vi.mocked(api.getVersion).mockResolvedValue({ ...record(edit), restore_refusals: { before: "entity_missing", after: null } });
    renderChanges();
    await choose("My automation");
    const [before, after] = openRestore();
    expect(before).toHaveAttribute("aria-disabled", "true");
    // Shown in the menu itself, not only on hover.
    expect(before.textContent).toContain("This entity is no longer in Home Assistant, so its settings can't be restored.");
    expect(after).not.toHaveAttribute("aria-disabled", "true");
  });

  it("treats the server's stale refusal like the panel's own check", async () => {
    const center = new Notifications();
    const original = version({ action: "edit", has_before: true });
    vi.mocked(api.listVersions).mockResolvedValue(page([original]));
    vi.mocked(api.getVersion).mockResolvedValue(record(original));
    vi.mocked(api.restoreVersion).mockRejectedValue(Object.assign(new Error("changed"), { status: 409, code: "stale_version" }));
    renderChanges({ center });
    await choose("My automation");
    fireEvent.click(openRestore()[0]);
    fireEvent.click(within(comparisonWindow()).getByRole("button", { name: "Confirm Restore" }));
    await waitFor(() => expect(center.history[0]?.text).toBe("My automation has changed since you chose to restore it. Review the comparison and try again."));
    expect(within(comparisonWindow()).queryByText("changed")).toBeNull();
  });

  it("drops a change the server no longer keeps from the list", async () => {
    const gone = version({ action: "edit", has_before: true });
    const kept = version({ id: "v2", action: "edit", alias: "Other automation", resource_id: "aid2" });
    vi.mocked(api.listVersions).mockResolvedValue(page([gone, kept]));
    vi.mocked(api.getVersion).mockRejectedValue(Object.assign(new Error("Version not found."), { status: 404, code: "not_found" }));
    renderChanges();
    fireEvent.click(await within(listWindow()).findByText("My automation"));
    expect(await within(comparisonWindow()).findByText("This change is no longer kept. Each item keeps its newest 20 changes.")).toBeTruthy();
    await waitFor(() => expect(within(listWindow()).queryByText("My automation")).toBeNull());
    expect(within(listWindow()).getByText("Other automation")).toBeTruthy();
  });

  it("refuses a restore when the item changed after Restore was chosen", async () => {
    const center = new Notifications();
    const original = version({ action: "edit", has_before: true });
    const newer = version({ id: "v3", action: "edit", has_before: true });
    vi.mocked(api.listVersions)
      .mockResolvedValueOnce(page([original]))        // feed
      .mockResolvedValueOnce(page([original]))        // item history
      .mockResolvedValue(page([newer, original]));    // the stale check sees a newer change
    vi.mocked(api.getVersion).mockResolvedValue(record(original));
    renderChanges({ center });
    await choose("My automation");
    fireEvent.click(openRestore()[0]);
    fireEvent.click(within(comparisonWindow()).getByRole("button", { name: "Confirm Restore" }));
    await waitFor(() => expect(center.history[0]?.text).toBe("My automation has changed since you chose to restore it. Review the comparison and try again."));
    expect(api.restoreVersion).not.toHaveBeenCalled();
    expect(within(comparisonWindow()).queryByRole("group", { name: "Restore" })).toBeNull();
  });

  it("closes a pending confirmation when another change is chosen", async () => {
    const a = version({ id: "v1", alias: "Hall lights", action: "edit", has_before: true });
    const b = version({ id: "v2", resource_id: "aid2", alias: "Porch lights", action: "edit", has_before: true });
    vi.mocked(api.listVersions).mockResolvedValueOnce(page([a, b])).mockResolvedValue(page([a]));
    vi.mocked(api.getVersion).mockImplementation(async (id: string) => record(id === "v2" ? b : a));
    renderChanges();
    await choose("Hall lights");
    fireEvent.click(openRestore()[0]);
    expect(within(comparisonWindow()).getByRole("group", { name: "Restore" })).toBeTruthy();
    fireEvent.click(within(listWindow()).getByText("Porch lights"));
    await waitFor(() => expect(within(comparisonWindow()).getByRole("heading", { name: "Porch lights" })).toBeTruthy());
    expect(within(comparisonWindow()).queryByRole("group", { name: "Restore" })).toBeNull();
    expect(api.restoreVersion).not.toHaveBeenCalled();
  });

  it("Item History moves Comparison through the item's changes with focus staying in it", async () => {
    const latest = version({ id: "v2", action: "edit", has_before: true, timestamp: "2026-01-02T00:00:00Z" });
    const first = version({ id: "v1", action: "create" });
    vi.mocked(api.listVersions).mockResolvedValueOnce(page([latest])).mockResolvedValue(page([latest, first]));
    vi.mocked(api.getVersion).mockImplementation(async (id: string) => record(id === "v1" ? first : latest, id === "v1" ? { before: null } : {}));
    renderChanges();
    await choose("My automation");
    await waitFor(() => expect(rowsOf(historyWindow())).toHaveLength(2));
    rowsOf(historyWindow())[0].focus();
    fireEvent.keyDown(rowsOf(historyWindow())[0], { key: "ArrowDown" });
    await waitFor(() => expect(api.getVersion).toHaveBeenLastCalledWith("v1"));
    expect(historyWindow().contains(document.activeElement)).toBe(true);
    await waitFor(() => expect(within(comparisonWindow()).getByText("Create")).toBeTruthy());
  });
});

// A device YAML version is a raw text snapshot ({content}), the same shape as
// yaml_config and file. Rendering it through the structured path would dump
// {content, path} as YAML, burying the file inside a "content: |-" block with
// every line re-indented.
describe("Comparison of device YAML", () => {
  const DEVICE = 'esphome:\n  name: rf-blaster2\nlogger:\n  level: WARN\n';

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    // jsdom does not implement matchMedia, and Comparison reads it to pick its default layout.
    stubMatchMedia();
  });

  async function openDetail(after = DEVICE) {
    const summary = version({
      id: "v9", resource_type: "esphome_yaml", resource_id: "rf-blaster-2.yaml",
      alias: "rf-blaster-2.yaml", action: "edit", has_before: true, has_after: true,
    });
    vi.mocked(api.listVersions).mockResolvedValue(page([summary]));
    vi.mocked(api.getVersion).mockResolvedValue({ ...summary, request_id: "r1", before: { content: DEVICE }, after: { content: after } } as never);
    const view = renderChanges();
    fireEvent.click(await within(listWindow()).findByText("rf-blaster-2.yaml"));
    await waitFor(() => expect(api.getVersion).toHaveBeenCalledWith("v9"));
    await waitFor(() => expect(view.container.querySelector(".yaml-diff-cols")).toBeTruthy());
    return view;
  }

  it("shows a configuration file version as history only, with no Restore", async () => {
    const view = await openDetail(DEVICE.replace("WARN", "DEBUG"));
    expect(view.container.textContent).toContain("name: rf-blaster2");
    const reason = "Phoenix does not write configuration files back, so this version is history only. Copy its content and apply it by hand.";
    for (const side of openRestore()) {
      expect(side).toHaveAttribute("aria-disabled", "true");
      expect(side).toHaveAccessibleDescription(reason);
    }
  });

  it("renders the file itself, not a config: block wrapping it", async () => {
    await openDetail();
    const text = comparisonWindow().textContent ?? "";
    expect(text).toContain("name: rf-blaster2");
    // The structured path would have produced this wrapper.
    expect(text).not.toContain("content: |");
  });

  it("offers the code view and switches the panes out of the line diff", async () => {
    const view = await openDetail(DEVICE.replace("WARN", "DEBUG"));
    expect(view.container.querySelectorAll(".diff-line").length).toBeGreaterThan(0);
    fireEvent.click(view.getByRole("button", { name: /code editor view/i }));
    expect(view.container.querySelectorAll(".diff-line").length).toBe(0);
    expect(comparisonWindow().textContent).toContain("name: rf-blaster2");
    // No explanatory hint in Comparison (user, 2026-10-27): just the controls and Restore.
    expect(view.container.querySelector(".change-diff-hint")).toBeNull();
  });

  it("shares the choice with the approval diff rather than keeping its own", async () => {
    // storedCodeView is one key; choosing it on either surface applies on both.
    localStorage.setItem("phx-diff-code-view", "code");
    const { container } = await openDetail();
    expect(container.querySelectorAll(".diff-line").length).toBe(0);
  });
});
