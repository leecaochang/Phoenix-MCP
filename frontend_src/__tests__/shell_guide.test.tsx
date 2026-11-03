/**
 * The Guide: topic files compile with every problem reported, the shipped
 * topics cover every workspace, topics render safely with links and UI
 * labels, the right topic is chosen, and the window follows links and the
 * focused window. It is one window over every workspace, saved once, with
 * the window controls every window has except Dock. During the setup wizard
 * it shows each step's topic beside the step until the user moves it. A size
 * saved on a larger screen shrinks to fit a smaller one, such as an iPad.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { readdirSync, readFileSync } from "fs";
import { resolve } from "path";
import { GUIDE_FORMAT, compileGuide, parseTopic, type GuideBundle } from "../guide/compile";
import { GUIDE_LANGUAGES, GuideWindow, besideRect, fitGuideRect, clearGuideCache, findTopic, guideLanguages, renderTopic, topicById, useWizardGuide } from "../shell/guide";
import { WIZARD_STEPS } from "../views/OnboardingWizard";
import { GUIDE_CLOSED, WORKSPACE_IDS, readGuide } from "../shell/storage";
import { WorkspaceController } from "../shell/dock/controller";
import { LANGUAGES, flattenCatalog } from "../i18n";
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";

const TOPICS = resolve(__dirname, "../guide/topics");
const LABELS = flattenCatalog((en as { panel: unknown }).panel);
const labelExists = (key: string) => key in LABELS;
const file = (id: string, body: string, extra = "") => `---\n{ "id": "${id}", "title": "T"${extra} }\n---\n${body}\n`;

describe("compiling topics", () => {
  it("reads the JSON header and refuses a broken one", () => {
    expect(parseTopic(file("workspace.logs", "Body."))).toEqual({ id: "workspace.logs", title: "T", body: "Body.", covers: [] });
    expect(parseTopic("No header")).toMatch(/header/);
    expect(parseTopic("---\n{ nope }\n---\nBody")).toMatch(/not valid JSON/);
    expect(parseTopic(file("logs", "Body."))).toMatch(/bad topic id/);
    expect(parseTopic(file("workspace.logs", "Body.", ', "colour": "red"'))).toMatch(/unknown header fields: colour/);
    expect(parseTopic(file("workspace.logs", ""))).toMatch(/no text/);
  });

  it("reports duplicate ids, links to missing topics and unknown labels", () => {
    const { bundle, problems } = compileGuide("en", [
      { file: "a.md", source: file("workspace.logs", "See [Changes](guide:workspace.changes) and {{label:workspace.logs}}.") },
      { file: "b.md", source: file("workspace.logs", "Again.") },
      { file: "c.md", source: file("workspace.mesa", "See [Nowhere](guide:workspace.nowhere) and {{label:workspace.noSuchLabel}}.") },
    ], labelExists);
    expect(Object.keys(bundle.topics)).toEqual(["workspace.logs", "workspace.mesa"]);
    expect(problems).toEqual([
      "b.md: topic workspace.logs is also defined in a.md",
      'a.md: link to unknown topic "workspace.changes"',
      'c.md: link to unknown topic "workspace.nowhere"',
      'c.md: unknown label "workspace.noSuchLabel"',
    ]);
  });

  it("ships topics that compile cleanly, with an overview for every workspace, in the languages the panel knows", () => {
    expect(readdirSync(TOPICS).sort()).toEqual([...GUIDE_LANGUAGES].sort());
    // A folder is shipped by being there, so its name must be a language the panel offers.
    expect(GUIDE_LANGUAGES.filter((code) => !LANGUAGES.some((l) => l.code === code))).toEqual([]);
    const files = readdirSync(resolve(TOPICS, "en")).filter((f) => f.endsWith(".md")).map((f) => ({ file: f, source: readFileSync(resolve(TOPICS, "en", f), "utf8") }));
    const { bundle, problems } = compileGuide("en", files, labelExists);
    expect(problems).toEqual([]);
    for (const id of WORKSPACE_IDS) expect(bundle.topics[`workspace.${id}`], id).toBeDefined();
    for (const step of WIZARD_STEPS) expect(bundle.topics[`wizard.${step}`], step).toBeDefined();
  });
});

describe("rendering a topic", () => {
  it("escapes raw HTML, refuses unsafe links, follows topic links and fills in labels", () => {
    const html = renderTopic([
      "<script>alert(1)</script> <img src=x onerror=alert(1)>",
      "[bad](javascript:alert(1)) [site](https://example.com/docs) [next](guide:workspace.mesa)",
      "**{{label:workspace.restoreOriginal}}** and {{label:workspace.noSuchLabel}}",
    ].join("\n\n"));
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("javascript:");
    expect(html).toContain('<a href="https://example.com/docs" target="_blank" rel="noopener noreferrer">site</a>');
    expect(html).toContain('<a href="#" class="phx-guide-link" data-guide-topic="workspace.mesa">next</a>');
    expect(html).toContain("<strong>Reset This Workspace</strong>");
    expect(html).toContain('<code class="phx-guide-missing">workspace.noSuchLabel</code>');
  });

  it("chooses the focused window's topic, then the workspace overview, in the reader's language first", () => {
    const bundle = (language: string, ids: string[]): GuideBundle => ({ format: GUIDE_FORMAT, language, topics: Object.fromEntries(ids.map((id) => [id, { title: `${language} ${id}`, body: "x", anchors: [] }])) });
    const fr = bundle("fr", ["workspace.logs"]);
    const english = bundle("en", ["workspace.logs", "window.auditLog"]);
    expect(findTopic([fr, english], "logs", "auditLog")?.title).toBe("fr workspace.logs");
    expect(findTopic([english], "logs", "auditLog")?.title).toBe("en window.auditLog");
    expect(findTopic([english], "logs", null)?.title).toBe("en workspace.logs");
    expect(findTopic([null, english], "mesa", null)).toBeNull();
    expect(topicById([fr, english], "window.auditLog")?.title).toBe("en window.auditLog");
    // French ships translated pages (Welcome, user 2026-11-03); a language with none reads English only.
    expect(guideLanguages("fr")).toEqual(["fr", "en"]);
    expect(guideLanguages("xx")).toEqual(["en"]);
    expect(guideLanguages("en")).toEqual(["en"]);
  });
});

describe("the Guide window", () => {
  afterEach(() => { vi.unstubAllGlobals(); clearGuideCache(); });

  it("shows the workspace overview, follows a topic link and goes back, and moves on with the focused window", async () => {
    const topics = {
      "workspace.logs": { title: "Logs", body: "Read the [audit log](guide:window.auditLog).", anchors: [] },
      "window.auditLog": { title: "Audit Log", body: "Every request.", anchors: [] },
      "window.conversationLog": { title: "Conversation Log", body: "Conversations.", anchors: [] },
    };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics }) })));
    const controller = new WorkspaceController("logs", [], () => {}, (id) => id);
    render(<GuideWindow workspace="logs" controller={controller} language="en" rect={{ x: 10, y: 10, w: 360, h: 400 }} onRect={() => {}} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Logs"));
    fireEvent.click(screen.getByText("audit log"));
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Audit Log");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Logs");
    act(() => controller.setActiveWindow("conversationLog"));
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Conversation Log");
  });

  it("shows a wizard step's topic ahead of the focused window's, and the usual topic when the step has none", async () => {
    const topics = {
      "workspace.accessKeys": { title: "Access Keys", body: "Keys.", anchors: [] },
      "wizard.persona": { title: "Choosing a Persona", body: "Personas.", anchors: [] },
    };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics }) })));
    const controller = new WorkspaceController("accessKeys", [], () => {}, (id) => id);
    const view = render(<GuideWindow workspace="accessKeys" controller={controller} language="en" rect={{ x: 10, y: 10, w: 360, h: 400 }}
      onRect={() => {}} onClose={() => {}} topicId="wizard.persona" />);
    await waitFor(() => expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Choosing a Persona"));
    view.rerender(<GuideWindow workspace="accessKeys" controller={controller} language="en" rect={{ x: 10, y: 10, w: 360, h: 400 }}
      onRect={() => {}} onClose={() => {}} topicId="wizard.unknown" />);
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Access Keys");
  });

  it("is pinned beside the wizard step, level with the part it explains, and cannot be moved, resized or closed", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics: {} }) })));
    const box = (left: number, top: number, right: number, bottom: number) => ({ left, top, right, bottom, width: right - left, height: bottom - top, x: left, y: top, toJSON: () => ({}) });
    const frame = document.createElement("div");
    frame.className = "phx-workspace";
    const content = document.createElement("div");
    const part = document.createElement("div");
    content.appendChild(part);
    frame.appendChild(content);
    document.body.appendChild(frame);
    frame.getBoundingClientRect = () => box(260, 110, 1490, 940) as DOMRect;
    content.getBoundingClientRect = () => box(276, 150, 1036, 700) as DOMRect;
    part.getBoundingClientRect = () => box(296, 260, 1016, 500) as DOMRect;
    const onRect = vi.fn();
    const view = render(<GuideWindow workspace="accessKeys" controller={null} language="en" rect={{ x: 10, y: 10, w: 360, h: 560 }}
      onRect={onRect} onClose={() => {}} topicId="wizard.persona" follow={{ step: "persona", part, content }} />);
    const guide = screen.getByRole("region", { name: "Guide" });
    expect(guide).toHaveClass("is-pinned");
    expect(guide.style.left).toBe("1052px");
    expect(guide.style.top).toBe("260px");
    // Its own width beside the wizard, whatever size the user gave it elsewhere.
    expect(guide.style.width).toBe("400px");
    expect(within(guide).queryByRole("button", { name: "Close" })).toBeNull();
    expect(guide.querySelector(".agentcli-resize")).toBeNull();

    const title = guide.querySelector("header")!;
    fireEvent(title, new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 1100, clientY: 270 }));
    window.dispatchEvent(new MouseEvent("pointermove", { clientX: 900, clientY: 300 }));
    window.dispatchEvent(new MouseEvent("pointerup", {}));
    expect(guide.style.left).toBe("1052px");
    expect(onRect).not.toHaveBeenCalled();

    // After the wizard it is back where the user left it, and can be closed again.
    view.rerender(<GuideWindow workspace="accessKeys" controller={null} language="en" rect={{ x: 10, y: 10, w: 360, h: 560 }}
      onRect={onRect} onClose={() => {}} />);
    expect(guide).not.toHaveClass("is-pinned");
    expect(guide.style.left).toBe("10px");
    expect(guide.style.width).toBe("360px");
    expect(within(guide).getByRole("button", { name: "Close" })).toBeInTheDocument();
    frame.remove();
  });
});

describe("fitting the Guide to the screen", () => {
  it("shrinks a size saved on a larger screen and keeps the whole window on screen", () => {
    // Saved on a 1920 x 1200 desktop, opened on an iPad in portrait.
    expect(fitGuideRect({ x: 1400, y: 100, w: 500, h: 1050 }, 820, 1100)).toEqual({ x: 312, y: 42, w: 500, h: 1050 });
    expect(fitGuideRect({ x: 1400, y: 100, w: 500, h: 1050 }, 1180, 780)).toEqual({ x: 672, y: 8, w: 500, h: 764 });
    // A window that already fits is left alone.
    expect(fitGuideRect({ x: 40, y: 60, w: 400, h: 500 }, 1180, 780)).toEqual({ x: 40, y: 60, w: 400, h: 500 });
  });
});

describe("placing the Guide beside the wizard", () => {
  const frame = { left: 260, top: 110, right: 1490, bottom: 940 };
  it("goes to the right of the content, level with the part, keeping its size where it fits", () => {
    expect(besideRect({ left: 276, top: 150, right: 1036, bottom: 700 }, { left: 296, top: 260, right: 1016, bottom: 500 }, frame, { w: 400, h: 560 }))
      .toEqual({ x: 1052, y: 260, w: 400, h: 560 });
  });
  it("narrows to the room beside the content, and keeps to the workspace's right edge when there is too little", () => {
    expect(besideRect({ left: 276, top: 150, right: 1150, bottom: 700 }, { left: 0, top: 300, right: 0, bottom: 0 }, frame, { w: 400, h: 560 }).w).toBe(316);
    expect(besideRect({ left: 276, top: 150, right: 1300, bottom: 700 }, { left: 0, top: 300, right: 0, bottom: 0 }, frame, { w: 400, h: 560 }))
      .toEqual({ x: 1082, y: 300, w: 400, h: 560 });
  });
  it("stays inside the workspace when the part is low on the screen", () => {
    const r = besideRect({ left: 276, top: 150, right: 1036, bottom: 700 }, { left: 0, top: 900, right: 0, bottom: 0 }, frame, { w: 400, h: 560 });
    expect(r.y + r.h).toBeLessThanOrEqual(frame.bottom - 8);
    expect(r.y).toBe(372);
  });
});


describe("the wizard's step", () => {
  it("is the step last reported, and none once the wizard ends", () => {
    const step = (id: string) => ({ step: id, part: document.createElement("div"), content: document.createElement("div") });
    const hook = renderHook(() => useWizardGuide());
    expect(hook.result.current.step).toBeNull();
    act(() => hook.result.current.report(step("choose")));
    act(() => hook.result.current.report(step("persona")));
    expect(hook.result.current.step?.step).toBe("persona");
    act(() => hook.result.current.report(null));
    expect(hook.result.current.step).toBeNull();
  });
});

describe("one Guide over every workspace", () => {
  it("is saved once, open or closed, with its place and whether it is folded", () => {
    const rect = { x: 40, y: 80, w: 380, h: 520 };
    expect(readGuide({ open: true, rect, folded: true })).toEqual({ open: true, rect, folded: true });
    expect(readGuide({ open: false, rect: { x: 1, y: 1, w: 10, h: 10 } })).toEqual({ open: false });
    expect(readGuide("nope")).toEqual(GUIDE_CLOSED);
  });

  it("reads a record saved with one Guide per workspace: open if it was open anywhere, at the first place found", () => {
    const rect = { x: 40, y: 80, w: 380, h: 520 };
    expect(readGuide({ logs: { open: false, rect }, settings: { open: true } })).toEqual({ open: true, rect });
    expect(readGuide({ logs: { open: false }, nowhere: { open: true } })).toEqual({ open: false });
  });
});

describe("the Guide's window controls", () => {
  afterEach(() => { vi.unstubAllGlobals(); clearGuideCache(); document.body.innerHTML = ""; });
  const topics = { "workspace.logs": { title: "Logs", body: "Every request.", anchors: [] } };
  const box = (left: number, top: number, right: number, bottom: number) => ({ left, top, right, bottom, width: right - left, height: bottom - top, x: left, y: top, toJSON: () => ({}) }) as DOMRect;
  const shell = () => {
    const root = document.createElement("div");
    root.className = "phx-shell";
    const area = document.createElement("main");
    area.className = "phx-workspaces";
    area.getBoundingClientRect = () => box(0, 60, 1200, 860);
    // The Guide renders beside the workspace area, as in the shell; React replaces whatever its container holds.
    const host = document.createElement("div");
    root.append(area, host);
    document.body.appendChild(root);
    return host;
  };
  const rect = { x: 10, y: 10, w: 360, h: 400 };

  it("folds into its title bar, maximizes over the workspace area and restores, and cannot be docked", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics }) })));
    const onFold = vi.fn();
    const props = { workspace: "logs", controller: null, language: "en", rect, onRect: () => {}, onClose: () => {}, onFold };
    const view = render(<GuideWindow {...props} />, { container: shell() });
    const guide = screen.getByRole("region", { name: "Guide" });
    await waitFor(() => expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Logs"));
    expect(within(guide).queryByRole("button", { name: /Dock|Float/ })).toBeNull();

    fireEvent.click(within(guide).getByRole("button", { name: "Fold" }));
    expect(onFold).toHaveBeenLastCalledWith(true);
    view.rerender(<GuideWindow {...props} folded />);
    expect(guide).toHaveClass("is-folded");
    expect(guide.querySelector(".phx-guide-body")).toHaveAttribute("hidden");
    expect(guide.querySelector(".agentcli-resize")).toBeNull();
    expect(guide.style.height).toBe("");

    // Maximizing a folded Guide unfolds it.
    fireEvent.click(within(guide).getByRole("button", { name: "Maximize" }));
    expect(onFold).toHaveBeenLastCalledWith(false);
    view.rerender(<GuideWindow {...props} />);
    expect(guide).toHaveClass("is-maximized");
    expect([guide.style.left, guide.style.top, guide.style.width, guide.style.height]).toEqual(["8px", "68px", "1184px", "784px"]);
    expect(guide.querySelector(".agentcli-resize")).toBeNull();
    fireEvent.click(within(guide).getByRole("button", { name: "Restore" }));
    expect([guide.style.left, guide.style.top, guide.style.width, guide.style.height]).toEqual(["10px", "10px", "360px", "400px"]);
  });

  it("moves and resizes with the arrow keys from its window menu, saving when finished", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics }) })));
    const onRect = vi.fn();
    const onClose = vi.fn();
    render(<GuideWindow workspace="logs" controller={null} language="en" rect={rect} onRect={onRect} onClose={onClose} />, { container: shell() });
    const guide = screen.getByRole("region", { name: "Guide" });
    const menu = within(guide).getByRole("button", { name: "Window Menu" });

    fireEvent.click(menu);
    fireEvent.click(screen.getByRole("menuitem", { name: "Move with Keyboard" }));
    expect(screen.getByRole("status").textContent).toBe("Arrow keys move. Esc, Enter, or Tab finishes.");
    fireEvent.keyDown(menu, { key: "ArrowRight" });
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect([guide.style.left, guide.style.top]).toEqual(["34px", "34px"]);
    expect(onRect).not.toHaveBeenCalled();
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(onRect).toHaveBeenLastCalledWith({ x: 34, y: 34, w: 360, h: 400 });

    fireEvent.click(menu);
    fireEvent.click(screen.getByRole("menuitem", { name: "Resize with Keyboard" }));
    fireEvent.keyDown(menu, { key: "ArrowRight" });
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    fireEvent.keyDown(menu, { key: "Enter" });
    expect(onRect).toHaveBeenLastCalledWith({ x: 34, y: 34, w: 384, h: 376 });
    expect(screen.queryByRole("status")).toBeNull();

    fireEvent.click(menu);
    fireEvent.click(screen.getByRole("menuitem", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
