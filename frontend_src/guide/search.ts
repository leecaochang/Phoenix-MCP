/**
 * Search across the Guide (user, 2026-11-01): every section of every topic,
 * matched loosely enough to forgive a typo or a half-typed word. A section is
 * ranked by its own words: its heading counts most, then its keywords, then
 * its text, so the section that explains a term comes before the sections
 * that only share its page (user, 2026-11-01: "JEV" must lead to the JEV
 * section). The page's title and keywords let a section match a word it does
 * not use, but only alongside a word of its own. Every word typed must match,
 * so adding a word narrows the results. A near miss counts only for a word
 * the Guide has nowhere, so "meta" finds Meta and not every page about MESA
 * (user, 2026-11-03). Words are compared without case or accents. Text
 * written without spaces (Chinese, Japanese) is split into words by the
 * browser (user, 2026-11-03), but only to find where words begin and end: the
 * browser's dictionary breaks terms such as 密钥 into single characters, so
 * what the reader types is matched as written, best when it covers whole
 * words, and in the middle of a word only when it is found nowhere better.
 */

import { HEADING } from "./compile";

/** One searchable section: a topic's opening text has no anchor. */
export interface SearchSection {
  topic: string;
  anchor: string | null;
  /** The topic's title and the section's heading, as shown. */
  title: string;
  heading: string | null;
  text: string;
  /** The section's own keywords: the page's for its opening, the section's for the others. */
  keywords: string[];
  /** The page's keywords, which every section shares. */
  pageKeywords: string[];
}

export interface SearchHit {
  section: SearchSection;
  score: number;
  /** A line of the section's text around the first match. */
  snippet: string;
}

/** Lower-case, without accents. */
export function fold(s: string): string {
  return s.normalize("NFD").replace(/\p{M}+/gu, "").toLowerCase();
}

const tokens = (s: string): string[] => fold(s).split(/[^\p{L}\p{N}]+/u).filter(Boolean);

/** Edits between two words, stopping early once past `max`. */
function distance(a: string, b: string, max: number): number {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    let best = i;
    for (let j = 1; j <= b.length; j++) {
      row[j] = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      best = Math.min(best, row[j]);
    }
    if (best > max) return max + 1;
    prev = row;
  }
  return prev[b.length];
}

/** How well one typed word matches one word of the text: 3 exact, 2 the start of it, 1 near it (when allowed), 0 not at all. */
function wordScore(typed: string, word: string, near: boolean): number {
  if (word === typed) return 3;
  if (typed.length >= 2 && word.startsWith(typed)) return 2;
  const allowed = !near ? 0 : typed.length >= 8 ? 2 : typed.length >= 4 ? 1 : 0;
  return allowed && distance(typed, word, allowed) <= allowed ? 1 : 0;
}

/** Scripts written without spaces between words. */
const UNSPACED = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Thai}]/u;

/** The browser's word splitter, where it has one; without it unspaced text matches anywhere. */
interface WordSegment { segment: string; index: number; isWordLike?: boolean }
interface WordSegmenter { segment(text: string): Iterable<WordSegment> }
// Declared here: the project's TypeScript library predates Intl.Segmenter, which every supported browser has.
const Segmenter = (Intl as unknown as { Segmenter?: new (locale: undefined, options: { granularity: "word" }) => WordSegmenter }).Segmenter;
const SEGMENTER = Segmenter ? new Segmenter(undefined, { granularity: "word" }) : null;

/** A piece of text ready to match: its words, the text itself, and for unspaced scripts where its words begin and end. */
interface Field { words: string[]; folded: string; starts?: Set<number>; ends?: Set<number> }
function field(text: string): Field {
  const folded = fold(text);
  const f: Field = { words: tokens(text), folded };
  if (SEGMENTER && UNSPACED.test(folded)) {
    f.starts = new Set();
    f.ends = new Set();
    for (const { segment, index, isWordLike } of SEGMENTER.segment(folded)) {
      if (isWordLike) { f.starts.add(index); f.ends.add(index + segment.length); }
    }
  }
  return f;
}

/** Unspaced text typed: 3 covering whole words, 2 from the start of one, 1 from inside one, 0 not found. */
function runScore(typed: string, f: Field): number {
  let best = 0;
  for (let at = f.folded.indexOf(typed); at >= 0 && best < 3; at = f.folded.indexOf(typed, at + 1)) {
    if (!f.starts || !f.ends) return 1;
    best = Math.max(best, f.starts.has(at) ? (f.ends.has(at + typed.length) ? 3 : 2) : 1);
  }
  return best;
}

/** The best match for one typed word in a field. */
function bestIn(typed: string, f: Field, near = true): number {
  let best = 0;
  for (const w of f.words) {
    best = Math.max(best, wordScore(typed, w, near));
    if (best === 3) return 3;
  }
  if (!UNSPACED.test(typed)) return best;
  // Inside a word counts like a near miss; without the splitter any match counts, as before.
  const run = runScore(typed, f);
  return Math.max(best, run === 1 && f.starts && !near ? 0 : run);
}

/** Each section's fields, built once: splitting the text into words is the slow part. */
interface Fields { head: Field; keys: Field; text: Field; context: Field }
const FIELDS = new WeakMap<SearchSection, Fields>();
function fieldsOf(section: SearchSection): Fields {
  let f = FIELDS.get(section);
  if (!f) {
    f = {
      // A page's opening is headed by the page's title; a section by its own heading.
      head: field(section.anchor === null ? section.title : section.heading ?? ""),
      keys: field(section.keywords.join(" ")),
      text: field(section.text),
      context: field(section.anchor === null ? "" : `${section.title} ${section.pageKeywords.join(" ")}`),
    };
    FIELDS.set(section, f);
  }
  return f;
}

/** Rank the sections against a query; the best first, at most `limit`. Ties keep the order given. */
export function searchGuide(sections: readonly SearchSection[], query: string, limit = 30): SearchHit[] {
  const typed = tokens(query);
  if (!typed.length) return [];
  const phrase = typed.length > 1 ? typed.join(" ") : null;
  const hits: Array<SearchHit & { at: number }> = [];
  const fields = sections.map(fieldsOf);
  // Near misses, and matches inside an unspaced word, only for a word written nowhere in the Guide,
  // as it is or as the start of a word.
  const near = typed.map((word) => !fields.some((f) => [f.head, f.keys, f.text, f.context].some((x) => bestIn(word, x, false) >= 2)));
  sections.forEach((section, at) => {
    const { head, keys, text, context } = fields[at];
    let score = 0;
    let own = false;
    for (const [i, word] of typed.entries()) {
      const s = 3 * bestIn(word, head, near[i]) + 2 * bestIn(word, keys, near[i]) + bestIn(word, text, near[i]);
      if (s) { score += s; own = true; continue; }
      if (!bestIn(word, context, near[i])) return;
      score += 1;
    }
    if (!own) return;
    // The words typed together, as written in the heading or the text.
    if (phrase && head.words.join(" ").includes(phrase)) score += 6;
    else if (phrase && text.words.join(" ").includes(phrase)) score += 2;
    hits.push({ section, score, snippet: snippet(section.text, typed), at });
  });
  return hits.sort((a, b) => b.score - a.score || a.at - b.at).slice(0, limit).map(({ section, score, snippet }) => ({ section, score, snippet }));
}

/** About a line of text around the first typed word found, or the start of the text. */
function snippet(text: string, typed: readonly string[]): string {
  const folded = fold(text);
  const at = typed.map((w) => folded.indexOf(w)).filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? 0;
  const start = Math.max(0, text.lastIndexOf(" ", Math.max(0, at - 40)) + 1);
  const cut = text.slice(start, start + 140);
  return `${start > 0 ? "..." : ""}${cut}${start + 140 < text.length ? "..." : ""}`;
}

/**
 * A topic split into its sections as plain text: Markdown marks and link
 * targets removed, and `{{label:key}}` placeholders replaced by `label`.
 * `keywords` are the page's; `sectionKeywords` those of single sections, by anchor.
 */
export function topicSections(
  id: string, title: string, body: string, keywords: readonly string[], label: (key: string) => string,
  sectionKeywords: Readonly<Record<string, readonly string[]>> = {},
): SearchSection[] {
  const plain = (s: string) => s
    .replace(/\{\{label:([^}]*)\}\}/g, (_m, key: string) => label(key))
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`>|]/g, " ")
    .replace(/^\s*(?:[-+]|\d+\.)\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
  const shownTitle = plain(title);
  const sections: SearchSection[] = [];
  let anchor: string | null = null;
  let heading: string | null = null;
  let lines: string[] = [];
  const push = () => {
    const own = anchor === null ? keywords : sectionKeywords[anchor] ?? [];
    sections.push({ topic: id, anchor, title: shownTitle, heading, text: plain(lines.join(" ")), keywords: [...own], pageKeywords: [...keywords] });
    lines = [];
  };
  for (const line of body.split("\n")) {
    const h = HEADING.exec(line);
    if (!h) { lines.push(line); continue; }
    push();
    anchor = h[3];
    heading = plain(h[2]);
  }
  push();
  // An opening without text of its own still finds the topic by its title.
  return sections.filter((s, i) => i === 0 || s.text || s.heading);
}
