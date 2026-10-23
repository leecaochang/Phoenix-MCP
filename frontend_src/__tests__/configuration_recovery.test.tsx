import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api";
import { renderApprovals, reviewWindow } from "./approvals_harness";
import type { ApprovalRecord } from "../types";

vi.mock("../api", () => ({
  api: { listApprovals: vi.fn(), approveApproval: vi.fn(), rejectApproval: vi.fn(), getApproval: vi.fn() },
  currentHass: () => ({}),
}));

const recovery: ApprovalRecord = {
  id: "appr_recovery", access_key_id: "__configuration_recovery__", access_key_name: "Phoenix MCP",
  tool_name: "restore_configuration", cap_name: "__configuration_recovery__",
  args: { recovery_id: "saved-record", side: "before" },
  diff: {
    kind: "config_diff", summary: "Restore saved configuration for authored_file:www/recovery.txt",
    summary_key: "diff.configuration_recovery.restore", summary_params: { resource: "authored_file:www/recovery.txt" },
    before: "unexpected current content", after: "saved content",
  },
  status: "pending", created_at: "2026-09-30T00:00:00Z", expires_at: "2026-09-30T01:00:00Z",
  resolved_at: null, approved_by_user_id: null, rejected_reason: null,
  result: null, request_id: "recovery", client_ip: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [recovery], total: 1, limit: 50, offset: 0 });
});

it.each(["approve", "reject"] as const)("resolves recovery through the existing %s flow", async (action) => {
  vi.mocked(api.approveApproval).mockResolvedValue({ ...recovery, status: "approved" });
  vi.mocked(api.rejectApproval).mockResolvedValue({ ...recovery, status: "rejected" });
  const view = renderApprovals();
  fireEvent.click((await view.findAllByText(recovery.diff.summary!))[0]);
  await waitFor(() => expect(reviewWindow().querySelector(".phx-review")).toBeTruthy());
  const review = within(reviewWindow());
  // What deciding does, in words, beside the exact change.
  expect(review.getByText(/Approve restores the saved configuration shown in Details/)).toBeTruthy();
  expect(review.getByText(/Reject keeps the current configuration/)).toBeTruthy();
  expect(review.getByText("unexpected current content")).toBeTruthy();
  expect(review.getByText("saved content")).toBeTruthy();
  expect(review.queryByText("Verify")).toBeNull();
  if (action === "approve") {
    fireEvent.click(review.getByRole("button", { name: "Approve and Execute" }));
    await waitFor(() => expect(api.approveApproval).toHaveBeenCalledWith(recovery.id));
  } else {
    fireEvent.click(review.getByRole("button", { name: "Reject" }));
    fireEvent.click(await review.findByRole("button", { name: "Confirm Rejection" }));
    await waitFor(() => expect(api.rejectApproval).toHaveBeenCalledWith(recovery.id, {}));
  }
  await waitFor(() => expect(review.queryByRole("button", { name: "Approve and Execute" })).toBeNull());
});
