/**
 * Agent Chat open in two tabs of one browser. A wipe in one tab reaches the
 * other through the storage events a real browser sends between tabs; the
 * window event a purge fires stays inside its own page.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

const agentCliChat = vi.fn();
const getAgentCliModels = vi.fn();
vi.mock("../api", () => ({
  agentCliChat: (...args: unknown[]) => agentCliChat(...args),
  api: { getAgentCliModels: (...args: unknown[]) => getAgentCliModels(...args), getApproval: vi.fn(async () => ({ status: "pending" })), getSettings: vi.fn() },
}));
import { AgentCliWindow } from "../components/AgentCliWindow";
import { __resetAgentCliState, patchDurable, getDurable, setSessionDraft, setSessionTurns } from "../utils/agentcli_state";

const KEYS = [{ id: "k1", name: "one" }] as never;
const INSTANCES = [{ id: "i1", kind: "claude", name: "A", model: "m" }] as never;
const CONV = "phx-agentcli-conversation:test-admin";
const chatWindow = () => <AgentCliWindow accessKeys={KEYS} instances={INSTANCES} memoryChars={960000} initialAccessKeyId="k1" onClose={() => {}} />;

/**
 * Run the real purge in a second copy of the module, standing for the other
 * tab. Its storage writes reach this tab as storage events, and its window event
 * is stopped, as a page boundary would.
 */
async function wipeInAnotherTab(): Promise<void> {
  vi.resetModules();
  const otherTab = await import("../utils/agentcli_state");
  const stopPageEvent = (event: Event) => event.stopImmediatePropagation();
  window.addEventListener("phx-agentcli-purged", stopPageEvent, { capture: true });
  const realSet = Storage.prototype.setItem;
  const realRemove = Storage.prototype.removeItem;
  const relay = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function set(this: Storage, key: string, value: string) {
    realSet.call(this, key, value);
    window.dispatchEvent(new StorageEvent("storage", { key, newValue: value }));
  });
  const relayRemove = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(function remove(this: Storage, key: string) {
    const oldValue = localStorage.getItem(key);
    realRemove.call(this, key);
    window.dispatchEvent(new StorageEvent("storage", { key, oldValue, newValue: null }));
  });
  otherTab.purgeAgentCliStorage();
  relay.mockRestore();
  relayRemove.mockRestore();
  window.removeEventListener("phx-agentcli-purged", stopPageEvent, { capture: true });
}

beforeEach(() => {
  __resetAgentCliState();
  localStorage.clear();
  getAgentCliModels.mockResolvedValue({ models: ["m"] });
  agentCliChat.mockReset();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("a wipe in another tab", () => {
  it("is not undone by a turn running in this tab", async () => {
    setSessionTurns([{ entries: [{ kind: "user", text: "secret old", ts: 1 }], messages: [] }]);
    let emit: (name: string, payload: unknown) => void = () => {};
    let finish: () => void = () => {};
    agentCliChat.mockImplementation((_body: unknown, on: typeof emit) => { emit = on; return new Promise<void>((resolve) => { finish = resolve; }); });
    render(chatWindow());
    await act(async () => {});
    const box = screen.getByRole("textbox");
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await act(async () => {});
    await act(async () => { emit("assistant_delta", { text: "tool output secret" }); });

    await act(async () => { await wipeInAnotherTab(); });
    expect(localStorage.getItem(CONV)).toBeNull();

    await act(async () => { emit("messages", { messages: [] }); finish(); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 80)); });
    expect(localStorage.getItem(CONV)).toBeNull();
  });

  it("empties an idle window and closes this page's storage", async () => {
    setSessionTurns([{ entries: [{ kind: "user", text: "secret old", ts: 1 }], messages: [] }]);
    render(chatWindow());
    await act(async () => {});
    expect(screen.queryByText("secret old")).not.toBeNull();

    await act(async () => { await wipeInAnotherTab(); });
    await act(async () => {});
    expect(screen.queryByText("secret old")).toBeNull();

    setSessionDraft("typed after the wipe");
    setSessionTurns([{ entries: [], messages: [] }]);
    patchDurable({ open: true });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(localStorage.getItem(CONV)).toBeNull();
    expect(localStorage.getItem("phx-agentcli:test-admin")).toBeNull();
    expect(getDurable().open).toBe(false);
  });

  it("reaches this tab only through a purge that removed something", async () => {
    await wipeInAnotherTab();
    __resetAgentCliState();
    render(chatWindow());
    await act(async () => {});
    localStorage.clear();
    const heard = vi.fn();
    window.addEventListener("storage", heard);
    await act(async () => { await wipeInAnotherTab(); });
    window.removeEventListener("storage", heard);
    expect(heard).not.toHaveBeenCalled();
  });

  it("leaves a later page free to save", async () => {
    await wipeInAnotherTab();
    __resetAgentCliState();
    setSessionTurns([{ entries: [], messages: [] }]);
    expect(localStorage.getItem(CONV)).not.toBeNull();
  });
});
