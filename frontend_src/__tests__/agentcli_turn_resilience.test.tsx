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
  render(<AgentCliWindow accessKeys={KEYS} instances={INSTANCES} scrollbackLines={100} initialAccessKeyId="k" onClose={() => {}} />);
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
    expect(screen.getByText("The connection to Home Assistant dropped before this reply finished.")).toBeTruthy();
    expect(box.value).toBe("restart home assistant");
  });

  it("keeps a reply already under way and leaves the text out of the composer, as the model may have acted on it", async () => {
    serve(() => droppingStream(frame("assistant_delta", { text: "Restarting now" })));
    const box = await sendInWindow("restart home assistant");
    expect(document.querySelector(".agentcli-msg-assistant")?.textContent).toContain("Restarting now");
    expect(document.querySelector(".agentcli-error")).toBeNull();
    expect(screen.getByText("The connection to Home Assistant dropped before this reply finished.")).toBeTruthy();
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
    lines: 2,
  });
  const resumedTurn = (text: string): Turn => ({
    entries: [{ kind: "assistant", text: "ok", thinking: "" }],
    messages: [{ role: "assistant", content: text }],
    lines: 1,
  });

  it("sends everything that fits", () => {
    const turns = [userTurn("a"), userTurn("b")];
    expect(memoryStart(turns)).toBe(0);
    expect(memoryMessages(turns, 100)).toHaveLength(4);
  });

  it("drops the oldest turns once the rest fill the request", () => {
    const turns = [userTurn(big("a", 600_000)), userTurn(big("b", 300_000)), userTurn(big("c", 300_000))];
    expect(memoryStart(turns)).toBe(1);
    const sent = memoryMessages(turns, 100);
    expect(sent).toHaveLength(4);
    expect(JSON.stringify(sent)).not.toContain("aaaa");
  });

  it("never starts memory inside a turn resumed from the round cap", () => {
    const turns = [userTurn(big("a", 500_000)), resumedTurn(big("b", 300_000)), userTurn(big("c", 300_000))];
    expect(memoryStart(turns)).toBe(2);
  });

  it("sends nothing rather than a newest turn that cannot fit", () => {
    expect(memoryMessages([userTurn(big("a", 2_000_000))], 100)).toEqual([]);
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
    render(<AgentCliWindow accessKeys={KEYS} instances={INSTANCES} scrollbackLines={100} initialAccessKeyId="k" onClose={() => {}} />);
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
