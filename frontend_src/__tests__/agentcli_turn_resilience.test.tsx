/**
 * How a turn ends when it does not end normally, through the real api module:
 * a stream that stops after its headers is Home Assistant or the page going
 * away, not the provider failing; a request the server refuses gives the typed
 * text back. Also what the next request carries once the conversation outgrows
 * what the server accepts.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { AgentCliWindow, memoryMessages, memoryStart, type Turn } from "../components/AgentCliWindow";
import { agentCliChat, setHass } from "../api";
import { ConnectionLostError, homeAssistantReachable, __resetConnectionState } from "../utils/connection";
import { __resetAgentCliState, setSessionTurns } from "../utils/agentcli_state";
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";

const encoder = new TextEncoder();
const frame = (name: string, data: unknown) => encoder.encode(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);

/** A response whose body fails after the given frames, as a dropped connection does. */
function droppingStream(...frames: Uint8Array[]): Response {
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of frames) controller.enqueue(chunk);
      setTimeout(() => controller.error(new TypeError("network error")), 5);
    },
  }), { status: 200, headers: { "content-type": "text/event-stream" } });
}

const INSTANCES = [{ id: "i", kind: "claude", name: "A", model: "m" }] as never;
const KEYS = [{ id: "k", name: "k" }] as never;

function serve(chat: () => Response) {
  setHass({
    user: { id: "test-admin" },
    fetchWithAuth: async (url: string) => (String(url).includes("/models")
      ? new Response(JSON.stringify({ models: ["m"] }), { status: 200 })
      : chat()),
  });
}

async function sendInWindow(text: string): Promise<HTMLTextAreaElement> {
  render(<AgentCliWindow accessKeys={KEYS} instances={INSTANCES} memoryChars={960000} initialAccessKeyId="k" onClose={() => {}} />);
  await act(async () => {});
  const box = screen.getByRole("textbox") as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 40)); });
  return box;
}

beforeEach(() => { __resetConnectionState(); __resetAgentCliState(); });
afterEach(() => { vi.restoreAllMocks(); });

describe("a stream dropped mid-turn", () => {
  it("reports Home Assistant as away through the api layer", async () => {
    setHass({ user: { id: "test-admin" }, fetchWithAuth: async () => droppingStream(frame("ready", {})) });
    await expect(agentCliChat({ access_key_id: "k", instance_id: "i", messages: [] }, () => {}))
      .rejects.toBeInstanceOf(ConnectionLostError);
    expect(homeAssistantReachable()).toBe(false);
  });

  it("leaves the connection alone when the turn itself was stopped", async () => {
    const stop = new AbortController();
    setHass({
      user: { id: "test-admin" },
      fetchWithAuth: async () => new Response(new ReadableStream({
        start(controller) { stop.signal.addEventListener("abort", () => controller.error(new DOMException("Aborted", "AbortError"))); },
      }), { status: 200 }),
    });
    const outcome = expect(agentCliChat({ access_key_id: "k", instance_id: "i", messages: [] }, () => {}, stop.signal))
      .rejects.not.toBeInstanceOf(ConnectionLostError);
    await new Promise((resolve) => setTimeout(resolve, 0));
    stop.abort();
    await outcome;
    expect(homeAssistantReachable()).toBe(true);
  });

  it("does not take a failure in the event handler for a lost connection", async () => {
    setHass({ user: { id: "test-admin" }, fetchWithAuth: async () => droppingStream(frame("ready", {})) });
    await expect(agentCliChat({ access_key_id: "k", instance_id: "i", messages: [] }, () => { throw new RangeError("handler"); }))
      .rejects.toBeInstanceOf(RangeError);
    expect(homeAssistantReachable()).toBe(true);
  });

  it("says the connection went, keeps the provider out of it, and gives the text back", async () => {
    serve(() => droppingStream(frame("ready", {})));
    const box = await sendInWindow("restart home assistant");
    expect(document.querySelector(".agentcli-error")).toBeNull();
    expect(screen.getByText("The connection to Home Assistant dropped before this reply finished.", { selector: ".agentcli-notice" })).toBeTruthy();
    expect(box.value).toBe("restart home assistant");
  });

  it("keeps a reply already under way and leaves the text out of the composer, as the model may have acted on it", async () => {
    serve(() => droppingStream(frame("assistant_delta", { text: "Restarting now" })));
    const box = await sendInWindow("restart home assistant");
    expect(document.querySelector(".agentcli-msg-assistant")?.textContent).toContain("Restarting now");
    expect(document.querySelector(".agentcli-error")).toBeNull();
    expect(screen.getByText("The connection to Home Assistant dropped before this reply finished.", { selector: ".agentcli-notice" })).toBeTruthy();
    expect(box.value).toBe("");
  });
});

describe("a request the server refuses", () => {
  const refusal = (status: number, error: string, message: string) => () =>
    new Response(JSON.stringify({ error, message }), { status, headers: { "content-type": "application/json" } });

  it("says what to do about one too large and gives the text back", async () => {
    serve(refusal(413, "request_too_large", "Request body too large."));
    const box = await sendInWindow("a very long paste");
    expect(document.querySelector(".agentcli-error")?.textContent).toContain("/clear");
    expect(box.value).toBe("a very long paste");
  });

  it("shows any other refusal as it came and gives the text back", async () => {
    serve(refusal(503, "service_unavailable", "Phoenix MCP is disabled (kill switch)."));
    const box = await sendInWindow("turn on the lights");
    expect(document.querySelector(".agentcli-error")?.textContent).toContain("kill switch");
    expect(box.value).toBe("turn on the lights");
  });
});

describe("model memory within the request limit", () => {
  const big = (fill: string, size: number) => fill.repeat(size);
  const userTurn = (text: string): Turn => ({
    entries: [{ kind: "user", text: "ask" }, { kind: "assistant", text: "ok", thinking: "" }],
    messages: [{ role: "user", content: text }, { role: "assistant", content: "ok" }],
  });
  const resumedTurn = (text: string): Turn => ({
    entries: [{ kind: "assistant", text: "ok", thinking: "" }],
    messages: [{ role: "assistant", content: text }],
  });

  it("sends everything that fits", () => {
    const turns = [userTurn("a"), userTurn("b")];
    expect(memoryStart(turns)).toBe(0);
    expect(memoryMessages(turns)).toHaveLength(4);
  });

  it("drops the oldest turns once the rest fill the request", () => {
    const turns = [userTurn(big("a", 600_000)), userTurn(big("b", 300_000)), userTurn(big("c", 300_000))];
    expect(memoryStart(turns)).toBe(1);
    const sent = memoryMessages(turns);
    expect(sent).toHaveLength(4);
    expect(JSON.stringify(sent)).not.toContain("aaaa");
  });

  it("never starts memory inside a turn resumed from the round cap", () => {
    const turns = [userTurn(big("a", 500_000)), resumedTurn(big("b", 300_000)), userTurn(big("c", 300_000))];
    expect(memoryStart(turns)).toBe(2);
  });

  it("sends nothing rather than a newest turn that cannot fit", () => {
    expect(memoryMessages([userTurn(big("a", 2_000_000))])).toEqual([]);
  });

  it("counts non-ASCII text as the escapes the server measures", () => {
    expect(memoryStart([userTurn(big("a", 200_000)), userTurn(big("家", 150_000))])).toBe(1);
    expect(memoryStart([userTurn(big("a", 200_000)), userTurn(big("a", 150_000))])).toBe(0);
  });

  it("leaves image bytes to their own budget", () => {
    const withImage: Turn = {
      ...userTurn("look"),
      messages: [{ role: "user", content: [{ type: "image", source: { data: big("a", 3_000_000) } }] }],
    };
    expect(memoryStart([withImage])).toBe(0);
  });

  it("tells the reader once, and sends what fits", async () => {
    const seen: { messages: unknown[] }[] = [];
    setHass({
      user: { id: "test-admin" },
      fetchWithAuth: async (url: string, init: RequestInit) => {
        if (String(url).includes("/models")) return new Response(JSON.stringify({ models: ["m"] }), { status: 200 });
        seen.push(JSON.parse(init.body as string));
        return new Response(frame("done", {}), { status: 200 });
      },
    });
    setSessionTurns([userTurn(big("a", 600_000)), userTurn(big("b", 500_000))]);
    render(<AgentCliWindow accessKeys={KEYS} instances={INSTANCES} memoryChars={960000} initialAccessKeyId="k" onClose={() => {}} />);
    await act(async () => {});
    const box = screen.getByRole("textbox");
    const send = async (text: string) => {
      fireEvent.change(box, { target: { value: text } });
      fireEvent.keyDown(box, { key: "Enter" });
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
    };
    await send("next");
    expect(JSON.stringify(seen[0].messages)).not.toContain("aaaa");
    expect(JSON.stringify(seen[0].messages)).toContain("bbbb");
    expect(document.querySelectorAll(".agentcli-notice")).toHaveLength(1);
    await send("and again");
    expect(document.querySelectorAll(".agentcli-notice")).toHaveLength(1);
  });
});

describe("a message long enough to push out earlier turns", () => {
  const big = (fill: string, size: number) => fill.repeat(size);
  const userTurn = (text: string): Turn => ({
    entries: [{ kind: "user", text: "ask" }, { kind: "assistant", text: "ok", thinking: "" }],
    messages: [{ role: "user", content: text }, { role: "assistant", content: "ok" }],
  });
  const seen: { messages: unknown[] }[] = [];

  async function open() {
    seen.length = 0;
    setHass({
      user: { id: "test-admin" },
      fetchWithAuth: async (url: string, init: RequestInit) => {
        if (String(url).includes("/models")) return new Response(JSON.stringify({ models: ["m"] }), { status: 200 });
        seen.push(JSON.parse(init.body as string));
        return new Response(frame("done", {}), { status: 200 });
      },
    });
    setSessionTurns([userTurn(big("a", 3000)), userTurn(big("b", 3000))]);
    render(<AgentCliWindow accessKeys={KEYS} instances={INSTANCES} memoryChars={10000} initialAccessKeyId="k" onClose={() => {}} />);
    await act(async () => {});
    const box = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: big("p", 6000) } });
    fireEvent.keyDown(box, { key: "Enter" });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
    return box;
  }
  const click = async (name: string) => {
    fireEvent.click(screen.getByRole("button", { name }));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
  };

  it("asks before sending, and sends nothing yet", async () => {
    const box = await open();
    expect(seen).toHaveLength(0);
    expect(screen.getByText(/won't see 1 earlier message/)).toBeInTheDocument();
    expect(box.value).toHaveLength(6000);
  });

  it("Send Anyway sends with what still fits", async () => {
    await open();
    await click("Send Anyway");
    expect(seen).toHaveLength(1);
    expect(JSON.stringify(seen[0].messages)).not.toContain("aaaa");
    expect(JSON.stringify(seen[0].messages)).toContain("bbbb");
  });

  it("Send in New Chat clears the conversation and sends no memory", async () => {
    await open();
    await click("Send in New Chat");
    expect(seen).toHaveLength(1);
    expect(seen[0].messages).toEqual([]);
    expect(screen.queryByText("ask")).not.toBeInTheDocument();
  });

  it("Edit Message keeps the text and closes the warning", async () => {
    const box = await open();
    await click("Edit Message");
    expect(seen).toHaveLength(0);
    expect(screen.queryByText(/won't see/)).not.toBeInTheDocument();
    expect(box.value).toHaveLength(6000);
  });
});

describe("what a screen reader hears when a turn ends early", () => {
  // The turn's own announcement; each message's copy button has a status of its own.
  const announced = () => [...document.querySelectorAll('.sr-only[role="status"]:not(.phx-copy-status)')].map((el) => el.textContent).join("|");

  it("says the connection dropped", async () => {
    serve(() => droppingStream(frame("ready", {})));
    await sendInWindow("x");
    expect(announced()).toBe("The connection to Home Assistant dropped before this reply finished.");
  });

  it("says the connection dropped, not the partial reply it overtook", async () => {
    serve(() => droppingStream(frame("assistant_delta", { text: "half a reply" })));
    await sendInWindow("x");
    expect(screen.getByText("The connection to Home Assistant dropped before this reply finished.", { selector: ".agentcli-notice" })).toBeTruthy();
    expect(announced()).toBe("The connection to Home Assistant dropped before this reply finished.");
  });

  it("says a message was too large", async () => {
    serve(() => new Response(JSON.stringify({ error: "request_too_large", message: "Request body too large." }), { status: 413, headers: { "content-type": "application/json" } }));
    await sendInWindow("x");
    expect(announced()).toContain("/clear");
  });

  it("says why Phoenix refused a request", async () => {
    serve(() => new Response(JSON.stringify({ error: "forbidden", message: "Kill switch on." }), { status: 403, headers: { "content-type": "application/json" } }));
    await sendInWindow("x");
    expect(announced()).toContain("Kill switch on.");
  });

  it("says a request failed while Home Assistant still answers", async () => {
    serve(() => ({ ok: true, body: { getReader: () => { throw new Error("no reader"); } } }) as unknown as Response);
    await sendInWindow("x");
    expect(document.querySelector(".agentcli-error")?.textContent).toContain("The provider is temporarily unavailable.");
    expect(announced()).toBe("Error: The provider is temporarily unavailable.");
  });

  it("says Home Assistant is not answering when the request never left", async () => {
    setHass({
      user: { id: "test-admin" },
      fetchWithAuth: async (url: string) => {
        if (String(url).includes("/models")) return new Response(JSON.stringify({ models: ["m"] }), { status: 200 });
        throw new TypeError("offline");
      },
    });
    await sendInWindow("x");
    expect(announced()).toBe(en.panel.shell.offlineBody);
  });

  it("says a streamed error, not the earlier text of that turn", async () => {
    serve(() => new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(frame("assistant_delta", { text: "first round" }));
        controller.enqueue(frame("error", { code: "network", message: "boom", retryable: false }));
        controller.close();
      },
    }), { status: 200, headers: { "content-type": "text/event-stream" } }));
    await sendInWindow("x");
    expect(announced()).toBe("Error: The provider is temporarily unavailable.");
  });

  describe("on a notice", () => {
    const stream = (...frames: Uint8Array[]) => () => new Response(new ReadableStream({
      start(controller) {
        for (const chunk of frames) controller.enqueue(chunk);
        controller.close();
      },
    }), { status: 200, headers: { "content-type": "text/event-stream" } });
    const emptyTurn = frame("notice", { code: "empty_turn", message: "english empty" });
    const maxTokens = frame("notice", { code: "max_tokens_stop", message: "english limit" });
    const done = frame("done", {});

    it("says a turn ended with no reply, in the reader's language", async () => {
      serve(stream(emptyTurn, done));
      await sendInWindow("x");
      expect(announced()).toBe(en.panel.agentchat.notice.empty_turn);
    });

    it("says a turn hit its limit with no reply", async () => {
      serve(stream(maxTokens, done));
      await sendInWindow("x");
      expect(announced()).toBe(en.panel.agentchat.notice.max_tokens_stop);
    });

    it("says the reply first, then that it was cut off", async () => {
      serve(stream(frame("assistant_delta", { text: "half a **reply**" }), maxTokens, done));
      await sendInWindow("x");
      expect(announced()).toBe(`half a reply ${en.panel.agentchat.notice.max_tokens_stop}`);
    });

    it("keeps the notice whole after a long reply", async () => {
      serve(stream(frame("assistant_delta", { text: "word ".repeat(200) }), maxTokens, done));
      await sendInWindow("x");
      expect(announced().endsWith(en.panel.agentchat.notice.max_tokens_stop)).toBe(true);
    });

    it("keeps a notice that does not end the turn out of it", async () => {
      serve(stream(frame("notice", { code: "bad_tool_call_retry", message: "retrying" }), frame("assistant_delta", { text: "hello" }), done));
      await sendInWindow("x");
      expect(announced()).toBe("hello");
    });

    it("says a streamed error instead of the notice before it", async () => {
      serve(stream(maxTokens, frame("error", { code: "network", message: "boom", retryable: false })));
      await sendInWindow("x");
      expect(announced()).toBe("Error: The provider is temporarily unavailable.");
    });
  });

  it("says a repeated failure again", async () => {
    serve(() => new Response(JSON.stringify({ error: "request_too_large", message: "Request body too large." }), { status: 413, headers: { "content-type": "application/json" } }));
    const box = await sendInWindow("x");
    const first = announced();
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((frameCallback) => frames.push(frameCallback));
    fireEvent.change(box, { target: { value: "again" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 40)); });
    expect(announced()).toBe("");
    act(() => { for (const frameCallback of frames) frameCallback(0); });
    expect(announced()).toBe(first);
  });

  it("says the model no longer remembers the oldest messages", async () => {
    const big = "x".repeat(1_100_000);
    setSessionTurns([{ entries: [{ kind: "user", text: "old" }], messages: [{ role: "user", content: big }] }, { entries: [{ kind: "user", text: "newer" }], messages: [{ role: "user", content: "b" }] }]);
    let release: () => void = () => {};
    serve(() => new Response(new ReadableStream({ start(controller) { release = () => controller.close(); } }), { status: 200, headers: { "content-type": "text/event-stream" } }));
    await sendInWindow("x");
    expect(announced()).toContain("no longer sees");
    await act(async () => { release(); });
  });
});
