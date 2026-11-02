/**
 * The Help bar's standard (user, 2026-11-02), checked on the English catalog: one
 * sentence of about 15 words, 20 at most, and a second short one only for a default
 * or what is lost; none of the phrasings the Guide's Writing rules ban; and every Help
 * key present in the catalog. The keys are found where the source hands them to Help:
 * `data-help`, `helpRow`, a `help` prop or field, a menu item's `hint`, and the description
 * keys of the capability and persona lists.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "fs";
import { join, resolve } from "path";
import { VOICE_PATTERNS } from "../guide/compile";
import { flattenCatalog } from "../i18n";
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";

const SRC = resolve(__dirname, "..");
const catalog = flattenCatalog(en.panel as Record<string, unknown>);

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "__tests__" || name === "guide" && dir === SRC ? [] : sources(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
}

const KEY = String.raw`["']([A-Za-z]\w*(?:\.\w+)+)["']`;
/** Where a catalog key becomes Help text. */
const HELP_SITES = [
  String.raw`data-help=\{?${KEY}`,
  String.raw`data-help=\{[^}]*?\?\s*${KEY}\s*:\s*${KEY}`,
  String.raw`data-help-text=\{t(?:Plain)?\(${KEY}`,
  String.raw`helpRow\(t(?:Plain)?\(${KEY}`,
  String.raw`\bhelp=\{?t?(?:Plain)?\(?${KEY}`,
  String.raw`\bhelp:\s*t?\(?${KEY}`,
  String.raw`\b(?:helpKey|descriptionKey|completeHelp|rowHelp|allHelp):\s*${KEY}`,
  String.raw`\b(?:helpKey|completeHelp|rowHelp|allHelp)=${KEY}`,
].map((pattern) => new RegExp(pattern, "g"));
const MENU_HINT = new RegExp(String.raw`\bhint:\s*t\(${KEY}`, "g");

function helpKeys(): Set<string> {
  const keys = new Set<string>();
  for (const file of sources(SRC)) {
    const text = readFileSync(file, "utf8");
    for (const site of HELP_SITES) for (const match of text.matchAll(site)) for (const key of match.slice(1)) if (key) keys.add(key);
    // A menu item's hint reads in Help; other objects' hints, such as setup steps, are shown on screen.
    for (const line of text.split("\n").filter((l) => l.includes("onSelect"))) {
      for (const match of line.matchAll(MENU_HINT)) keys.add(match[1]);
    }
  }
  // Rows drawn from lists read their description in Help.
  for (const key of Object.keys(catalog)) if (/^(caps\.cap_\w+|personas\.\w+)\.description$/.test(key)) keys.add(key);
  return keys;
}

const plain = (text: string) => text.replace(/<[^>]+>/g, "");
const words = (text: string) => plain(text).split(/\s+/).filter(Boolean).length;
const sentences = (text: string) => plain(text).split(/(?<=[.!?])\s+/).filter((s) => s.trim()).length;

describe("Help text", () => {
  const keys = helpKeys();

  it("finds the Help keys the source uses", () => {
    // A floor, so a broken extraction fails instead of checking nothing.
    expect(keys.size).toBeGreaterThan(300);
  });

  it("names only keys the catalog has", () => {
    expect([...keys].filter((key) => !(key in catalog)).sort()).toEqual([]);
  });

  it("stays within one sentence of 20 words, plus a short second one at most", () => {
    const long = [...keys].filter((key) => typeof catalog[key] === "string")
      .filter((key) => words(catalog[key]) > 20 || sentences(catalog[key]) > 2)
      .map((key) => `${key}: ${words(catalog[key])} words, ${sentences(catalog[key])} sentences`);
    expect(long).toEqual([]);
  });

  it("avoids the phrasings the Writing rules ban", () => {
    const found = [...keys].filter((key) => typeof catalog[key] === "string").flatMap((key) =>
      VOICE_PATTERNS.filter(({ pattern }) => pattern.test(plain(catalog[key]))).map(({ rule }) => `${key}: ${rule}`));
    expect(found).toEqual([]);
  });
});
