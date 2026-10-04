/**
 * An approval whose saved action is already executing offers no actions.
 *
 * Approve runs its tool INLINE in the admin's request, so nothing resolves for
 * seconds. During that window the tab must not offer Approve and Reject on an
 * approval already being acted on from Agent Chat or a notification, because the
 * server could only answer the second click with a 409.
 *
 * Two independent sources feed the locked state and both are pinned, because
 * they cover different situations: the live claim event only reaches a panel
 * that was already open, while the row's own `in_progress` makes a page loaded
 * or reloaded mid-execution agree.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";
import { ApprovalsView } from "../views/ApprovalsView";
import { api } from "../api";
import type { ApprovalRecord } from "../types";

vi.mock("../api", () => ({
  api: { listApprovals: vi.fn(), getApproval: vi.fn(), approveApproval: vi.fn(), rejectApproval: vi.fn() },
  currentHass: () => ({}),
}));

function record(over: Partial<ApprovalRecord> = {}): ApprovalRecord {
  return {
    id: "appr_1",
    token_id: "t1",
    token_name: "my_token",
    tool_name: "restart_ha",
    cap_name: "cap_restart",
    args: {},
    diff: { kind: "system_action", summary: "Restart Home Assistant" },
    status: "pending",
    created_at: "2026-01-01T00:00:00Z",
    expires_at: "2026-01-01T01:00:00Z",
    resolved_at: null,
    approved_by_user_id: null,
    rejected_reason: null,
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

    const { findByText, findByRole } = render(
      <ApprovalsView tab="pending" onTabChange={() => {}}
                     claimedApprovals={new Set(["appr_1"])} />,
    );
    fireEvent.click(await findByText("Restart Home Assistant"));

    // The accessible name reports why the stable visible button is locked.
    const approve = await findByRole("button", { name: "Approving..." });
    expect(approve).toHaveAttribute("aria-disabled", "true");
    expect(await findByRole("button", { name: "Reject" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(approve);
    expect(api.approveApproval).not.toHaveBeenCalled();
  });

  it("locks them from the row's own in_progress, for a panel loaded mid-execution", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record({ in_progress: true })], total: 1, limit: 50, offset: 0,
    });

    const { findByText, findByRole } = render(
      <ApprovalsView tab="pending" onTabChange={() => {}} />,
    );
    fireEvent.click(await findByText("Restart Home Assistant"));

    expect(await findByRole("button", { name: "Approving..." })).toHaveAttribute("aria-disabled", "true");
    expect(await findByRole("button", { name: "Reject" })).toHaveAttribute("aria-disabled", "true");
  });

  it("leaves an unclaimed approval fully actionable", async () => {
    // The guard above asserts a DISABLED state, which would also hold if the
    // buttons were disabled for some unrelated reason; this is the control.
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record()], total: 1, limit: 50, offset: 0,
    });
    vi.mocked(api.approveApproval).mockResolvedValue(record({ status: "approved" }));

    const { findByText, findByRole } = render(
      <ApprovalsView tab="pending" onTabChange={() => {}} />,
    );
    fireEvent.click(await findByText("Restart Home Assistant"));

    const approve = await findByRole("button", { name: "Approve and execute" });
    expect(approve).not.toHaveAttribute("aria-disabled", "true");
    fireEvent.click(approve);
    await waitFor(() => expect(api.approveApproval).toHaveBeenCalledWith("appr_1"));
  });

  it("marks the pending row itself, so it reads as busy without opening it", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record({ in_progress: true })], total: 1, limit: 50, offset: 0,
    });

    const { findByText } = render(
      <ApprovalsView tab="pending" onTabChange={() => {}} />,
    );
    expect(await findByText("Being processed")).toBeInTheDocument();
  });
});
