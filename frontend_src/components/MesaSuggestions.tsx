/**
 * Wording for MESA's suggested profiles, computed by the backend scanner
 * (blast-radius orchestrators, unprotected risky devices): the signal that
 * raised each one, its suggested mode, and its reason in the reader's language.
 */

import { t, hasMessage, tnKey } from "../i18n";
import type { MesaSuggestion } from "../types";

export const SIGNAL_LABEL_KEYS: Record<string, string> = {
  blast_radius: "mesa.signalBlastRadius",
  naked_risky: "mesa.signalUnprotected",
};

/**
 * The suggestion sentence, in the operator's language.
 *
 * The backend stores English (it is written verbatim into control_reason when
 * the suggestion is applied, so it is the record). It also sends the template
 * key and its params, including keys for the sub-phrases the sentence splices
 * in: translating the sentence alone would leave an English clause inside it.
 * Falls back to the stored English whenever anything is missing, so an older
 * record or a new template still reads.
 */
export function suggestionReason(s: MesaSuggestion): string {
  const stored = s.reason_key ? `mesaSuggestion.${s.reason_key}` : "";
  if (!stored || !hasMessage(stored)) return s.reason;
  const params: Record<string, string | number> = { ...(s.reason_params ?? {}) };
  // The backend picks .one or .other by English rules; the reader's language
  // may need another category (Polish and Russian "few").
  const plural = /\.(one|other)$/.test(stored) && typeof params.count === "number";
  const key = plural ? tnKey(stored.replace(/\.(one|other)$/, ""), params.count as number) : stored;
  for (const [param, holder] of [["noun_key", "noun"], ["concern_key", "concern"], ["baseline_key", "baseline_note"]] as const) {
    const sub = params[param];
    if (typeof sub === "string" && sub && hasMessage(`mesaSuggestion.${sub}`)) {
      params[holder] = t(`mesaSuggestion.${sub}`);
    }
  }
  return t(key, params);
}

export const MODE_LABEL_KEYS: Record<string, string> = {
  confirm: "mesa.modeConfirm",
  prohibited: "mesa.modeProhibited",
  read_only: "mesa.modeReadOnly",
  autonomous: "mesa.modeAutonomous",
};

