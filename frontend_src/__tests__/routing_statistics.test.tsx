import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
const get = vi.fn(), reset = vi.fn();
vi.mock("../api", () => ({ api: { getRoutingStatistics: (...args: unknown[]) => get(...args), resetRoutingStatistics: (...args: unknown[]) => reset(...args) } }));
import { RoutingStatistics } from "../components/RoutingStatistics";
const empty = { since: "2026-09-25T00:00:00Z", outcomes: { local: 0, decision: 0, provider: 0, pending: 0, failed: 0, cancelled: 0 }, provider_turns: 0, provider_usage: { requests: 0, reports: 0, input_tokens: 0, output_tokens: 0 }, decision_usage: { requests: 0, reports: 0, input_tokens: 0, output_tokens: 0 } };
beforeEach(() => { get.mockReset(); reset.mockReset(); });
function expand() { const details = screen.getByText("Routing statistics").parentElement!; details.setAttribute("open", ""); fireEvent(details, new Event("toggle")); }
it("loads only on expansion and never presents missing usage as zero", async () => {
  get.mockResolvedValue({ chat: { ...empty, outcomes: { ...empty.outcomes, local: 1, failed: 1 }, provider_usage: { requests: 1, reports: 0, input_tokens: 0, output_tokens: 0 } } });
  render(<RoutingStatistics surface="chat" />);
  expect(get).not.toHaveBeenCalled(); expand();
  expect(await screen.findByText("50% of requests completed without the conversational model.")).toBeVisible();
  expect(screen.getByText("Unavailable")).toBeVisible();
  expect(screen.getByText("1 requests missing usage")).toBeVisible();
  expect(screen.getByText(/External MCP clients/)).toBeVisible();
});
it("requires confirmation and resets only the selected surface", async () => {
  get.mockResolvedValue({ voice: empty }); reset.mockResolvedValue({ voice: empty });
  render(<RoutingStatistics surface="voice" />); expand();
  await screen.findByText("No requests recorded yet.");
  fireEvent.click(screen.getByRole("button", { name: "Reset statistics" }));
  expect(reset).not.toHaveBeenCalled();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reset statistics" }));
  await waitFor(() => expect(reset).toHaveBeenCalledExactlyOnceWith("voice"));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
it("keeps an error recoverable without discarding prior counters", async () => {
  get.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ chat: empty });
  render(<RoutingStatistics surface="chat" />); expand();
  await screen.findByRole("alert"); fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("No requests recorded yet.")).toBeVisible();
});
