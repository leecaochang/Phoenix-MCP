/**
 * The Approvals windows point the Guide at the section about each control
 * (user, 2026-11-01): the Show filter and search, the list or Select
 * Multiple while ticking, Approve and Reject, and each part of Request
 * Details. The pages and sections themselves are checked by guide_contract.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api";
import type { ApprovalRecord } from "../types";
import { guideTargetFor } from "../shell/guideTarget";
import { listRows, pickBoxes, renderApprovals, reviewWindow } from "./approvals_harness";

vi.mock("../api", () => ({
  api: { listApprovals: vi.fn(), getApproval: vi.fn(), batchApproveApprovals: vi.fn() },
  currentHass: () => ({}),
}));

const record = (id: string): ApprovalRecord => ({
  id, access_key_id: "t1", access_key_name: "my_access_key", tool_name: "edit_automation", cap_name: "cap_automation_write",
  args: {}, diff: { kind: "yaml_diff", summary: `Edit ${id}` }, status: "pending", created_at: "2026-01-01T00:00:00Z",
  expires_at: "2099-01-01T01:00:00Z", resolved_at: null, approved_by_user_id: null, rejected_reason: null, result: null,
  request_id: "r1", client_ip: null,
});

/** The harness's sections, marked as the shell marks its windows. */
function asWindows() {
  const ids: Record<string, string> = { "Approvals window": "approvals", "Review window": "review", "Request Details window": "requestDetails" };
  for (const [label, id] of Object.entries(ids)) document.querySelector(`section[aria-label="${label}"]`)!.setAttribute("data-phx-window", id);
}

describe("the Guide in Approvals", () => {
  beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); });

  it("opens the section about the control selected", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [record("a"), record("b")], total: 2, limit: 50, offset: 0 });
    const utils = renderApprovals();
    await utils.findByText("Edit a");
    asWindows();
    expect(guideTargetFor(utils.getByRole("combobox", { name: "Show" }))).toMatchObject({ topic: "window.approvals", anchor: "show" });
    expect(guideTargetFor(listRows()[0])).toMatchObject({ topic: "window.approvals", anchor: "list" });

    fireEvent.click(listRows()[0]);
    const review = reviewWindow();
    const approve = await within(review).findByRole("button", { name: "Approve" });
    expect(guideTargetFor(approve)).toMatchObject({ windowId: "review", topic: "window.review", anchor: "approve" });
    expect(guideTargetFor(within(review).getByRole("button", { name: "Reject" }))).toMatchObject({ anchor: "reject" });
    const args = await waitFor(() => [...document.querySelectorAll("summary")].find((s) => s.textContent === "Arguments")!);
    expect(guideTargetFor(args)).toMatchObject({ windowId: "requestDetails", topic: "window.requestDetails", anchor: "raw-args" });

    fireEvent.click(utils.getByRole("button", { name: "Select Multiple" }));
    await waitFor(() => expect(pickBoxes()).toHaveLength(2));
    expect(guideTargetFor(pickBoxes()[0])).toMatchObject({ topic: "window.approvals", anchor: "select-multiple" });
  });
});
