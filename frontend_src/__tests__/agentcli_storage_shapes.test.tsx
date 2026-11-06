/**
 * Browser storage can hold anything: an older build's data or another script's.
 * The window opens on whatever it finds, because the boundary around a window in
 * the panel offers no way to close it, and the bad data would stay.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";

const getAgentCliModels = vi.fn();
vi.mock("../api", () => ({
  agentCliChat: vi.fn(),
  api: { getAgentCliModels: (...args: unknown[]) => getAgentCliModels(...args), getApproval: vi.fn(async () => ({ status: "pending" })), getSettings: vi.fn() },
}));
import { AgentCliWindow } from "../components/AgentCliWindow";
import { __reloadFromStorage, __resetAgentCliState, getDurable, patchDurable } from "../utils/agentcli_state";

const KEYS = [{ id: "k1", name: "one" }] as never;
const INSTANCES = [{ id: "i1", kind: "claude", name: "A", model: "m" }] as never;
const CONV = "phx-agentcli-conversation:test-admin";
const DURABLE = "phx-agentcli:test-admin";

async function opens(): Promise<void> {
  const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
  render(<AgentCliWindow accessKeys={KEYS} instances={INSTANCES} scrollbackLines={500} initialAccessKeyId="k1" onClose={() => {}} />);
  await act(async () => {});
  quiet.mockRestore();
  expect(document.querySelector(".agentcli-window")).not.toBeNull();
}

beforeEach(() => { __resetAgentCliState(); localStorage.clear(); getAgentCliModels.mockResolvedValue({ models: ["m"] }); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("malformed saved turns", () => {
  const entry = (value: unknown) => [{ entries: [value], messages: [], lines: 1 }];
  const shapes: [string, unknown][] = [
    ["a null turn", [null, 5, "x"]],
    ["entries not a list", [{ entries: "x", messages: [], lines: 1 }]],
    ["a null entry", [{ entries: [null, 5], messages: [], lines: 1 }]],
    ["user text a number", entry({ kind: "user", text: 5 })],
    ["assistant without thinking", entry({ kind: "assistant", text: "a" })],
    ["assistant thinking a number", entry({ kind: "assistant", text: "a", thinking: 5 })],
    ["user images a string", entry({ kind: "user", text: "a", images: "x" })],
    ["user images of the wrong types", entry({ kind: "user", text: "a", images: [null, 5, { name: 5 }] })],
    ["a time that is a string", entry({ kind: "user", text: "a", ts: "nope" })],
    ["approval without a status", entry({ kind: "approval", approvalId: "a", toolName: "t" })],
    ["approval status a number", entry({ kind: "approval", approvalId: "a", toolName: "t", status: 5 })],
    ["approval reason an object", entry({ kind: "approval", approvalId: "a", toolName: "t", status: "rejected", reason: { a: 1 } })],
    ["notice without a message", entry({ kind: "notice" })],
    ["error with odd fields", entry({ kind: "error", code: "auth", message: "m", messageParams: 5, rateLimit: "x", providerDetail: 5 })],
    ["error message an object", entry({ kind: "error", code: 5, message: { a: 1 } })],
    ["tool call without a name", entry({ kind: "tool_call" })],
    ["tool result summary an object", entry({ kind: "tool_result", name: "n", summary: { a: 1 }, isError: 1 })],
    ["tool image without fields", entry({ kind: "tool_image" })],
    ["progress an object", entry({ kind: "progress", message: { a: 1 } })],
    ["messages an object", [{ entries: [], messages: { a: 1 }, lines: "x" }]],
    ["an unknown kind", entry({ kind: "zzz" })],
  ];
  for (const verbose of [false, true]) {
    it.each(shapes)(`%s, verbose ${verbose}`, async (_name, turns) => {
      localStorage.setItem(CONV, JSON.stringify({ logConversationId: "x", turns, draft: "", usage: {} }));
      patchDurable({ options: { ...getDurable().options, verbose } });
      __reloadFromStorage();
      await opens();
    });
  }
});

describe("a malformed saved conversation", () => {
  it.each<[string, unknown]>([
    ["usage a string", { turns: [], usage: "abc" }],
    ["usage null", { turns: [], usage: null }],
    ["usage of the wrong types", { turns: [], usage: { input: "x", output: {}, context: null } }],
    ["a list", [1, 2]],
    ["a number", 5],
    ["a string", "x"],
    ["null", null],
    ["turns an object", { turns: { a: 1 } }],
    ["draft a number", { draft: 5 }],
  ])("%s", async (_name, value) => {
    localStorage.setItem(CONV, JSON.stringify(value));
    __reloadFromStorage();
    await opens();
  });
});

describe("malformed window state", () => {
  it.each<[string, unknown]>([
    ["position null", { pos: null }],
    ["position a string", { pos: "x" }],
    ["size a string", { size: "x", open: true }],
    ["size of the wrong types", { size: { w: "a", h: null } }],
    ["options a number", { options: 5 }],
    ["options a string", { options: "x" }],
    ["access key a number", { accessKeyId: 5 }],
    ["provider an object", { instanceId: { a: 1 } }],
    ["model an object", { model: { a: 1 } }],
    ["prefill a number", { prefill: 5 }],
    ["a list", [1]],
    ["a number", 5],
    ["text size a string", { textSizePt: "x" }],
    ["options of the wrong types", { options: { effort: 5, temperature: 7, thinking: "x" } }],
  ])("%s", async (_name, value) => {
    localStorage.setItem(DURABLE, JSON.stringify(value));
    await opens();
  });
});
