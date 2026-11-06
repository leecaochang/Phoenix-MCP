/**
 * An approval whose saved action is already executing offers no actions.
 *
 * Approve runs its tool INLINE in the admin's request, so nothing resolves for
 * seconds. During that window Review must not offer Approve and Reject on an
 * approval already being acted on from Agent Chat or a notification, because the
 * server could only answer the second click with a 409.
 *
 * Two independent sources feed the locked state and both are pinned, because
 * they cover different situations: the live claim event only reaches a panel
 * that was already open, while the row's own `in_progress` makes a page loaded
 * or reloaded mid-execution agree.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api";
import { renderApprovals, reviewWindow } from "./approvals_harness";
import type { ApprovalRecord } from "../types";

vi.mock("../api", () => ({
  api: { listApprovals: vi.fn(), getApproval: vi.fn(), approveApproval: vi.fn(), rejectApproval: vi.fn() },
  currentHass: () => ({}),
}));

function record(over: Partial<ApprovalRecord> = {}): ApprovalRecord {
  return {
    id: "appr_1",
    access_key_id: "t1",
    access_key_name: "my_access_key",
    tool_name: "restart_ha",
    cap_name: "cap_restart",
    args: {},
    diff: { kind: "system_action", summary: "Restart Home Assistant" },
    status: "pending",
    created_at: "2026-01-01T00:00:00Z",
    expires_at: "2099-01-01T01:00:00Z",
    resolved_at: null,
    approved_by_user_id: null,
    closure_reason: null,
    result: null,
    request_id: "r1",
    client_ip: null,
    ...over,
  };
}

describe("an approval claimed for execution", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("locks Approve and Reject when the claim arrived as an event", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record()], total: 1, limit: 50, offset: 0,
    });

    const { findByText, findByRole } = renderApprovals({ claimedApprovals: new Set(["appr_1"]) });
    fireEvent.click(await findByText("Restart Home Assistant"));

    // The stable visible button reports that it is busy, and does nothing.
    const approve = await findByRole("button", { name: "Approve" });
    expect(approve).toHaveAttribute("aria-disabled", "true");
    expect(approve).toHaveAttribute("aria-busy", "true");
    expect(await findByRole("button", { name: "Reject" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(approve);
    expect(api.approveApproval).not.toHaveBeenCalled();
  });

  it("locks them from the row's own in_progress, for a panel loaded mid-execution", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record({ in_progress: true })], total: 1, limit: 50, offset: 0,
    });

    const { findByText, findByRole } = renderApprovals();
    fireEvent.click(await findByText("Restart Home Assistant"));

    expect(await findByRole("button", { name: "Approve" })).toHaveAttribute("aria-disabled", "true");
    expect(await findByRole("button", { name: "Reject" })).toHaveAttribute("aria-disabled", "true");
    expect(within(reviewWindow()).getByText("Being processed")).toBeInTheDocument();
  });

  it("leaves an unclaimed approval fully actionable", async () => {
    // The guard above asserts a DISABLED state, which would also hold if the
    // buttons were disabled for some unrelated reason; this is the control.
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record()], total: 1, limit: 50, offset: 0,
    });
    vi.mocked(api.approveApproval).mockResolvedValue(record({ status: "approved" }));

    const { findByText, findByRole } = renderApprovals();
    fireEvent.click(await findByText("Restart Home Assistant"));

    const approve = await findByRole("button", { name: "Approve" });
    expect(approve).not.toHaveAttribute("aria-disabled", "true");
    fireEvent.click(approve);
    await waitFor(() => expect(api.approveApproval).toHaveBeenCalledWith("appr_1"));
  });

  it("shows a decided request's outcome even while its claim is still held", async () => {
    // The release can arrive after the decision, or not at all for this panel.
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record({ status: "approved", resolved_at: "2026-01-01T00:10:00Z" })], total: 1, limit: 50, offset: 0,
    });
    const { findByText, queryByText } = renderApprovals({ scope: "history", claimedApprovals: new Set(["appr_1"]) });
    expect(await findByText("Approved")).toBeInTheDocument();
    expect(queryByText("Being processed")).toBeNull();
  });

  it("marks the pending row itself, so it reads as busy without opening it", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record({ in_progress: true })], total: 1, limit: 50, offset: 0,
    });

    const { findByText } = renderApprovals();
    expect(await findByText("Being processed")).toBeInTheDocument();
  });
});
