/** Resolve the light or dark theme class for every Phoenix host element. */

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

/** Replace whichever theme class an element carries with the resolved one. */
export function applyThemeClass(element: Element, themeClass: ThemeClass | null): void {
  element.classList.remove("phx-theme-dark", "phx-theme-light");
  if (themeClass) element.classList.add(themeClass);
}
