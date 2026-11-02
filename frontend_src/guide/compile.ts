/**
 * Guide topics: one Markdown file per topic, opening with a small JSON header
 * between two `---` lines, compiled by the build into the JSON the panel
 * loads. Compiling checks every header, that ids are unique, that each
 * `guide:` link names a topic and section that exist, that each
 * `{{label:<key>}}` placeholder names a catalog key, that each file a topic
 * covers exists, and that no paragraph, sentence or list item is too long to
 * read beside the window it explains or, in English, uses a phrasing the
 * Writing rules ban. A broken topic fails the build instead
 * of the panel. The rules for writing topics are in docs-internal/agents/guide.md.
 *
 * Topic ids say what a topic explains: `workspace.<workspace id>` for a
 * workspace overview, `window.<window id>` for one window, `wizard.<step>` for
 * a setup step and `topic.<name>` for a deeper dive. A heading carries the
 * anchor of its section, `## Name {#anchor}`, which links and the panel's
 * controls point at (`window.settingsProviders#decision-provider`).
 */

export const GUIDE_FORMAT = 2;

export interface GuideTopic {
  /** May hold `{{label:<key>}}` placeholders, so a page is named as its window is. */
  title: string;
  /** Markdown, with `## Heading {#anchor}` sections, `guide:` links and `{{label:<key>}}` placeholders. */
  body: string;
  /** The topic it sits under in the Guide's contents; none for a top-level topic. */
  parent?: string;
  /** Its place among the topics under the same parent; lower comes first. */
  order?: number;
  /** Words a reader may search for that the topic does not use itself. */
  keywords?: string[];
  /** Words that should find one section in particular, by its anchor. */
  sectionKeywords?: Record<string, string[]>;
  /** The anchors of its sections, in order. */
  anchors: string[];
}

export interface GuideBundle {
  format: number;
  language: string;
  topics: Record<string, GuideTopic>;
}

export const TOPIC_ID = /^(workspace|window|wizard|topic)\.[A-Za-z0-9][A-Za-z0-9_-]*$/;
/** A section anchor: lower-case words joined by dashes. */
export const ANCHOR = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
/** A section heading: `## Name {#anchor}` (a section) or `### Name {#anchor}` (a part of one). */
export const HEADING = /^(#{2,3})\s+(.*?)\s*\{#([^}]*)\}\s*$/;
/** A link to another topic or section: `[text](guide:<topic id>)`, `(guide:<topic id>#<anchor>)` or `(guide:#<anchor>)`. */
export const GUIDE_LINK = /\]\(guide:([^)\s#]*)(?:#([^)\s]*))?\)/g;
/** A UI label taken from the panel's catalog: `{{label:<key>}}`. */
export const LABEL = /\{\{label:([^}]*)\}\}/g;
export const LABEL_KEY = /^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$/;

/**
 * Text short enough to read in a narrow window beside the screen it explains
 * (user, 2026-11-01): long paragraphs are split, long sentences reworded.
 */
export const LIMITS = { paragraphWords: 60, sentenceWords: 30, itemWords: 40 } as const;

const HEADER_FIELDS = new Set(["id", "title", "parent", "order", "keywords", "sectionKeywords", "covers"]);

export interface ParsedTopic {
  id: string;
  title: string;
  body: string;
  parent?: string;
  order?: number;
  keywords?: string[];
  sectionKeywords?: Record<string, string[]>;
  /** Repository paths of the code the topic describes; checked, never shipped. */
  covers: string[];
}

/** One topic file: its header and body, or the reason it cannot be read. */
export function parseTopic(source: string): ParsedTopic | string {
  const text = source.replace(/\r\n/g, "\n");
  const match = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(text);
  if (!match) return "missing the JSON header between --- lines";
  let header: unknown;
  try { header = JSON.parse(match[1]); } catch { return "the header is not valid JSON"; }
  if (!header || typeof header !== "object" || Array.isArray(header)) return "the header is not a JSON object";
  const h = header as Record<string, unknown>;
  const unknown = Object.keys(h).filter((k) => !HEADER_FIELDS.has(k));
  if (unknown.length) return `unknown header fields: ${unknown.join(", ")}`;
  if (typeof h.id !== "string" || !TOPIC_ID.test(h.id)) return `bad topic id ${JSON.stringify(h.id)}`;
  if (typeof h.title !== "string" || !h.title.trim()) return "missing a title";
  if (h.parent !== undefined && (typeof h.parent !== "string" || !TOPIC_ID.test(h.parent))) return `bad parent ${JSON.stringify(h.parent)}`;
  if (h.order !== undefined && (typeof h.order !== "number" || !Number.isFinite(h.order))) return "order must be a number";
  const strings = (v: unknown) => Array.isArray(v) && v.every((s) => typeof s === "string" && s.trim());
  if (h.keywords !== undefined && !strings(h.keywords)) return "keywords must be a list of words";
  const bySection = h.sectionKeywords;
  if (bySection !== undefined && (!bySection || typeof bySection !== "object" || Array.isArray(bySection) || !Object.values(bySection).every(strings))) {
    return "sectionKeywords must map section anchors to lists of words";
  }
  if (h.covers !== undefined && !strings(h.covers)) return "covers must be a list of repository paths";
  const body = match[2].trim();
  if (!body) return "the topic has no text";
  return {
    id: h.id, title: h.title.trim(), body,
    ...(h.parent !== undefined ? { parent: h.parent as string } : {}),
    ...(h.order !== undefined ? { order: h.order as number } : {}),
    ...(h.keywords !== undefined ? { keywords: h.keywords as string[] } : {}),
    ...(bySection !== undefined ? { sectionKeywords: bySection as Record<string, string[]> } : {}),
    covers: (h.covers as string[] | undefined) ?? [],
  };
}

/** The words of a line as a reader counts them: labels and link targets add none of their own. */
function words(text: string): number {
  const plain = text.replace(LABEL, "X").replace(/\]\([^)]*\)/g, "]").replace(/[*_`#>[\]]/g, " ");
  return plain.split(/\s+/).filter(Boolean).length;
}

/** Problems with the length of a topic's paragraphs, sentences and list items. */
export function lengthProblems(body: string): string[] {
  const problems: string[] = [];
  const short = (s: string) => (s.length > 60 ? `${s.slice(0, 60)}...` : s);
  const sentences = (text: string) => {
    for (const sentence of text.split(/(?<=[.!?。！？])\s+/)) {
      if (words(sentence) > LIMITS.sentenceWords) problems.push(`sentence over ${LIMITS.sentenceWords} words: "${short(sentence.trim())}"`);
    }
  };
  let paragraph: string[] = [];
  const flush = () => {
    if (!paragraph.length) return;
    const text = paragraph.join(" ");
    if (words(text) > LIMITS.paragraphWords) problems.push(`paragraph over ${LIMITS.paragraphWords} words: "${short(text)}"`);
    sentences(text);
    paragraph = [];
  };
  let inCode = false;
  for (const line of body.split("\n")) {
    if (/^```/.test(line)) { flush(); inCode = !inCode; continue; }
    if (inCode) continue;
    if (!line.trim() || /^#{1,6}\s/.test(line) || line.includes("|")) { flush(); continue; }
    const item = /^\s*(?:[-*+]|\d+\.)\s+(.*)$/.exec(line);
    if (item) {
      flush();
      if (words(item[1]) > LIMITS.itemWords) problems.push(`list item over ${LIMITS.itemWords} words: "${short(item[1])}"`);
      sentences(item[1]);
      continue;
    }
    paragraph.push(line.trim());
  }
  flush();
  return problems;
}

/**
 * English phrasings the Writing rules ban that are wrong almost every time they appear
 * (user, 2026-11-01): the reader is the one acting, and steps are short sentences, not
 * chains. Ambiguous ones ("at once", "is shown") are left to review, since they are
 * often right. Code is skipped; each pattern names the rule it enforces.
 */
export const VOICE_PATTERNS: ReadonlyArray<{ pattern: RegExp; rule: string }> = [
  {
    pattern: /\b(?:can|could|may|must|should|will) be (?:added|chosen|selected|set|opened|changed|turned|removed|edited|created|entered|cleared|deleted|renamed|saved|switched|found)\b/i,
    rule: "talk to the reader: say what they do (\"Add\", \"Select\"), not what can be done",
  },
  { pattern: /\b(?:Phoenix|which|that|it) then\b/i, rule: "no chains: split the step into its own sentence" },
  { pattern: /\bruns? out\b/i, rule: "use the everyday word: \"times out\", \"is used up\"" },
];

/** Problems with the voice of an English topic: phrasings the Writing rules ban. */
export function voiceProblems(body: string): string[] {
  const problems: string[] = [];
  const prose = body.replace(/^```[\s\S]*?^```/gm, "").replace(/`[^`\n]*`/g, "");
  for (const line of prose.split("\n")) {
    for (const { pattern, rule } of VOICE_PATTERNS) {
      const match = pattern.exec(line);
      if (match) problems.push(`"${match[0]}": ${rule}`);
    }
  }
  return problems;
}

/** The anchors of a topic's sections, with any heading that lacks one or repeats one. */
export function sectionAnchors(body: string): { anchors: string[]; problems: string[] } {
  const anchors: string[] = [];
  const problems: string[] = [];
  let inCode = false;
  for (const line of body.split("\n")) {
    if (/^```/.test(line)) { inCode = !inCode; continue; }
    if (inCode || !/^#{1,6}\s/.test(line)) continue;
    const h = HEADING.exec(line);
    if (!h) { problems.push(`heading without an anchor, or not ## or ###: "${line.trim()}"`); continue; }
    if (!ANCHOR.test(h[3])) { problems.push(`bad anchor ${JSON.stringify(h[3])}`); continue; }
    if (anchors.includes(h[3])) { problems.push(`anchor #${h[3]} is used twice`); continue; }
    anchors.push(h[3]);
  }
  return { anchors, problems };
}

/** Compile one language's topic files; `problems` lists everything that would break in the panel. */
export function compileGuide(
  language: string,
  files: ReadonlyArray<{ file: string; source: string }>,
  labelExists: (key: string) => boolean,
  fileExists: (path: string) => boolean = () => true,
): { bundle: GuideBundle; problems: string[] } {
  const topics: Record<string, GuideTopic> = {};
  const problems: string[] = [];
  const from = new Map<string, string>();
  for (const { file, source } of files) {
    const topic = parseTopic(source);
    if (typeof topic === "string") { problems.push(`${file}: ${topic}`); continue; }
    if (from.has(topic.id)) { problems.push(`${file}: topic ${topic.id} is also defined in ${from.get(topic.id)}`); continue; }
    from.set(topic.id, file);
    const { anchors, problems: headings } = sectionAnchors(topic.body);
    const voice = language === "en" ? voiceProblems(topic.body) : [];
    for (const p of [...headings, ...lengthProblems(topic.body), ...voice]) problems.push(`${file}: ${p}`);
    for (const path of topic.covers) if (!fileExists(path)) problems.push(`${file}: covers a file that does not exist: ${path}`);
    for (const anchor of Object.keys(topic.sectionKeywords ?? {})) {
      if (!anchors.includes(anchor)) problems.push(`${file}: sectionKeywords names a section that does not exist: #${anchor}`);
    }
    topics[topic.id] = {
      title: topic.title, body: topic.body, anchors,
      ...(topic.parent !== undefined ? { parent: topic.parent } : {}),
      ...(topic.order !== undefined ? { order: topic.order } : {}),
      ...(topic.keywords !== undefined ? { keywords: topic.keywords } : {}),
      ...(topic.sectionKeywords !== undefined ? { sectionKeywords: topic.sectionKeywords } : {}),
    };
  }
  for (const [id, topic] of Object.entries(topics)) {
    const file = from.get(id);
    if (topic.parent !== undefined && !topics[topic.parent]) problems.push(`${file}: parent ${JSON.stringify(topic.parent)} is not a topic`);
    for (const [, target, anchor] of topic.body.matchAll(GUIDE_LINK)) {
      const to = target ? topics[target] : topic;
      if (!to) problems.push(`${file}: link to unknown topic ${JSON.stringify(target)}`);
      else if (anchor !== undefined && !to.anchors.includes(anchor)) problems.push(`${file}: link to unknown section ${JSON.stringify(`${target || id}#${anchor}`)}`);
    }
    for (const text of [topic.title, topic.body]) {
      for (const [, key] of text.matchAll(LABEL)) {
        if (!LABEL_KEY.test(key) || !labelExists(key)) problems.push(`${file}: unknown label ${JSON.stringify(key)}`);
      }
    }
  }
  // A parent chain must end at a top-level topic.
  for (const id of Object.keys(topics)) {
    const seen = new Set<string>([id]);
    for (let at = topics[id].parent; at && topics[at]; at = topics[at].parent) {
      if (seen.has(at)) { problems.push(`${from.get(id)}: the parent chain loops through ${at}`); break; }
      seen.add(at);
    }
  }
  return { bundle: { format: GUIDE_FORMAT, language, topics }, problems };
}

/**
 * A translation has the same topics and sections as English, so every link
 * and every control's target works in every language; a topic not translated
 * yet is read in English.
 */
export function translationProblems(english: GuideBundle, other: GuideBundle): string[] {
  const problems: string[] = [];
  for (const [id, topic] of Object.entries(other.topics)) {
    const source = english.topics[id];
    if (!source) { problems.push(`${other.language}: topic ${id} has no English original`); continue; }
    if (source.anchors.join() !== topic.anchors.join()) problems.push(`${other.language}: topic ${id} has sections ${topic.anchors.join(", ") || "(none)"}, English has ${source.anchors.join(", ") || "(none)"}`);
    if ((source.parent ?? "") !== (topic.parent ?? "")) problems.push(`${other.language}: topic ${id} sits under ${topic.parent ?? "(top)"}, English under ${source.parent ?? "(top)"}`);
  }
  return problems;
}
