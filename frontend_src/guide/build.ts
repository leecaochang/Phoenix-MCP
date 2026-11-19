/**
 * The Guide as the build compiles it, read from the repository: each language's
 * topic files with the tool reference generated beside them (reference.ts),
 * then compiled and checked (compile.ts). The build plugin in vite.config.ts and
 * the tests that check the shipped Guide both use it, so they see the same pages.
 * Node only; the panel never imports it.
 */

import { existsSync, readdirSync, readFileSync } from "fs";
import { resolve } from "path";
import { GUIDE_FORMAT, compileGuide, translationProblems, type GuideBundle } from "./compile";
import { DETAIL_LABELS, REFERENCE_FILE, guideSources, referenceText, shipReference, toolTextBlocks, type ReferenceData } from "./reference";

export interface GuideBuild {
  /** Each language's bundle as compiled, reference pages in full, English first. */
  compiled: GuideBundle[];
  /** The files to ship: `guide/<language>.json` and the shared `guide/tools.json`. */
  files: Array<{ fileName: string; source: string }>;
  /** Every source file read, for the build to watch. */
  read: string[];
  problems: string[];
}

/** Compile every language's Guide from the repository at `repo`. */
export function buildGuide(repo: string): GuideBuild {
  const topicsDir = resolve(repo, "frontend_src/guide/topics");
  const catalogs = resolve(repo, "custom_components/phoenix_mcp/catalogs");
  const referencePath = resolve(repo, "frontend_src/guide/tool_reference.json");
  const toolsTopicPath = resolve(topicsDir, "en/topic.tools.md");
  const read = [referencePath, toolsTopicPath];
  const catalogOf = (language: string): Record<string, unknown> | undefined => {
    const path = resolve(catalogs, `${language}.json`);
    if (!existsSync(path)) return undefined;
    read.push(path);
    return JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
  };
  const english = catalogOf("en")!;
  const panel = english.panel as Record<string, unknown>;
  const englishTools = english.guideTools as Record<string, unknown>;
  const englishSummaries = englishTools.summary as Record<string, string>;
  const englishRef = (englishTools.ref ?? {}) as Record<string, unknown>;
  const data = JSON.parse(readFileSync(referencePath, "utf8")) as ReferenceData;
  const toolsTopic = readFileSync(toolsTopicPath, "utf8");
  const labelOf = (key: string): string | undefined => {
    let node: unknown = panel;
    for (const part of key.split(".")) node = node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined;
    return typeof node === "string" ? node : undefined;
  };
  const labelExists = (key: string) => labelOf(key) !== undefined;
  const fileExists = (path: string) => !path.startsWith("/") && !path.includes("..") && existsSync(resolve(repo, path));
  const languages = readdirSync(topicsDir, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name).sort();
  const compiled: GuideBundle[] = [];
  const files: GuideBuild["files"] = [];
  const problems: string[] = DETAIL_LABELS.filter((key) => typeof englishRef[key] !== "string")
    .map((key) => `Guide topics: en.json lacks guideTools.ref.${key}`);
  // English first: a translation's links may lead to pages only English has.
  for (const language of ["en", ...languages.filter((l) => l !== "en")]) {
    const dir = resolve(topicsDir, language);
    const topics = readdirSync(dir).filter((f) => f.endsWith(".md")).sort().map((f) => {
      const path = resolve(dir, f);
      read.push(path);
      return { file: `${language}/${f}`, source: readFileSync(path, "utf8") };
    });
    // A language has its own reference where it translates the overview it hangs from; its
    // catalog then carries the reference's text. Any other reads the English pages.
    const ownReference = topics.some((t) => t.file === `${language}/topic.tools.md`);
    const ownTools = language === "en" ? englishTools : (catalogOf(language)?.guideTools as Record<string, unknown> | undefined);
    if (ownReference && !ownTools) problems.push(`Guide topics: ${language} translates topic.tools but its catalog has no guideTools section`);
    const summaries = (ownTools?.summary ?? {}) as Record<string, string>;
    const text = referenceText(englishTools, ownTools);
    const sources = guideSources(language, topics, data, toolsTopic, text, (tool) => summaries[tool] ?? englishSummaries[tool], ownReference);
    const result = compileGuide(language, sources.files, labelExists, fileExists, language === "en" ? (key) => labelOf(key)! : undefined,
      sources.generated, compiled.find((b) => b.language === "en"));
    problems.push(...sources.problems.map((p) => `Guide topics: ${p}`), ...result.problems.map((p) => `Guide topics: ${p}`));
    const bundle = ownReference ? { ...result.bundle, refLabels: Object.fromEntries(DETAIL_LABELS.map((key) => [key, text(`ref.${key}`)])) } : result.bundle;
    compiled.push(bundle);
    files.push({ fileName: `guide/${language}.json`, source: JSON.stringify(shipReference(bundle, sources.shipped)) });
  }
  files.push({ fileName: `guide/${REFERENCE_FILE}.json`, source: JSON.stringify({ format: GUIDE_FORMAT, blocks: toolTextBlocks(data) }) });
  const source = compiled[0];
  problems.push(...compiled.slice(1).flatMap((b) => translationProblems(source, b)).map((p) => `Guide translations: ${p}`));
  return { compiled, files, read, problems };
}
