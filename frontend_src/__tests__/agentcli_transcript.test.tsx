/**
 * The transcript under a running turn: what redraws on a keystroke and on a
 * token, where the view stays while someone reads, and what happens when
 * another tab of the same browser saves the conversation.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

const agentCliChat = vi.fn();
const getAgentCliModels = vi.fn();
vi.mock("../api", () => ({
  agentCliChat: (...args: unknown[]) => agentCliChat(...args),
  api: { getAgentCliModels: (...args: unknown[]) => getAgentCliModels(...args), getApproval: vi.fn(), getSettings: vi.fn() },
}));
const parses = { count: 0 };
vi.mock("../utils/markdown", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../utils/markdown")>();
  return { ...actual, renderMarkdown: (text: string, options?: never) => { parses.count += 1; return actual.renderMarkdown(text, options); } };
});

import { AgentCliWindow } from "../components/AgentCliWindow";
import { __reloadFromStorage, __resetAgentCliState, getSessionTurns, getSessionUsage, setSessionTurns } from "../utils/agentcli_state";
import type { AccessKeyRecord, AgentCliInstance } from "../types";

const KEYS = [{ id: "k1", name: "one" }] as AccessKeyRecord[];
const INSTANCES: AgentCliInstance[] = [{ id: "i1", kind: "claude", name: "A", model: "m" }];
const CONV_KEY = "phx-agentcli-conversation:test-admin";

let emit: (name: string, payload: unknown) => void = () => {};
let finish: () => void = () => {};

async function openWindow() {
  render(<AgentCliWindow accessKeys={KEYS} instances={INSTANCES} scrollbackLines={5000} initialAccessKeyId="k1" onClose={() => {}} />);
  await act(async () => {});
  return screen.getByRole("textbox") as HTMLTextAreaElement;
}

async function send(box: HTMLTextAreaElement, text = "hello") {
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await act(async () => {});
}

const waitForDraw = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });

beforeEach(() => {
  __resetAgentCliState();
  getAgentCliModels.mockResolvedValue({ models: ["m"] });
  parses.count = 0;
  agentCliChat.mockReset();
  agentCliChat.mockImplementation((_body: unknown, onEvent: typeof emit) => {
    emit = onEvent;
    return new Promise<void>((resolve) => { finish = resolve; });
  });
});
afterEach(() => { vi.restoreAllMocks(); });

describe("what redraws", () => {
  const restored = (count: number) => Array.from({ length: count }, (_, index) => ({
    entries: [{ kind: "assistant", text: `## reply ${index}\n\n${"word ".repeat(20)}`, thinking: "" }],
    messages: [],
    lines: 1,
  }));

  it("parses no reply again on a keystroke", async () => {
    setSessionTurns(restored(50));
    const box = await openWindow();
    parses.count = 0;
    fireEvent.change(box, { target: { value: "h" } });
    expect(parses.count).toBe(0);
  });

  it("parses only the reply being written on a token, and at most once a frame", async () => {
    setSessionTurns(restored(50));
    const box = await openWindow();
    await send(box);
    await act(async () => { emit("assistant_delta", { text: "x" }); });
    await waitForDraw();
    parses.count = 0;
    // Each token in a task of its own, as they arrive off the network; a batch would hide a draw per token.
    for (let token = 0; token < 10; token += 1) await act(async () => { emit("assistant_delta", { text: " y" }); });
    expect(parses.count).toBeLessThan(4);
    await waitForDraw();
    expect(document.querySelector(".agentcli-body")?.textContent).toContain("x y y");
    await act(async () => { finish(); });
  });

  it("folds every token into the turn when the stream ends between frames", async () => {
    const box = await openWindow();
    await send(box);
    await act(async () => {
      emit("assistant_delta", { text: "first" });
      emit("assistant_delta", { text: " second" });
      finish();
    });
    expect(screen.getByText("first second")).toBeTruthy();
  });
});

describe("the view while a turn runs", () => {
  const CONTENT = 5000;
  let body: HTMLElement;

  async function streaming() {
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(function (this: HTMLElement) {
      return this.classList.contains("agentcli-body") ? CONTENT : 0;
    });
    const box = await openWindow();
    await send(box);
    body = document.querySelector(".agentcli-body") as HTMLElement;
    await act(async () => { emit("assistant_delta", { text: "first" }); });
    await waitForDraw();
    return box;
  }

  it("follows new content from the end of the transcript", async () => {
    await streaming();
    expect(body.scrollTop).toBe(CONTENT);
    await act(async () => { finish(); });
  });

  it("stays where a reader scrolled to, through tokens and tool activity and the end of the turn", async () => {
    await streaming();
    body.scrollTop = 100;
    await act(async () => { emit("assistant_delta", { text: " second" }); });
    await waitForDraw();
    await act(async () => { emit("tool_call", { id: "t", name: "get_state", arguments: {} }); });
    await act(async () => { await new Promise((resolve) => requestAnimationFrame(resolve)); });
    expect(body.scrollTop).toBe(100);
    await act(async () => { finish(); });
    expect(body.scrollTop).toBe(100);
  });

  it("follows again once the reader is back at the end", async () => {
    await streaming();
    body.scrollTop = 100;
    await act(async () => { emit("assistant_delta", { text: " second" }); });
    await waitForDraw();
    body.scrollTop = CONTENT - 4;
    Object.defineProperty(body, "scrollHeight", { configurable: true, value: CONTENT + 200 });
    await act(async () => { emit("assistant_delta", { text: " third" }); });
    await waitForDraw();
    expect(body.scrollTop).toBe(CONTENT + 200);
    await act(async () => { finish(); });
  });

  it("does not take a shrinking transcript view for the reader scrolling up", async () => {
    await streaming();
    Object.defineProperty(body, "clientHeight", { configurable: true, value: 400 });
    Object.defineProperty(body, "scrollHeight", { configurable: true, value: CONTENT });
    await act(async () => { emit("assistant_delta", { text: " second" }); });
    await waitForDraw();
    body.scrollTop = CONTENT - 400;
    Object.defineProperty(body, "clientHeight", { configurable: true, value: 300 });
    Object.defineProperty(body, "scrollHeight", { configurable: true, value: CONTENT + 100 });
    await act(async () => { emit("assistant_delta", { text: " third" }); });
    await waitForDraw();
    expect(body.scrollTop).toBe(CONTENT + 100);
    await act(async () => { finish(); });
  });

  it("goes to the end for the reader's own message", async () => {
    const box = await streaming();
    body.scrollTop = 100;
    await act(async () => { finish(); });
    await act(async () => {});
    expect(body.scrollTop).toBe(100);
    await send(box, "next");
    expect(body.scrollTop).toBe(CONTENT);
    await act(async () => { finish(); });
  });
});

describe("two tabs of one browser", () => {
  const savedByOtherTab = (text: string, revision: number) => {
    const value = JSON.stringify({
      logConversationId: "other", turnsRevision: revision, draft: "", usage: { input: 7, output: 3, context: 7, noData: false },
      turns: [{ entries: [{ kind: "user", text }], messages: [{ role: "user", content: text }], lines: 1 }],
    });
    localStorage.setItem(CONV_KEY, value);
    window.dispatchEvent(new StorageEvent("storage", { key: CONV_KEY, newValue: value }));
  };

  it("shows what the other tab saved, without writing it back", async () => {
    await openWindow();
    const writes = vi.spyOn(Storage.prototype, "setItem");
    await act(async () => { savedByOtherTab("from the other tab", 0.5); });
    expect(screen.getByText("from the other tab")).toBeTruthy();
    expect(getSessionUsage().input).toBe(7);
    expect(writes.mock.calls.filter(([key]) => key === CONV_KEY)).toHaveLength(1);
  });

  it("does not bring the older conversation back on its next save", async () => {
    setSessionTurns([{ entries: [{ kind: "user", text: "old" }], messages: [], lines: 1 }]);
    const box = await openWindow();
    await act(async () => { savedByOtherTab("newer", 0.5); });
    await send(box);
    await act(async () => { finish(); });
    __reloadFromStorage();
    const stored = JSON.stringify(getSessionTurns());
    expect(stored).toContain("newer");
    expect(stored).not.toContain("old");
  });

  it("leaves a running turn alone, and its save stands", async () => {
    const box = await openWindow();
    await send(box, "mine");
    await act(async () => { savedByOtherTab("from the other tab", 0.5); });
    expect(screen.queryByText("from the other tab")).toBeNull();
    await act(async () => { finish(); });
    expect(screen.getByText("mine")).toBeTruthy();
    __reloadFromStorage();
    expect(JSON.stringify(getSessionTurns())).toContain("mine");
  });

  it("clears here when the other tab clears", async () => {
    setSessionTurns([{ entries: [{ kind: "user", text: "shared" }], messages: [], lines: 1 }]);
    await openWindow();
    await act(async () => {
      localStorage.removeItem(CONV_KEY);
      window.dispatchEvent(new StorageEvent("storage", { key: CONV_KEY, newValue: null }));
    });
    expect(screen.queryByText("shared")).toBeNull();
  });
});
