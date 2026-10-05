/** Match each search word loosely against the text in a loaded list. */
export function matchesListSearch(text: string, query: string): boolean {
  const normalized = text.normalize("NFKC").toLowerCase();
  return query.normalize("NFKC").toLowerCase().trim().split(/\s+/u).every(word => {
    let cursor = 0;
    for (const character of word) {
      const index = normalized.indexOf(character, cursor);
      if (index < 0) return false;
      cursor = index + character.length;
    }
    return true;
  });
}
