/**
 * Memoized components read their text through module-level helpers, so a language
 * or format change is not a prop change. They must follow it all the same.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

const getAgentCliModels = vi.fn();
vi.mock("../api", () => ({
  agentCliChat: vi.fn(),
  api: { getAgentCliModels: (...args: unknown[]) => getAgentCliModels(...args), getApproval: vi.fn(async () => ({ status: "pending" })), getSettings: vi.fn() },
}));
import { AgentCliWindow } from "../components/AgentCliWindow";
import { PassThroughNotice } from "../components/PassThroughNotice";
import { primeTranslations } from "../i18n";
import { localeClock, setFormatLocale } from "../i18n/format";
import { __resetAgentCliState, patchDurable, setSessionTurns } from "../utils/agentcli_state";
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";
import de from "../../custom_components/phoenix_mcp/catalogs/de.json";

const KEYS = [{ id: "k1", name: "one" }] as never;
const INSTANCES = [{ id: "i1", kind: "claude", name: "A", model: "m" }] as never;
const chatWindow = () => <AgentCliWindow accessKeys={KEYS} instances={INSTANCES} memoryChars={960000} initialAccessKeyId="k1" onClose={() => {}} />;
const transcript = () => document.querySelector(".agentcli-body")!.textContent;

beforeEach(() => {
  __resetAgentCliState();
  getAgentCliModels.mockResolvedValue({ models: ["m"] });
  primeTranslations(en.panel);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  primeTranslations(en.panel);
  setFormatLocale({});
});

describe("Agent Chat transcript", () => {
  it("shows finished items in the new language on the window's next draw", async () => {
    setSessionTurns([{ entries: [
      { kind: "assistant", text: "hello", thinking: "" },
      { kind: "approval", approvalId: "a1", toolName: "x", status: "rejected", reason: "" },
      { kind: "error", code: "auth", message: "m" },
    ], messages: [] }]);
    const view = render(chatWindow());
    await act(async () => {});
    expect(transcript()).toContain(en.panel.agentchat.authorAgent);

    primeTranslations(de.panel, "de");
    view.rerender(chatWindow());
    await act(async () => {});
    expect(transcript()).toContain(de.panel.agentchat.authorAgent);
    expect(transcript()).toContain(de.panel.agentchat.notice.auth);
    expect(transcript()).not.toContain(en.panel.agentchat.notice.auth);
  });

  it("redraws a timestamp when only the time format changes", async () => {
    const ts = Date.UTC(2026, 0, 15, 15, 30);
    setFormatLocale({ locale: { language: "en", time_format: "12" } });
    patchDurable({ showTimestamps: true });
    setSessionTurns([{ entries: [{ kind: "user", text: "hello", ts }], messages: [] }]);
    const view = render(chatWindow());
    await act(async () => {});
    const twelve = localeClock(ts);
    expect(transcript()).toContain(twelve);

    setFormatLocale({ locale: { language: "en", time_format: "24" } });
    expect(localeClock(ts)).not.toBe(twelve);
    view.rerender(chatWindow());
    await act(async () => {});
    expect(transcript()).toContain(localeClock(ts));
  });
});

describe("the pass-through notice", () => {
  it("follows a language change", () => {
    const accessKey = { id: "k1", name: "one" } as never;
    const onUpdate = () => {};
    const view = render(<PassThroughNotice accessKey={accessKey} onUpdate={onUpdate} />);
    expect(screen.getAllByText(en.panel.accessKeys.switchToScoped).length).toBeGreaterThan(0);

    primeTranslations(de.panel, "de");
    view.rerender(<PassThroughNotice accessKey={accessKey} onUpdate={onUpdate} />);
    expect(screen.getAllByText(de.panel.accessKeys.switchToScoped).length).toBeGreaterThan(0);
  });
});
