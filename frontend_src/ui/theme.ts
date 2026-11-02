/** Resolve the light or dark theme class, and the color theme, for every Phoenix host element. */

export type ThemePreference = "light" | "dark" | "auto";
export type ThemeClass = "phx-theme-dark" | "phx-theme-light";

const STORAGE_KEY = "phx-theme";

/** The operator's per-browser choice from Settings; "auto" when unset or unreadable. */
export function readThemePreference(): ThemePreference {
  let saved: string | null = null;
  try { saved = localStorage.getItem(STORAGE_KEY); } catch { /* storage blocked: use auto */ }
  return saved === "light" || saved === "dark" ? saved : "auto";
}

/**
 * An explicit Phoenix choice wins; "auto" follows Home Assistant's own dark-mode
 * flag. Null leaves the stylesheet's prefers-color-scheme rule in charge, for a
 * page that has not reported its mode yet.
 */
export function resolveThemeClass(
  darkMode: boolean | undefined,
  preference: ThemePreference = readThemePreference(),
): ThemeClass | null {
  if (preference === "light") return "phx-theme-light";
  if (preference === "dark") return "phx-theme-dark";
  if (darkMode === true) return "phx-theme-dark";
  if (darkMode === false) return "phx-theme-light";
  return null;
}

/** The color themes, in Settings order; "spartan" is the default and sets no attribute. */
export const PALETTES = ["spartan", "wayfinding", "framed", "mist", "office", "aubergine"] as const;
export type Palette = (typeof PALETTES)[number];

const PALETTE_KEY = "phx-palette";
const CHANGED = "phx-theme-changed";

/** The operator's per-browser color theme; "spartan" when unset, unknown or unreadable. */
export function readPalette(): Palette {
  let saved: string | null = null;
  try { saved = localStorage.getItem(PALETTE_KEY); } catch { /* storage blocked: use the default */ }
  return (PALETTES as readonly string[]).includes(saved ?? "") ? saved as Palette : "spartan";
}

/** Save a color theme for this browser and tell every Phoenix host to repaint. */
export function setPalette(palette: Palette): void {
  try { localStorage.setItem(PALETTE_KEY, palette); } catch { /* storage blocked: this page only */ }
  window.dispatchEvent(new CustomEvent(CHANGED));
}

/** Follow theme changes made on this page, or in another tab of this browser. */
export function subscribeTheme(listener: () => void): () => void {
  const storage = (e: StorageEvent) => { if (e.key === PALETTE_KEY || e.key === STORAGE_KEY) listener(); };
  window.addEventListener(CHANGED, listener);
  window.addEventListener("storage", storage);
  return () => { window.removeEventListener(CHANGED, listener); window.removeEventListener("storage", storage); };
}

/** Replace whichever theme class an element carries with the resolved one, and mark its color theme. */
export function applyThemeClass(element: Element, themeClass: ThemeClass | null, palette: Palette = readPalette()): void {
  element.classList.remove("phx-theme-dark", "phx-theme-light");
  if (themeClass) element.classList.add(themeClass);
  if (palette === "spartan") element.removeAttribute("data-phx-palette");
  else element.setAttribute("data-phx-palette", palette);
}
