import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor, act, within } from "@testing-library/react";
import { useState } from "react";
import { ConversationsView } from "../views/ConversationsView";
import { ToolRegistry, ToolRegistryContext, WindowPlaceContext } from "../shell/tools";
import { LogDetailsContent, LogSelection, LogSelectionContext } from "../shell/logDetails";
import { api } from "../api";
import { validApiResponse } from "../api_response";
import type { AccessKeyRecord, ConversationEntry, ConversationListResponse, ConversationSummary } from "../types";

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

/** The Conversation Log with Log Details beside it, as in the Logs workspace. */
function Logs({ accessKeys }: { accessKeys?: AccessKeyRecord[] }) {
  const [selection] = useState(() => new LogSelection());
  return <LogSelectionContext.Provider value={selection}><ConversationsView accessKeys={accessKeys} /><LogDetailsContent /></LogSelectionContext.Provider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getConversations).mockImplementation(async () => list());
  vi.mocked(api.getConversation).mockResolvedValue(detail);
  vi.mocked(api.clearConversations).mockResolvedValue({ cleared: true });
});

describe("ConversationsView", () => {
  it("says storage is unavailable from a region already in the page", async () => {
    let answer = list();
    vi.mocked(api.getConversations).mockImplementation(async () => answer);
    const view = render(<Logs />);
    await view.findAllByText(/get_state|one|MCP/);
    const region = view.container.querySelector(".phx-status-region");
    expect(region).toHaveAttribute("role", "status");
    expect(region).toBeEmptyDOMElement();
    answer = { ...list(), available: false };
    fireEvent.click(view.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(region?.textContent).not.toBe(""));
    expect(view.container.querySelector(".phx-status-region")).toBe(region);
  });

  it("keeps the log shown when a background update fails, as the Audit Log does", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const view = render(<Logs />);
    await view.findAllByText(/get_state|one|MCP/);
    vi.mocked(api.getConversations).mockRejectedValue(new Error("offline"));
    await act(async () => { vi.advanceTimersByTime(5100); });
    expect(view.queryByText("Conversation history could not be loaded. Refresh to try again.")).toBeNull();
    vi.useRealTimers();
  });

  it("resolves renamed access keys without changing retained evidence or collapsing the turn", async () => {
    const stored = { ...detail, metadata: { access_key_id: "key-1", access_key_name: "Old_name" } };
    vi.mocked(api.getConversation).mockResolvedValue(stored);
    const keys = [{ id: "key-1", name: "Current_name" }] as AccessKeyRecord[];
    const view = render(<Logs accessKeys={keys} />);
    fireEvent.click((await view.findByText("Tool exchange; prompt unavailable")).closest("tr")!);
    await view.findByText("Current_name");
    expect(view.queryByText("Old_name")).toBeNull();
    view.rerender(<Logs accessKeys={[{ ...keys[0], name: "Renamed_again" }]} />);
    await view.findByText("Renamed_again");
    expect(view.container.querySelector(".phx-log-details")).toBeTruthy();
    expect(stored.metadata.access_key_name).toBe("Old_name");
    view.rerender(<Logs accessKeys={[]} />);
    await view.findByText("Old_name");
  });

  it("loads details only on expansion and renders untrusted content as inert text", async () => {
    const view = render(<Logs />);
    const title = await view.findByText("Tool exchange; prompt unavailable");
    expect(api.getConversation).not.toHaveBeenCalled();
    fireEvent.click(title.closest("tr")!);
    await view.findByRole("region", { name: "Prompt" });
    expect(api.getConversation).toHaveBeenCalledWith("one");
    expect(view.getByText(/did not provide the user/)).toBeTruthy();
    expect(view.getByText(/external client did not provide/)).toBeTruthy();
    expect(view.container.querySelector("script")).toBeNull();
    expect(view.getByText("<script>alert(1)</script>")).toBeTruthy();
  });

  it("labels an image-only prompt instead of calling it missing", async () => {
    const image = { ...summary, coverage: "partial_conversation" as const, source: "agentcli", prompt_attachments: 1 };
    vi.mocked(api.getConversations).mockImplementation(async () => list([image]));
    vi.mocked(api.getConversation).mockResolvedValue({ ...detail, ...image, prompt: [{ attachment: true, type: "image", omitted: "media" }] });
    const view = render(<Logs />);
    fireEvent.click((await view.findByText("Image or File Prompt")).closest("tr")!);
    // In the row, as the heading of Log Details, and as the prompt itself.
    await waitFor(() => expect(view.getAllByText("Image or File Prompt")).toHaveLength(3));
    expect(view.queryByText(/did not receive a usable prompt/)).toBeNull();
  });

  it("discards stale filtered responses", async () => {
    const old = deferred<ConversationListResponse>();
    vi.mocked(api.getConversations).mockReturnValueOnce(old.promise).mockResolvedValueOnce(list([{ ...summary, excerpt: "Current" }]));
    const view = render(<Logs />);
    fireEvent.change(view.getByLabelText("Filter by source"), { target: { value: "voice" } });
    await view.findByText("Current");
    await act(async () => old.resolve(list([{ ...summary, excerpt: "Stale" }])));
    expect(view.queryByText("Stale")).toBeNull();
  });

  it("requires explicit clear and removes opened payloads", async () => {
    const view = render(<Logs />);
    const title = await view.findByText("Tool exchange; prompt unavailable");
    fireEvent.click(title.closest("tr")!);
    await view.findByRole("region", { name: "Prompt" });
    fireEvent.click(view.getByText("Clear Conversation History"));
    expect(api.clearConversations).not.toHaveBeenCalled();
    fireEvent.click(within(view.getByRole("group", { name: "Clear Conversation History" })).getByRole("button", { name: "Clear Conversation History" }));
    await waitFor(() => expect(api.clearConversations).toHaveBeenCalledOnce());
    await view.findByText("No conversations to show");
    expect(view.queryByRole("region", { name: "Prompt" })).toBeNull();
  });

  it("offers Clear on the toolbar inside the shell and still asks before deleting", async () => {
    const registry = new ToolRegistry();
    const view = render(
      <ToolRegistryContext.Provider value={registry}>
        <WindowPlaceContext.Provider value={{ workspace: "logs", window: "conversationLog" }}>
          <ConversationsView />
        </WindowPlaceContext.Provider>
      </ToolRegistryContext.Provider>,
    );
    await view.findByText("Tool exchange; prompt unavailable", undefined, { timeout: 3000 });
    expect(view.queryByText("Clear Conversation History")).toBeNull();
    const clear = registry.latest("logs", "conversationLog", "clear");
    expect(clear).toMatchObject({ label: "Clear Conversation History", disabled: false });
    // Red only once it asks to confirm.
    expect(clear!.tone).toBeUndefined();
    act(() => clear!.onSelect!());
    expect(api.clearConversations).not.toHaveBeenCalled();
    const confirm = view.getByRole("group", { name: "Clear Conversation History" });
    expect(within(confirm).getByRole("button", { name: "Clear Conversation History" })).toHaveClass("btn-danger");
    expect(confirm.compareDocumentPosition(view.container.querySelector(".phx-table")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Icon and text, like every in-window action, and a Cancel beside it.
    const go = within(confirm).getByRole("button", { name: "Clear Conversation History" });
    expect(go.querySelector("svg")).not.toBeNull();
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toBeTruthy();
    fireEvent.click(go);
    await waitFor(() => expect(api.clearConversations).toHaveBeenCalledOnce(), { timeout: 3000 });
    await waitFor(() => expect(registry.latest("logs", "conversationLog", "clear")?.disabled).toBe(true), { timeout: 3000 });
  });

  it("hides the text search with the other filters on the toolbar's Filters", async () => {
    const registry = new ToolRegistry();
    const view = render(
      <ToolRegistryContext.Provider value={registry}>
        <WindowPlaceContext.Provider value={{ workspace: "logs", window: "conversationLog" }}>
          <ConversationsView />
        </WindowPlaceContext.Provider>
      </ToolRegistryContext.Provider>,
    );
    await view.findByText("Tool exchange; prompt unavailable", undefined, { timeout: 3000 });
    expect(view.queryByRole("searchbox")).toBeNull();
    act(() => registry.latest("logs", "conversationLog", "filters")!.onSelect!());
    const search = view.getByRole("searchbox");
    // A search counts as an active filter, so hidden filters still say one is applied.
    fireEvent.change(search, { target: { value: "lamp" } });
    act(() => registry.latest("logs", "conversationLog", "filters")!.onSelect!());
    expect(view.queryByRole("searchbox")).toBeNull();
    // Hidden filters stop applying, and come back as they were when shown again.
    expect(registry.latest("logs", "conversationLog", "filters")!.badge).toBeUndefined();
    await waitFor(() => expect(vi.mocked(api.getConversations)).toHaveBeenLastCalledWith(expect.objectContaining({ search: "" })));
    act(() => registry.latest("logs", "conversationLog", "filters")!.onSelect!());
    expect(view.getByRole("searchbox")).toHaveValue("lamp");
    expect(registry.latest("logs", "conversationLog", "filters")!.badge).toBe(1);
  });

  it("keeps expanded entries through a refresh and reports recoverable errors", async () => {
    const view = render(<Logs />);
    const title = await view.findByText("Tool exchange; prompt unavailable");
    fireEvent.click(title.closest("tr")!);
    await view.findByRole("region", { name: "Prompt" });
    vi.mocked(api.getConversations).mockRejectedValueOnce(new Error("unavailable"));
    fireEvent.click(view.getByRole("button", { name: "Refresh" }));
    await view.findByRole("alert");
    expect(view.getByRole("region", { name: "Prompt" })).toBeTruthy();
    fireEvent.click(view.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(view.queryByRole("alert")).toBeNull());
    expect(view.getByRole("region", { name: "Prompt" })).toBeTruthy();
    expect(title.closest("tr")).toHaveAttribute("aria-selected", "true");
  });

  it("marks activity and failures with icons and exposes identity and error text without opening payloads", async () => {
    vi.mocked(api.getConversations).mockResolvedValue(list([{ ...summary, status: "failed" }]));
    vi.mocked(api.getConversation).mockResolvedValue({ ...detail, source: "agentcli", status: "failed", response_status: "failed",
      metadata: { access_key_id: "access-key-id", access_key_name: "Kitchen client", provider: "openai", model: "selected-model" },
      events: [...detail.events,
        { kind: "approval_requested", timestamp: summary.started_at, tool_id: "review", payload: { status: "pending" } },
        { kind: "error", timestamp: summary.updated_at, tool_id: null, payload: { code: "unauthorized", message: "This access key is no longer authorized." } },
      ],
    });
    const view = render(<Logs />);
    fireEvent.click((await view.findByText("Tool exchange; prompt unavailable")).closest("tr")!);
    const message = await view.findByText("This access key is no longer authorized.");
    expect(message.closest(".phx-log-details")).toBeTruthy();
    expect(message.closest("li")?.className).toContain("conversation-event-error");
    expect(view.container.querySelectorAll(".conversation-event > svg[aria-hidden='true']")).toHaveLength(4);
    expect(view.container.querySelector(".conversation-status.conversation-signal-error svg")).toBeTruthy();
    expect(view.getByText("Kitchen client")).toBeTruthy();
    expect(view.getByText("selected-model")).toBeTruthy();
    const technical = view.container.querySelector(".conversation-technical")!;
    expect(technical.textContent).toContain('"source": "agentcli"');
    expect(technical.textContent).toContain('"response_outcome": "failed"');
    expect(technical.textContent).toContain('"access_key_id": "access-key-id"');
  });

  it("rejects malformed API envelopes", () => {
    expect(validApiResponse("/conversations", "GET", list())).toBe(true);
    expect(validApiResponse("/conversations", "GET", { ...list(), entries: [{ id: "one" }] })).toBe(false);
    expect(validApiResponse("/conversations/one", "GET", { ...detail, events: [{}] })).toBe(false);
    expect(validApiResponse("/conversations/one", "GET", detail)).toBe(true);
  });
});

it("shows mixed outcomes and tool titles without the redundant intro or storage line", async () => {
  vi.mocked(api.getConversations).mockResolvedValue({ ...list([{ ...summary, first_tool: "call_service", execution_status: "partial", tool_count: 2 }]), bytes: 500000, max_bytes: 25000000 });
  const view = render(<Logs />);
  await view.findByText("call_service");
  expect(view.container.querySelector(".conversation-status")?.textContent).toBe("Mixed outcomes");
  fireEvent.click(view.getByText("call_service").closest("tr")!);
  expect(view.queryByText("Storage: 0.5 of 25 MB")).toBeNull();
  expect(view.queryByText("Recording enabled")).toBeNull();
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
  const view = render(<Logs />);
  fireEvent.click((await view.findByText("Tool exchange; prompt unavailable")).closest("tr")!);
  await view.findByText("Let me check.");
  const rows = view.container.querySelectorAll(".conversation-event");
  expect(rows[0].textContent).toContain("Let me check.");
  expect(rows[1].textContent).toContain("get_state");
  expect(rows[3].textContent).toContain("Reply generated");
  expect(view.getAllByText("The light is on.")).toHaveLength(1);
  expect(view.getByText(/Log entity IDs is off/)).toBeTruthy();
  expect(view.getByText(/3 events are not shown/)).toBeTruthy();
  expect(rows[0].querySelector("time")?.textContent).not.toBe("00:00:00");
});


it("opens grouped turns in chronological order and preserves reading position on refresh", async () => {
  const first = { ...summary, id: "first", source: "agentcli", excerpt: "First question" };
  const second = { ...first, id: "second", excerpt: "Follow-up question", started_at: "2026-10-03T01:00:00Z" };
  vi.mocked(api.getConversations).mockResolvedValue(list([{ ...first, turns: [first, second], history_missing: true }]));
  vi.mocked(api.getConversation).mockImplementation(async id => ({ ...detail, ...(id === "first" ? first : second), prompt: id === "first" ? "First question" : "Follow-up question", response: `${id} reply`, events: [] }));
  const view = render(<Logs />);
  fireEvent.click((await view.findByText("First question")).closest("tr")!);
  await view.findByText("second reply");
  const turns = view.container.querySelectorAll(".conversation-turn");
  expect(turns[0].textContent).toContain("First question");
  expect(turns[1].textContent).toContain("Follow-up question");
  expect(view.getByText(/Earlier turns are unavailable/)).toBeTruthy();
  expect(api.getConversations).toHaveBeenCalledWith(expect.objectContaining({ grouped: true }));
  const firstNode = turns[0];
  fireEvent.click(view.getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(api.getConversations).toHaveBeenCalledTimes(2));
  expect(view.container.querySelectorAll(".conversation-turn")[0]).toBe(firstNode);
});

it("combines a delegated operation with routing and pairs its result without hiding rejected proposals", async () => {
  vi.mocked(api.getConversation).mockResolvedValue({ ...detail, events: [
    { ...detail.events[0], tool_id: "outer", payload: { name: "home_request", arguments: { request: "Turn on the light" } } },
    { kind: "routing", timestamp: summary.started_at, tool_id: "outer", payload: { route: "provider", provider: "example", model: "model-one", result: "completed" } },
    { ...detail.events[0], parent_tool_id: "outer" },
    detail.events[1],
    { kind: "error", timestamp: summary.updated_at, tool_id: "outer", payload: { message: "Target ambiguous; not executed.", name: "intent__HassTurnOn" } },
    { ...detail.events[1], tool_id: "outer" },
  ] });
  const view = render(<Logs />);
  fireEvent.click((await view.findByText("Tool exchange; prompt unavailable")).closest("tr")!);
  await view.findByText("home_request");
  expect(view.getByText("Sent to LLM provider: example").closest(".conversation-route-inline")).toBeTruthy();
  expect(view.container.querySelectorAll(".conversation-detail > section > .conversation-timeline > li")).toHaveLength(1);
  expect(view.getByText("Target ambiguous; not executed.")).toBeVisible();
  expect(view.getAllByText("Tool Result")).toHaveLength(2);
});


it("does not label a missing tool receipt as still running after interruption", async () => {
  vi.mocked(api.getConversation).mockResolvedValue({ ...detail, status: "interrupted", response_status: "interrupted", events: [detail.events[0]] });
  const view = render(<Logs />);
  fireEvent.click((await view.findByText("Tool exchange; prompt unavailable")).closest("tr")!);
  await view.findByText("get_state");
  expect(view.container.querySelector(".conversation-event-body > .conversation-outcome")?.textContent).toBe("Outcome unknown");
});

it("shows copyable tool invocations and explains provider purpose, output and checked text", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  vi.mocked(api.getConversation).mockResolvedValue({ ...detail, events: [
    ...detail.events,
    { kind: "routing", tool_id: null, timestamp: summary.started_at, payload: { route: "assist", matched: false, reason: "no_match", input: { text: "turn on the lamp" } } },
    { kind: "routing", tool_id: null, timestamp: summary.started_at, payload: { route: "decision_provider", result: "completed", operation: "target", input: { text: "turn on the lamp" } } },
    { kind: "routing", tool_id: null, timestamp: summary.started_at, payload: { route: "provider", result: "completed", provider: "test-provider", model: "test-model", purpose: "tool_results", text_characters: 25, tool_call_count: 1 } },
    { kind: "routing", tool_id: null, timestamp: summary.started_at, payload: { route: "decision_result", reason: "context_unavailable", executed: false } },
  ] });
  const view = render(<Logs />);
  fireEvent.click((await view.findByText("Tool exchange; prompt unavailable")).closest("tr")!);
  const invocation = await view.findByText(/get_state \{/);
  expect(invocation.closest(".phx-log-details")).toBeTruthy();
  fireEvent.click(invocation.closest(".conversation-evidence")!.querySelector("button")!);
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('get_state {\n  "entity_id": "light.test"\n}'));
  expect(view.getByText("Assist did not recognize a supported command.")).toBeTruthy();
  expect(view.getByText("Purpose: select a device from the available candidates.")).toBeTruthy();
  expect(view.getByText("Sent tool results and conversation context to the model for the next step.")).toBeTruthy();
  expect(view.getByText("Returned text: 25 characters. Proposed tool calls: 1. Tool execution is recorded separately.")).toBeTruthy();
  expect(view.getByText("The conversation could not safely be shared for decision review.")).toBeTruthy();
  expect(view.getAllByText("Text Checked")).toHaveLength(2);
  expect(view.getAllByText("Model request completed")).toHaveLength(2);
  expect(view.getAllByText("turn on the lamp")).toHaveLength(2);
});


it.each(["missing", "rejected", "unavailable"])("copies through HTTP fallback and reports the actual result (%s)", async mode => {
  const writeText = vi.fn().mockRejectedValue(new Error("Not allowed"));
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: mode === "rejected" ? { writeText } : undefined });
  const command = vi.fn(() => mode !== "unavailable");
  document.execCommand = command;
  const view = render(<Logs />);
  fireEvent.click((await view.findByText("Tool exchange; prompt unavailable")).closest("tr")!);
  const invocation = await view.findByText(/get_state \{/);
  const evidence = invocation.closest(".conversation-evidence")!;
  const button = evidence.querySelector("button")!;
  expect(button.getAttribute("aria-label")).toBe("Copy text");
  expect(button.textContent).toBe("");
  expect(button.querySelector("svg[aria-hidden='true']")).toBeTruthy();
  button.focus();
  fireEvent.click(button);
  await waitFor(() => expect(evidence.querySelector("[role='status']")?.textContent).toBe(mode === "unavailable" ? "Copy failed. Select the text to copy it." : "Copied to clipboard"));
  expect(command).toHaveBeenCalledWith("copy");
  expect(document.querySelector("textarea")).toBeNull();
  expect(document.activeElement).toBe(button);
});
