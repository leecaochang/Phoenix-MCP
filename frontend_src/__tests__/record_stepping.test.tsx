/**
 * Stepping through records like Log Details does, and a paged list that keeps
 * its pages: Comparison and Review step through the list a record was chosen
 * from, in its shown order, and the Changes list keeps pages loaded with Load
 * More when it updates itself.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api";
import type { ApprovalRecord, VersionListResponse, VersionSummary } from "../types";
import { comparisonWindow, listWindow, renderChanges } from "./changes_harness";
import { renderApprovals, reviewWindow } from "./approvals_harness";

vi.mock("../api", () => ({
  api: {
    listVersions: vi.fn(),
    getVersion: vi.fn(),
    restoreVersion: vi.fn(),
    listAccessKeys: vi.fn().mockResolvedValue([]),
    listArchivedAccessKeys: vi.fn().mockResolvedValue([]),
    listApprovals: vi.fn(),
    approveApproval: vi.fn(),
    rejectApproval: vi.fn(),
    getApproval: vi.fn(),
  },
  currentHass: () => ({}),
}));

function version(id: string, alias: string, minute: number): VersionSummary {
  return {
    id, resource_type: "automation", resource_id: `aid-${id}`, alias, action: "edit", access_key_id: "t1", access_key_name: "key",
    approved_by_user_id: null, timestamp: `2026-01-01T00:${String(minute).padStart(2, "0")}:00Z`, has_before: true, has_after: true,
  };
}
const page = (versions: VersionSummary[], total: number): VersionListResponse => ({ resource_type: null, resource_id: null, versions, total });

function approval(id: string, summary: string, minute: number): ApprovalRecord {
  return {
    id, access_key_id: "t1", access_key_name: "key", tool_name: "restart_ha", cap_name: "cap_restart", args: {},
    diff: { kind: "system_action", summary }, status: "pending", created_at: `2026-08-13T04:${String(minute).padStart(2, "0")}:00Z`,
    expires_at: "2026-08-13T05:00:00Z", resolved_at: null, approved_by_user_id: null, rejected_reason: null, result: null, request_id: id, client_ip: null,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  // Comparison asks whether the screen is narrow to choose stacked or side by side.
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, onchange: null, addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false }));
});

describe("the Changes list and Comparison", () => {
  it("keeps the pages loaded with Load More when the list updates itself, adding only new changes", async () => {
    let configChanged: (() => void) | undefined;
    const hass = { connection: { subscribeEvents: vi.fn(async (cb: () => void) => { configChanged = cb; return () => {}; }) } };
    vi.mocked(api.listVersions)
      .mockResolvedValueOnce(page([version("v3", "Third", 3), version("v2", "Second", 2)], 3))
      .mockResolvedValueOnce(page([version("v1", "First", 1)], 3))
      .mockResolvedValue(page([version("v4", "Fourth", 4), version("v3", "Third", 3)], 4));
    const view = renderChanges({ hass });
    await view.findByText("Third");
    fireEvent.click(view.getByText("Load More"));
    await view.findByText("First");
    await waitFor(() => expect(configChanged).toBeTypeOf("function"));
    configChanged!();
    await view.findByText("Fourth");
    // The second page stayed: before, an update replaced every loaded page with the first.
    expect(view.getByText("First")).toBeTruthy();
    expect(view.getByText("Showing 4 of 4")).toBeTruthy();
  });

  it("steps Comparison through the list in its shown order with Previous and Next", async () => {
    const rows = [version("v3", "Third", 3), version("v2", "Second", 2), version("v1", "First", 1)];
    vi.mocked(api.listVersions).mockResolvedValue(page(rows, 3));
    vi.mocked(api.getVersion).mockImplementation(async (id: string) => ({ ...rows.find((r) => r.id === id)!, request_id: "r", before: {}, after: {} }));
    const view = renderChanges();
    fireEvent.click(await within(listWindow()).findByText("Second"));
    await waitFor(() => expect(within(comparisonWindow()).getByRole("heading", { name: "Second" })).toBeTruthy());
    fireEvent.click(within(comparisonWindow()).getByRole("button", { name: "Next Record" }));
    await waitFor(() => expect(within(comparisonWindow()).getByRole("heading", { name: "First" })).toBeTruthy());
    expect(within(comparisonWindow()).getByRole("button", { name: "Next Record" })).toBeDisabled();
    fireEvent.click(within(comparisonWindow()).getByRole("button", { name: "Previous Record" }));
    fireEvent.click(await within(comparisonWindow()).findByRole("button", { name: "Previous Record" }));
    await waitFor(() => expect(within(comparisonWindow()).getByRole("heading", { name: "Third" })).toBeTruthy());
    expect(within(comparisonWindow()).getByRole("button", { name: "Previous Record" })).toBeDisabled();
    expect(view).toBeTruthy();
  });
});

describe("Approvals Review", () => {
  it("steps through the list in its shown order with Previous and Next", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [approval("a1", "Older request", 1), approval("a2", "Newer request", 2)], total: 2, limit: 50, offset: 0 });
    const view = renderApprovals();
    // Newest first by default.
    fireEvent.click((await view.findAllByText("Newer request"))[0]);
    await waitFor(() => expect(within(reviewWindow()).getByRole("heading", { name: "Newer request" })).toBeTruthy());
    expect(within(reviewWindow()).getByRole("button", { name: "Previous Record" })).toBeDisabled();
    fireEvent.click(within(reviewWindow()).getByRole("button", { name: "Next Record" }));
    await waitFor(() => expect(within(reviewWindow()).getByRole("heading", { name: "Older request" })).toBeTruthy());
    expect(within(reviewWindow()).getByRole("button", { name: "Next Record" })).toBeDisabled();
  });
});
