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
    await view.findByText("Prompt");
    expect(api.getConversation).toHaveBeenCalledWith("one");
    expect(view.getByText(/did not provide the user/)).toBeTruthy();
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
    await view.findByText("Prompt");
    fireEvent.click(view.getByText("Clear conversation history"));
    expect(api.clearConversations).not.toHaveBeenCalled();
    fireEvent.click(view.getAllByText("Clear conversation history")[1]);
    await waitFor(() => expect(api.clearConversations).toHaveBeenCalledOnce());
    await view.findByText("No conversations to show");
    expect(view.queryByText("Prompt")).toBeNull();
  });

  it("keeps expanded entries through a refresh and reports recoverable errors", async () => {
    const view = render(<ConversationsView />);
    const title = await view.findByText("Tool exchange; prompt unavailable");
    fireEvent.click(title.closest("summary")!);
    await view.findByText("Prompt");
    vi.mocked(api.getConversations).mockRejectedValueOnce(new Error("unavailable"));
    fireEvent.click(view.getByText("Refresh"));
    await view.findByRole("alert");
    expect(view.getByText("Prompt")).toBeTruthy();
    fireEvent.click(view.getByText("Refresh"));
    await waitFor(() => expect(view.queryByRole("alert")).toBeNull());
    expect(title.closest("details")?.open).toBe(true);
  });

  it("marks activity and failures with icons and exposes identity and error text without opening payloads", async () => {
    vi.mocked(api.getConversations).mockResolvedValue(list([{ ...summary, status: "failed" }]));
    vi.mocked(api.getConversation).mockResolvedValue({ ...detail, source: "agentcli", status: "failed", response_status: "failed",
      metadata: { token_id: "token-id", token_name: "Kitchen client", provider: "openai", model: "selected-model" },
      events: [...detail.events,
        { kind: "approval_requested", timestamp: summary.started_at, tool_id: "review", payload: { status: "pending" } },
        { kind: "error", timestamp: summary.updated_at, tool_id: null, payload: { code: "unauthorized", message: "This token is no longer authorized." } },
      ],
    });
    const view = render(<ConversationsView />);
    fireEvent.click((await view.findByText("Tool exchange; prompt unavailable")).closest("summary")!);
    const message = await view.findByText("This token is no longer authorized.");
    expect(message.closest("details")?.className).toBe("conversation-entry");
    expect(message.closest("li")?.className).toContain("conversation-event-error");
    expect(view.container.querySelectorAll(".conversation-event > svg[aria-hidden='true']")).toHaveLength(4);
    expect(view.container.querySelector(".conversation-status.conversation-signal-error svg")).toBeTruthy();
    expect(view.getByText("Kitchen client")).toBeTruthy();
    expect(view.getByText("selected-model")).toBeTruthy();
    const technical = view.container.querySelector(".conversation-technical")!;
    expect(technical.textContent).toContain('"source": "agentcli"');
    expect(technical.textContent).toContain('"response_outcome": "failed"');
    expect(technical.textContent).toContain('"token_id": "token-id"');
  });

  it("rejects malformed API envelopes", () => {
    expect(validApiResponse("/conversations", "GET", list())).toBe(true);
    expect(validApiResponse("/conversations", "GET", { ...list(), entries: [{ id: "one" }] })).toBe(false);
    expect(validApiResponse("/conversations/one", "GET", { ...detail, events: [{}] })).toBe(false);
    expect(validApiResponse("/conversations/one", "GET", detail)).toBe(true);
  });
});

it("shows mixed outcomes, tool titles and storage without zero counts", async () => {
  vi.mocked(api.getConversations).mockResolvedValue({ ...list([{ ...summary, first_tool: "call_service", execution_status: "partial", tool_count: 2 }]), bytes: 500000, max_bytes: 25000000 });
  const view = render(<ConversationsView />);
  await view.findByText("call_service");
  expect(view.container.querySelector(".conversation-status")?.textContent).toBe("Mixed outcomes");
  expect(view.getByText("Storage: 0.5 of 25 MB")).toBeTruthy();
  expect(view.getByText(/2 tool calls/)).toBeTruthy();
  expect(view.queryByText(/0 changes/)).toBeNull();
});

it("keeps narration, tool activity and reply in order with omissions explained", async () => {
  vi.mocked(api.getConversation).mockResolvedValue({ ...detail, response: "The light is on.", response_status: "produced", omitted_events: 3,
    field_omissions: { prompt: "privacy" }, events: [
      { kind: "assistant_text", timestamp: summary.started_at, tool_id: null, payload: "Let me check." },
      ...detail.events,
      { kind: "assistant_text", timestamp: summary.updated_at, tool_id: null, payload: "The light is on." },
    ],
  });
  const view = render(<ConversationsView />);
  fireEvent.click((await view.findByText("Tool exchange; prompt unavailable")).closest("summary")!);
  await view.findByText("Let me check.");
  const rows = view.container.querySelectorAll(".conversation-event");
  expect(rows[0].textContent).toContain("Let me check.");
  expect(rows[1].textContent).toContain("get_state");
  expect(rows[3].textContent).toContain("Reply generated");
  expect(view.getAllByText("The light is on.")).toHaveLength(1);
  expect(view.getByText(/entity-name logging is disabled/)).toBeTruthy();
  expect(view.getByText(/3 events are not shown/)).toBeTruthy();
  expect(rows[0].querySelector("time")?.textContent).not.toBe("00:00:00");
});
