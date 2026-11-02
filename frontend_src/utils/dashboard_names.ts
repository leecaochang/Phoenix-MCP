/**
 * Display names for the dashboards Phoenix records under fixed English names,
 * and the parts of a change summary the panel rebuilds in the reader's language
 * (a card's location, a file's size).
 *
 * Records name the default dashboard "(default)" or "(default dashboard)" (older
 * restore requests say "Default") and the Energy dashboard "Energy dashboard",
 * because agents read those records too. A storage dashboard's url_path always
 * contains a hyphen, so none of these can be a real dashboard's own name.
 */

import { localeFileSize, t, type Params } from "../i18n";

const SENTINEL_KEYS: Readonly<Record<string, string>> = {
  "(default)": "changes.defaultDashboard",
  "(default dashboard)": "changes.defaultDashboard",
  Default: "changes.defaultDashboard",
  "Energy dashboard": "changes.energyDashboard",
};

const DASHBOARD_TYPES = new Set(["dashboard", "energy"]);

/** The catalog name for a sentinel dashboard name; any other name unchanged. */
export function dashboardDisplayName(name: string, type: string | null | undefined): string {
  const key = type && DASHBOARD_TYPES.has(type) ? SENTINEL_KEYS[name] : undefined;
  return key ? t(key) : name;
}

/** Summary params with a sentinel dashboard `label` replaced, for dashboard targets only. */
export function withDashboardLabel(params: Params | undefined, type: string | null | undefined): Params | undefined {
  if (!params || typeof params.label !== "string") return params;
  const label = dashboardDisplayName(params.label, type);
  return label === params.label ? params : { ...params, label };
}

const SIZE_KEYS = new Set(["version.size", "version.size.was"]);

/** A size change's params with the sizes formatted for the reader's locale, when the byte counts were stored. */
export function withLocaleSizes(key: string, params: Params | undefined): Params | undefined {
  if (!params || !SIZE_KEYS.has(key) || typeof params.size_bytes !== "number") return params;
  const out: Params = { ...params, size: localeFileSize(params.size_bytes) };
  if (typeof params.before_bytes === "number") out.before = localeFileSize(params.before_bytes);
  return out;
}

const CARD_CHANGE_KEYS = new Set(["version.card.added", "version.card.edited", "version.card.deleted"]);

/**
 * A card change's params with `where` and `subject` rebuilt from the translated
 * location parts. Records written before the indices were stored keep their
 * English `where`, which is still correct, just not translated.
 */
export function withCardLocation(key: string, params: Params | undefined): Params | undefined {
  if (!params || !CARD_CHANGE_KEYS.has(key) || typeof params.view_index !== "number") return params;
  const parts = [t("version.loc.view", { index: params.view_index })];
  if (typeof params.section_index === "number") parts.push(t("version.loc.section", { index: params.section_index }));
  if (typeof params.card_index === "number") parts.push(t("version.loc.card", { index: params.card_index }));
  const cardType = typeof params.card_type === "string" && params.card_type ? params.card_type : null;
  return { ...params, where: parts.join(t("common.listSeparator")), subject: cardType ?? t("changes.cardNoun") };
}
