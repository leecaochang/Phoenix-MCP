/**
 * The Changes windows point the Guide at the section about each control:
 * the filters, the list, the view toggles, Restore and
 * its confirmation, and stepping through changes. The pages and sections
 * themselves are checked by guide_contract.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api";
import type { VersionSummary } from "../types";
import { guideTargetFor } from "../shell/guideTarget";
import { comparisonWindow, listWindow, renderChanges, rowsOf } from "./changes_harness";

vi.mock("../api", () => ({
  api: {
    listVersions: vi.fn(),
    getVersion: vi.fn(),
    restoreVersion: vi.fn(),
    listAccessKeys: vi.fn().mockResolvedValue([]),
    listArchivedAccessKeys: vi.fn().mockResolvedValue([]),
  },
}));

const version: VersionSummary = {
  id: "v1", resource_type: "automation", resource_id: "aid1", alias: "My automation", action: "edit", access_key_id: "t1",
  access_key_name: "my_access_key", approved_by_user_id: null, timestamp: "2026-01-01T00:00:00Z", has_before: true, has_after: true,
};

describe("the Guide in Changes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, onchange: null, addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false }));
  });

  it("opens the section about the control selected", async () => {
    vi.mocked(api.listVersions).mockResolvedValue({ resource_type: null, resource_id: null, versions: [version], total: 1 });
    vi.mocked(api.getVersion).mockResolvedValue({ ...version, request_id: "r1", before: { alias: "Before" }, after: { alias: "After" } });
    renderChanges();
    fireEvent.click(await within(listWindow()).findByText("My automation"));
    await waitFor(() => expect(comparisonWindow().querySelector(".change-comparison")).toBeTruthy());
    listWindow().setAttribute("data-phx-window", "changes");
    comparisonWindow().setAttribute("data-phx-window", "comparison");

    expect(guideTargetFor(rowsOf(listWindow())[0])).toMatchObject({ topic: "window.changes", anchor: "list" });
    expect(guideTargetFor(listWindow().querySelector('[data-filter="type"]')!)).toMatchObject({ topic: "window.changes", anchor: "filters" });
    const restore = within(comparisonWindow()).getByRole("button", { name: "Restore" });
    expect(guideTargetFor(restore)).toMatchObject({ windowId: "comparison", topic: "window.comparison", anchor: "restore" });
    expect(guideTargetFor(within(comparisonWindow()).getByRole("button", { name: "Next Record" }))).toMatchObject({ anchor: "step" });
    expect(guideTargetFor(comparisonWindow().querySelector(".change-diff-toolbar button")!)).toMatchObject({ anchor: "views" });
  });
});
