/**
 * The Guide: topic files compile with every problem reported, the shipped
 * topics cover every workspace, topics render safely with links and UI
 * labels, the right topic is chosen, and the window follows links and the
 * focused window.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readdirSync, readFileSync } from "fs";
import { resolve } from "path";
import { compileGuide, parseTopic, type GuideBundle } from "../guide/compile";
import { GUIDE_LANGUAGES, GuideWindow, findTopic, guideLanguages, renderTopic, topicById } from "../shell/guide";
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
  afterEach(() => vi.unstubAllGlobals());

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
});
