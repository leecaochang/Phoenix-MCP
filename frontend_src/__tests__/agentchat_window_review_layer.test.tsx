import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  api: {
    listAccessKeys: vi.fn().mockResolvedValue([]),
    getAgentCliProviders: vi.fn().mockResolvedValue({ instances: [] }),
    getSettings: vi.fn().mockResolvedValue({ kill_switch: false, agentcli_memory_chars: 960000 }),
  },
  setHass: vi.fn(),
}));

vi.mock("../i18n", () => ({
  loadTranslations: vi.fn().mockResolvedValue(undefined),
  resolveLanguage: vi.fn().mockReturnValue("en"),
  syncTranslations: vi.fn().mockResolvedValue(false),
  registerLanguageRoot: vi.fn(),
}));

vi.mock("../components/AgentCliWindow", () => ({
  AgentCliWindow: () => null,
  focusAgentCliPopup: vi.fn().mockReturnValue(false),
}));

import { act, render } from "@testing-library/react";
import { hideAgentChat, setAgentChatDock, showAgentChat } from "../inject/AgentChatWindow";
import { Modal } from "../components/Modal";
import { openDialogs } from "../utils/dialog_presence";
import {
  notifyAgentChatReviewClosed,
  notifyAgentChatReviewOpened,
} from "../utils/agentchat_review";

describe("global Agent Chat approval-review layer", () => {
  afterEach(() => {
    hideAgentChat();
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("detaches the fixed host during review and restores it on the next frame", () => {
    const resumes: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      resumes.push(callback);
      return 1;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});

    showAgentChat("access-key-1");
    const host = document.body.lastElementChild as HTMLElement;
    expect(host).toBeTruthy();
    expect(host.dataset.jsBuild).toBeTruthy();

    notifyAgentChatReviewOpened("approval-1");
    expect(host.isConnected).toBe(false);

    notifyAgentChatReviewClosed("approval-1");
    expect(host.isConnected).toBe(false);
    expect(resumes).toHaveLength(1);

    resumes[0](performance.now());
    expect(host.isConnected).toBe(true);
    expect(document.body.lastElementChild).toBe(host);
  });

  function frames(): FrameRequestCallback[] {
    const resumes: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      resumes.push(callback);
      return resumes.length;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    return resumes;
  }

  const dialog = (key: string) => <Modal key={key} titleId={key}><h2 id={key}>{key}</h2></Modal>;

  it("steps aside while any Phoenix dialog is open and returns after the last closes", () => {
    const resumes = frames();
    showAgentChat("access-key-1");
    const host = document.body.lastElementChild as HTMLElement;
    const view = render(<>{dialog("a")}{dialog("b")}</>);
    expect(host.isConnected).toBe(false);
    act(() => view.rerender(<>{dialog("a")}</>));
    expect(host.isConnected).toBe(false);
    expect(resumes).toHaveLength(0);
    act(() => view.unmount());
    resumes[resumes.length - 1]?.(performance.now());
    expect(host.isConnected).toBe(true);
    expect(openDialogs().size).toBe(0);
  });

  it("stays away while a review is still open after a dialog closes", () => {
    const resumes = frames();
    showAgentChat("access-key-1");
    const host = document.body.lastElementChild as HTMLElement;
    notifyAgentChatReviewOpened("approval-1");
    const view = render(dialog("a"));
    act(() => view.unmount());
    resumes.forEach((resume) => resume(performance.now()));
    expect(host.isConnected).toBe(false);
    notifyAgentChatReviewClosed("approval-1");
    resumes[resumes.length - 1]?.(performance.now());
    expect(host.isConnected).toBe(true);
  });

  it("opens away from a dialog that is already showing", () => {
    const resumes = frames();
    const view = render(dialog("a"));
    showAgentChat("access-key-1");
    expect(document.querySelector("[data-js-build]")).toBeNull();
    act(() => view.unmount());
    resumes[resumes.length - 1]?.(performance.now());
    expect(document.querySelector("[data-js-build]")).not.toBeNull();
  });

  it("stays in place while docked in My Workspace", () => {
    frames();
    const slot = document.createElement("div");
    document.body.appendChild(slot);
    showAgentChat("access-key-1");
    setAgentChatDock({ slot, docked: true, toggle: () => {} });
    const host = document.querySelector("[data-js-build]") as HTMLElement;
    const view = render(dialog("a"));
    expect(host.isConnected).toBe(true);
    act(() => view.unmount());
    setAgentChatDock(null);
  });
});
