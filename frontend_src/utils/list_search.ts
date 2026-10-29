import { foldForSearch } from "../i18n/format";

/**
 * Match every search word against the text in a loaded list. A word has to
 * appear in the text as it was typed: matching its letters in order anywhere
 * in the text, as this did, made nonsense like "ssss" find rows whose text
 * happens to hold four s's (user, 2026-10-29). Several words all have to
 * appear, in any order and in any part of the text the list searches.
 */
export function matchesListSearch(text: string, query: string): boolean {
  const normalized = foldForSearch(text);
  return foldForSearch(query).trim().split(/\s+/u)
    .every((word) => !word || normalized.includes(word));
}
