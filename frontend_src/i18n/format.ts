// Shared presentation formatting for the panel and injected bundles.
// Phoenix's selected language supplies defaults; explicit HA profile choices
// override them. Machine values and protocol payloads never use these helpers.
// First day of week has no consumer: Phoenix has no calendar or week-based UI.
// This module must not import api.ts or index.ts (which installs its state).

/** Formatting preferences from HA's FrontendLocaleData. */
export interface HassLocale {
  language?: string;
  number_format?: string;
  time_format?: string;
  date_format?: string;
  time_zone?: string;
}

interface HassLike {
  language?: string;
  locale?: HassLocale;
  config?: { time_zone?: string };
}

let dateLocale: string | undefined;
let pluralLocale: string | undefined;
let numberLocale: string | string[] | undefined;
let numberGrouping = true;
let hour12: boolean | undefined;
let dateFormat = "language";
let timeZone: string | undefined;
let compactFormatter: Intl.NumberFormat | null = null;
let collator: Intl.Collator | null = null;
let pluralRules: Intl.PluralRules | null = null;
let preferenceSignature: string | null = null;
const dateFormatters = new Map<string, Intl.DateTimeFormat>();

/** Keep regional/script tags even when Phoenix has no matching translation. */
export function validFormatLocale(value: unknown): string | undefined {
  if (typeof value !== "string" || !value.trim()) return undefined;
  try {
    return Intl.getCanonicalLocales(value.trim().replace(/_/g, "-"))[0];
  } catch {
    return undefined;
  }
}

/** HA's numberFormatToLocale, including the ungrouped English `none` mode. */
function resolveNumberLocale(format: string | undefined, language: string | undefined): string | string[] | undefined {
  switch (format) {
    case "comma_decimal": return ["en-US", "en"];
    case "decimal_comma": return ["de", "es", "it"];
    case "space_comma": return ["fr", "sv", "cs"];
    case "quote_decimal": return ["de-CH"];
    case "none": return "en-US";
    case "system": return undefined;
    default: return language;
  }
}

function resolveHour12(format: string | undefined, language: string | undefined): boolean | undefined {
  if (format === "12") return true;
  if (format === "24") return false;
  if (format !== "language" && format !== "system") return undefined;
  return new Intl.DateTimeFormat(format === "system" ? undefined : language, {
    hour: "numeric",
  }).resolvedOptions().hour12;
}

function validTimeZone(value: unknown): string | undefined {
  if (typeof value !== "string" || !value) return undefined;
  try {
    return new Intl.DateTimeFormat("en", { timeZone: value }).resolvedOptions().timeZone;
  } catch {
    return undefined;
  }
}

/** Adopt presentation preferences; catalogLanguage controls grammar only. */
export function setFormatLocale(hass: unknown, language?: string, catalogLanguage?: string): boolean {
  const h = (hass ?? {}) as HassLike;
  const locale = h.locale ?? {};
  const inputSignature = JSON.stringify([language, catalogLanguage, h.language, locale.language, locale.number_format, locale.time_format, locale.date_format, locale.time_zone, h.config?.time_zone]);
  if (inputSignature === preferenceSignature) return false;
  preferenceSignature = inputSignature;
  const before = signature();
  dateLocale = validFormatLocale(language) ?? validFormatLocale(locale.language) ?? validFormatLocale(h.language);
  pluralLocale = validFormatLocale(catalogLanguage) ?? dateLocale;
  numberLocale = resolveNumberLocale(locale.number_format, dateLocale);
  numberGrouping = locale.number_format !== "none";
  hour12 = resolveHour12(locale.time_format, dateLocale);
  dateFormat = ["system", "DMY", "MDY", "YMD"].includes(locale.date_format ?? "")
    ? locale.date_format! : "language";
  const localZone = validTimeZone(new Intl.DateTimeFormat().resolvedOptions().timeZone);
  const serverZone = validTimeZone(h.config?.time_zone);
  timeZone = locale.time_zone === "server"
    ? serverZone ?? localZone ?? "UTC"
    : localZone ?? serverZone ?? "UTC";
  if (signature() === before) return false;
  compactFormatter = null;
  collator = null;
  pluralRules = null;
  dateFormatters.clear();
  return true;
}

// A format-only change must repaint without fetching another catalog.
function signature(): string {
  return JSON.stringify([dateLocale, pluralLocale, numberLocale, numberGrouping, hour12, dateFormat, timeZone]);
}

export function getFormatLocale(): string | undefined {
  return dateLocale;
}

function dateFormatter(kind: "date" | "shortDate" | "clock" | "time"): Intl.DateTimeFormat {
  let formatter = dateFormatters.get(kind);
  if (!formatter) {
    const isDate = kind === "date" || kind === "shortDate";
    const options: Intl.DateTimeFormatOptions = isDate
      ? { month: "numeric", day: "numeric", ...(kind === "date" ? { year: "numeric" } : {}) }
      : { hour: "2-digit", minute: "2-digit", ...(kind === "time" ? { second: "2-digit" } : {}),
          ...(hour12 === undefined ? {} : { hourCycle: hour12 ? "h12" : "h23" }) };
    formatter = new Intl.DateTimeFormat(isDate && dateFormat === "system" ? undefined : dateLocale, {
      ...options, timeZone,
    });
    dateFormatters.set(kind, formatter);
  }
  return formatter;
}

function formatDate(date: Date, short = false): string {
  const formatter = dateFormatter(short ? "shortDate" : "date");
  if (dateFormat === "language" || dateFormat === "system") return formatter.format(date);
  // Match HA's numeric-date override: retain locale digits and separators,
  // reorder the calendar fields. The compact form omits the year.
  const parts = formatter.formatToParts(date);
  const value = (type: string) => parts.find(part => part.type === type)?.value;
  const fields = dateFormat === "DMY" ? ["day", "month", "year"]
    : dateFormat === "YMD" ? ["year", "month", "day"] : ["month", "day", "year"];
  const separator = value("literal") ?? "/";
  const last = parts[parts.length - 1];
  const suffix = last?.type === "literal" && !(dateLocale === "bg" && dateFormat === "YMD") ? last.value : "";
  return fields.filter(field => !short || field !== "year").map(value).join(separator) + suffix;
}

/** Date only, in the selected zone and calendar order. */
export function localeDate(iso: string | number | Date): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "Invalid Date" : formatDate(date);
}

/** Numeric date and time, independently respecting both profile preferences. */
export function localeDateTime(iso: string | number | Date): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "Invalid Date" : `${formatDate(date)}, ${dateFormatter("time").format(date)}`;
}

/** Month, day and time for the narrow audit table. */
export function localeDateTimeShort(iso: string | number | Date): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "Invalid Date" : `${formatDate(date, true)}, ${dateFormatter("clock").format(date)}`;
}

/** Wall clock, hours and minutes only. */
export function localeClock(ts: string | number | Date): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? "Invalid Date" : dateFormatter("clock").format(date);
}

/** A plain number, grouped per the operator's HA number-format preference. */
export function localeNumber(value: number): string {
  // Preserve numeric evidence in interpolated summaries; Intl's default of
  // three fractional digits can otherwise turn a small nonzero value into 0.
  return value.toLocaleString(numberLocale, { useGrouping: numberGrouping, maximumSignificantDigits: 21 });
}

/** CLDR plural category for a count in the active panel language. */
export function localePluralCategory(value: number): Intl.LDMLPluralRule {
  if (pluralRules === null) pluralRules = new Intl.PluralRules(pluralLocale);
  return pluralRules.select(value);
}

/**
 * A large count in the locale's own short form: 48.2K, 4.8万, 1,2 Mio.
 *
 * Hand-rolled "48.2k" was wrong outside English in a way that is easy to miss,
 * because it is not a translation problem: Chinese groups by 10^4 (万), so no
 * amount of translating the suffix produces the right NUMBER. CLDR knows the
 * grouping as well as the suffix, which is why this delegates rather than
 * appending a localized unit to an English-scaled figure. Note some locales
 * legitimately decline to abbreviate thousands at all (German renders 48.200),
 * so this can be wider than the English form.
 *
 * Zero and non-finite collapse to "0" so a missing usage counter reads as
 * nothing rather than as NaN.
 */
export function localeCompactNumber(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0";
  if (compactFormatter === null) {
    compactFormatter = new Intl.NumberFormat(numberLocale, {
      notation: "compact",
      maximumFractionDigits: 1,
      useGrouping: numberGrouping,
    });
  }
  return compactFormatter.format(value);
}

/**
 * Compare two display strings for sorting, in the panel's language.
 *
 * A bare a.localeCompare(b) sorts by the BROWSER's language, so a panel in one
 * language ordered its lists by another's rules; the difference is invisible in
 * English and immediate in any language whose alphabet or accent handling
 * differs. Collation options are deliberately left at their defaults: switching
 * on numeric collation would reorder existing lists ("Light 10" before
 * "Light 2"), which is a separate decision from localizing them.
 */
export function compareStrings(a: string, b: string): number {
  if (collator === null) collator = new Intl.Collator(dateLocale);
  return collator.compare(a, b);
}
