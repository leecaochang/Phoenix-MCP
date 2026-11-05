/**
 * Guide Search finds what readers type: every AI provider
 * Phoenix offers, named as the backend names it, leads to the AI Providers
 * page, Agent Chat and the wizard's provider step in every language the Guide
 * is written in. A provider added to llm/accounts.py without its name in those
 * pages' keywords fails here.
 */

import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "fs";
import { join, resolve } from "path";
import { compileGuide } from "../guide/compile";
import { searchGuide, topicSections } from "../guide/search";
import { flattenCatalog } from "../i18n";

const REPO = resolve(__dirname, "../..");
const PAGES = ["window.settingsProviders", "window.agentChat", "wizard.provider"];

/** The provider labels the backend lists, such as "DeepSeek" or "Ollama (local)". */
function providerLabels(): string[] {
  const source = readFileSync(resolve(REPO, "custom_components/phoenix_mcp/llm/accounts.py"), "utf8");
  return [...source.matchAll(/_definition\(\s*"[a-z_]+",\s*"([^"]+)"/g)].map((m) => m[1]);
}

function sections(language: string) {
  const dir = resolve(REPO, "frontend_src/guide/topics", language);
  const files = readdirSync(dir).filter((f) => f.endsWith(".md")).map((f) => ({ file: f, source: readFileSync(join(dir, f), "utf8") }));
  const catalog = JSON.parse(readFileSync(resolve(REPO, `custom_components/phoenix_mcp/catalogs/${language}.json`), "utf8")) as { panel: unknown };
  const labels = flattenCatalog(catalog.panel);
  const label = (key: string) => labels[key] ?? key;
  const { bundle } = compileGuide(language, files, () => true);
  return Object.entries(bundle.topics).flatMap(([id, topic]) => topicSections(id, topic.title, topic.body, topic.keywords ?? [], label, topic.sectionKeywords));
}

describe("Guide Search", () => {
  const labels = providerLabels();

  it("reads every provider the backend offers", () => {
    expect(labels).toEqual(expect.arrayContaining(["Anthropic", "DeepSeek", "OpenAI", "Ollama (local)"]));
    expect(labels.length).toBeGreaterThan(15);
  });

  for (const language of ["en", "zh-Hans", "zh-Hant"]) {
    it(`finds the provider pages by each provider's name, in ${language}`, () => {
      const all = sections(language);
      const missing: string[] = [];
      for (const name of labels) {
        // A label's note in brackets, such as "(local)", is not part of the name.
        const query = name.replace(/\s*\([^)]*\)/g, "");
        const found = new Set(searchGuide(all, query, 200).map((h) => h.section.topic));
        for (const page of PAGES) if (!found.has(page)) missing.push(`${query}: ${page}`);
      }
      expect(missing).toEqual([]);
    });
  }
});
