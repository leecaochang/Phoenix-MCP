/**
 * The Guide's framework (user, 2026-11-01): topics compile with sections,
 * parents, keywords and covered files checked, and short enough to read
 * beside a window; translations keep English's pages and sections; a page
 * renders as sections a control can point at; Contents lists every page in
 * order; Search forgives typos and narrows with every word; the Guide
 * follows the control the reader selects, scrolling to its section; and one
 * browser-like history with breadcrumbs lets the reader find their way back.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { GUIDE_FORMAT, compileGuide, lengthProblems, translationProblems, type GuideTopic } from "../guide/compile";
import { fold, searchGuide, topicSections } from "../guide/search";
import { GuideTargetContext, GuideWindow, clearGuideCache, guideContents, pushStep, renderTopic, topicAncestors, topicTitle } from "../shell/guide";
import { GuideTargetTracker, guideTargetFor, parseGuideRef } from "../shell/guideTarget";
import { WorkspaceController } from "../shell/dock/controller";

const labelExists = (key: string) => key === "workspace.aiProviders" || key === "settings.addAccount";
const file = (header: string, body: string) => `---\n{ ${header} }\n---\n${body}\n`;

describe("compiling topics with sections", () => {
  it("collects section anchors and checks links to pages and sections", () => {
    const { bundle, problems } = compileGuide("en", [
      { file: "a.md", source: file('"id": "window.settingsProviders", "title": "{{label:workspace.aiProviders}}"', "Intro.\n\n## Accounts {#accounts}\n\nText.\n\n### Adding {#adding}\n\nSee [here](guide:#accounts), [there](guide:topic.routing#how) and [nowhere](guide:#missing).") },
      { file: "b.md", source: file('"id": "topic.routing", "title": "Routing", "parent": "window.settingsProviders", "order": 2, "keywords": ["shortcut"]', "## How it works {#how}\n\nText.") },
    ], labelExists);
    expect(bundle.topics["window.settingsProviders"].anchors).toEqual(["accounts", "adding"]);
    expect(bundle.topics["topic.routing"]).toMatchObject({ parent: "window.settingsProviders", order: 2, keywords: ["shortcut"], anchors: ["how"] });
    expect(problems).toEqual(['a.md: link to unknown section "window.settingsProviders#missing"']);
  });

  it("keeps section keywords for sections that exist", () => {
    const { bundle, problems } = compileGuide("en", [
      { file: "a.md", source: file('"id": "topic.a", "title": "A", "sectionKeywords": { "one": ["JEV"], "gone": ["x"] }', "## One {#one}\n\nText.") },
      { file: "b.md", source: file('"id": "topic.b", "title": "B", "sectionKeywords": ["JEV"]', "Text.") },
    ], labelExists);
    expect(bundle.topics["topic.a"].sectionKeywords).toEqual({ one: ["JEV"], gone: ["x"] });
    expect(problems).toEqual([
      "a.md: sectionKeywords names a section that does not exist: #gone",
      "b.md: sectionKeywords must map section anchors to lists of words",
    ]);
  });

  it("refuses headings without anchors, repeated anchors, unknown parents, loops and missing covered files", () => {
    const { problems } = compileGuide("en", [
      { file: "a.md", source: file('"id": "topic.a", "title": "A", "parent": "topic.b", "covers": ["frontend_src/nope.tsx"]', "## No anchor\n\n## One {#one}\n\n## Again {#one}\n\n#### Too deep {#deep}") },
      { file: "b.md", source: file('"id": "topic.b", "title": "B", "parent": "topic.a"', "Text.") },
      { file: "c.md", source: file('"id": "topic.c", "title": "C", "parent": "topic.none"', "Text.") },
    ], labelExists, (path) => path !== "frontend_src/nope.tsx");
    expect(problems).toEqual([
      'a.md: heading without an anchor, or not ## or ###: "## No anchor"',
      "a.md: anchor #one is used twice",
      'a.md: heading without an anchor, or not ## or ###: "#### Too deep {#deep}"',
      "a.md: covers a file that does not exist: frontend_src/nope.tsx",
      'c.md: parent "topic.none" is not a topic',
      "a.md: the parent chain loops through topic.a",
      "b.md: the parent chain loops through topic.b",
    ]);
  });

  it("keeps paragraphs, sentences and list items short, counting a label as one word", () => {
    const long = (n: number) => Array.from({ length: n }, (_, i) => `w${i}`).join(" ");
    expect(lengthProblems(`${long(29)}.`)).toEqual([]);
    expect(lengthProblems(`${long(31)}.`)).toEqual([expect.stringMatching(/^sentence over 30 words/)]);
    expect(lengthProblems(`${long(25)}. ${long(25)}. ${long(15)}.`)).toEqual([expect.stringMatching(/^paragraph over 60 words/)]);
    expect(lengthProblems(`- ${long(20)}. ${long(25)}.`)).toEqual([expect.stringMatching(/^list item over 40 words/)]);
    expect(lengthProblems(`{{label:settings.a.very.long.key}} ${long(28)}.`)).toEqual([]);
    expect(lengthProblems(`\`\`\`\n${long(80)}\n\`\`\``)).toEqual([]);
  });

  it("holds a translation to English's pages, sections and places", () => {
    const topic = (anchors: string[], parent?: string): GuideTopic => ({ title: "T", body: "x", anchors, ...(parent ? { parent } : {}) });
    const en = { format: GUIDE_FORMAT, language: "en", topics: { "window.a": topic(["one", "two"]), "topic.b": topic([], "window.a") } };
    const de = { format: GUIDE_FORMAT, language: "de", topics: { "window.a": topic(["one"]), "topic.b": topic([]), "topic.c": topic([]) } };
    expect(translationProblems(en, de)).toEqual([
      "de: topic window.a has sections one, English has one, two",
      "de: topic topic.b sits under (top), English under window.a",
      "de: topic topic.c has no English original",
    ]);
  });
});

describe("rendering a page", () => {
  it("wraps each section and its parts so a control can point at them, and links to sections", () => {
    const html = renderTopic("Intro.\n\n## Accounts {#accounts}\n\nA.\n\n### Adding {#adding}\n\nB. [Up](guide:#accounts) [Away](guide:topic.x#how)\n\n## Checks {#checks}\n\nC.");
    expect(html).toMatch(/^<p>Intro.<\/p>\n<section class="phx-guide-section" data-guide-section="accounts">\n<h4>Accounts<\/h4>/);
    expect(html).toContain('<section class="phx-guide-section" data-guide-section="adding">\n<h5>Adding</h5>');
    expect(html).toContain('<a href="#" class="phx-guide-link" data-guide-anchor="accounts">Up</a>');
    expect(html).toContain('<a href="#" class="phx-guide-link" data-guide-topic="topic.x" data-guide-anchor="how">Away</a>');
    // Adding closes inside Accounts, which closes before Checks opens.
    expect(html).toMatch(/<p>B\..*<\/p>\n<\/section>\n<\/section>\n<section class="phx-guide-section" data-guide-section="checks">/s);
    expect(html.match(/<section/g)).toHaveLength(3);
    expect(html.match(/<\/section>/g)).toHaveLength(3);
  });

  it("names a page with its window's label", () => {
    expect(topicTitle("{{label:workspace.aiProviders}}")).toBe("AI Providers");
  });

  it("lists the pages as a tree, in order and then by title", () => {
    const t = (title: string, parent?: string, order?: number): GuideTopic => ({ title, body: "x", anchors: [], ...(parent ? { parent } : {}), ...(order !== undefined ? { order } : {}) });
    const tree = guideContents({
      "workspace.settings": t("Settings", undefined, 6),
      "workspace.logs": t("Logs", undefined, 5),
      "window.b": t("Beta", "workspace.settings", 2),
      "window.a": t("Alpha", "workspace.settings", 2),
      "window.z": t("Zed", "workspace.settings", 1),
      "topic.deep": t("Deep", "window.a"),
    });
    expect(tree.map((n) => n.title)).toEqual(["Logs", "Settings"]);
    expect(tree[1].children.map((n) => n.title)).toEqual(["Zed", "Alpha", "Beta"]);
    expect(tree[1].children[1].children.map((n) => n.id)).toEqual(["topic.deep"]);
  });
});

describe("searching the Guide", () => {
  const sections = [
    ...topicSections("window.settingsProviders", "{{label:workspace.aiProviders}}", "Accounts for models.\n\n## Decision provider {#decision-provider}\n\nA small model decides whether a command can skip the conversational model.", ["typesafe"], (k) => (k === "workspace.aiProviders" ? "AI Providers" : k)),
    ...topicSections("window.settingsSafety", "Safety", "## Kill switch {#kill-switch}\n\nStops every Phoenix endpoint at once.", [], (k) => k),
  ];

  it("splits a page into its sections as plain text", () => {
    expect(sections.map((s) => [s.topic, s.anchor, s.title, s.heading])).toEqual([
      ["window.settingsProviders", null, "AI Providers", null],
      ["window.settingsProviders", "decision-provider", "AI Providers", "Decision provider"],
      // A page that opens with a heading is still found by its title.
      ["window.settingsSafety", null, "Safety", null],
      ["window.settingsSafety", "kill-switch", "Safety", "Kill switch"],
    ]);
  });

  it("matches whole words, word starts and typos, ranking headings first", () => {
    expect(searchGuide(sections, "kill switch")[0].section.anchor).toBe("kill-switch");
    expect(searchGuide(sections, "decis")[0].section.anchor).toBe("decision-provider");
    expect(searchGuide(sections, "desicion")[0].section.anchor).toBe("decision-provider");
    expect(searchGuide(sections, "typesafe").map((h) => h.section.topic)).toContain("window.settingsProviders");
    expect(searchGuide(sections, "endpoint")[0].snippet).toContain("Stops every Phoenix endpoint");
  });

  it("puts the section that explains a word before the sections that only share its page", () => {
    const page = topicSections("window.p", "AI Providers",
      "Accounts.\n\n## Accounts {#accounts}\n\nThe table.\n\n## Decision Provider {#decision-provider}\n\nPhoenix offers TypeSafe / JEV.\n\n## Timeout {#timeout}\n\nHow long to wait.",
      ["model"], (k) => k, { "decision-provider": ["JEV"] });
    const routing = topicSections("topic.r", "Routing", "Order.\n\n## Order {#order}\n\nSentences first.\n\n## Follow-ups {#follow-ups}\n\nA follow-up needs a JEV model.", ["JEV"], (k) => k);
    const found = (q: string) => searchGuide([...page, ...routing], q).map((h) => `${h.section.topic}#${h.section.anchor ?? ""}`);
    // The section with the word as a keyword and in its text, then the page with it as a keyword, then the text alone.
    expect(found("JEV")).toEqual(["window.p#decision-provider", "topic.r#", "topic.r#follow-ups"]);
    // A page's keyword lets a section match alongside a word of its own, never alone.
    expect(found("model timeout")).toEqual(["window.p#timeout"]);
    expect(found("decision provider")[0]).toBe("window.p#decision-provider");
  });

  it("matches inside words only for scripts written without spaces", () => {
    expect(searchGuide(topicSections("topic.x", "X", "Rapid changes.", [], (k) => k), "api")).toEqual([]);
    expect(searchGuide(topicSections("topic.j", "J", "設定を変更します。", [], (k) => k), "変更")).toHaveLength(1);
  });

  it("needs every word to match, and ignores case and accents", () => {
    expect(searchGuide(sections, "kill model")).toEqual([]);
    expect(searchGuide(sections, "KÏLL")[0].section.anchor).toBe("kill-switch");
    expect(fold("Électricité")).toBe("electricite");
    expect(searchGuide(sections, "  ")).toEqual([]);
  });
});

describe("following the reader's selection", () => {
  const dom = () => {
    document.body.innerHTML = `
      <div id="root">
        <div data-phx-window="settingsProviders">
          <summary id="tagged" data-guide="window.settingsProviders#decision-provider">Decision</summary>
          <div data-guide="topic.routing"><button id="page">Go</button></div>
          <input id="plain" />
          <p id="space">Space</p>
        </div>
        <div data-phx-window="help"><button id="help">Help</button></div>
        <button id="outside">Toolbar</button>
      </div>`;
    return (id: string) => document.getElementById(id)!;
  };

  it("reads a control's own section, a page, or a resolver's answer, and nothing outside a window or in Help", () => {
    const el = dom();
    expect(parseGuideRef("window.a#b")).toEqual({ topic: "window.a", anchor: "b" });
    expect(parseGuideRef("#b")).toEqual({ topic: null, anchor: "b" });
    expect(guideTargetFor(el("tagged"))).toEqual({ windowId: "settingsProviders", topic: "window.settingsProviders", anchor: "decision-provider" });
    expect(guideTargetFor(el("page"))).toEqual({ windowId: "settingsProviders", topic: "topic.routing", anchor: null });
    expect(guideTargetFor(el("plain"), [(e) => (e.id === "plain" ? "provider-add" : null)])).toEqual({ windowId: "settingsProviders", topic: null, anchor: "provider-add" });
    expect(guideTargetFor(el("space"))).toEqual({ windowId: "settingsProviders", topic: null, anchor: null });
    expect(guideTargetFor(el("help"))).toBeNull();
    expect(guideTargetFor(el("outside"))).toBeNull();
  });

  it("follows selection and focus, and keeps the chosen section when the window's empty space is clicked", () => {
    const el = dom();
    const tracker = new GuideTargetTracker();
    const stop = tracker.attach(el("root"));
    const seen = vi.fn();
    tracker.subscribe(seen);
    fireEvent.pointerDown(el("tagged"));
    expect(tracker.current()?.anchor).toBe("decision-provider");
    fireEvent.pointerDown(el("space"));
    expect(tracker.current()?.anchor).toBe("decision-provider");
    fireEvent.focusIn(el("page"));
    expect(tracker.current()?.topic).toBe("topic.routing");
    fireEvent.pointerOver(el("tagged"));
    expect(tracker.current()?.topic).toBe("topic.routing");
    expect(seen).toHaveBeenCalledTimes(2);
    stop();
    fireEvent.pointerDown(el("tagged"));
    expect(tracker.current()?.topic).toBe("topic.routing");
  });
});

describe("the Guide window with sections, Contents and Search", () => {
  afterEach(() => { vi.unstubAllGlobals(); clearGuideCache(); document.body.innerHTML = ""; });
  const topics: Record<string, GuideTopic> = {
    "workspace.settings": { title: "Settings", body: "Six windows.", anchors: [], order: 6 },
    "window.settingsProviders": { title: "AI Providers", body: "Accounts.\n\n## Decision provider {#decision-provider}\n\nA small model.", anchors: ["decision-provider"], parent: "workspace.settings" },
  };

  it("opens the selected window's page at the selected section and highlights it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics }) })));
    const controller = new WorkspaceController("settings", [], () => {}, (id) => id);
    const tracker = new GuideTargetTracker();
    document.body.innerHTML = '<div id="root"><div data-phx-window="settingsProviders"><summary id="s" data-guide="window.settingsProviders#decision-provider">D</summary></div></div>';
    tracker.attach(document.getElementById("root")!);
    render(<GuideTargetContext.Provider value={tracker}>
      <GuideWindow workspace="settings" controller={controller} language="en" rect={{ x: 10, y: 10, w: 360, h: 400 }} onRect={() => {}} onClose={() => {}} />
    </GuideTargetContext.Provider>);
    await waitFor(() => expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Settings"));
    act(() => {
      controller.setActiveWindow("settingsProviders");
      fireEvent.pointerDown(document.getElementById("s")!);
    });
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("AI Providers");
    expect(document.querySelector('[data-guide-section="decision-provider"]')).toHaveClass("is-target");
  });

  it("keeps the workspace's page when the shell opens a window by itself, until the reader selects in it", async () => {
    const withAttention = { ...topics, "window.attention": { title: "Needs Attention", body: "Things to check.", anchors: [], parent: "workspace.settings" } };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics: withAttention }) })));
    const controller = new WorkspaceController("settings", [], () => {}, (id) => id);
    const tracker = new GuideTargetTracker();
    document.body.innerHTML = '<div id="root"><div data-phx-window="attention"><button id="b">Mark Reviewed</button></div></div>';
    tracker.attach(document.getElementById("root")!);
    render(<GuideTargetContext.Provider value={tracker}>
      <GuideWindow workspace="settings" controller={controller} language="en" rect={{ x: 10, y: 10, w: 360, h: 400 }} onRect={() => {}} onClose={() => {}} opensItself={new Set(["attention"])} />
    </GuideTargetContext.Provider>);
    await waitFor(() => expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Settings"));
    act(() => { controller.setActiveWindow("attention"); });
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Settings");
    act(() => { fireEvent.focusIn(document.getElementById("b")!); });
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Needs Attention");
  });

  it("lists every page in Contents and finds a section in Search, with Back returning", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics }) })));
    const controller = new WorkspaceController("settings", [], () => {}, (id) => id);
    render(<GuideWindow workspace="settings" controller={controller} language="en" rect={{ x: 10, y: 10, w: 360, h: 400 }} onRect={() => {}} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Settings"));
    fireEvent.click(screen.getByRole("button", { name: "Contents" }));
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Contents");
    fireEvent.click(screen.getByRole("link", { name: "AI Providers" }));
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("AI Providers");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "Search the Guide" }), { target: { value: "decison" } });
    fireEvent.click(screen.getByRole("link", { name: "AI Providers: Decision provider" }));
    expect(document.querySelector('[data-guide-section="decision-provider"]')).toHaveClass("is-target");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Search");
    fireEvent.change(screen.getByRole("searchbox", { name: "Search the Guide" }), { target: { value: "zebra" } });
    expect(screen.getByText("Nothing in the Guide matches.")).toBeInTheDocument();
  });
});

describe("finding the way back", () => {
  afterEach(() => { vi.unstubAllGlobals(); clearGuideCache(); document.body.innerHTML = ""; });
  const page = (id: string, anchor: string | null = null) => ({ kind: "topic" as const, id, anchor });

  it("keeps one history: a repeat adds nothing, a new page drops those ahead, and it holds 50 pages", () => {
    let h = pushStep({ steps: [], at: -1 }, page("a"));
    h = pushStep(h, page("b", "x"));
    expect(pushStep(h, page("b", "x"))).toBe(h);
    h = pushStep(h, { kind: "search" });
    expect(h.at).toBe(2);
    h = pushStep({ ...h, at: 0 }, page("c"));
    expect(h.steps.map((s) => (s.kind === "topic" ? s.id : s.kind))).toEqual(["a", "c"]);
    for (let i = 0; i < 60; i++) h = pushStep(h, page(`p${i}`));
    expect(h.steps).toHaveLength(50);
    expect(h.at).toBe(49);
  });

  it("lists the pages above a page in Contents, from the top", () => {
    const t = (parent?: string): GuideTopic => ({ title: "T", body: "x", anchors: [], ...(parent ? { parent } : {}) });
    const topics = { "workspace.settings": t(), "window.settingsChat": t("workspace.settings"), "topic.commandRouting": t("window.settingsChat") };
    expect(topicAncestors(topics, "topic.commandRouting")).toEqual(["workspace.settings", "window.settingsChat"]);
    expect(topicAncestors(topics, "workspace.settings")).toEqual([]);
  });

  it("goes Back and Forward through selections and links, greyed out at each end, with breadcrumbs above the title", async () => {
    const topics: Record<string, GuideTopic> = {
      "workspace.settings": { title: "Settings", body: "Six windows.", anchors: [] },
      "window.settingsChat": { title: "Agent Chat", body: "## History {#chat-memory}\n\nLines. See [routing](guide:topic.commandRouting).", anchors: ["chat-memory"], parent: "workspace.settings" },
      "window.settingsSafety": { title: "Safety", body: "## Kill switch {#kill-switch}\n\nStops.", anchors: ["kill-switch"], parent: "workspace.settings" },
      "topic.commandRouting": { title: "How a Command Is Routed", body: "Order.", anchors: [], parent: "window.settingsChat" },
    };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ format: GUIDE_FORMAT, language: "en", topics }) })));
    const controller = new WorkspaceController("settings", [], () => {}, (id) => id);
    const tracker = new GuideTargetTracker();
    document.body.innerHTML = `<div id="root">
      <div data-phx-window="settingsChat"><input id="memory" data-guide="window.settingsChat#chat-memory" /></div>
      <div data-phx-window="settingsSafety"><input id="kill" data-guide="window.settingsSafety#kill-switch" /></div></div>`;
    tracker.attach(document.getElementById("root")!);
    render(<GuideTargetContext.Provider value={tracker}>
      <GuideWindow workspace="settings" controller={controller} language="en" rect={{ x: 10, y: 10, w: 360, h: 400 }} onRect={() => {}} onClose={() => {}} />
    </GuideTargetContext.Provider>);
    const title = () => screen.getByRole("heading", { level: 3 }).textContent;
    const back = () => screen.getByRole("button", { name: "Back" });
    const forward = () => screen.getByRole("button", { name: "Forward" });
    await waitFor(() => expect(title()).toBe("Settings"));
    expect(back()).toBeDisabled();
    expect(forward()).toBeDisabled();
    expect(screen.queryByRole("navigation", { name: "Where this page sits" })).toBeNull();
    act(() => { fireEvent.pointerDown(document.getElementById("memory")!); });
    expect(title()).toBe("Agent Chat");
    act(() => { fireEvent.pointerDown(document.getElementById("kill")!); });
    expect(title()).toBe("Safety");
    fireEvent.click(back());
    expect(title()).toBe("Agent Chat");
    expect(document.querySelector('[data-guide-section="chat-memory"]')).toHaveClass("is-target");
    fireEvent.click(back());
    expect(title()).toBe("Settings");
    expect(back()).toBeDisabled();
    fireEvent.click(forward());
    fireEvent.click(forward());
    expect(title()).toBe("Safety");
    expect(forward()).toBeDisabled();
    // A link after going back drops the pages ahead.
    fireEvent.click(back());
    fireEvent.click(screen.getByText("routing"));
    expect(title()).toBe("How a Command Is Routed");
    expect(forward()).toBeDisabled();
    const crumbs = screen.getByRole("navigation", { name: "Where this page sits" });
    expect([...crumbs.querySelectorAll("a")].map((a) => a.textContent)).toEqual(["Settings", "Agent Chat"]);
    fireEvent.click(within(crumbs).getByText("Settings"));
    expect(title()).toBe("Settings");
    fireEvent.click(back());
    expect(title()).toBe("How a Command Is Routed");
  });
});
