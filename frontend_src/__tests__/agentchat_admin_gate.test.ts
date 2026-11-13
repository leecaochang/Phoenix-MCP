/**
 * The global Agent Chat opens only for an administrator, even when the user changes
 * between the request and the window module loading, and it harvests cards only on a
 * dashboard page.
 */
import { afterEach, expect, it, vi } from "vitest";

const shown = vi.hoisted(() => ({ count: 0, harvests: 0 }));
vi.mock("../inject/AgentChatWindow", () => ({
  setAgentChatDock: vi.fn(),
  showAgentChat: () => { shown.count += 1; },
  hideAgentChat: vi.fn(),
  isAgentChatVisible: () => false,
  focusAgentChatPopup: () => false,
}));
vi.mock("../utils/card_harvest", () => ({ syncCardCatalog: async () => { shown.harvests += 1; } }));

const FLAG = "__phxAgentChatBootstrapped";
type Ha = HTMLElement & { hass: Record<string, unknown> };
type Bridge = { open: () => Promise<void> };

afterEach(() => {
  ((window as unknown as Record<string, unknown>)[FLAG] as { dispose?: () => void } | undefined)?.dispose?.();
  document.body.replaceChildren();
  delete (window as unknown as Record<string, unknown>)[FLAG];
  delete (window as unknown as Record<string, unknown>).__phxAgentChat;
  shown.count = 0;
  shown.harvests = 0;
  localStorage.clear();
  vi.useRealTimers();
  vi.resetModules();
});

async function boot(path: string, extra: Record<string, unknown> = {}): Promise<{ ha: Ha; bridge: Bridge }> {
  vi.useFakeTimers();
  window.history.replaceState({}, "", path);
  const ha = document.createElement("home-assistant") as Ha;
  ha.hass = { user: { id: "u1", is_admin: true }, ...extra };
  document.body.appendChild(ha);
  await import("../inject/agentchat");
  await vi.advanceTimersByTimeAsync(0);
  return { ha, bridge: (window as unknown as { __phxAgentChat: Bridge }).__phxAgentChat };
}

it("shows the window to an administrator", async () => {
  const { bridge } = await boot("/config");
  await bridge.open();
  expect(shown.count).toBe(1);
});

it("shows nothing once the signed-in user is no longer an administrator", async () => {
  const { ha, bridge } = await boot("/config");
  ha.hass = { user: { id: "u1", is_admin: false } };
  await bridge.open();
  expect(shown.count).toBe(0);
});

it("harvests cards only on a dashboard page", async () => {
  await boot("/lovelace/0", { panels: { lovelace: { component_name: "lovelace" } } });
  await vi.advanceTimersByTimeAsync(3100);
  expect(shown.harvests).toBe(1);
});

it("does not harvest on a configuration page", async () => {
  await boot("/config/entities", { panels: { config: { component_name: "config" } } });
  await vi.advanceTimersByTimeAsync(3100);
  expect(shown.harvests).toBe(0);
});
