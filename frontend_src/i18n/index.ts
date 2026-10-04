// Panel string catalog.
//
// Strings live in custom_components/phoenix_mcp/catalogs/{lang}.json under the
// "panel" key and arrive from Phoenix's own admin API, not HA's
// frontend/get_translations command (which reads only translations/): hassfest
// rejects any top-level key there outside HA's closed set of categories, which
// fails the HACS submission.
//
// helpers.panel_catalog serves English as the base with the requested language
// overlaid, so a key a translator has not reached resolves to English
// server-side and this module needs no fallback chain of its own. A translated
// string whose {placeholder} set differs from English is dropped in favour of
// English, which is why the interpolation syntax here is Python format style.
//
// Keep catalog loading independent from the admin API and its import graph.

import { localeNumber, localePluralCategory, setFormatLocale, validFormatLocale } from "./format";

// Date, time and number formatting lives in ./format and is re-exported here so
// every consumer has one import for "anything locale-dependent". The dependency
// runs one way only (format imports nothing), which is what lets loadTranslations
// adopt the locale below without a cycle.
export {
  compareStrings,
  getFormatLocale,
  localeCompactNumber,
  localeClock,
  localeDate,
  localeDateTime,
  localeDateTimeShort,
  localeNumber,
  localePluralCategory,
  setFormatLocale,
} from "./format";
export type { HassLocale } from "./format";

// Phoenix serves its own catalog, already flattened to dotted keys and already
// English-backed, so there is no prefix to strip and no fallback chain here.
const CATALOG_URL = "/api/phoenix-mcp/admin/catalog/";

export type Params = Record<string, string | number>;

/**
 * The locales this build ships, each labelled with its own endonym.
 *
 * The endonym is deliberately NOT a catalog entry. A language picker shows every
 * option in its own language so a reader can find theirs even when the UI is
 * currently in one they cannot read, which means the label is the same string in
 * every catalog and translating it would be wrong rather than merely redundant.
 *
 * Must stay in step with custom_components/phoenix_mcp/catalogs/*.json; a
 * contract test fails if it drifts.
 */
export const LANGUAGES: ReadonlyArray<{ code: string; endonym: string }> = [
  { code: "de", endonym: "Deutsch" },
  { code: "en", endonym: "English" },
  { code: "es", endonym: "Español" },
  { code: "fr", endonym: "Français" },
  { code: "it", endonym: "Italiano" },
  { code: "ja", endonym: "日本語" },
  { code: "ko", endonym: "한국어" },
  { code: "nl", endonym: "Nederlands" },
  { code: "pl", endonym: "Polski" },
  { code: "ru", endonym: "Русский" },
  { code: "zh-Hans", endonym: "中文（简体）" },
  { code: "zh-Hant", endonym: "繁體中文" },
];

/** Follow the Home Assistant user profile rather than overriding it. */
export const LANGUAGE_AUTO = "auto";

const SIMPLIFIED_CHINESE_REGIONS = new Set(["CN", "MY", "SG"]);
const TRADITIONAL_CHINESE_REGIONS = new Set(["HK", "MO", "TW"]);

function normalizeLanguageTag(language: unknown): { base: string; subtags: string[] } | null {
  if (typeof language !== "string") return null;
  const raw = language.trim().replace(/_/g, "-");
  if (!raw) return null;
  const parts = raw.split("-");
  if (parts.some((part) => !part)) return null;
  return { base: parts[0].toLowerCase(), subtags: parts.slice(1) };
}

function knownLanguage(language: unknown): string | null {
  const normalized = normalizeLanguageTag(language);
  if (!normalized) return null;
  const exact = [normalized.base, ...normalized.subtags].join("-").toLowerCase();
  const exactMatch = LANGUAGES.find((item) => item.code.toLowerCase() === exact);
  if (exactMatch) return exactMatch.code;

  if (normalized.base === "zh") {
    const script = normalized.subtags.find((part) => part.length === 4);
    const region = normalized.subtags.find((part) =>
      (part.length === 2 || part.length === 3) && /^[a-z0-9]+$/i.test(part));
    if (script) {
      const scriptCode = `zh-${script[0].toUpperCase()}${script.slice(1).toLowerCase()}`;
      const scriptMatch = LANGUAGES.find((item) => item.code.toLowerCase() === scriptCode.toLowerCase());
      if (scriptMatch) return scriptMatch.code;
    }
    if (region && TRADITIONAL_CHINESE_REGIONS.has(region.toUpperCase())) return "zh-Hant";
    if (!region || SIMPLIFIED_CHINESE_REGIONS.has(region.toUpperCase())) return "zh-Hans";
  }

  return LANGUAGES.find((item) => item.code.toLowerCase() === normalized.base)?.code ?? null;
}

/** Map regional and script variants to the closest catalog Phoenix ships. */
export function canonicalLanguage(language: unknown): string {
  return knownLanguage(language) ?? "en";
}

// Per browser, same shape as the "phx-theme" preference, and for the same
// reason: the two injected bundles load on arbitrary HA pages outside the panel
// and read it from the same origin without needing an API call.
const LANGUAGE_KEY = "phx-lang";

/** The stored override, or LANGUAGE_AUTO when unset or not a shipped language. */
export function getLanguagePreference(): string {
  let saved: string | null = null;
  try { saved = localStorage.getItem(LANGUAGE_KEY); } catch { /* storage blocked: use auto */ }
  return knownLanguage(saved) ?? LANGUAGE_AUTO;
}

/**
 * Persist the override and tell the other bundles. The panel repaints itself;
 * the global Agent Chat window and the injected buttons live outside this
 * element and cannot see the change any other way.
 */
export function setLanguagePreference(pref: string): void {
  try { localStorage.setItem(LANGUAGE_KEY, knownLanguage(pref) ?? pref); } catch { /* storage blocked: skip persistence */ }
  window.dispatchEvent(new CustomEvent("phx-language-changed"));
}

/** The language to actually fetch: the override, else HA's profile language. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function resolveLanguage(hass: any): string {
  const pref = getLanguagePreference();
  if (pref !== LANGUAGE_AUTO) return pref;
  return canonicalLanguage(hass?.language);
}

function formatLanguage(hass: any, resolved: string): string {
  if (getLanguagePreference() !== LANGUAGE_AUTO) return resolved;
  return validFormatLocale(hass?.locale?.language) ?? validFormatLocale(hass?.language) ?? resolved;
}

let strings: Record<string, string> = {};
let ready = false;
let loaded: string | null = null;
let requested: string | null = null;
let requestGeneration = 0;
const retryAfter = new Map<string, number>();
const CATALOG_RETRY_MS = 30_000;
const catalogRequests = new Map<string, Promise<Record<string, string> | null>>();

// The injected bundles can outlive one <home-assistant> root while HA replaces
// it during navigation or a frontend reload. Prefer the current root whenever
// it exposes HA's authenticated fetch, then fall back to the captured object.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function catalogHass(fallback: any): any {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const live = (document.querySelector("home-assistant") as any)?.hass;
    if (typeof live?.fetchWithAuth === "function") return live;
  } catch { /* use the caller's object */ }
  return fallback;
}

export function isI18nReady(): boolean {
  return ready;
}

/** Collapse a nested catalog object into dotted keys. */
export function flattenCatalog(obj: unknown, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  if (!obj || typeof obj !== "object") return out;
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") {
      out[path] = value;
    } else if (value && typeof value === "object") {
      Object.assign(out, flattenCatalog(value, path));
    }
  }
  return out;
}

/** Install a nested catalog synchronously. Used by tests and any sync path. */
export function primeTranslations(catalog: unknown, language = "en"): void {
  const resolved = canonicalLanguage(language);
  retryAfter.clear();
  strings = flattenCatalog(catalog);
  loaded = resolved;
  requested = resolved;
  setFormatLocale({}, resolved);
  requestGeneration += 1;
  ready = true;
}

/**
 * Fetch this panel's strings for one language.
 *
 * Marks the catalog ready even on failure: a panel rendering raw keys is a
 * visibly broken deploy, which beats a spinner that never resolves.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function loadTranslations(hass: any, language: string): Promise<boolean> {
  const resolved = canonicalLanguage(language || "en");
  if (Date.now() < (retryAfter.get(resolved) ?? 0)) return false;
  requested = resolved;
  const generation = ++requestGeneration;
  const formattingLocale = formatLanguage(hass, resolved);
  let request = catalogRequests.get(resolved);
  if (!request) {
    request = (async () => {
      const url = `${CATALOG_URL}${encodeURIComponent(resolved)}`;
      const requestHass = catalogHass(hass);
      if (typeof requestHass?.fetchWithAuth !== "function") return null;
      const res = await requestHass.fetchWithAuth(url);
      const body = res.ok ? await res.json() : null;
      const next: Record<string, string> = {};
      for (const [key, value] of Object.entries(body?.resources ?? {})) {
        if (typeof value === "string") next[key] = value;
      }
      return Object.keys(next).length > 0 ? next : null;
    })();
    catalogRequests.set(resolved, request);
    void request.finally(() => {
      if (catalogRequests.get(resolved) === request) catalogRequests.delete(resolved);
    }).catch(() => { /* the awaiting owner reports the failure */ });
  }
  try {
    const next = await request;
    if (next) retryAfter.delete(resolved);
    else retryAfter.set(resolved, Date.now() + CATALOG_RETRY_MS);
    if (generation === requestGeneration && next) {
      strings = next;
      loaded = resolved;
    }
  } catch (e) {
    retryAfter.set(resolved, Date.now() + CATALOG_RETRY_MS);
    if (generation === requestGeneration) {
      console.error("Phoenix MCP: could not load panel translations", e);
    }
  } finally {
    if (generation === requestGeneration) {
      setFormatLocale(hass, formattingLocale, resolved);
      ready = true;
    }
  }
  return generation === requestGeneration;
}

/**
 * Refetch only when the language that should be showing is not the one loaded.
 *
 * The stored preference is not the only way the answer changes: under "auto" it
 * follows the Home Assistant profile language, which an admin can change in
 * their HA profile without ever touching this panel and without a full page
 * load. Callers can run this on every hass update, since it is a comparison
 * until something actually differs.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function syncTranslations(hass: any): Promise<boolean> {
  const want = resolveLanguage(hass);
  if (want === loaded) {
    const superseded = requested !== null && requested !== want;
    requested = want;
    requestGeneration += 1;
    // Same language, but HA's own 24-hour or number-format preference may have
    // moved; adopting it is a repaint even though no catalog is refetched.
    return setFormatLocale(hass, formatLanguage(hass, want), want) || superseded;
  }
  return loadTranslations(hass, want);
}

/** True when the catalog has this key. */
export function hasMessage(key: string): boolean {
  return key in strings;
}

/** The stored template for a key, or the key itself when it is missing. */
export function rawMessage(key: string): string {
  return strings[key] ?? key;
}

export function interpolate(template: string, params?: Params): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? (typeof params[name] === "number" ? localeNumber(params[name]) : params[name]) : match,
  );
}

export function t(key: string, params?: Params): string {
  return interpolate(rawMessage(key), params);
}

/**
 * Plural form selected by the active catalog locale. A missing locale-specific
 * category falls back to "other", then "one", while count is always available
 * to the template as {count}.
 */
export function tn(base: string, count: number, params?: Params): string {
  const category = localePluralCategory(count);
  const keys = [`${base}.${category}`, `${base}.other`, `${base}.one`];
  const key = keys.find((candidate) => hasMessage(candidate)) ?? keys[0];
  return t(key, { count, ...params });
}
