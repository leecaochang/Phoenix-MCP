import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api";
import type { ApprovalsScope } from "../shell/approvals";
import { listRows, renderApprovals, reviewWindow } from "./approvals_harness";
import type { ApprovalRecord } from "../types";
import {
  AGENTCHAT_REVIEW_DECIDED_EVENT,
  reviewDecisionApprovalId,
} from "../utils/agentchat_review";

vi.mock("../api", () => ({
  api: { listApprovals: vi.fn(), approveApproval: vi.fn(), rejectApproval: vi.fn(), getApproval: vi.fn() },
  currentHass: () => ({}),
}));

function record(over: Partial<ApprovalRecord> = {}): ApprovalRecord {
  return {
    id: "appr_summary",
    access_key_id: "t1",
    access_key_name: "foxy_access_key",
    tool_name: "restart_ha",
    cap_name: "cap_restart",
    args: { technical: true },
    diff: { kind: "system_action", summary: "Restart Home Assistant", summary_key: "diff.restart_ha", summary_params: {} },
    status: "pending",
    created_at: "2026-08-13T04:00:00Z",
    expires_at: "2099-08-13T05:00:00Z",
    resolved_at: null,
    approved_by_user_id: null,
    closure_reason: null,
    result: null,
    request_id: "r1",
    client_ip: null,
    ...over,
  };
}

/** Show the request in Review, choosing it from the list. */
async function open(recordValue = record(), scope: ApprovalsScope = "pending") {
  vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [recordValue], total: 1, limit: 50, offset: 0 });
  const view = renderApprovals({ scope });
  fireEvent.click((await view.findAllByText("Restart Home Assistant"))[0]);
  await waitFor(() => expect(view.container.querySelector(".phx-review")).toBeTruthy());
  return view;
}

const details = () => within(document.querySelector<HTMLElement>('section[aria-label="Request Details window"]')!);

describe("Review and Request Details", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("filters the list by search and updates itself without remounting its controls", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [record()], total: 1, limit: 50, offset: 0 });
    const view = renderApprovals();
    await view.findByText("Restart Home Assistant");
    const search = view.getByRole("searchbox");
    // A word has to appear as it was typed; its letters in order are not enough.
    fireEvent.change(search, { target: { value: "rst hm" } });
    expect(view.queryByText("Restart Home Assistant")).toBeNull();
    fireEvent.change(search, { target: { value: "home restart" } });
    expect(view.getByText("Restart Home Assistant")).toBeVisible();
    fireEvent.change(search, { target: { value: "unmatched-value" } });
    expect(view.queryByText("Restart Home Assistant")).toBeNull();
    expect(view.getByText("No approvals match your search.")).toBeVisible();
    // The list updates itself, so there is no Refresh button.
    expect(view.queryByRole("button", { name: "Refresh Approvals" })).toBeNull();
    expect(view.getByRole("searchbox")).toBe(search);
  });

  it("empties Review and Request Details when the list is told to show other requests", async () => {
    const view = await open();
    expect(reviewWindow().querySelector(".phx-review")).toBeTruthy();
    expect(details().queryByText("Select a request to see its details.")).toBeNull();
    // Showing another set of requests leaves nothing chosen in the list.
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [], total: 0, limit: 50, offset: 0 });
    fireEvent.change(view.getByLabelText("Show"), { target: { value: "history" } });
    await waitFor(() => expect(reviewWindow().querySelector(".phx-review")).toBeNull());
    expect(details().getByText("Select a request to see its details.")).toBeVisible();
  });

  it("updates itself while it is shown, in every scope", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [record()], total: 1, limit: 50, offset: 0 });
      const view = renderApprovals();
      await view.findByText("Restart Home Assistant");
      const first = vi.mocked(api.listApprovals).mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(vi.mocked(api.listApprovals).mock.calls.length).toBeGreaterThan(first);

      // History updates too, and a failed update keeps what is shown.
      fireEvent.change(view.getByRole("combobox"), { target: { value: "history" } });
      await waitFor(() => expect(vi.mocked(api.listApprovals).mock.lastCall?.[0]?.status).toBeUndefined());
      const before = vi.mocked(api.listApprovals).mock.calls.length;
      vi.mocked(api.listApprovals).mockRejectedValueOnce(new Error("NetworkError"));
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(vi.mocked(api.listApprovals).mock.calls.length).toBeGreaterThan(before);
      expect(view.queryByText("NetworkError")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the summary in Review and the technical detail in Request Details, building inputs only on request", async () => {
    await open();
    expect(within(reviewWindow()).getByText("Home Assistant will be briefly unavailable while it restarts.")).toBeTruthy();
    expect(within(reviewWindow()).queryByText("Capability")).toBeNull();
    expect(details().getByText("Capability")).toBeTruthy();
    expect(details().getByText("cap_restart")).toBeTruthy();
    // Inputs can be large (a whole dashboard): built only when opened.
    expect(details().queryByText(/technical/)).toBeNull();
    const inputs = details().getByText("Arguments").closest("details")!;
    inputs.open = true;
    fireEvent(inputs, new Event("toggle"));
    expect(await details().findByText(/technical/)).toBeTruthy();
  });

  it("asks for an optional reason inside Review, focused, and allows an empty one", async () => {
    vi.mocked(api.rejectApproval).mockResolvedValue(record({ status: "rejected" }));
    const view = await open();
    fireEvent.click(view.getByRole("button", { name: "Reject" }));
    const input = await view.findByLabelText("Optional reason");
    expect(document.activeElement).toBe(input);
    expect(view.getByRole("button", { name: "Reject" })).toHaveAttribute("aria-pressed", "true");
    // Approve stays where it is but cannot be pressed while a rejection is being written.
    expect(view.getByRole("button", { name: "Approve" })).toHaveAttribute("aria-disabled", "true");
    expect(view.getByRole("button", { name: "Cancel Rejection" })).toBeTruthy();
    fireEvent.click(view.getByRole("button", { name: "Confirm Rejection" }));
    await waitFor(() => expect(api.rejectApproval).toHaveBeenCalledWith("appr_summary", {}));
  });

  it("Cancel Rejection and Escape leave the request pending", async () => {
    const view = await open();
    fireEvent.click(view.getByRole("button", { name: "Reject" }));
    fireEvent.click(await view.findByRole("button", { name: "Cancel Rejection" }));
    expect(view.queryByLabelText("Optional reason")).toBeNull();
    fireEvent.click(view.getByRole("button", { name: "Reject" }));
    fireEvent.keyDown(await view.findByLabelText("Optional reason"), { key: "Escape" });
    expect(view.queryByLabelText("Optional reason")).toBeNull();
    expect(api.rejectApproval).not.toHaveBeenCalled();
    expect(view.getByRole("button", { name: "Approve" })).not.toHaveAttribute("aria-disabled", "true");
  });

  it.each(["approve", "reject"] as const)("signals Agent Chat after a successful %s", async (action) => {
    const updated = record({ status: action === "approve" ? "approved" : "rejected" });
    vi.mocked(api.approveApproval).mockResolvedValue(updated);
    vi.mocked(api.rejectApproval).mockResolvedValue(updated);
    const decided = vi.fn((event: Event) => reviewDecisionApprovalId(event));
    window.addEventListener(AGENTCHAT_REVIEW_DECIDED_EVENT, decided);
    try {
      const view = await open();
      if (action === "approve") {
        fireEvent.click(view.getByRole("button", { name: "Approve" }));
      } else {
        fireEvent.click(view.getByRole("button", { name: "Reject" }));
        fireEvent.click(await view.findByRole("button", { name: "Confirm Rejection" }));
      }
      await waitFor(() => expect(decided).toHaveBeenCalledOnce());
      expect(decided.mock.results[0].value).toBe("appr_summary");
    } finally {
      window.removeEventListener(AGENTCHAT_REVIEW_DECIDED_EVENT, decided);
    }
  });

  it("keeps the Approve button and its label while busy, then shows the outcome in place", async () => {
    let finish!: (value: ApprovalRecord) => void;
    vi.mocked(api.approveApproval).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const view = await open();
    const approve = view.getByRole("button", { name: "Approve" });
    approve.focus();
    fireEvent.click(approve);
    await waitFor(() => expect(approve).toHaveAttribute("aria-busy", "true"));
    expect(approve).toHaveTextContent("Approve");
    fireEvent.click(approve);
    expect(api.approveApproval).toHaveBeenCalledOnce();

    finish(record({ status: "approved", resolved_at: "2026-08-13T04:05:00Z" }));
    expect(await within(reviewWindow()).findByText("The request completed successfully.")).toBeTruthy();
    expect(view.queryByRole("button", { name: "Approve" })).toBeNull();
    // Focus does not fall to the page with Approve: the request's title takes it, and the
    // outcome is spoken from a status region that was there before the decision.
    expect(document.activeElement).toBe(within(reviewWindow()).getByRole("heading", { name: "Restart Home Assistant" }));
    expect(within(reviewWindow()).getByRole("status")).toHaveTextContent("The request completed successfully.");
  });

  it("uses friendly titles in History, with the technical summary in Request Details", async () => {
    const resolved = record({
      status: "approved",
      resolved_at: "2026-08-13T04:05:00Z",
      tool_name: "edit_dashboard_card",
      diff: {
        kind: "config_diff",
        summary: "Replace card 2 on dashboard 'Home' (view 0)",
        summary_key: "diff.dashboard_card.edit",
        summary_params: { card_index: 2, label: "Home", view_index: 0 },
      },
    });
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [resolved], total: 1, limit: 50, offset: 0 });
    const view = renderApprovals({ scope: "history" });
    expect(await view.findByText("Update a card on dashboard 'Home'")).toBeTruthy();
    expect(view.queryByText("Replace card 2 on dashboard 'Home' (view 0)")).toBeNull();

    fireEvent.click(listRows()[0]);
    expect(await details().findByText("Replace card 2 on dashboard 'Home' (view 0)")).toBeTruthy();
  });

  it("uses friendly titles in Pending", async () => {
    const pending = record({
      tool_name: "edit_dashboard_card",
      diff: {
        kind: "config_diff",
        summary: "Replace card 2 on dashboard 'Home' (view 0)",
        summary_key: "diff.dashboard_card.edit",
        summary_params: { card_index: 2, label: "Home", view_index: 0 },
      },
    });
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [pending], total: 1, limit: 50, offset: 0 });
    const view = renderApprovals();
    expect(await view.findByText("Update a card on dashboard 'Home'")).toBeTruthy();
    expect(view.queryByText("Replace card 2 on dashboard 'Home' (view 0)")).toBeNull();
  });

  it.each([
    ["approved", null, null, "The request completed successfully."],
    ["rejected", "not now", null, "The request was rejected and was not run. Reason: not now"],
    ["expired", null, null, "The request expired and was not run."],
    ["cancelled", "admin_cancelled", null, "The request was cancelled and was not run. Reason: Cancelled by admin"],
    ["failed", "execution_failed", { tool_result: { content: [{ type: "text", text: "Forbidden." }] } }, "The request was approved but did not complete. Error: The action could not be completed. Request Details shows the full error under Result."],
    ["failed", "execution_interrupted", null, "Execution was interrupted and the change may have been applied. Verify the current state before requesting it again."],
  ] as const)("shows a friendly %s outcome", async (status, rejectedReason, result, expected) => {
    const resolved = record({ status, closure_reason: rejectedReason, result, resolved_at: "2026-08-13T04:05:00Z" });
    await open(resolved, "history");
    expect(await within(reviewWindow()).findByText(expected)).toBeTruthy();
    expect(within(reviewWindow()).queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it.each(["pending", "history"] as const)("uses arrows in the %s list to move Review through it", async (scope) => {
    const first = record({
      id: `${scope}_first`,
      status: scope === "pending" ? "pending" : "approved",
      resolved_at: scope === "pending" ? null : "2026-08-13T04:05:00Z",
    });
    const second = record({
      id: `${scope}_second`,
      status: scope === "pending" ? "pending" : "approved",
      resolved_at: scope === "pending" ? null : "2026-08-13T04:06:00Z",
      tool_name: "reconfigure_zigbee_device",
      diff: {
        kind: "system_action",
        summary: "Reconfigure Zigbee device Fitness PIR ZB",
        summary_key: "diff.zigbee_reconfigure.device",
        summary_params: { label: "Fitness PIR ZB" },
      },
    });
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [first, second], total: 2, limit: 50, offset: 0 });
    const view = renderApprovals({ scope });
    fireEvent.click((await view.findAllByText("Restart Home Assistant"))[0]);
    await waitFor(() => expect(within(reviewWindow()).getByRole("heading", { name: "Restart Home Assistant" })).toBeTruthy());

    fireEvent.keyDown(listRows()[0], { key: "ArrowDown" });
    expect(await within(reviewWindow()).findByRole("heading", { name: "Reconfigure (re-interview) Zigbee device Fitness PIR ZB" })).toBeTruthy();
    expect(listRows()[1]).toHaveFocus();
    fireEvent.keyDown(listRows()[1], { key: "ArrowUp" });
    expect(await within(reviewWindow()).findByRole("heading", { name: "Restart Home Assistant" })).toBeTruthy();
  });
});
