import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api";
import { AGENTCHAT_REVIEW_CLOSED_EVENT, AGENTCHAT_REVIEW_DECIDED_EVENT } from "../utils/agentchat_review";
import type { ApprovalRecord } from "../types";
import { listRows, renderApprovals, reviewWindow, type HarnessProps } from "./approvals_harness";

// A notification or Agent Chat links to ONE request. It opens in Review with
// the queue beside it in Approvals, so no banner has to point at the list.
// Agent Chat may have stepped aside for the review (on a narrow screen), so it
// must hear when the review ends: "decided" after a decision, "closed" when the
// operator leaves the request undecided, exactly once.

vi.mock("../api", () => ({
  api: { listApprovals: vi.fn(), getApproval: vi.fn(), approveApproval: vi.fn(), batchApproveApprovals: vi.fn() },
  currentHass: () => ({}),
}));

function record(id: string, over: Partial<ApprovalRecord> = {}): ApprovalRecord {
  return {
    id,
    access_key_id: "t1",
    access_key_name: "my_access_key",
    tool_name: "edit_automation",
    cap_name: "cap_automation_write",
    args: {},
    diff: { kind: "yaml_diff", summary: `Edit ${id}` },
    status: "pending",
    created_at: "2026-01-01T00:00:00Z",
    expires_at: "2099-01-01T01:00:00Z",
    resolved_at: null,
    approved_by_user_id: null,
    rejected_reason: null,
    result: null,
    request_id: "r1",
    client_ip: null,
    ...over,
  };
}

/** Render as if arriving from a link onto `openId`. */
async function renderDeepLink(records: ApprovalRecord[], openId: string, props: HarnessProps = {}) {
  vi.mocked(api.listApprovals).mockResolvedValue({
    approvals: records, total: records.length, limit: 50, offset: 0,
  });
  const opened = records.find((r) => r.id === openId);
  vi.mocked(api.getApproval).mockResolvedValue(opened as ApprovalRecord);
  const utils = renderApprovals({ openApprovalId: openId, ...props });
  await waitFor(() => expect(within(reviewWindow()).queryByRole("heading", { name: `Edit ${openId}` })).toBeTruthy());
  return utils;
}

const closed = vi.fn();
const decided = vi.fn();

describe("a request opened from a link", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.addEventListener(AGENTCHAT_REVIEW_CLOSED_EVENT, closed);
    window.addEventListener(AGENTCHAT_REVIEW_DECIDED_EVENT, decided);
  });
  afterEach(() => {
    window.removeEventListener(AGENTCHAT_REVIEW_CLOSED_EVENT, closed);
    window.removeEventListener(AGENTCHAT_REVIEW_DECIDED_EVENT, decided);
  });

  it("opens in Review, brings Review into view, and marks its row in the queue", async () => {
    const onReveal = vi.fn();
    await renderDeepLink([record("a"), record("b"), record("c")], "a", { onReveal });
    expect(onReveal).toHaveBeenCalledOnce();
    await waitFor(() => expect(listRows()[0]).toHaveAttribute("aria-selected", "true"));
    expect(listRows()).toHaveLength(3);
  });

  it("clears the route only once the request is in Review", async () => {
    const onConsumedDeepLink = vi.fn();
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [record("a")], total: 1, limit: 50, offset: 0 });
    let resolveApproval!: (value: ApprovalRecord) => void;
    vi.mocked(api.getApproval).mockReturnValue(new Promise((resolve) => { resolveApproval = resolve; }));
    renderApprovals({ openApprovalId: "a", onConsumedDeepLink });
    await waitFor(() => expect(api.getApproval).toHaveBeenCalledWith("a"));
    expect(onConsumedDeepLink).not.toHaveBeenCalled();
    await act(async () => { resolveApproval(record("a")); });
    await waitFor(() => expect(onConsumedDeepLink).toHaveBeenCalledOnce());
    expect(within(reviewWindow()).getByRole("heading", { name: "Edit a" })).toBeTruthy();
  });

  it("switches Show to History for a request already decided", async () => {
    const onScopeChange = vi.fn();
    const resolved = record("a", { status: "approved", resolved_at: "2026-01-01T00:05:00Z" });
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [record("b")], total: 1, limit: 50, offset: 0 });
    vi.mocked(api.getApproval).mockResolvedValue(resolved);
    const { findByRole } = renderApprovals({ openApprovalId: "a", onScopeChange });
    await waitFor(() => expect(onScopeChange).toHaveBeenCalledWith("history"));
    expect((await findByRole("combobox", { name: "Show" }) as HTMLSelectElement).value).toBe("history");
    expect(within(reviewWindow()).queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("tells Agent Chat the review closed when the link cannot be opened, and clears the route", async () => {
    const onConsumedDeepLink = vi.fn();
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [], total: 0, limit: 50, offset: 0 });
    vi.mocked(api.getApproval).mockRejectedValue(new Error("Not found"));
    renderApprovals({ openApprovalId: "gone", onConsumedDeepLink });
    await waitFor(() => expect(onConsumedDeepLink).toHaveBeenCalledOnce());
    expect(closed).toHaveBeenCalledOnce();
    expect((closed.mock.calls[0][0] as CustomEvent).detail).toEqual({ approvalId: "gone" });
  });

  it("tells Agent Chat the review closed, once, when the operator moves to another request", async () => {
    await renderDeepLink([record("a"), record("b"), record("c")], "a");
    expect(closed).not.toHaveBeenCalled();
    fireEvent.click(listRows()[1]);
    await waitFor(() => expect(closed).toHaveBeenCalledOnce());
    expect((closed.mock.calls[0][0] as CustomEvent).detail).toEqual({ approvalId: "a" });
    fireEvent.click(listRows()[2]);
    fireEvent.click(listRows()[0]);
    expect(closed).toHaveBeenCalledOnce();
  });

  it("tells Agent Chat the review closed when the operator leaves the workspace", async () => {
    const { update } = await renderDeepLink([record("a"), record("b")], "a", { active: true });
    update({ active: false });
    await waitFor(() => expect(closed).toHaveBeenCalledOnce());
    expect((closed.mock.calls[0][0] as CustomEvent).detail).toEqual({ approvalId: "a" });
  });

  it("tells Agent Chat it was decided, and not closed, after a decision", async () => {
    vi.mocked(api.approveApproval).mockResolvedValue(record("a", { status: "approved", resolved_at: "2026-01-01T00:05:00Z" }));
    const { getByRole } = await renderDeepLink([record("a"), record("b")], "a");
    fireEvent.click(getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(decided).toHaveBeenCalledOnce());
    expect((decided.mock.calls[0][0] as CustomEvent).detail).toEqual({ approvalId: "a" });
    fireEvent.click(listRows()[0]);
    expect(closed).not.toHaveBeenCalled();
  });

  it("sends nothing when a request chosen from the list is left", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record("a"), record("b")], total: 2, limit: 50, offset: 0,
    });
    const { findByText } = renderApprovals();
    await findByText("Edit a");
    fireEvent.click(listRows()[0]);
    fireEvent.click(listRows()[1]);
    expect(closed).not.toHaveBeenCalled();
  });

  it("leaves an unconsumed deep-link intact when the panel unmounts mid-fetch", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record("a")], total: 1, limit: 50, offset: 0,
    });
    let resolveApproval!: (value: ApprovalRecord) => void;
    vi.mocked(api.getApproval).mockReturnValue(new Promise((resolve) => {
      resolveApproval = resolve;
    }));
    const onConsumedDeepLink = vi.fn();
    const { unmount } = renderApprovals({ openApprovalId: "a", onConsumedDeepLink });
    await waitFor(() => expect(api.getApproval).toHaveBeenCalledWith("a"));

    unmount();
    await act(async () => {
      resolveApproval(record("a"));
      await Promise.resolve();
    });

    expect(onConsumedDeepLink).not.toHaveBeenCalled();
  });
});
