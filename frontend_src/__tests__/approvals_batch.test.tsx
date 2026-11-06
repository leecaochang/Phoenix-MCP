import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api";
import type { ApprovalRecord } from "../types";
import { listRows, pickBoxes, renderApprovals, reviewWindow } from "./approvals_harness";

// Batch approve is an operator convenience only: the server still runs each id
// through the same single-approval path. These cover the panel's own contract,
// which is selection (what may be ticked), ordering (what is sent), and the
// three-part result, since "approved 6" alone reads as success on a run that
// stopped at item 7.

vi.mock("../api", () => ({
  api: { listApprovals: vi.fn(), getApproval: vi.fn(), batchApproveApprovals: vi.fn() },
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
    closure_reason: null,
    result: null,
    request_id: "r1",
    client_ip: null,
    ...over,
  };
}

async function renderPending(records: ApprovalRecord[], props = {}) {
  vi.mocked(api.listApprovals).mockResolvedValue({
    approvals: records, total: records.length, limit: 50, offset: 0,
  });
  const utils = renderApprovals(props);
  if (records.length) await utils.findByText(records[0].diff.summary!);
  return utils;
}

describe("batch approve", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("offers no selection for a single pending approval", async () => {
    const { getByRole } = await renderPending([record("a")]);
    expect(getByRole("button", { name: "Select Multiple" })).toBeDisabled();
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    expect(pickBoxes()).toHaveLength(0);
  });

  it("shows tick boxes only in selection mode, and Cancel Selection leaves it", async () => {
    const { getByRole, queryByRole } = await renderPending([record("a"), record("b")]);
    expect(pickBoxes()).toHaveLength(0);
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    expect(pickBoxes()).toHaveLength(2);
    fireEvent.click(getByRole("button", { name: "Cancel Selection" }));
    expect(pickBoxes()).toHaveLength(0);
    expect(queryByRole("button", { name: /Approve \d Selected/ })).toBeNull();
  });

  it("sends the ticked ids in the order shown", async () => {
    vi.mocked(api.batchApproveApprovals).mockResolvedValue({
      applied: [{ approval_id: "a", tool_name: "edit_automation" },
                { approval_id: "c", tool_name: "edit_automation" }],
      failed: null,
      remaining: [],
    });
    const { getByRole } = await renderPending(
      [record("a"), record("b"), record("c")],
    );
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    // Each box is named by its request, not by the tool all three share.
    expect(new Set(pickBoxes().map((b) => b.getAttribute("aria-label"))).size).toBe(3);
    // Tick c first, then a: the request must still follow list order, so that a
    // "stopped at" names a row the operator can find.
    fireEvent.click(pickBoxes()[2]);
    fireEvent.click(pickBoxes()[0]);
    fireEvent.click(getByRole("button", { name: "Approve 2 Selected" }));
    await waitFor(() => expect(api.batchApproveApprovals).toHaveBeenCalled());
    expect(vi.mocked(api.batchApproveApprovals).mock.calls[0][0]).toEqual(["a", "c"]);
  });

  it("ticking never changes the request in Review, and Space on a row ticks it", async () => {
    const { getByRole } = await renderPending([record("a"), record("b"), record("c")]);
    fireEvent.click(listRows()[1]);
    expect(within(reviewWindow()).getByText("Edit b")).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    fireEvent.click(pickBoxes()[0]);
    fireEvent.keyDown(listRows()[2], { key: " " });
    expect(pickBoxes().map((b) => b.checked)).toEqual([true, false, true]);
    // The row a keyboard user ticked with Space says so, and the count is spoken.
    expect(listRows()[2].getAttribute("aria-label")).toMatch(/selected for batch approval$/);
    expect(listRows()[1].getAttribute("aria-label")).not.toMatch(/selected for batch approval/);
    expect(document.querySelector('.approvals-list-view > .sr-only[role="status"]')?.textContent).toBe("2 selected");
    expect(within(reviewWindow()).getByText("Edit b")).toBeTruthy();
    expect(getByRole("button", { name: "Approve 2 Selected" })).toBeTruthy();
  });

  it("select-all ticks every selectable row", async () => {
    const { getByRole, findByRole } = await renderPending([record("a"), record("b")]);
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    fireEvent.click(getByRole("checkbox", { name: "Select all" }));
    expect(await findByRole("button", { name: "Approve 2 Selected" })).toBeTruthy();
  });

  it("will not tick an approval that is already executing", async () => {
    // The server would answer 409 on its claim and halt the run on a row the
    // operator never chose, so it is not selectable in the first place. Three
    // rows, because a claimed one does not count toward the two that turn
    // batching on at all.
    const { getByRole } = await renderPending([record("a", { in_progress: true }), record("b"), record("c")]);
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    expect(pickBoxes()[0].disabled).toBe(true);
    expect(pickBoxes()[1].disabled).toBe(false);
    fireEvent.keyDown(listRows()[0], { key: " " });
    expect(pickBoxes()[0].checked).toBe(false);
  });

  it("offers no selection when only one row is selectable", async () => {
    // A lone tick box with no button to press reads as a broken control,
    // and a claimed row does not make a second one "selectable".
    const { getByRole } = await renderPending([record("a", { in_progress: true }), record("b")]);
    expect(getByRole("button", { name: "Select Multiple" })).toBeDisabled();
  });

  it("retires the result banner once the queue moves under it", async () => {
    // Otherwise "Approved 2" sits above two freshly-arrived rows it does not
    // describe, which is what an operator reads as the current state.
    vi.mocked(api.batchApproveApprovals).mockResolvedValue({
      applied: [{ approval_id: "a", tool_name: "edit_automation" },
                { approval_id: "b", tool_name: "edit_automation" }],
      failed: null,
      remaining: [],
    });
    const { getByRole, findByText, queryByText, update } = await renderPending(
      [record("a"), record("b")],
    );
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    fireEvent.click(getByRole("checkbox", { name: "Select all" }));
    fireEvent.click(getByRole("button", { name: "Approve 2 Selected" }));
    expect(await findByText("Approved 2.")).toBeTruthy();

    // A new approval arrives on the next poll.
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record("x"), record("y")], total: 2, limit: 50, offset: 0,
    });
    update({ refreshSignal: 1 });
    await waitFor(() => expect(queryByText("Approved 2.")).toBeNull());
  });

  it("keeps the result once the run's own reload removes what it approved", async () => {
    vi.mocked(api.batchApproveApprovals).mockResolvedValue({
      applied: [{ approval_id: "a", tool_name: "edit_automation" },
                { approval_id: "b", tool_name: "edit_automation" }],
      failed: null,
      remaining: [],
    });
    const { getByRole, findByText, getByText } = await renderPending([record("a"), record("b")]);
    // The reload after the run answers a moment later, without the approved rows.
    vi.mocked(api.listApprovals).mockImplementation(() => new Promise((resolve) => {
      setTimeout(() => resolve({ approvals: [], total: 0, limit: 50, offset: 0 }), 30);
    }));
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    fireEvent.click(getByRole("checkbox", { name: "Select all" }));
    fireEvent.click(getByRole("button", { name: "Approve 2 Selected" }));
    expect(await findByText("Approved 2.")).toBeTruthy();
    await new Promise((r) => setTimeout(r, 80));
    expect(getByText("Approved 2.")).toBeTruthy();
  });

  it("keeps the result while reloads and approval events catch up in several steps", async () => {
    vi.mocked(api.batchApproveApprovals).mockResolvedValue({
      applied: [{ approval_id: "a", tool_name: "edit_automation" },
                { approval_id: "b", tool_name: "edit_automation" }],
      failed: null,
      remaining: [],
    });
    const { getByRole, findByText, getByText, update } = await renderPending([record("a"), record("b")]);
    // The run's own reload still sees one row; the event-driven reload after it sees none.
    vi.mocked(api.listApprovals)
      .mockResolvedValueOnce({ approvals: [record("b")], total: 1, limit: 50, offset: 0 })
      .mockResolvedValue({ approvals: [], total: 0, limit: 50, offset: 0 });
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    fireEvent.click(getByRole("checkbox", { name: "Select all" }));
    fireEvent.click(getByRole("button", { name: "Approve 2 Selected" }));
    expect(await findByText("Approved 2.")).toBeTruthy();
    update({ refreshSignal: 1 });
    await waitFor(() => expect(api.listApprovals).toHaveBeenCalledTimes(3));
    await new Promise((r) => setTimeout(r, 30));
    expect(getByText("Approved 2.")).toBeTruthy();
  });

  it("reports applied, the stopping point, and what is left pending", async () => {
    vi.mocked(api.batchApproveApprovals).mockResolvedValue({
      applied: [{ approval_id: "a", tool_name: "edit_automation" }],
      failed: {
        approval_id: "b", tool_name: "edit_automation", status: 409,
        error: "conflict", message: "Capability is now denied for this access key.",
      },
      remaining: ["c"],
    });
    const { getByRole, findByText } = await renderPending(
      [record("a"), record("b"), record("c")],
    );
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    fireEvent.click(getByRole("checkbox", { name: "Select all" }));
    fireEvent.click(getByRole("button", { name: "Approve 3 Selected" }));

    expect(await findByText("Approved 1.")).toBeTruthy();
    expect(await findByText(/Capability is now denied/)).toBeTruthy();
    // The remaining count is the fact the operator acts on: untouched, not lost.
    expect(await findByText("1 left pending.")).toBeTruthy();
  });

  it("names a failed run's reason in words, not its slug", async () => {
    // An executor that reported an error finalizes the record as execution_failed
    // with no message, which the banner once showed raw.
    vi.mocked(api.batchApproveApprovals).mockResolvedValue({
      applied: [],
      failed: { approval_id: "a", tool_name: "edit_automation", status: 200, error: "execution_failed" },
      remaining: ["b"],
    });
    const { getByRole, findByText, queryByText } = await renderPending([record("a"), record("b")]);
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    fireEvent.click(getByRole("checkbox", { name: "Select all" }));
    fireEvent.click(getByRole("button", { name: "Approve 2 Selected" }));
    expect(await findByText(/Execution failed/)).toBeTruthy();
    expect(queryByText(/execution_failed/)).toBeNull();
  });

  it("select-all stops at the server's batch limit of 50", async () => {
    const many = Array.from({ length: 55 }, (_, i) => record(`r${i}`));
    const { getByRole, findByRole } = await renderPending(many);
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    fireEvent.click(getByRole("checkbox", { name: "Select all" }));
    expect(await findByRole("button", { name: "Approve 50 Selected" })).toBeTruthy();
    // The rest cannot be ticked one by one either, until one is unticked.
    expect(pickBoxes()[54].disabled).toBe(true);
    fireEvent.click(pickBoxes()[0]);
    expect(pickBoxes()[54].disabled).toBe(false);
  });

  it("offers neither Approve nor a tick box for a request past its expiry", async () => {
    const { getByRole } = await renderPending([
      record("a", { expires_at: "2020-01-01T00:00:00Z" }), record("b"), record("c"),
    ]);
    fireEvent.click(listRows()[0]);
    expect(within(reviewWindow()).queryByRole("button", { name: "Approve" })).toBeNull();
    expect(within(reviewWindow()).getByRole("button", { name: "Reject" })).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    expect(pickBoxes()[0].disabled).toBe(true);
    expect(pickBoxes()[1].disabled).toBe(false);
  });

  it("keeps only the untouched remainder ticked after a stop", async () => {
    vi.mocked(api.batchApproveApprovals).mockResolvedValue({
      applied: [{ approval_id: "a", tool_name: "edit_automation" }],
      failed: { approval_id: "b", tool_name: "edit_automation", status: 500, error: "internal_error" },
      remaining: ["c"],
    });
    const { getByRole, findByRole } = await renderPending(
      [record("a"), record("b"), record("c")],
    );
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    fireEvent.click(getByRole("checkbox", { name: "Select all" }));
    fireEvent.click(getByRole("button", { name: "Approve 3 Selected" }));
    // One click retries exactly the remainder once the cause is dealt with.
    expect(await findByRole("button", { name: "Approve 1 Selected" })).toBeTruthy();
  });

  it("ends selection once nothing is left", async () => {
    vi.mocked(api.batchApproveApprovals).mockResolvedValue({
      applied: [{ approval_id: "a", tool_name: "edit_automation" }, { approval_id: "b", tool_name: "edit_automation" }],
      failed: null,
      remaining: [],
    });
    const { getByRole, findByRole } = await renderPending([record("a"), record("b")]);
    fireEvent.click(getByRole("button", { name: "Select Multiple" }));
    fireEvent.click(getByRole("checkbox", { name: "Select all" }));
    fireEvent.click(getByRole("button", { name: "Approve 2 Selected" }));
    expect(await findByRole("button", { name: "Select Multiple" })).toBeTruthy();
    expect(pickBoxes()).toHaveLength(0);
  });
});
