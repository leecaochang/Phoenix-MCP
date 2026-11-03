/**
 * Welcome to the Guide (user, 2026-11-02/03): a demo of what the Guide does,
 * with nothing to finish. The Guide opens on it once and then on the reader's
 * page, Back always reaches it, and it stays on screen while tips react:
 * selecting in a window invites the reader to read about it, the page's own
 * search suggests a word, and a tried tip shows a check. A phone leaves out
 * Quick Help's tip, the wizard never shows it, Customize Appearance sets the
 * mode and color theme, Dismiss sits at both ends, Contents and Search are
 * plain links, and saved state reads back safely, earlier builds' included.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { existsSync, readFileSync } from "fs";
import { resolve } from "path";
import { GUIDE_FORMAT, parseTopic } from "../guide/compile";
import { GuideTargetContext, GuideWindow, PhoneGuide, clearGuideCache } from "../shell/guide";
import { GuideTargetTracker } from "../shell/guideTarget";
import { WelcomeContext, welcomeFirst, welcomeSteps, type Welcome } from "../shell/guideWelcome";
import { GUIDE_CLOSED, readGuide, type WelcomeState, type WelcomeStep } from "../shell/storage";
import { readPalette } from "../ui/theme";
import { LANGUAGES } from "../i18n";

// The shipped page, so the card is tested against the words readers get.
const page = parseTopic(readFileSync(resolve(__dirname, "../guide/topics/en/topic.welcome.md"), "utf8"));
if (typeof page === "string") throw new Error(page);
const anchors = [...page.body.matchAll(/\{#([^}]+)\}/g)].map((m) => m[1]);
const topics = {
  "topic.welcome": { title: page.title, body: page.body, anchors },
  "workspace.logs": { title: "Logs Workspace", body: "Every request.", anchors: [] },
  "window.auditLog": { title: "Audit Log", body: "One row per request, with its outcome.", anchors: [] },
};

function welcomeOf(state: WelcomeState | undefined, { phone = false, tried = [] as WelcomeStep[] } = {}) {
  return {
    state, phone, tried, mark: vi.fn<Welcome["mark"]>(), markSeen: vi.fn<Welcome["markSeen"]>(), showing: vi.fn<NonNullable<Welcome["showing"]>>(),
    theme: "auto" as const, setTheme: vi.fn<NonNullable<Welcome["setTheme"]>>(),
  };
}

const rect = { x: 10, y: 10, w: 360, h: 500 };
function renderGuide(welcome: Welcome, tracker = new GuideTargetTracker(), topicId: string | null = null) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics }) })));
  const view = (w: Welcome) => (
    <GuideTargetContext.Provider value={tracker}>
      <WelcomeContext.Provider value={w}>
        <GuideWindow workspace="logs" controller={null} language="en" rect={rect} onRect={() => {}} onClose={() => {}} topicId={topicId} />
      </WelcomeContext.Provider>
    </GuideTargetContext.Provider>
  );
  const result = render(view(welcome));
  return { ...result, rerenderWith: (w: Welcome) => result.rerender(view(w)) };
}
const title = () => screen.getByRole("heading", { level: 3 }).textContent;
const tips = () => [...document.querySelectorAll<HTMLElement>("[data-welcome-step]")].map((li) => `${li.dataset.welcomeStep}${li.classList.contains("is-tried") ? " tried" : ""}`);
const tipText = (step: string) => document.querySelector(`[data-welcome-step=${step}] .phx-welcome-step-text`)!.textContent;

/** A window in the panel the reader can select something in. */
function auditWindow(tracker: GuideTargetTracker) {
  const root = document.createElement("div");
  root.innerHTML = '<section data-phx-window="auditLog"><button type="button">Row</button></section>';
  document.body.appendChild(root);
  const stop = tracker.attach(root);
  return { select: () => act(() => { fireEvent.pointerDown(root.querySelector("button")!); }), remove: () => { stop(); root.remove(); } };
}

describe("when the Guide opens on Welcome", () => {
  it("is until the reader has seen it once; a phone leaves out Quick Help's tip", () => {
    expect(welcomeFirst(undefined)).toBe(true);
    expect(welcomeFirst({ seen: true })).toBe(false);
    expect(welcomeSteps(false)).toEqual(["point", "select", "search"]);
    expect(welcomeSteps(true)).toEqual(["select", "search"]);
  });

  it("is saved with the Guide, earlier builds' checklists read as seen once dismissed, and Reset clears it", () => {
    expect(readGuide({ open: true, welcome: { seen: true } })).toEqual({ open: true, welcome: { seen: true } });
    expect(readGuide({ open: true, welcome: { done: ["point"], dismissed: true } })).toEqual({ open: true, welcome: { seen: true } });
    expect(readGuide({ open: false, welcome: { done: ["point", "search"] } })).toEqual({ open: false });
    expect(readGuide({ open: false, welcome: "yes" })).toEqual({ open: false });
    // Reset All Workspaces saves the Guide closed, which shows Welcome first again.
    expect(GUIDE_CLOSED).toEqual({ open: false });
  });
});

describe("Welcome in every language", () => {
  it("ships translated, so a reader never meets an English greeting around their own labels (user, 2026-11-03)", () => {
    const missing = LANGUAGES.map((l) => l.code).filter((code) => !existsSync(resolve(__dirname, `../guide/topics/${code}/topic.welcome.md`)));
    expect(missing).toEqual([]);
  });
});

describe("the Welcome page", () => {
  afterEach(() => { vi.unstubAllGlobals(); clearGuideCache(); localStorage.removeItem("phx-palette"); });

  it("opens first once, says so, tells the shell it is showing, and its Dismiss turns to the window's page", async () => {
    const welcome = welcomeOf(undefined);
    renderGuide(welcome);
    await waitFor(() => expect(title()).toBe("Welcome to the Guide"));
    await waitFor(() => expect(welcome.markSeen).toHaveBeenCalledTimes(1));
    expect(welcome.showing).toHaveBeenLastCalledWith(true);
    expect(tips()).toEqual(["point", "select", "search"]);
    // A demo: nothing to tick off, no progress and no finishing line.
    expect(document.querySelector(".phx-welcome-progress, .phx-welcome-finished")).toBeNull();
    // Labels come from the catalog, so the tip names Quick Help and the Help menu as the panel does.
    expect(tipText("point")).toBe("Point at a buttonQuick Help, along the bottom, says what it does. Show or hide it in the Help menu.");

    const guide = screen.getByRole("region", { name: "Guide" });
    // Dismiss in the header and again at the end.
    const dismiss = within(guide).getAllByRole("button", { name: "Dismiss" });
    expect(dismiss).toHaveLength(2);
    fireEvent.click(dismiss[1]);
    await waitFor(() => expect(title()).toBe("Logs Workspace"));
    expect(welcome.showing).toHaveBeenLastCalledWith(false);
    fireEvent.click(within(guide).getByRole("button", { name: "Back" }));
    await waitFor(() => expect(title()).toBe("Welcome to the Guide"));
  });

  it("once seen, opens on the reader's page, with Welcome the first page Back reaches", async () => {
    const welcome = welcomeOf({ seen: true });
    renderGuide(welcome);
    await waitFor(() => expect(title()).toBe("Logs Workspace"));
    // Back is offered once the history holds Welcome beneath this page.
    await waitFor(() => expect(screen.getByRole("button", { name: "Back" })).not.toHaveAttribute("aria-disabled", "true"));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(title()).toBe("Welcome to the Guide"));
    expect(screen.getByRole("button", { name: "Back" })).toHaveAttribute("aria-disabled", "true");
    expect(welcome.markSeen).not.toHaveBeenCalled();
  });

  it("stays on screen when the reader selects in a window, inviting them to read about it in the tip's own line", async () => {
    const tracker = new GuideTargetTracker();
    const panel = auditWindow(tracker);
    const welcome = welcomeOf(undefined);
    renderGuide(welcome, tracker);
    await waitFor(() => expect(title()).toBe("Welcome to the Guide"));
    expect(tipText("select")).toBe("Select something in a windowThe Guide finds the page about it.");

    panel.select();
    await waitFor(() => expect(tipText("select")).toBe("Select something in a windowRead more about Audit Log"));
    expect(title()).toBe("Welcome to the Guide");
    expect(welcome.mark).toHaveBeenCalledWith("select");

    // The invitation opens the page, and Back returns to Welcome.
    fireEvent.click(screen.getByRole("link", { name: "Read more about Audit Log" }));
    await waitFor(() => expect(title()).toBe("Audit Log"));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(title()).toBe("Welcome to the Guide"));
    panel.remove();
  });

  it("follows selections again once the reader has left Welcome", async () => {
    const tracker = new GuideTargetTracker();
    const panel = auditWindow(tracker);
    const welcome = welcomeOf(undefined);
    renderGuide(welcome, tracker);
    await waitFor(() => expect(title()).toBe("Welcome to the Guide"));
    fireEvent.click(screen.getAllByRole("button", { name: "Dismiss" })[0]);
    await waitFor(() => expect(title()).toBe("Logs Workspace"));
    panel.select();
    await waitFor(() => expect(title()).toBe("Audit Log"));
    expect(welcome.mark).not.toHaveBeenCalled();
    panel.remove();
  });

  it("suggests a word in its own search box, and lists matches that open their pages", async () => {
    const welcome = welcomeOf(undefined);
    renderGuide(welcome);
    await waitFor(() => expect(title()).toBe("Welcome to the Guide"));
    const box = screen.getByRole("searchbox", { name: "Search the Guide" });
    expect(box).toHaveAttribute("placeholder", "Try “deepseek”");
    fireEvent.change(box, { target: { value: "  " } });
    expect(welcome.mark).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: "outcome" } });
    expect(welcome.mark).toHaveBeenCalledWith("search");
    expect(title()).toBe("Welcome to the Guide");
    fireEvent.click(screen.getByRole("link", { name: "Audit Log" }));
    await waitFor(() => expect(title()).toBe("Audit Log"));
  });

  it("shows a check on each tip the reader tried", async () => {
    renderGuide(welcomeOf(undefined, { tried: ["point", "search"] }));
    await waitFor(() => expect(title()).toBe("Welcome to the Guide"));
    expect(tips()).toEqual(["point tried", "select", "search tried"]);
  });

  it("sets the light or dark mode with icons and the color theme with thumbnails, as Settings does", async () => {
    const welcome = welcomeOf(undefined);
    renderGuide(welcome);
    await waitFor(() => expect(title()).toBe("Welcome to the Guide"));
    const modes = screen.getByRole("group", { name: "Theme" });
    expect(within(modes).getAllByRole("button").map((b) => [b.getAttribute("aria-label"), b.textContent])).toEqual([["Light", ""], ["Auto", ""], ["Dark", ""]]);
    expect(within(modes).getByRole("button", { name: "Auto" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(modes).getByRole("button", { name: "Dark" }));
    expect(welcome.setTheme).toHaveBeenCalledWith("dark");
    const themes = screen.getByRole("group", { name: "Color theme" });
    expect(within(themes).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual(["Spartan", "Wayfinding", "Framed", "Mist", "Office", "Aubergine"]);
    fireEvent.click(within(themes).getByRole("button", { name: "Mist" }));
    expect(readPalette()).toBe("mist");
    await waitFor(() => expect(within(themes).getByRole("button", { name: "Mist" })).toHaveAttribute("aria-pressed", "true"));
  });

  it("plays no part beside the setup wizard", async () => {
    const welcome = welcomeOf(undefined);
    renderGuide(welcome, new GuideTargetTracker(), "workspace.logs");
    await waitFor(() => expect(title()).toBe("Logs Workspace"));
    expect(screen.getByRole("button", { name: "Back" })).toHaveAttribute("aria-disabled", "true");
    expect(welcome.markSeen).not.toHaveBeenCalled();
  });

  it("gives way to the wizard's page when the wizard starts while it is showing (user, 2026-11-03)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics }) })));
    const welcome = welcomeOf(undefined);
    const view = (topicId: string | null) => (
      <WelcomeContext.Provider value={welcome}>
        <GuideWindow workspace="logs" controller={null} language="en" rect={rect} onRect={() => {}} onClose={() => {}} topicId={topicId} />
      </WelcomeContext.Provider>
    );
    const { rerender } = render(view(null));
    await waitFor(() => expect(title()).toBe("Welcome to the Guide"));
    // The wizard reports its step: its page replaces Welcome, and Back still returns to Welcome.
    rerender(view("window.auditLog"));
    await waitFor(() => expect(title()).toBe("Audit Log"));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(title()).toBe("Welcome to the Guide"));
  });

  it("leaves Quick Help's tip out on a phone", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics }) })));
    render(
      <WelcomeContext.Provider value={welcomeOf(undefined, { phone: true })}>
        <PhoneGuide workspace="logs" language="en" onClose={() => {}} />
      </WelcomeContext.Provider>,
    );
    await waitFor(() => expect(title()).toBe("Welcome to the Guide"));
    expect(tips()).toEqual(["select", "search"]);
  });
});

describe("Contents and Search", () => {
  afterEach(() => { vi.unstubAllGlobals(); clearGuideCache(); });

  it("are two links at the top of every page, with nothing to unfold first", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics }) })));
    render(<GuideWindow workspace="logs" controller={null} language="en" rect={rect} onRect={() => {}} onClose={() => {}} />);
    await waitFor(() => expect(title()).toBe("Logs Workspace"));
    expect(document.querySelector(".phx-guide-tools details, details.phx-guide-tools")).toBeNull();
    expect([...document.querySelectorAll(".phx-guide-tools button")].map((b) => b.textContent)).toEqual(["Contents", "Search"]);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(screen.getByRole("searchbox", { name: "Search the Guide" })).toBeInTheDocument();
  });
});
