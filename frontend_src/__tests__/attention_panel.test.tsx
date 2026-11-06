import { beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, waitFor, within } from "@testing-library/react";
import { AttentionList, useAttention } from "../components/AttentionPanel";
import { api } from "../api";
import type { AttentionItem } from "../types";

vi.mock("../api", () => ({ api: { listAttention: vi.fn(), reviewAttention: vi.fn() } }));
const item: AttentionItem = { kind: "agent", id: "request", tool: "call_service", access_key_name: "Kitchen assistant", started_at: "2026-10-03T00:00:00Z", dispatch_result: "received" };

/** The list as the Needs Attention window shows it. */
function AttentionPanel({ onShowRequest }: { onShowRequest?: (requestId: string) => void } = {}) {
  const state = useAttention();
  return <AttentionList state={state} onShowRequest={onShowRequest} />;
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/phoenix-mcp");
  vi.mocked(api.listAttention).mockResolvedValue({ items: [item] });
  vi.mocked(api.reviewAttention).mockResolvedValue({ reviewed: true });
});

it("says nothing needs attention when there is nothing to review", async () => {
  vi.mocked(api.listAttention).mockResolvedValue({ items: [] });
  const view = render(<AttentionPanel />);
  expect(await view.findByText("Nothing needs attention.")).toBeVisible();
});

it("keeps the confirmation, busy, until the review is saved", async () => {
  let done!: (value: { reviewed: boolean }) => void;
  vi.mocked(api.reviewAttention).mockReturnValue(new Promise((resolve) => { done = resolve; }));
  const view = render(<AttentionPanel />);
  await view.findByText("Check a previous action");
  fireEvent.click(view.getByRole("button", { name: "Mark Reviewed" }));
  fireEvent.click(view.getByRole("checkbox", { name: "I checked the action outcome" }));
  fireEvent.click(within(view.getByRole("group", { name: "Mark Reviewed" })).getByRole("button", { name: "Mark Reviewed" }));
  expect(view.getByRole("group", { name: "Mark Reviewed" })).toBeTruthy();
  vi.mocked(api.listAttention).mockResolvedValue({ items: [] });
  await act(async () => { done({ reviewed: true }); });
  await waitFor(() => expect(view.queryByRole("group", { name: "Mark Reviewed" })).toBeNull());
});

it("names the request and offers to show it in the Audit Log", async () => {
  const onShowRequest = vi.fn();
  const view = render(<AttentionPanel onShowRequest={onShowRequest} />);
  await view.findByText(/Check a previous action|MESA could not start|Could not load/);
  // The request id is on the card: it is what ties the item to its audit row.
  expect(view.getByText("request")).toBeVisible();
  fireEvent.click(view.getByRole("button", { name: "Show in Audit Log" }));
  expect(onShowRequest).toHaveBeenCalledWith("request");
  expect(api.reviewAttention).not.toHaveBeenCalled();
});

it("labels a calendar item's ID as a calendar receipt, not a request", async () => {
  vi.mocked(api.listAttention).mockResolvedValue({ items: [{ ...item, kind: "calendar", id: "receipt-1" }] });
  const view = render(<AttentionPanel />);
  expect(await view.findByText("receipt-1")).toBeVisible();
  expect(view.getByText("Calendar receipt")).toBeVisible();
  expect(view.queryByText("Request")).toBeNull();
});

it("asks for the acknowledgement in place and never repeats an action", async () => {
  const view = render(<AttentionPanel />);
  await view.findByText(/Check a previous action|MESA could not start|Could not load/);
  expect(view.getByText(/Home Assistant answered/)).toBeVisible();
  // Mark Reviewed opens the confirmation; it does not review on its own.
  fireEvent.click(view.getByRole("button", { name: "Mark Reviewed" }));
  expect(api.reviewAttention).not.toHaveBeenCalled();
  const confirm = view.getByRole("group", { name: "Mark Reviewed" });
  const button = within(confirm).getByRole("button", { name: "Mark Reviewed" });
  expect(button).toBeDisabled();
  expect(within(confirm).getByText(/does not repeat or undo/)).toBeVisible();
  fireEvent.click(within(confirm).getByRole("checkbox", { name: "I checked the action outcome" }));
  vi.mocked(api.listAttention).mockResolvedValue({ items: [] });
  fireEvent.click(button);
  await waitFor(() => expect(api.reviewAttention).toHaveBeenCalledWith("agent", "request"));
  await waitFor(() => expect(view.getByText("Nothing needs attention.")).toBeVisible());
});

it("cancels the confirmation without reviewing", async () => {
  const view = render(<AttentionPanel />);
  await view.findByText(/Check a previous action|MESA could not start|Could not load/);
  fireEvent.click(view.getByRole("button", { name: "Mark Reviewed" }));
  fireEvent.click(within(view.getByRole("group", { name: "Mark Reviewed" })).getByRole("button", { name: "Cancel" }));
  expect(view.queryByRole("group", { name: "Mark Reviewed" })).toBeNull();
  expect(api.reviewAttention).not.toHaveBeenCalled();
  expect(view.getByText("Check a previous action")).toBeVisible();
});

it("keeps a failed review visible with a clear retry path", async () => {
  vi.mocked(api.reviewAttention).mockRejectedValue(new Error("The review could not be saved."));
  const view = render(<AttentionPanel />);
  await view.findByText(/Check a previous action|MESA could not start|Could not load/);
  fireEvent.click(view.getByRole("button", { name: "Mark Reviewed" }));
  fireEvent.click(view.getByRole("checkbox", { name: "I checked the action outcome" }));
  fireEvent.click(within(view.getByRole("group", { name: "Mark Reviewed" })).getByRole("button", { name: "Mark Reviewed" }));
  expect(await view.findByRole("alert")).toHaveTextContent("The review could not be saved.");
  expect(view.getByText("Check a previous action")).toBeVisible();
});

it("shows infrastructure help without offering acknowledgement", async () => {
  vi.mocked(api.listAttention).mockResolvedValue({ items: [
    { kind: "mesa", id: "mesa" }, { kind: "mesa_rename", id: "mesa_rename" }, { kind: "calendar_storage", id: "calendar_storage" }, { kind: "audit_storage", id: "audit_storage" },
  ] });
  const view = render(<AttentionPanel />);
  await view.findByText(/Check a previous action|MESA could not start|Could not load/);
  expect(view.getByText("MESA could not start")).toBeVisible();
  expect(view.getByText("A renamed entity's profile is not in storage")).toBeVisible();
  expect(view.getByText("The audit log is not reaching storage")).toBeVisible();
  expect(view.queryByRole("checkbox")).toBeNull();
  expect(view.queryByRole("group", { name: "Mark Reviewed" })).toBeNull();
  expect(view.queryByRole("button", { name: "Mark Reviewed" })).toBeNull();
});

it("does not mistake a failed load for a healthy state", async () => {
  vi.mocked(api.listAttention).mockRejectedValueOnce(new Error("network"));
  const view = render(<AttentionPanel />);
  await view.findByText(/Check a previous action|MESA could not start|Could not load/);
  expect(view.getByText(/Could not load/)).toBeVisible();
  expect(view.queryByText("Nothing needs attention.")).toBeNull();
  // The list keeps itself up to date, so there is no Refresh button:
  // returning to the window reads it again.
  expect(view.queryByRole("button", { name: "Refresh" })).toBeNull();
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(await view.findByText("Check a previous action")).toBeVisible();
});

it("discards stale polls after review and unmounted responses", async () => {
  let resolvePoll!: (response: { items: AttentionItem[] }) => void;
  const poll = new Promise<{ items: AttentionItem[] }>(resolve => { resolvePoll = resolve; });
  const view = render(<AttentionPanel />);
  await view.findByText(/Check a previous action|MESA could not start|Could not load/);
  vi.mocked(api.listAttention).mockReturnValueOnce(poll);
  window.dispatchEvent(new Event("focus"));
  fireEvent.click(view.getByRole("button", { name: "Mark Reviewed" }));
  fireEvent.click(view.getByRole("checkbox", { name: "I checked the action outcome" }));
  vi.mocked(api.listAttention).mockResolvedValue({ items: [] });
  fireEvent.click(within(view.getByRole("group", { name: "Mark Reviewed" })).getByRole("button", { name: "Mark Reviewed" }));
  await waitFor(() => expect(view.getByText("Nothing needs attention.")).toBeVisible());
  await act(async () => { resolvePoll({ items: [item] }); });
  expect(view.queryByText("Check a previous action")).toBeNull();
  view.unmount();
});

it("preserves escaped request metadata", async () => {
  window.history.replaceState({}, "", "/phoenix-mcp#attention");
  vi.mocked(api.listAttention).mockResolvedValue({ items: [{ ...item, access_key_name: "<script>unsafe</script>", id: "r/escaped" }] });
  const view = render(<AttentionPanel />);
  expect(await view.findByText(/<script>unsafe<\/script>/)).toBeVisible();
  expect(view.container.querySelector("script")).toBeNull();
});
