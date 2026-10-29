// Localization repairs: text the panel used to show in English or with the
// wrong grammar, and the language attributes of Phoenix roots. Each case primes
// a small catalog and asserts the rendered words, not just that a key exists.

import { describe, it, expect, afterEach } from "vitest";
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";
import { foldForSearch, primeTranslations, rawMessage, registerLanguageRoot, t, tn, tnKey } from "../i18n";
import { chatNoticeText, failureDetailText } from "../utils/chat_notice";
import { dashboardDisplayName, withCardLocation, withDashboardLabel, withLocaleSizes } from "../utils/dashboard_names";
import { approvalParams } from "../utils/approval_summary";
import { matchesListSearch } from "../utils/list_search";

afterEach(() => primeTranslations(en.panel));

describe("plural selection", () => {
  it("chooses the Polish few form that English lacks", () => {
    primeTranslations({ x: { one: "{count} plik", few: "{count} pliki", many: "{count} plików", other: "{count} pliku" } }, "pl");
    expect(tn("x", 1)).toBe("1 plik");
    expect(tn("x", 3)).toBe("3 pliki");
    expect(tn("x", 5)).toBe("5 plików");
    expect(tnKey("x", 22)).toBe("x.few");
  });

  it("falls back to other when a category is missing", () => {
    primeTranslations({ x: { one: "a", other: "b" } }, "ru");
    expect(tnKey("x", 3)).toBe("x.other");
  });
});

describe("unknown label-map values", () => {
  it("render as empty text instead of throwing", () => {
    const missing = ({} as Record<string, string>)["unknown_status"];
    expect(rawMessage(missing)).toBe("");
    expect(t(missing, { count: 1 })).toBe("");
  });
});

describe("language roots", () => {
  it("carry the displayed catalog's lang and a left-to-right dir", () => {
    const root = document.createElement("div");
    document.body.appendChild(root);
    registerLanguageRoot(root);
    primeTranslations({ a: "b" }, "ja");
    expect(root.lang).toBe("ja");
    expect(root.dir).toBe("ltr");
    primeTranslations({ a: "b" }, "zh-Hant");
    expect(root.lang).toBe("zh-Hant");
    root.remove();
  });
});

describe("dashboard names", () => {
  it("maps the fixed English names only for dashboard targets", () => {
    primeTranslations({ changes: { defaultDashboard: "Standard-Dashboard", energyDashboard: "Energie-Dashboard" } }, "de");
    expect(dashboardDisplayName("(default)", "dashboard")).toBe("Standard-Dashboard");
    expect(dashboardDisplayName("(default dashboard)", "dashboard")).toBe("Standard-Dashboard");
    expect(dashboardDisplayName("Energy dashboard", "energy")).toBe("Energie-Dashboard");
    // A helper genuinely named "Default" is not a dashboard sentinel.
    expect(dashboardDisplayName("Default", "helper")).toBe("Default");
    expect(withDashboardLabel({ label: "(default dashboard)" }, "dashboard")).toEqual({ label: "Standard-Dashboard" });
  });
});

describe("change summaries", () => {
  it("rebuild the card location from translated parts", () => {
    primeTranslations({
      common: { listSeparator: "、" },
      changes: { cardNoun: "カード" },
      version: { "loc.view": "ビュー {index}", "loc.card": "カード {index}" },
    }, "ja");
    const params = withCardLocation("version.card.edited", { subject: "card", where: "view 0, card 2", view_index: 0, card_index: 2 });
    expect(params).toMatchObject({ subject: "カード", where: "ビュー 0、カード 2" });
    // A known card type is an identifier and stays as it is.
    expect(withCardLocation("version.card.added", { view_index: 1, card_type: "tile" })).toMatchObject({ subject: "tile" });
    // An older record without indices keeps its stored English.
    expect(withCardLocation("version.card.added", { subject: "card", where: "view 0" })).toEqual({ subject: "card", where: "view 0" });
  });

  it("format sizes for the reader's locale when the byte counts were stored", () => {
    primeTranslations({}, "de");
    const params = withLocaleSizes("version.size.was", { size: "12.3 KB", before: "1.0 KB", size_bytes: 12595, before_bytes: 1024 });
    expect(params?.size).toBe("12,3 kB");
    expect(params?.before).toBe("1 kB");
  });
});

describe("approval params", () => {
  it("translate blueprint domains and Energy source types", () => {
    primeTranslations({
      approvalSummary: { "named.kind.automation": "Automation" },
      approvals: { energySolar: "Solar" },
    }, "de");
    expect(approvalParams({ summary_key: "diff.blueprint.delete", summary_params: { domain: "automation", rel: "x.yaml" } }))
      .toMatchObject({ domain: "Automation" });
    expect(approvalParams({ summary_key: "diff.edit_energy_config.set_source.create", summary_params: { source_type: "solar", fields: "" } }))
      .toMatchObject({ source_type: "Solar" });
  });
});

describe("chat notices", () => {
  it("translate a stored error by its code and keep a provider's own words", () => {
    primeTranslations({ agentchat: { notice: {
      upstream: "Der Anbieter ist vorübergehend nicht verfügbar.",
      provider_retry_exhausted: "Keine Antwort nach etwa {seconds}s. {detail}",
    } } }, "de");
    expect(chatNoticeText("upstream", "Provider request failed (HTTP 503).")).toBe("Der Anbieter ist vorübergehend nicht verfügbar.");
    expect(chatNoticeText("provider_retry_exhausted", "English", { seconds: 30, detail: "Provider request failed (HTTP 503).", detail_code: "upstream" }))
      .toBe("Keine Antwort nach etwa 30s. Der Anbieter ist vorübergehend nicht verfügbar.");
    expect(failureDetailText("overloaded_error: try later", "")).toBe("overloaded_error: try later");
    expect(chatNoticeText("not_a_code", "Relayed provider text")).toBe("Relayed provider text");
  });
});

describe("search folding", () => {
  it("matches across case and Latin accents, not across Japanese voicing", () => {
    primeTranslations({}, "de");
    expect(matchesListSearch("Übersicht Küche", "ubersicht kuche")).toBe(true);
    expect(foldForSearch("が")).not.toBe(foldForSearch("か"));
    expect(foldForSearch("거실")).toBe("거실");
  });
});
