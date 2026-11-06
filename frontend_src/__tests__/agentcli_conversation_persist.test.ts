/**
 * The Agent Chat conversation survives a full page reload.
 *
 * Window state (geometry, access key/provider/model) and the conversation tier
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
  __resetAgentCliState, __reloadFromStorage, setAgentCliOwner, purgeAgentCliStorage, onSavedConversationChange,
  announceRunningTurn, watchOtherTabTurn,
} from "../utils/agentcli_state";
import { getReasonDraft, setReasonDraft } from "../utils/approval_reason_draft";

const CONV_KEY = "phx-agentcli-conversation:test-admin";

beforeEach(() => __resetAgentCliState());
afterEach(() => vi.restoreAllMocks());

/** What a page does last: a pending draft is saved. */
const flushAll = () => window.dispatchEvent(new Event("pagehide"));

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
    patchDurable({ size: { w: 700, h: 500 }, accessKeyId: "t1" });
    setSessionTurns([{ id: 1 }]);
    // Losing the conversation must not disturb geometry or account selection.
    localStorage.removeItem(CONV_KEY);
    __reloadFromStorage();

    expect(getSessionTurns()).toEqual([]);
    expect(getDurable().size).toEqual({ w: 700, h: 500 });
    expect(getDurable().accessKeyId).toBe("t1");
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
        { type: "text", text: "[image unavailable after reload]" },
        { type: "text", text: "[image unavailable after reload]" },
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

    patchDurable(agentCliOpenPatch(getDurable(), "access-key-2", { w: 1200, h: 900 }));

    expect(getDurable()).toMatchObject({
      open: true,
      minimized: false,
      pos: { x: 290, y: 210 },
      accessKeyId: "access-key-2",
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
    patchDurable({ accessKeyId: "first-access-key" });
    setAgentCliOwner("second");
    expect(getSessionTurns()).toEqual([]);
    expect(getDurable().accessKeyId).toBe("");
    setSessionTurns([{ text: "late first reply" }], "first");
    patchDurable({ accessKeyId: "late-first-access-key" }, "first");
    expect(getSessionTurns()).toEqual([]);
    expect(getDurable().accessKeyId).toBe("");
    setSessionTurns([{ text: "second private reply" }]);
    setAgentCliOwner("first");
    expect(getSessionTurns()).toEqual([{ text: "first private reply" }]);
    expect(getDurable().accessKeyId).toBe("first-access-key");
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

it("keeps the owner-scoped access key selection when loading the former field", () => {
  __resetAgentCliState();
  setAgentCliOwner("migration-owner");
  localStorage.setItem("phx-agentcli:migration-owner", JSON.stringify({
    tokenId: "existing-key", instanceId: "existing-provider", model: "existing-model",
    size: { w: 700, h: 500 },
  }));
  expect(getDurable()).toMatchObject({ accessKeyId: "existing-key", instanceId: "existing-provider", model: "existing-model", size: { w: 700, h: 500 } });
  patchDurable({ open: true });
  const saved = JSON.parse(localStorage.getItem("phx-agentcli:migration-owner")!);
  expect(saved.accessKeyId).toBe("existing-key");
  expect(saved).not.toHaveProperty("tokenId");
  setAgentCliOwner("different-owner");
  expect(getDurable().accessKeyId).toBe("");
});

describe("what is left in the browser", () => {
  const day = 24 * 60 * 60 * 1000;
  const stored = () => Object.keys(localStorage).filter((key) => key.startsWith("phx-agentcli"));

  it("removes every user's chat and unsent rejection reasons", () => {
    setSessionTurns([{ text: "alarm code 1234" }]);
    patchDurable({ accessKeyId: "t1" });
    setAgentCliOwner("second-admin");
    setSessionTurns([{ text: "second" }]);
    setReasonDraft("a1", "not this one");

    purgeAgentCliStorage();

    expect(stored()).toEqual([]);
    expect(getReasonDraft("a1")).toBe("");
  });

  it("stays removed when this page saves afterwards, in this bundle or another", () => {
    setSessionDraft("typing");
    setSessionTurns([{ text: "before the wipe" }]);
    purgeAgentCliStorage();

    setSessionTurns([{ text: "a turn folded at pagehide" }], "test-admin");
    setSessionUsage({ input: 1, output: 1, context: 1, noData: false }, "test-admin");
    patchDurable({ open: true }, "test-admin");
    flushAll();
    expect(stored()).toEqual([]);
    expect(localStorage.getItem("null")).toBeNull();

    setAgentCliOwner("next-user");
    setSessionTurns([{ text: "works again for the next identity" }]);
    expect(stored()).toHaveLength(1);
    purgeAgentCliStorage();
    setAgentCliOwner("test-admin");
  });

  it("closes the other bundles in the page", () => {
    setSessionTurns([{ text: "before" }]);
    window.dispatchEvent(new Event("phx-agentcli-purged"));
    setSessionTurns([{ text: "after" }], "test-admin");
    expect(localStorage.getItem(CONV_KEY)).toContain("before");
    expect(localStorage.getItem(CONV_KEY)).not.toContain("after");
  });

  it("deletes a conversation left unused past its age the next time a user signs in", () => {
    vi.useFakeTimers({ now: 0 });
    setSessionTurns([{ text: "old" }]);
    vi.setSystemTime(29 * day);
    setAgentCliOwner(null);
    setAgentCliOwner("test-admin");
    expect(getSessionTurns()).toEqual([{ text: "old" }]);
    setSessionUsage({ input: 1, output: 1, context: 1, noData: false });
    vi.setSystemTime(59 * day);
    setAgentCliOwner(null);
    setAgentCliOwner("test-admin");
    expect(getSessionTurns()).toEqual([{ text: "old" }]);
    vi.setSystemTime(90 * day);
    setAgentCliOwner(null);
    setAgentCliOwner("test-admin");
    expect(getSessionTurns()).toEqual([]);
    expect(localStorage.getItem(CONV_KEY)).toBeNull();
    vi.useRealTimers();
  });

  it("gives a conversation saved before ages were recorded the full age from today", () => {
    vi.useFakeTimers({ now: 1000 * day });
    localStorage.setItem(CONV_KEY, JSON.stringify({ turns: [{ text: "kept" }] }));
    setAgentCliOwner(null);
    setAgentCliOwner("test-admin");
    expect(getSessionTurns()).toEqual([{ text: "kept" }]);
    vi.setSystemTime(1031 * day);
    setAgentCliOwner(null);
    setAgentCliOwner("test-admin");
    expect(getSessionTurns()).toEqual([]);
    vi.useRealTimers();
  });
});

describe("another tab saving", () => {
  const otherTabSaves = (saved: Record<string, unknown>) => {
    const value = JSON.stringify(saved);
    localStorage.setItem(CONV_KEY, value);
    window.dispatchEvent(new StorageEvent("storage", { key: CONV_KEY, newValue: value }));
  };

  it("replaces this tab's cached copy", () => {
    setSessionTurns([{ text: "mine" }]);
    otherTabSaves({ turns: [{ text: "theirs" }], turnsRevision: 0.5, logConversationId: "theirs" });
    expect(getSessionTurns()).toEqual([{ text: "theirs" }]);
    expect(getLogConversationId(true)).toBe("theirs");
  });

  it("tells a listener only when the turns moved, not for a draft", () => {
    const listener = vi.fn();
    const stop = onSavedConversationChange(listener);
    otherTabSaves({ turns: [{ text: "theirs" }], turnsRevision: 0.5 });
    expect(listener).toHaveBeenCalledOnce();
    otherTabSaves({ turns: [{ text: "theirs" }], turnsRevision: 0.5, draft: "typing there" });
    expect(listener).toHaveBeenCalledOnce();
    stop();
  });

  it("ignores other keys and other users", () => {
    setSessionTurns([{ text: "mine" }]);
    window.dispatchEvent(new StorageEvent("storage", { key: "phx-agentcli-conversation:someone-else", newValue: "{}" }));
    window.dispatchEvent(new StorageEvent("storage", { key: "phx-theme", newValue: "dark" }));
    localStorage.removeItem(CONV_KEY);
    expect(getSessionTurns()).toEqual([{ text: "mine" }]);
  });
});

describe("a turn running in another tab", () => {
  const TURN_KEY = "phx-agentcli-turn:test-admin";
  const otherTab = (at = Date.now()) => {
    localStorage.setItem(TURN_KEY, JSON.stringify({ tab: "other", at }));
    window.dispatchEvent(new StorageEvent("storage", { key: TURN_KEY }));
  };

  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); localStorage.removeItem(TURN_KEY); });

  it("is reported when it starts and when it ends", () => {
    const seen: boolean[] = [];
    const stop = watchOtherTabTurn((running) => seen.push(running));
    otherTab();
    localStorage.removeItem(TURN_KEY);
    window.dispatchEvent(new StorageEvent("storage", { key: TURN_KEY }));
    expect(seen).toEqual([false, true, false]);
    stop();
  });

  it("is seen by a tab opened while it runs", () => {
    localStorage.setItem(TURN_KEY, JSON.stringify({ tab: "other", at: Date.now() }));
    const seen: boolean[] = [];
    watchOtherTabTurn((running) => seen.push(running))();
    expect(seen).toEqual([true]);
  });

  it("stops counting once its heartbeat goes stale, as when that tab dies", () => {
    const seen: boolean[] = [];
    const stop = watchOtherTabTurn((running) => seen.push(running));
    otherTab();
    vi.advanceTimersByTime(29_000);
    expect(seen[seen.length - 1]).toBe(true);
    vi.advanceTimersByTime(1_000);
    expect(seen[seen.length - 1]).toBe(false);
    stop();
  });

  it("stays reported while its heartbeat renews", () => {
    const seen: boolean[] = [];
    const stop = watchOtherTabTurn((running) => seen.push(running));
    otherTab();
    vi.advanceTimersByTime(20_000);
    otherTab();
    vi.advanceTimersByTime(20_000);
    expect(seen[seen.length - 1]).toBe(true);
    stop();
  });

  it("is not reported for this tab's own turn, which renews until it ends", () => {
    const seen: boolean[] = [];
    const watching = watchOtherTabTurn((running) => seen.push(running));
    const end = announceRunningTurn();
    const first = localStorage.getItem(TURN_KEY);
    window.dispatchEvent(new StorageEvent("storage", { key: TURN_KEY }));
    expect(seen).toEqual([false, false]);
    vi.advanceTimersByTime(10_000);
    expect(localStorage.getItem(TURN_KEY)).not.toBe(first);
    end();
    expect(localStorage.getItem(TURN_KEY)).toBeNull();
    watching();
  });

  it("ends with the page", () => {
    announceRunningTurn();
    window.dispatchEvent(new Event("pagehide"));
    expect(localStorage.getItem(TURN_KEY)).toBeNull();
  });

  it("leaves another tab's record alone when this tab's turn ends", () => {
    const end = announceRunningTurn();
    localStorage.setItem(TURN_KEY, JSON.stringify({ tab: "other", at: Date.now() }));
    end();
    expect(localStorage.getItem(TURN_KEY)).not.toBeNull();
  });

  it("belongs to one user and goes with a purge", () => {
    expect(announceRunningTurn("someone-else")).toBeTypeOf("function");
    expect(localStorage.getItem(TURN_KEY)).toBeNull();
    announceRunningTurn();
    purgeAgentCliStorage();
    expect(localStorage.getItem(TURN_KEY)).toBeNull();
  });
});
