import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
const get = vi.fn(), reset = vi.fn();
vi.mock("../api", () => ({ api: { getRoutingStatistics: (...args: unknown[]) => get(...args), resetRoutingStatistics: (...args: unknown[]) => reset(...args) } }));
import { RoutingStatistics } from "../components/RoutingStatistics";
import { helpFor } from "../shell/help";
const empty = { since: "2026-09-25T00:00:00Z", outcomes: { local: 0, decision: 0, provider: 0, pending: 0, failed: 0, cancelled: 0 }, provider_turns: 0, provider_usage: { requests: 0, reports: 0, input_tokens: 0, output_tokens: 0 }, decision_usage: { requests: 0, reports: 0, input_tokens: 0, output_tokens: 0 } };
beforeEach(() => { get.mockReset(); reset.mockReset(); });
function expand() { const details = screen.getByText("Routing Statistics").parentElement!; details.setAttribute("open", ""); fireEvent(details, new Event("toggle")); }
it("loads only on expansion and never presents missing usage as zero", async () => {
  get.mockResolvedValue({ chat: { ...empty, outcomes: { ...empty.outcomes, local: 1, failed: 1 }, provider_usage: { requests: 1, reports: 0, input_tokens: 0, output_tokens: 0 } } });
  render(<RoutingStatistics surface="chat" />);
  expect(get).not.toHaveBeenCalled(); expand();
  expect(await screen.findByText("50% of requests completed without the conversational model.")).toBeVisible();
  expect(screen.getByText("Unavailable")).toBeVisible();
  expect(screen.getByText("1 request missing usage")).toBeVisible();
  // What the counts cover is in Help, not in the window.
  expect(screen.queryByText(/External MCP clients/)).toBeNull();
  expect(helpFor(screen.getByText("Routing Statistics").parentElement!.querySelector("dl"))).toMatch(/External MCP clients.*Reported tokens only/);
});
it("confirms in place and resets only the selected surface", async () => {
  get.mockResolvedValue({ voice: empty }); reset.mockResolvedValue({ voice: empty });
  render(<RoutingStatistics surface="voice" />); expand();
  await screen.findByText("No requests recorded yet.");
  fireEvent.click(screen.getByRole("button", { name: "Reset Statistics" }));
  expect(reset).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).toBeNull();
  const confirm = screen.getByRole("group", { name: "Reset Statistics" });
  expect(confirm).toHaveTextContent("Clear statistics for this card only");
  fireEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("group", { name: "Reset Statistics" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Reset Statistics" }));
  fireEvent.click(within(screen.getByRole("group", { name: "Reset Statistics" })).getByRole("button", { name: "Reset Statistics" }));
  await waitFor(() => expect(reset).toHaveBeenCalledExactlyOnceWith("voice"));
  await waitFor(() => expect(screen.queryByRole("group", { name: "Reset Statistics" })).toBeNull());
});
it("keeps an error recoverable without discarding prior counters", async () => {
  get.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ chat: empty });
  render(<RoutingStatistics surface="chat" />); expand();
  await screen.findByRole("alert"); fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("No requests recorded yet.")).toBeVisible();
});
