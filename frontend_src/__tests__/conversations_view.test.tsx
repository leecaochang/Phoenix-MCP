import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor, act } from "@testing-library/react";
import { ConversationsView } from "../views/ConversationsView";
import { api } from "../api";
import { validApiResponse } from "../api_response";
import type { ConversationEntry, ConversationListResponse, ConversationSummary } from "../types";

vi.mock("../api", () => ({ api: { getConversations: vi.fn(), getConversation: vi.fn(), clearConversations: vi.fn() } }));
const summary: ConversationSummary = {
  id: "one", source: "mcp", started_at: "2026-10-03T00:00:00Z", updated_at: "2026-10-03T00:00:01Z",
  coverage: "tool_exchange", status: "completed", execution_status: "allowed", response_status: "client_unavailable",
  excerpt: "", tool_count: 1, change_count: 0, omissions: [],
};
const detail: ConversationEntry = { ...summary, prompt: null, response: null, metadata: { request_id: "r1" }, events: [
  { kind: "tool_call", timestamp: summary.started_at, tool_id: "t1", payload: { name: "get_state", arguments: { entity_id: "light.test" } } },
  { kind: "tool_result", timestamp: summary.updated_at, tool_id: "t1", payload: "<script>alert(1)</script>", outcome: "allowed" },
] };
const list = (entries = [summary]): ConversationListResponse => ({ entries, total: entries.length, enabled: true, available: true, bytes: 100 });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getConversations).mockImplementation(async () => list());
  vi.mocked(api.getConversation).mockResolvedValue(detail);
  vi.mocked(api.clearConversations).mockResolvedValue({ cleared: true });
});

describe("ConversationsView", () => {
  it("loads details only on expansion and renders untrusted content as inert text", async () => {
    const view = render(<ConversationsView />);
    const title = await view.findByText("Tool exchange; prompt unavailable");
    expect(api.getConversation).not.toHaveBeenCalled();
    fireEvent.click(title.closest("summary")!);
    await view.findByText("Your prompt");
    expect(api.getConversation).toHaveBeenCalledWith("one");
    expect(view.getByText(/original prompt was not available/)).toBeTruthy();
    expect(view.getByText(/external client did not provide/)).toBeTruthy();
    expect(view.container.querySelector("script")).toBeNull();
    expect(view.getByText("<script>alert(1)</script>")).toBeTruthy();
  });

  it("discards stale filtered responses", async () => {
    const old = deferred<ConversationListResponse>();
    vi.mocked(api.getConversations).mockReturnValueOnce(old.promise).mockResolvedValueOnce(list([{ ...summary, excerpt: "Current" }]));
    const view = render(<ConversationsView />);
    fireEvent.change(view.getByLabelText("Filter by source"), { target: { value: "voice" } });
    await view.findByText("Current");
    await act(async () => old.resolve(list([{ ...summary, excerpt: "Stale" }])));
    expect(view.queryByText("Stale")).toBeNull();
  });

  it("requires explicit clear and removes opened payloads", async () => {
    const view = render(<ConversationsView />);
    const title = await view.findByText("Tool exchange; prompt unavailable");
    fireEvent.click(title.closest("summary")!);
    await view.findByText("Your prompt");
    fireEvent.click(view.getByText("Clear conversation history"));
    expect(api.clearConversations).not.toHaveBeenCalled();
    fireEvent.click(view.getAllByText("Clear conversation history")[1]);
    await waitFor(() => expect(api.clearConversations).toHaveBeenCalledOnce());
    await view.findByText("No conversations to show");
    expect(view.queryByText("Your prompt")).toBeNull();
  });

  it("keeps expanded entries through a refresh and reports recoverable errors", async () => {
    const view = render(<ConversationsView />);
    const title = await view.findByText("Tool exchange; prompt unavailable");
    fireEvent.click(title.closest("summary")!);
    await view.findByText("Your prompt");
    vi.mocked(api.getConversations).mockRejectedValueOnce(new Error("unavailable"));
    fireEvent.click(view.getByText("Refresh"));
    await view.findByRole("alert");
    expect(view.getByText("Your prompt")).toBeTruthy();
    fireEvent.click(view.getByText("Refresh"));
    await waitFor(() => expect(view.queryByRole("alert")).toBeNull());
    expect(title.closest("details")?.open).toBe(true);
  });

  it("rejects malformed API envelopes", () => {
    expect(validApiResponse("/conversations", "GET", list())).toBe(true);
    expect(validApiResponse("/conversations", "GET", { ...list(), entries: [{ id: "one" }] })).toBe(false);
    expect(validApiResponse("/conversations/one", "GET", { ...detail, events: [{}] })).toBe(false);
    expect(validApiResponse("/conversations/one", "GET", detail)).toBe(true);
  });
});
