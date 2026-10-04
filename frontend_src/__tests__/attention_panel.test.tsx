import { beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, waitFor } from "@testing-library/react";
import { AttentionPanel } from "../components/AttentionPanel";
import { api } from "../api";
import type { AttentionItem } from "../types";

vi.mock("../api", () => ({ api: { listAttention: vi.fn(), reviewAttention: vi.fn() } }));
const item: AttentionItem = { kind: "agent", id: "request", tool: "call_service", access_key_name: "Kitchen assistant", started_at: "2026-10-03T00:00:00Z", dispatch_result: "received" };

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/phoenix-mcp");
  vi.mocked(api.listAttention).mockResolvedValue({ items: [item] });
  vi.mocked(api.reviewAttention).mockResolvedValue({ reviewed: true });
});

it("stays quiet with nothing to review", async () => {
  vi.mocked(api.listAttention).mockResolvedValue({ items: [] });
  const view = render(<AttentionPanel />);
  await waitFor(() => expect(api.listAttention).toHaveBeenCalled());
  expect(view.container).toBeEmptyDOMElement();
});

it("requires explicit checking and never repeats an action", async () => {
  const view = render(<AttentionPanel />);
  fireEvent.click(await view.findByText("Needs attention"));
  expect(view.getByText(/returned a result/)).toBeVisible();
  const button = view.getByRole("button", { name: "Mark reviewed" });
  expect(button).toBeDisabled();
  expect(api.reviewAttention).not.toHaveBeenCalled();
  fireEvent.click(view.getByRole("checkbox", { name: "I checked the action outcome" }));
  vi.mocked(api.listAttention).mockResolvedValue({ items: [] });
  fireEvent.click(button);
  await waitFor(() => expect(api.reviewAttention).toHaveBeenCalledWith("agent", "request"));
  await waitFor(() => expect(view.getByText("Nothing needs attention.")).toBeVisible());
});

it("keeps a failed review visible with a clear retry path", async () => {
  vi.mocked(api.reviewAttention).mockRejectedValue(new Error("The review could not be saved."));
  const view = render(<AttentionPanel />);
  fireEvent.click(await view.findByText("Needs attention"));
  fireEvent.click(view.getByRole("checkbox"));
  fireEvent.click(view.getByRole("button", { name: "Mark reviewed" }));
  expect(await view.findByRole("alert")).toHaveTextContent("The review could not be saved.");
  expect(view.getByText("Check a previous action")).toBeVisible();
});

it("shows infrastructure help without offering acknowledgement", async () => {
  vi.mocked(api.listAttention).mockResolvedValue({ items: [{ kind: "mesa", id: "mesa" }, { kind: "calendar_storage", id: "calendar_storage" }] });
  const view = render(<AttentionPanel />);
  fireEvent.click(await view.findByText("Needs attention"));
  expect(view.getByText("MESA could not start")).toBeVisible();
  expect(view.queryByRole("checkbox")).toBeNull();
  expect(view.queryByRole("button", { name: "Mark reviewed" })).toBeNull();
});

it("does not mistake a failed load for a healthy state", async () => {
  vi.mocked(api.listAttention).mockRejectedValueOnce(new Error("network"));
  const view = render(<AttentionPanel />);
  fireEvent.click(await view.findByText("Needs attention"));
  expect(view.getByText(/Could not load/)).toBeVisible();
  expect(view.queryByText("Nothing needs attention.")).toBeNull();
  fireEvent.click(view.getByRole("button", { name: "Refresh" }));
  expect(await view.findByText("Check a previous action")).toBeVisible();
});

it("discards stale polls after review and unmounted responses", async () => {
  let resolvePoll!: (response: { items: AttentionItem[] }) => void;
  const poll = new Promise<{ items: AttentionItem[] }>(resolve => { resolvePoll = resolve; });
  const view = render(<AttentionPanel />);
  fireEvent.click(await view.findByText("Needs attention"));
  vi.mocked(api.listAttention).mockReturnValueOnce(poll);
  fireEvent.click(view.getByRole("button", { name: "Refresh" }));
  fireEvent.click(view.getByRole("checkbox"));
  vi.mocked(api.listAttention).mockResolvedValue({ items: [] });
  fireEvent.click(view.getByRole("button", { name: "Mark reviewed" }));
  await waitFor(() => expect(view.getByText("Nothing needs attention.")).toBeVisible());
  await act(async () => { resolvePoll({ items: [item] }); });
  expect(view.queryByText("Check a previous action")).toBeNull();
  view.unmount();
});

it("opens a deep link directly and preserves escaped request metadata", async () => {
  window.history.replaceState({}, "", "/phoenix-mcp#attention");
  vi.mocked(api.listAttention).mockResolvedValue({ items: [{ ...item, access_key_name: "<script>unsafe</script>", id: "r/escaped" }] });
  const view = render(<AttentionPanel />);
  expect(await view.findByText(/<script>unsafe<\/script>/)).toBeVisible();
  expect(view.container.querySelector("script")).toBeNull();
  expect(view.getByRole("button", { name: /Needs attention/ })).toHaveAttribute("aria-expanded", "true");
});
