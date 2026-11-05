/** Global Agent Chat waits for Home Assistant authentication to finish. */

import { afterEach, describe, expect, it, vi } from "vitest";

const ACTIVE_FLAG = "__phxAgentChatBootstrapped";

describe("global Agent Chat bootstrap", () => {
  afterEach(() => {
    const controller = (window as unknown as Record<string, unknown>)[ACTIVE_FLAG] as
      | { dispose?: () => void }
      | undefined;
    controller?.dispose?.();
    vi.clearAllTimers();
    vi.useRealTimers();
    document.body.replaceChildren();
    delete (window as unknown as Record<string, unknown>)[ACTIVE_FLAG];
    delete (window as unknown as Record<string, unknown>).__phxAgentChat;
    vi.resetModules();
  });

  it("removes an admin's chat from the browser when a non-admin session opens here", async () => {
    vi.useFakeTimers();
    localStorage.setItem("phx-agentcli-conversation:an-admin", JSON.stringify({ turns: [{ text: "tool output" }] }));
    localStorage.setItem("phx-agentcli:an-admin", JSON.stringify({ accessKeyId: "k" }));
    const homeAssistant = document.createElement("home-assistant") as HTMLElement & { hass: Record<string, unknown> };
    homeAssistant.hass = { user: { id: "a-member", is_admin: false } };
    document.body.appendChild(homeAssistant);

    await import("../inject/agentchat");
    await vi.advanceTimersByTimeAsync(0);

    expect(Object.keys(localStorage).filter((key) => key.startsWith("phx-agentcli"))).toEqual([]);
    expect((window as unknown as Record<string, unknown>).__phxAgentChat).toBeUndefined();
  });

  it("leaves another admin's chat where it is", async () => {
    vi.useFakeTimers();
    localStorage.setItem("phx-agentcli-conversation:an-admin", JSON.stringify({ turns: [{ text: "tool output" }] }));
    const homeAssistant = document.createElement("home-assistant") as HTMLElement & { hass: Record<string, unknown> };
    homeAssistant.hass = { enableShortcuts: true, user: { id: "other-admin", is_admin: true } };
    document.body.appendChild(homeAssistant);

    await import("../inject/agentchat");
    await vi.advanceTimersByTimeAsync(0);

    expect(localStorage.getItem("phx-agentcli-conversation:an-admin")).not.toBeNull();
    localStorage.clear();
  });

  it("installs after hass.user arrives later than the module", async () => {
    vi.useFakeTimers();
    const homeAssistant = document.createElement("home-assistant") as HTMLElement & {
      hass: Record<string, unknown>;
    };
    homeAssistant.hass = {};
    document.body.appendChild(homeAssistant);

    await import("../inject/agentchat");
    expect((window as unknown as Record<string, unknown>).__phxAgentChat).toBeUndefined();

    homeAssistant.hass = {
      enableShortcuts: true,
      user: { is_admin: true },
    };
    await vi.advanceTimersByTimeAsync(250);

    expect((window as unknown as Record<string, unknown>).__phxAgentChat)
      .toMatchObject({
        ready: true,
        isVisible: expect.any(Function),
        restore: expect.any(Function),
      });
  });
});
