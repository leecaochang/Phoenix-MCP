/**
 * English Guide pages read in another language (user, 2026-11-03): controls
 * are named in English like the sentences around them, so titles, links and
 * Contents read in English too, while the reader's own name for each control
 * still finds its section in Search. The English bundle carries the English
 * text of the labels its pages use; translations carry none.
 */

import { describe, it, expect } from "vitest";
import { GUIDE_FORMAT, compileGuide, type GuideBundle } from "../guide/compile";
import { searchGuide, topicSections } from "../guide/search";
import { readAsEnglish } from "../shell/guide";

const file = (id: string, title: string, body: string) => `---\n{ "id": "${id}", "title": "${title}" }\n---\n${body}\n`;
const ENGLISH: Record<string, string> = { "agentchat.smaller": "Smaller Text", "agentchat.larger": "Larger Text", "agentchat.title": "Agent Chat" };
const READER: Record<string, string> = { "agentchat.smaller": "縮小文字", "agentchat.larger": "放大文字", "agentchat.title": "Agent Chat" };

describe("the English bundle's labels", () => {
  it("carries the English text of every label its pages use, and a translation carries none", () => {
    const source = [{ file: "a.md", source: file("window.agentChat", "{{label:agentchat.title}}", "Intro.\n\n## Text size {#size}\n\n**{{label:agentchat.smaller}}** and **{{label:agentchat.larger}}** change it.") }];
    const english = compileGuide("en", source, (k) => k in ENGLISH, () => true, (k) => ENGLISH[k]);
    expect(english.problems).toEqual([]);
    expect(english.bundle.labels).toEqual(ENGLISH);
    expect(compileGuide("ja", source, (k) => k in ENGLISH).bundle.labels).toBeUndefined();
  });
});

describe("an English page read in another language", () => {
  const bundle: GuideBundle = {
    format: GUIDE_FORMAT, language: "en", labels: ENGLISH,
    topics: {
      "window.agentChat": {
        title: "{{label:agentchat.title}}", anchors: ["size"], keywords: ["chat"],
        body: "Talk to your agent here.\n\n## Text size {#size}\n\n**{{label:agentchat.smaller}}** and **{{label:agentchat.larger}}** change the size of the conversation's text.",
      },
    },
  };
  const read = readAsEnglish(bundle, (k) => READER[k] ?? k);
  const page = read.topics["window.agentChat"];

  it("names every control in English, like its sentences", () => {
    expect(page.body).toContain("**Smaller Text** and **Larger Text** change the size of the conversation's text.");
    expect(page.body).not.toMatch(/縮小文字|放大文字|\{\{label:/);
    expect(page.title).toBe("Agent Chat");
  });

  it("adds the reader's names for those controls to their section's search words, and only where they differ", () => {
    expect(page.sectionKeywords).toEqual({ size: ["縮小文字", "放大文字"] });
    expect(page.keywords).toEqual(["chat"]);
    const sections = topicSections("window.agentChat", page.title, page.body, page.keywords ?? [], (k) => k, page.sectionKeywords);
    const hits = searchGuide(sections, "縮小文字");
    expect(hits.map((h) => [h.section.anchor, h.section.title])).toEqual([["size", "Agent Chat"]]);
  });

  it("leaves a translated page alone, so it keeps the reader's labels", () => {
    const translated: GuideBundle = { format: GUIDE_FORMAT, language: "ja", topics: bundle.topics };
    // The Guide only reads the English bundle this way; a translation is shown as written.
    expect(translated.labels).toBeUndefined();
    expect(readAsEnglish({ ...translated, labels: undefined }, (k) => READER[k] ?? k).topics["window.agentChat"].body).toContain("{{label:agentchat.smaller}}");
  });
});
