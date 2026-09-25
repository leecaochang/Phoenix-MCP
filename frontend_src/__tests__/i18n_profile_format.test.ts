import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  LANGUAGES, getFormatLocale, interpolate, loadTranslations, localeClock,
  localeCompactNumber, localeDate, localeDateTime, localeDateTimeShort,
  localeNumber, primeTranslations, resolveLanguage, setFormatLocale,
  setLanguagePreference, syncTranslations, tn,
} from "../i18n";
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";

const ISO = "2026-03-04T22:05:06Z";
const profile = (locale: Record<string, string> = {}, zone = "UTC") => ({
  language: "en-US",
  locale: { language: "en-US", time_zone: "server", time_format: "24", date_format: "language", number_format: "language", ...locale },
  config: { time_zone: zone },
  fetchWithAuth: vi.fn(async () => ({ ok: true, json: async () => ({ resources: {
    "items.one": "{count} item", "items.other": "{count} items",
  } }) })),
});

beforeEach(() => {
  localStorage.clear();
  primeTranslations(en.panel);
});
afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  primeTranslations(en.panel);
});

describe("HA date and time preferences", () => {
  it.each([
    ["DMY", "4/3/2026", "4/3"],
    ["MDY", "3/4/2026", "3/4"],
    ["YMD", "2026/3/4", "3/4"],
  ])("honors %s in full dates and narrow audit timestamps", (format, full, short) => {
    setFormatLocale(profile({ date_format: format }));
    expect(localeDate(ISO)).toBe(full);
    expect(localeDateTime(ISO)).toBe(`${full}, 22:05:06`);
    expect(localeDateTimeShort(ISO)).toBe(`${short}, 22:05`);
  });

  it("uses the server zone for dates, full/short timestamps, and chat clocks", () => {
    setFormatLocale(profile({}, "Asia/Tokyo"));
    expect(localeDate(ISO)).toBe("3/5/2026");
    expect(localeDateTime(ISO)).toBe("3/5/2026, 07:05:06");
    expect(localeDateTimeShort(ISO)).toBe("3/5, 07:05");
    expect(localeClock(ISO)).toBe("07:05");
    expect(setFormatLocale(profile({}, "America/New_York"))).toBe(true);
    expect(localeDateTime(ISO)).toBe("3/4/2026, 17:05:06");
  });

  it("follows DST and uses midnight 00 in explicit 24-hour mode", () => {
    setFormatLocale(profile({}, "America/New_York"));
    expect(localeClock("2026-03-08T06:59:00Z")).toBe("01:59");
    expect(localeClock("2026-03-08T07:00:00Z")).toBe("03:00");
    expect(localeClock("2026-11-01T05:30:00Z")).toBe("01:30");
    expect(localeClock("2026-11-01T06:30:00Z")).toBe("01:30");
    expect(localeClock("2026-03-04T05:00:00Z")).toBe("00:00");
  });

  it("uses browser time for local mode regardless of server configuration", () => {
    setFormatLocale(profile({ time_zone: "local" }, "Pacific/Kiritimati"));
    expect(localeClock(ISO)).toBe(new Intl.DateTimeFormat("en-US", {
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).format(new Date(ISO)));
  });

  it("handles invalid locale and server-zone preferences without throwing", () => {
    setFormatLocale(profile({ language: "not a locale", time_format: "24" }, "Invalid/Zone"));
    expect(getFormatLocale()).toBe("en-US");
    expect(localeClock(ISO)).toMatch(/^\d{2}:\d{2}$/);
    for (const format of [localeDate, localeDateTime, localeDateTimeShort, localeClock]) {
      expect(format("not a date")).toBe("Invalid Date");
    }
  });

  it("keeps system date formatting separate from Chinese time labels and explicit hours", () => {
    setFormatLocale(profile({ date_format: "system", time_format: "12" }), "zh-Hans");
    const expectedDate = new Intl.DateTimeFormat(undefined, {
      year: "numeric", month: "numeric", day: "numeric", timeZone: "UTC",
    }).format(new Date(ISO));
    expect(localeDate(ISO)).toBe(expectedDate);
    expect(localeDateTime(ISO)).toBe(`${expectedDate}, 下午10:05:06`);
  });

  it("uses browser hour convention for system time and Phoenix's language for language time", () => {
    setFormatLocale(profile({ time_format: "system" }), "zh-Hans");
    const use12 = new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions().hour12;
    expect(localeClock(ISO)).toBe(new Intl.DateTimeFormat("zh-Hans", {
      hour: "2-digit", minute: "2-digit", hourCycle: use12 ? "h12" : "h23", timeZone: "UTC",
    }).format(new Date(ISO)));
    setFormatLocale(profile({ time_format: "language" }), "zh-Hans");
    expect(localeClock(ISO)).toBe("22:05");
  });

  it.each(LANGUAGES.map(({ code }) => code))("applies explicit preferences across %s", language => {
    setFormatLocale(profile({ date_format: "DMY", time_format: "24", number_format: "decimal_comma" }), language);
    const parts = new Intl.DateTimeFormat(language, {
      year: "numeric", month: "numeric", day: "numeric", timeZone: "UTC",
    }).formatToParts(new Date(ISO));
    const numericParts = parts.filter(p => ["day", "month", "year"].includes(p.type));
    const actual = localeDate(ISO);
    const day = numericParts.find(p => p.type === "day")!.value;
    const year = numericParts.find(p => p.type === "year")!.value;
    expect(actual.startsWith(day)).toBe(true);
    expect(actual.includes(year)).toBe(true);
    expect(localeNumber(1234.5)).toBe("1.234,5");
    const expectedClock = new Intl.DateTimeFormat(language, {
      hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC",
    }).format(new Date(ISO));
    expect(localeClock(ISO)).toBe(expectedClock);
  });
});

describe("catalog and formatting independence", () => {
  it("uses Chinese defaults with an English HA profile, retaining explicit choices", async () => {
    setLanguagePreference("zh-Hans");
    const hass = profile({ time_format: "language", number_format: "language" });
    await syncTranslations(hass);
    expect(getFormatLocale()).toBe("zh-Hans");
    expect(localeDate(ISO)).toBe("2026/3/4");
    expect(localeClock(ISO)).toBe("22:05");
    expect(localeCompactNumber(48200)).toBe("4.8万");
    hass.locale = { ...hass.locale, date_format: "DMY", time_format: "12", number_format: "decimal_comma" };
    expect(await syncTranslations(hass)).toBe(true);
    expect(localeDate(ISO)).toBe("4/3/2026");
    expect(localeClock(ISO)).toBe("下午10:05");
    expect(localeNumber(1234.5)).toBe("1.234,5");
    expect(hass.fetchWithAuth).toHaveBeenCalledTimes(1);
  });

  it("retains unsupported HA locale formatting but uses English fallback grammar", async () => {
    const hass = profile({ language: "ar-EG", number_format: "language" });
    hass.language = "ar-EG";
    expect(resolveLanguage(hass)).toBe("en");
    await loadTranslations(hass, resolveLanguage(hass));
    expect(getFormatLocale()).toBe("ar-EG");
    expect(localeNumber(1234)).toBe("١٬٢٣٤");
    expect(tn("items", 1)).toBe("١ item");
    expect(tn("items", 2)).toBe("٢ items");
    expect(tn("items", 0)).toBe("٠ items");
  });

  it("retains regional formatting with the same English catalog", async () => {
    const hass = profile({ language: "en-US" });
    await loadTranslations(hass, "en");
    expect(localeDate(ISO)).toBe("3/4/2026");
    hass.language = "en-GB";
    hass.locale.language = "en-GB";
    expect(await syncTranslations(hass)).toBe(true);
    expect(localeDate(ISO)).toBe("04/03/2026");
    expect(hass.fetchWithAuth).toHaveBeenCalledTimes(1);
  });

  it("repaints for date and zone changes without a catalog request", async () => {
    const hass = profile({ date_format: "MDY" });
    await loadTranslations(hass, "en");
    hass.locale.date_format = "YMD";
    expect(await syncTranslations(hass)).toBe(true);
    expect(localeDate(ISO)).toBe("2026/3/4");
    hass.config.time_zone = "Asia/Tokyo";
    expect(await syncTranslations(hass)).toBe(true);
    expect(localeDateTime(ISO)).toBe("2026/3/5, 07:05:06");
    expect(await syncTranslations(hass)).toBe(false);
    expect(hass.fetchWithAuth).toHaveBeenCalledTimes(1);
  });

  it("does not let an older catalog request restore stale date/zone preferences", async () => {
    let finish!: (value: unknown) => void;
    const older = { ...profile({ date_format: "MDY" }), fetchWithAuth: () => new Promise(resolve => { finish = resolve; }) };
    const pending = loadTranslations(older, "de");
    const latest = profile({ date_format: "YMD" }, "Asia/Tokyo");
    await loadTranslations(latest, "en");
    finish({ ok: true, json: async () => ({ resources: { probe: "alt" } }) });
    expect(await pending).toBe(false);
    expect(localeDateTime(ISO)).toBe("2026/3/5, 07:05:06");
  });
});

describe("human-facing numeric interpolation", () => {
  it("preserves small values and precision while changing decimal separators", () => {
    setFormatLocale(profile({ number_format: "decimal_comma" }));
    expect(interpolate("{value}", { value: 0.00001 })).toBe("0,00001");
    expect(localeNumber(1.23456789)).toBe("1,23456789");
  });

  it.each([
    ["comma_decimal", "1,234.5"], ["decimal_comma", "1.234,5"],
    ["space_comma", "1\u202f234,5"], ["none", "1234.5"],
  ])("formats numeric parameters using %s without rewriting strings", (format, expected) => {
    setFormatLocale(profile({ number_format: format }));
    expect(interpolate("{count} / {id}", { count: 1234.5, id: "1234.5" })).toBe(`${expected} / 1234.5`);
    expect(interpolate("{missing}", {})).toBe("{missing}");
  });

  it("selects plural grammar using the raw count, then formats the count for display", () => {
    primeTranslations({ items: { one: "{count} item", other: "{count} items" } });
    setFormatLocale(profile({ number_format: "decimal_comma" }));
    expect(tn("items", 1234)).toBe("1.234 items");
    expect(tn("items", 1)).toBe("1 item");
  });
});
