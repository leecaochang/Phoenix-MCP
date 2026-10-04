/**
 * The Agent Chat conversation survives a full page reload.
 *
 * Window state (geometry, token/provider/model) and the conversation tier
 * (transcript, unsent message box, usage counters) persist under separate
 * storage keys, so a conversation too large to store can be dropped without
 * taking the window geometry and account selections down with it.
 */
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  getSessionTurns, setSessionTurns, getLogConversationId,
  getSessionDraft, setSessionDraft,
  getSessionUsage, setSessionUsage,
  agentCliOpenPatch, getDurable, patchDurable,
  __resetAgentCliState, __reloadFromStorage,
} from "../utils/agentcli_state";

const CONV_KEY = "phx-agentcli-conversation:test-admin";

beforeEach(() => __resetAgentCliState());
afterEach(() => vi.restoreAllMocks());

describe("conversation survives a reload", () => {
  it("restores transcript, draft, and usage", () => {
    setSessionTurns([{ id: 1 }, { id: 2 }]);
    setSessionDraft("turn off the kitchen lights");
    setSessionUsage({ input: 120, output: 45, context: 120, noData: false });

    __reloadFromStorage();

    expect(getSessionTurns()).toEqual([{ id: 1 }, { id: 2 }]);
    expect(getSessionDraft()).toBe("turn off the kitchen lights");
    expect(getSessionUsage()).toEqual({ input: 120, output: 45, context: 120, noData: false });
  });

  it("clearing the history clears the stored copy too", () => {
    setSessionTurns([{ id: 1 }]);
    setSessionTurns([]);              // what "Clear chat history" / "/clear" does

    __reloadFromStorage();

    expect(getSessionTurns()).toEqual([]);
  });

  it("keeps the conversation separate from the window state", () => {
    patchDurable({ size: { w: 700, h: 500 }, tokenId: "t1" });
    setSessionTurns([{ id: 1 }]);
    // Losing the conversation must not disturb geometry or account selection.
    localStorage.removeItem(CONV_KEY);
    __reloadFromStorage();

    expect(getSessionTurns()).toEqual([]);
    expect(getDurable().size).toEqual({ w: 700, h: 500 });
    expect(getDurable().tokenId).toBe("t1");
  });

  it("strips camera bytes while preserving an unavailable image placeholder", () => {
    const bytes = "iVBORw0KGgo=";
    setSessionTurns([{
      entries: [{ kind: "tool_image", id: "tc1", name: "get_camera_image", mimeType: "image/png",
                  dataUrl: `data:image/png;base64,${bytes}`, alt: "Camera image" }],
      messages: [{ role: "assistant", content: [
        { type: "image", source: { media_type: "image/png", data: bytes } },
        { type: "image_url", image_url: { url: `data:image/png;base64,${bytes}` } },
      ] }],
      lines: 1,
    }]);

    const stored = localStorage.getItem(CONV_KEY) ?? "";
    expect(stored).not.toContain(bytes);
    expect(stored).not.toContain("data:image/png");

    __reloadFromStorage();
    const [turn] = getSessionTurns() as Array<{ entries: Array<Record<string, unknown>>; messages: unknown[] }>;
    expect(turn.entries[0]).toMatchObject({ kind: "tool_image", unavailable: true });
    expect(turn.entries[0].dataUrl).toBeUndefined();
    expect(turn.messages).toEqual([
      { role: "assistant", content: [
        { type: "text", text: "[camera image unavailable after reload]" },
        { type: "text", text: "[camera image unavailable after reload]" },
      ] },
    ]);
  });
});

describe("Agent Chat open geometry", () => {
  it("preserves the last dragged position for a shortcut reopen", () => {
    patchDurable({
      open: false,
      minimized: true,
      pos: { x: 137, y: 246 },
      size: { w: 620, h: 480 },
    });

    patchDurable(agentCliOpenPatch(getDurable()));

    expect(getDurable()).toMatchObject({
      open: true,
      minimized: false,
      pos: { x: 137, y: 246 },
      size: { w: 620, h: 480 },
    });
  });

  it("centers a button-driven summon in the supplied viewport", () => {
    patchDurable({ pos: { x: 137, y: 246 }, size: { w: 620, h: 480 } });

    patchDurable(agentCliOpenPatch(getDurable(), "token-2", { w: 1200, h: 900 }));

    expect(getDurable()).toMatchObject({
      open: true,
      minimized: false,
      pos: { x: 290, y: 210 },
      tokenId: "token-2",
    });
  });
});

describe("degrades safely", () => {
  it("drops the oldest turns when the transcript will not fit", () => {
    // Each turn ~200KB; well past the 2MB cap at 20 of them.
    const big = (id: number) => ({ id, blob: "x".repeat(200_000) });
    setSessionTurns(Array.from({ length: 20 }, (_, i) => big(i)));

    __reloadFromStorage();
    const kept = getSessionTurns() as { id: number }[];

    expect(kept.length).toBeGreaterThan(0);
    expect(kept.length).toBeLessThan(20);
    // The NEWEST turns are the ones kept.
    expect(kept[kept.length - 1].id).toBe(19);
    expect(JSON.stringify(kept).length).toBeLessThanOrEqual(2_000_000);
  });

  it("keeps the draft even when no transcript fits at all", () => {
    setSessionDraft("still here");
    setSessionTurns([{ blob: "x".repeat(3_000_000) }]);   // one turn, over cap alone

    __reloadFromStorage();

    expect(getSessionTurns()).toEqual([]);
    expect(getSessionDraft()).toBe("still here");
  });

  it("starts clean on corrupt stored data instead of throwing", () => {
    localStorage.setItem(CONV_KEY, "{not json");
    __reloadFromStorage();

    expect(getSessionTurns()).toEqual([]);
    expect(getSessionDraft()).toBe("");
  });

  it("ignores a stored payload of the wrong shape", () => {
    localStorage.setItem(CONV_KEY, JSON.stringify({ turns: "nope", draft: 42 }));
    __reloadFromStorage();

    expect(getSessionTurns()).toEqual([]);
    expect(getSessionDraft()).toBe("");
  });

  it("does not throw when storage is unavailable", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });

    expect(() => setSessionDraft("no storage here")).not.toThrow();
    // Still readable in memory for this page load.
    expect(getSessionDraft()).toBe("no storage here");
  });
});

describe("HA user isolation", () => {
  it("keeps each user's transcript and durable choices separate and drops stale writers", async () => {
    const { setAgentCliOwner } = await import("../utils/agentcli_state");
    setAgentCliOwner("first");
    setSessionTurns([{ text: "first private reply" }]);
    patchDurable({ tokenId: "first-token" });
    setAgentCliOwner("second");
    expect(getSessionTurns()).toEqual([]);
    expect(getDurable().tokenId).toBe("");
    setSessionTurns([{ text: "late first reply" }], "first");
    patchDurable({ tokenId: "late-first-token" }, "first");
    expect(getSessionTurns()).toEqual([]);
    expect(getDurable().tokenId).toBe("");
    setSessionTurns([{ text: "second private reply" }]);
    setAgentCliOwner("first");
    expect(getSessionTurns()).toEqual([{ text: "first private reply" }]);
    expect(getDurable().tokenId).toBe("first-token");
    setAgentCliOwner(null);
    expect(getSessionTurns()).toEqual([]);
    setSessionTurns([{ text: "anonymous" }]);
    expect(localStorage.getItem("phx-agentcli-conversation")).toBeNull();
  });

  it("coalesces draft typing and flushes the latest text on pagehide", () => {
    vi.useFakeTimers();
    const writes = vi.spyOn(Storage.prototype, "setItem");
    setSessionDraft("a"); setSessionDraft("ab"); setSessionDraft("abc");
    expect(writes).not.toHaveBeenCalled();
    vi.advanceTimersByTime(300);
    expect(writes).toHaveBeenCalledOnce();
    setSessionDraft("abcd");
    window.dispatchEvent(new Event("pagehide"));
    expect(writes).toHaveBeenCalledTimes(2);
    expect(JSON.parse(localStorage.getItem(CONV_KEY)!)).toMatchObject({ draft: "abcd" });
    vi.useRealTimers();
  });
});


it("keeps explicit log identity through reload and changes it for an empty-history request", () => {
  const first = getLogConversationId(false);
  expect(first).toMatch(/^[0-9a-f-]{36}$/);
  __reloadFromStorage();
  expect(getLogConversationId(true)).toBe(first);
  expect(getLogConversationId(false)).not.toBe(first);
  expect(getLogConversationId(true, "another-user")).toBe("");
});


it("supports local HTTP without randomUUID and never blocks chat when randomness fails", () => {
  vi.spyOn(crypto, "randomUUID").mockImplementation(() => { throw new Error("Secure context required"); });
  const original = getLogConversationId(false);
  expect(original).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  const random = vi.spyOn(crypto, "getRandomValues").mockImplementation(() => { throw new Error("Unavailable"); });
  expect(getLogConversationId(false)).toBe("");
  random.mockRestore();
  expect(getLogConversationId(true)).not.toBe(original);
});
