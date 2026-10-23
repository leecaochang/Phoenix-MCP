/**
 * The Guide: topic files compile with every problem reported, the shipped
 * topics cover every workspace, topics render safely with links and UI
 * labels, the right topic is chosen, and the window follows links and the
 * focused window. During the setup wizard it shows each step's topic beside
 * the step until the user moves it.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { readdirSync, readFileSync } from "fs";
import { resolve } from "path";
import { compileGuide, parseTopic, type GuideBundle } from "../guide/compile";
import { GUIDE_LANGUAGES, GuideWindow, besideRect, clearGuideCache, findTopic, guideLanguages, renderTopic, topicById, useWizardGuide } from "../shell/guide";
import { WIZARD_STEPS } from "../views/OnboardingWizard";
import { WORKSPACE_IDS } from "../shell/storage";
import { WorkspaceController } from "../shell/dock/controller";
import { flattenCatalog } from "../i18n";
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";

const TOPICS = resolve(__dirname, "../guide/topics");
const LABELS = flattenCatalog((en as { panel: unknown }).panel);
const labelExists = (key: string) => key in LABELS;
const file = (id: string, body: string, extra = "") => `---\n{ "id": "${id}", "title": "T"${extra} }\n---\n${body}\n`;

describe("compiling topics", () => {
  it("reads the JSON header and refuses a broken one", () => {
    expect(parseTopic(file("workspace.logs", "Body."))).toEqual({ id: "workspace.logs", title: "T", body: "Body." });
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
    expect(html).toContain("<strong>Restore Original Layout</strong>");
    expect(html).toContain('<code class="phx-guide-missing">workspace.noSuchLabel</code>');
  });

  it("chooses the focused window's topic, then the workspace overview, in the reader's language first", () => {
    const bundle = (language: string, ids: string[]): GuideBundle => ({ format: 1, language, topics: Object.fromEntries(ids.map((id) => [id, { title: `${language} ${id}`, body: "x" }])) });
    const fr = bundle("fr", ["workspace.logs"]);
    const english = bundle("en", ["workspace.logs", "window.auditLog"]);
    expect(findTopic([fr, english], "logs", "auditLog")?.title).toBe("fr workspace.logs");
    expect(findTopic([english], "logs", "auditLog")?.title).toBe("en window.auditLog");
    expect(findTopic([english], "logs", null)?.title).toBe("en workspace.logs");
    expect(findTopic([null, english], "mesa", null)).toBeNull();
    expect(topicById([fr, english], "window.auditLog")?.title).toBe("en window.auditLog");
    expect(guideLanguages("fr")).toEqual(["en"]);
    expect(guideLanguages("en")).toEqual(["en"]);
  });
});

describe("the Guide window", () => {
  afterEach(() => { vi.unstubAllGlobals(); clearGuideCache(); });

  it("shows the workspace overview, follows a topic link and goes back, and moves on with the focused window", async () => {
    const topics = {
      "workspace.logs": { title: "Logs", body: "Read the [audit log](guide:window.auditLog)." },
      "window.auditLog": { title: "Audit Log", body: "Every request." },
      "window.conversationLog": { title: "Conversation Log", body: "Conversations." },
    };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: 1, language: "en", topics }) })));
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
      "workspace.accessKeys": { title: "Access Keys", body: "Keys." },
      "wizard.persona": { title: "Choosing a Persona", body: "Personas." },
    };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: 1, language: "en", topics }) })));
    const controller = new WorkspaceController("accessKeys", [], () => {}, (id) => id);
    const view = render(<GuideWindow workspace="accessKeys" controller={controller} language="en" rect={{ x: 10, y: 10, w: 360, h: 400 }}
      onRect={() => {}} onClose={() => {}} topicId="wizard.persona" />);
    await waitFor(() => expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Choosing a Persona"));
    view.rerender(<GuideWindow workspace="accessKeys" controller={controller} language="en" rect={{ x: 10, y: 10, w: 360, h: 400 }}
      onRect={() => {}} onClose={() => {}} topicId="wizard.unknown" />);
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Access Keys");
  });

  it("sits beside the wizard step, level with the part it explains, until the user moves it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: 1, language: "en", topics: {} }) })));
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
    const onMoved = vi.fn();
    const onRect = vi.fn();
    render(<GuideWindow workspace="accessKeys" controller={null} language="en" rect={{ x: 10, y: 10, w: 400, h: 560 }}
      onRect={onRect} onClose={() => {}} topicId="wizard.persona" follow={{ step: "persona", part, content }} onMoved={onMoved} />);
    const guide = screen.getByRole("region", { name: "Guide" });
    expect(guide.style.left).toBe("1052px");
    expect(guide.style.top).toBe("260px");
    expect(guide.style.width).toBe("400px");
    // The placement is not saved; only a move by the user is.
    expect(onRect).not.toHaveBeenCalled();

    const title = guide.querySelector("header")!;
    fireEvent(title, new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 1100, clientY: 270 }));
    window.dispatchEvent(new MouseEvent("pointermove", { clientX: 900, clientY: 300 }));
    window.dispatchEvent(new MouseEvent("pointerup", {}));
    expect(onMoved).toHaveBeenCalledOnce();
    expect(onRect).toHaveBeenCalledOnce();
    frame.remove();
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


describe("the wizard's hold on the Guide", () => {
  const step = (id: string) => ({ step: id, part: document.createElement("div"), content: document.createElement("div") });
  function setup(open = false) {
    const state = { open };
    const setOpen = vi.fn((next: boolean) => { state.open = next; });
    const hook = renderHook(() => useWizardGuide(() => state.open, setOpen));
    return { state, setOpen, hook };
  }

  it("a first run opens the Guide, which follows the steps and closes again at the end", () => {
    const { state, setOpen, hook } = setup();
    act(() => hook.result.current.report(step("choose"), true));
    expect(state.open).toBe(true);
    expect(hook.result.current.follow?.step).toBe("choose");
    act(() => hook.result.current.report(step("persona")));
    expect(hook.result.current.step?.step).toBe("persona");
    act(() => hook.result.current.report(null));
    expect(state.open).toBe(false);
    expect(setOpen.mock.calls).toEqual([[true], [false]]);
    expect(hook.result.current.step).toBeNull();
  });

  it("leaves the Guide alone when it was not a first run, was already open, or the user set it", () => {
    const later = setup();
    act(() => later.hook.result.current.report(step("choose"), false));
    act(() => later.hook.result.current.report(null));
    expect(later.setOpen).not.toHaveBeenCalled();

    const open = setup(true);
    act(() => open.hook.result.current.report(step("choose"), true));
    act(() => open.hook.result.current.report(null));
    expect(open.setOpen).not.toHaveBeenCalled();

    const toggled = setup();
    act(() => toggled.hook.result.current.report(step("choose"), true));
    act(() => { toggled.hook.result.current.userToggled(); toggled.state.open = true; });
    act(() => toggled.hook.result.current.report(null));
    expect(toggled.setOpen.mock.calls).toEqual([[true]]);
    expect(toggled.state.open).toBe(true);
  });

  it("stops placing a Guide the user moved, until the next wizard", () => {
    const { hook } = setup();
    act(() => hook.result.current.report(step("choose"), true));
    act(() => hook.result.current.onMoved());
    act(() => hook.result.current.report(step("persona")));
    expect(hook.result.current.follow).toBeNull();
    expect(hook.result.current.step?.step).toBe("persona");
    act(() => hook.result.current.report(null));
    act(() => hook.result.current.report(step("choose"), false));
    expect(hook.result.current.follow?.step).toBe("choose");
    // A move outside a wizard is just a move.
    act(() => hook.result.current.report(null));
    act(() => hook.result.current.onMoved());
    act(() => hook.result.current.report(step("choose")));
    expect(hook.result.current.follow?.step).toBe("choose");
  });
});
