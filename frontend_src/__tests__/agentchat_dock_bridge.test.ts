/**
 * The global Agent Chat's bridge carries My Workspace's slot from the panel.
 * The window module loads lazily, so a dock state sent before it loaded must
 * reach it when it does, ahead of the window opening; without a slot nothing
 * loads; with one, the module loads to show Chat there.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const calls: string[] = [];
const setAgentChatDock = vi.fn((dock: { slot: unknown } | null) => { calls.push(`dock:${dock?.slot ? "slot" : "none"}`); });
const showAgentChat = vi.fn(() => { calls.push("show"); });
vi.mock("../inject/AgentChatWindow", () => ({
  setAgentChatDock: (dock: { slot: unknown } | null) => setAgentChatDock(dock),
  showAgentChat: () => showAgentChat(),
  hideAgentChat: vi.fn(),
  isAgentChatVisible: () => false,
  focusAgentChatPopup: () => false,
}));

const FLAG = "__phxAgentChatBootstrapped";
type Bridge = { setDock: (dock: unknown) => void; open: () => Promise<void> };

async function boot(): Promise<Bridge> {
  const homeAssistant = document.createElement("home-assistant") as HTMLElement & { hass: unknown };
  homeAssistant.hass = { user: { id: "u1", is_admin: true } };
  document.body.appendChild(homeAssistant);
  await import("../inject/agentchat");
  return (window as unknown as { __phxAgentChat: Bridge }).__phxAgentChat;
}

describe("the global Agent Chat's dock bridge", () => {
  afterEach(() => {
    ((window as unknown as Record<string, unknown>)[FLAG] as { dispose?: () => void } | undefined)?.dispose?.();
    document.body.replaceChildren();
    delete (window as unknown as Record<string, unknown>)[FLAG];
    delete (window as unknown as Record<string, unknown>).__phxAgentChat;
    calls.length = 0;
    vi.clearAllMocks();
    vi.resetModules();
  });

  it("hands a dock state sent before the window loaded to it when it loads, before showing", async () => {
    const bridge = await boot();
    const toggle = vi.fn();
    bridge.setDock({ slot: null, docked: false, toggle });
    // No slot: nothing needs the window module yet.
    await Promise.resolve();
    expect(setAgentChatDock).not.toHaveBeenCalled();
    await bridge.open();
    expect(calls).toEqual(["dock:none", "show"]);
    expect(setAgentChatDock).toHaveBeenLastCalledWith({ slot: null, docked: false, toggle });
  });

  it("loads the window module when a slot asks for Chat, and passes later states straight through", async () => {
    const bridge = await boot();
    const slot = document.createElement("div");
    bridge.setDock({ slot, docked: true, toggle: () => {} });
    await vi.waitFor(() => expect(setAgentChatDock).toHaveBeenLastCalledWith(expect.objectContaining({ slot })));
    bridge.setDock(null);
    expect(setAgentChatDock).toHaveBeenLastCalledWith(null);
  });
});
