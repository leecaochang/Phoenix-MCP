/**
 * Search across the Guide (user, 2026-11-01): every section of every topic,
 * matched loosely enough to forgive a typo or a half-typed word. A section is
 * ranked by its own words: its heading counts most, then its keywords, then
 * its text, so the section that explains a term comes before the sections
 * that only share its page (user, 2026-11-01: "JEV" must lead to the JEV
 * section). The page's title and keywords let a section match a word it does
 * not use, but only alongside a word of its own. Every word typed must match,
 * so adding a word narrows the results. Words are compared without case or
 * accents; text without spaces between words (Chinese, Japanese) is matched
 * as written.
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

/** How well one typed word matches one word of the text: 3 exact, 2 the start of it, 1 near it, 0 not at all. */
function wordScore(typed: string, word: string): number {
  if (word === typed) return 3;
  if (typed.length >= 2 && word.startsWith(typed)) return 2;
  const allowed = typed.length >= 8 ? 2 : typed.length >= 4 ? 1 : 0;
  return allowed && distance(typed, word, allowed) <= allowed ? 1 : 0;
}

/** Scripts written without spaces between words. */
const UNSPACED = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Thai}]/u;

/** A piece of text ready to match: its words, and the text itself for unspaced scripts. */
interface Field { words: string[]; folded: string }
const field = (text: string): Field => ({ words: tokens(text), folded: fold(text) });

/** The best match for one typed word in a field. */
function bestIn(typed: string, f: Field): number {
  let best = 0;
  for (const w of f.words) {
    best = Math.max(best, wordScore(typed, w));
    if (best === 3) return 3;
  }
  // Text without spaces between words: a match anywhere counts.
  return best || (UNSPACED.test(typed) && f.folded.includes(typed) ? 1 : 0);
}

/** Rank the sections against a query; the best first, at most `limit`. Ties keep the order given. */
export function searchGuide(sections: readonly SearchSection[], query: string, limit = 30): SearchHit[] {
  const typed = tokens(query);
  if (!typed.length) return [];
  const phrase = typed.length > 1 ? typed.join(" ") : null;
  const hits: Array<SearchHit & { at: number }> = [];
  sections.forEach((section, at) => {
    // A page's opening is headed by the page's title; a section by its own heading.
    const head = field(section.anchor === null ? section.title : section.heading ?? "");
    const keys = field(section.keywords.join(" "));
    const text = field(section.text);
    const context = field(section.anchor === null ? "" : `${section.title} ${section.pageKeywords.join(" ")}`);
    let score = 0;
    let own = false;
    for (const word of typed) {
      const s = 3 * bestIn(word, head) + 2 * bestIn(word, keys) + bestIn(word, text);
      if (s) { score += s; own = true; continue; }
      if (!bestIn(word, context)) return;
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
