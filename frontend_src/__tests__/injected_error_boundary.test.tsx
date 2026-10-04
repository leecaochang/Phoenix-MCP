/** Independent injected roots retain a recoverable view after render failures. */
import { afterEach, expect, it, vi } from "vitest";
import { act, fireEvent, waitFor } from "@testing-library/react";

const state = vi.hoisted(() => ({ broken: true }));
vi.mock("../api", () => ({ api: {
  listAccessKeys: vi.fn().mockResolvedValue([]),
  getAgentCliProviders: vi.fn().mockResolvedValue({ instances: [] }),
  getSettings: vi.fn().mockResolvedValue({ kill_switch: false, agentcli_scrollback_lines: 500 }),
  getEntityTree: vi.fn().mockResolvedValue({}),
  getMesaVocabulary: vi.fn().mockResolvedValue({ canonical_tags: [] }),
}, setHass: vi.fn() }));
vi.mock("../components/AgentCliWindow", () => ({
  AgentCliWindow: () => { if (state.broken) throw new Error("fixture render failure"); return <p>Recovered chat</p>; },
  focusAgentCliPopup: vi.fn().mockReturnValue(false),
}));
vi.mock("../views/MesaView", () => ({
  ProfileEditor: () => { if (state.broken) throw new Error("fixture render failure"); return <p>Recovered editor</p>; },
}));
import { hideAgentChat, showAgentChat } from "../inject/AgentChatWindow";
import { QUICK_ADD_TAG } from "../inject/QuickAdd";

afterEach(async () => {
  await act(async () => { hideAgentChat(); document.body.replaceChildren(); });
  vi.restoreAllMocks();
});

it.each(["chat", "quickadd"])("keeps the %s root mounted with a working retry control", async surface => {
  state.broken = true;
  vi.spyOn(console, "error").mockImplementation(() => {});
  let host!: HTMLElement;
  await act(async () => {
    if (surface === "chat") { showAgentChat("token"); host = document.body.lastElementChild as HTMLElement; }
    else { host = document.createElement(QUICK_ADD_TAG); host.setAttribute("key", "light.test"); document.body.append(host); }
  });
  await waitFor(() => expect(host.shadowRoot?.querySelector('[role="alert"]')).not.toBeNull());
  expect(host.isConnected).toBe(true);
  expect(host.shadowRoot?.querySelector<HTMLElement>('[role="alert"]')?.parentElement?.style.pointerEvents).toBe("auto");
  expect([...host.shadowRoot!.querySelectorAll("button")].some(button => button.textContent === "Close")).toBe(true);
  state.broken = false;
  await act(async () => fireEvent.click(host.shadowRoot!.querySelector("button")!));
  await waitFor(() => expect(host.shadowRoot?.textContent).toContain(surface === "chat" ? "Recovered chat" : "Recovered editor"));
});
