import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api";
import { renderApprovals, reviewWindow } from "./approvals_harness";
import type { ApprovalRecord } from "../types";

vi.mock("../api", () => ({
  api: { listApprovals: vi.fn(), getApproval: vi.fn() },
}));

function record(over: Partial<ApprovalRecord> = {}): ApprovalRecord {
  return {
    id: "appr_1",
    access_key_id: "t1",
    access_key_name: "my_access_key",
    tool_name: "create_automation",
    cap_name: "cap_automation_write",
    args: {},
    diff: { summary: "Create automation 'Front door notify'" },
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

describe("Approvals pending pagination", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("loads the first pending page with limit/offset and shows Load more when more remain", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record({ id: "appr_1", diff: { summary: "First pending" } })],
      total: 2, limit: 50, offset: 0,
    });

    const { findByText, queryByText } = renderApprovals();
    await findByText("First pending");

    expect(api.listApprovals).toHaveBeenCalledWith({ status: "pending", limit: 50, offset: 0 });
    expect(queryByText("Load More")).toBeTruthy();
  });

  it("Load more appends the next page at the accumulated offset", async () => {
    vi.mocked(api.listApprovals).mockImplementation(async ({ offset = 0 } = {}) => ({
      approvals: [record({ id: offset ? "appr_2" : "appr_1", diff: { summary: offset ? "Second pending" : "First pending" } })],
      total: 2, limit: 50, offset,
    }));

    const { findByText, getByText, queryByText } = renderApprovals();
    await findByText("First pending");

    fireEvent.click(getByText("Load More"));
    await findByText("Second pending");

    expect(api.listApprovals).toHaveBeenLastCalledWith({ status: "pending", limit: 50, offset: 1 });
    expect(queryByText("Load More")).toBeNull();
  });

  it("shows a request in Review as decided once it is approved elsewhere", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record({ id: "appr_1", diff: { summary: "Front door notify" } })],
      total: 1, limit: 50, offset: 0,
    });
    const onCountChange = vi.fn();
    const { update, findByText, findByRole, queryByRole } = renderApprovals({ onCountChange, refreshSignal: 0 });

    // Choose it for review (a deep link lands here too).
    fireEvent.click(await findByText("Front door notify"));
    expect(await findByRole("button", { name: "Approve and Execute" })).toBeTruthy();

    // It is approved elsewhere (inline Approve in Agent Chat): the list now
    // returns nothing and getApproval reports it resolved. The event bumps
    // refreshSignal, which must stop Review offering a decision on it.
    vi.mocked(api.getApproval).mockResolvedValue(
      record({ id: "appr_1", status: "approved", resolved_at: "2026-01-01T00:30:00Z", diff: { summary: "Front door notify" } }),
    );
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [], total: 0, limit: 50, offset: 0 });
    update({ onCountChange, refreshSignal: 1 });

    await waitFor(() => expect(queryByRole("button", { name: "Approve and Execute" })).toBeNull());
    expect(api.getApproval).toHaveBeenCalledWith("appr_1");
    expect(onCountChange).toHaveBeenCalled();
    // Review keeps the request, now with its outcome.
    expect(within(reviewWindow()).getByRole("heading", { name: "Front door notify" })).toBeTruthy();
    expect(within(reviewWindow()).getByText("Approved")).toBeTruthy();
  });

  it("shows a request in Review as decided once it is rejected elsewhere", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record({ id: "appr_1", diff: { summary: "Front door notify" } })],
      total: 1, limit: 50, offset: 0,
    });
    const { update, findByText, findByRole, queryByRole } = renderApprovals({ refreshSignal: 0 });
    fireEvent.click(await findByText("Front door notify"));
    expect(await findByRole("button", { name: "Reject" })).toBeTruthy();

    // Rejected elsewhere (e.g. inline Reject in Agent Chat) fires the same
    // resolved event; the non-pending status must retire the decision buttons too.
    vi.mocked(api.getApproval).mockResolvedValue(
      record({ id: "appr_1", status: "rejected", resolved_at: "2026-01-01T00:30:00Z",
               rejected_reason: "not now", diff: { summary: "Front door notify" } }),
    );
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [], total: 0, limit: 50, offset: 0 });
    update({ refreshSignal: 1 });

    await waitFor(() => expect(queryByRole("button", { name: "Reject" })).toBeNull());
    expect(api.getApproval).toHaveBeenCalledWith("appr_1");
    expect(within(reviewWindow()).getByText(/not now/)).toBeTruthy();
  });

  it("clears Review when its request is gone", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record({ id: "appr_1", diff: { summary: "Front door notify" } })],
      total: 1, limit: 50, offset: 0,
    });
    const { update, findByText, findByRole } = renderApprovals({ refreshSignal: 0 });
    fireEvent.click(await findByText("Front door notify"));
    await findByRole("button", { name: "Reject" });
    vi.mocked(api.getApproval).mockRejectedValue(new Error("Not found"));
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [], total: 0, limit: 50, offset: 0 });
    update({ refreshSignal: 1 });
    expect(await findByText("Select a request to review.")).toBeTruthy();
  });
});

it("refreshes every loaded pending page on the polling callback", async () => {
  const timer = vi.spyOn(globalThis, "setInterval");
  vi.mocked(api.listApprovals).mockImplementation(async ({ offset = 0 } = {}) => ({
    approvals: Array.from({ length: 50 }, (_, i) => record({ id: `page-${offset + i}`, diff: { summary: `Pending ${offset + i}` } })),
    total: 100, limit: 50, offset,
  }));
  const view = renderApprovals();
  await view.findByText("Pending 0");
  fireEvent.click(view.getByText("Load More"));
  await view.findByText("Pending 99");
  const poll = timer.mock.calls.find(([, delay]) => delay === 10_000)![0] as () => Promise<void>;
  await act(async () => { await poll(); });
  expect(view.getByText("Pending 99")).toBeInTheDocument();
  expect(view.queryByText("Load More")).toBeNull();
  view.unmount();
  timer.mockRestore();
});

it("keeps the Show menu but withholds the empty message while the queue is loading", async () => {
  let finish!: (value: { approvals: ApprovalRecord[]; total: number; limit: number; offset: number }) => void;
  vi.mocked(api.listApprovals).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const view = renderApprovals();
  expect(view.getByRole("combobox", { name: "Show" })).toBeInTheDocument();
  expect(view.container.querySelector(".phx-table-empty")).toBeNull();
  await act(async () => finish({ approvals: [], total: 0, limit: 50, offset: 0 }));
  expect(view.container.querySelector(".phx-table-empty")).toHaveTextContent(/No pending approvals/);
});

it("shows the server's pending total before all pages are loaded", async () => {
  vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [record()], total: 60, limit: 50, offset: 0 });
  const view = renderApprovals();
  expect(await view.findByRole("option", { name: "Pending (60)" })).toBeInTheDocument();
});


it("keeps the Show menu mounted and focusable during its initial read", async () => {
  let complete!: (value: { approvals: ApprovalRecord[]; total: number; limit: number; offset: number }) => void;
  vi.mocked(api.listApprovals).mockReturnValue(new Promise((resolve) => { complete = resolve; }));
  const { getByRole } = renderApprovals();
  const show = getByRole("combobox", { name: "Show" });
  show.focus();
  expect(show).toHaveFocus();
  expect(show).not.toBeDisabled();
  await act(async () => complete({ approvals: [], total: 0, limit: 50, offset: 0 }));
  expect(getByRole("combobox", { name: "Show" })).toBe(show);
  expect(show).toHaveFocus();
});
