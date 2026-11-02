import { useEffect, useRef, useState } from "react";
import { BusyInput } from "./BusyButton";
import type { GlobalSettings } from "../types";
import { api } from "../api";
import { localeNumber } from "../i18n/format";
import { t } from "../i18n";
import { tRich } from "../i18n/rich";
import { SettingLabel, helpRow } from "../ui/SettingLabel";

interface Props {
  settings: GlobalSettings;
  saving: boolean;
  onChange: (key: keyof GlobalSettings, value: boolean | number) => void;
}

const LIMITS = [
  ["conversation_log_max_entries", 100, 1, 2000, "conversations.limit.entries", "conversations.limitEntriesHelp"],
  ["conversation_log_max_days", 7, 1, 365, "conversations.limit.days", "conversations.limitDaysHelp"],
  ["conversation_log_max_mb", 25, 1, 250, "conversations.limit.mb", "conversations.limitMbHelp"],
] as const;

/** How long after the last keystroke or arrow a limit saves itself. */
const SAVE_DELAY_MS = 600;

export function ConversationSettings({ settings, saving, onChange }: Props) {
  const [storage, setStorage] = useState<{ bytes: number; max_bytes?: number } | null>(null);
  useEffect(() => {
    let active = true;
    api.getConversations({ limit: 1 }).then(value => { if (active) setStorage(value); }).catch(() => { if (active) setStorage(null); });
    return () => { active = false; };
  }, [settings.conversation_log_max_entries, settings.conversation_log_max_days, settings.conversation_log_max_mb, saving]);
  const saved = [
    settings.conversation_log_max_entries ?? LIMITS[0][1],
    settings.conversation_log_max_days ?? LIMITS[1][1],
    settings.conversation_log_max_mb ?? LIMITS[2][1],
  ];
  const [draft, setDraft] = useState(saved.map(String));
  useEffect(() => setDraft(saved.map(String)), [saved[0], saved[1], saved[2]]); // eslint-disable-line react-hooks/exhaustive-deps
  // Each limit saves itself shortly after it changes, as every setting in Settings does (user, 2026-10-31).
  // A value out of range is not saved, and goes back to the saved one when the field is left.
  const timers = useRef<number[]>([]);
  useEffect(() => () => timers.current.forEach((timer) => window.clearTimeout(timer)), []);
  const valid = (index: number, raw: string) => {
    const [, , min, max] = LIMITS[index];
    return /^\d+$/.test(raw.trim()) && Number(raw) >= min && Number(raw) <= max;
  };
  const commit = (index: number, raw: string) => {
    window.clearTimeout(timers.current[index]);
    if (!valid(index, raw)) {
      setDraft((previous) => previous.map((value, i) => (i === index ? String(saved[i]) : value)));
      return;
    }
    if (Number(raw) !== saved[index]) onChange(LIMITS[index][0], Number(raw));
  };
  const edit = (index: number, raw: string) => {
    setDraft((previous) => previous.map((value, i) => (i === index ? raw : value)));
    window.clearTimeout(timers.current[index]);
    if (valid(index, raw)) timers.current[index] = window.setTimeout(() => commit(index, raw), SAVE_DELAY_MS);
  };
  // With all logging off nothing is recorded, so recording and its limits are greyed and say why in Help (user, 2026-10-31).
  const off = !!settings.disable_all_logging;
  const recordHelp = t(off ? "conversations.loggingDisabled" : "conversations.enableHelp");
  return <div className="phx-settings-form">
    <div className={`toggle-row${off ? " toggle-row-greyed" : ""}`} {...helpRow(recordHelp)}>
      <SettingLabel help={recordHelp}><span>{t("conversations.enable")}</span></SettingLabel>
      <label className="toggle-switch">
        <BusyInput type="checkbox" aria-label={t("conversations.enable")} data-help={off ? "conversations.loggingDisabled" : "conversations.privacyHelp"} checked={settings.conversation_log_enabled ?? false}
          busy={saving} disabled={settings.disable_all_logging} onChange={e => onChange("conversation_log_enabled", e.target.checked)} />
        <span className="toggle-switch-track" />
      </label>
    </div>
    {/* How retention and privacy work is in Help, on hover or focus. */}
    <fieldset className={`phx-retention${off ? " toggle-row-greyed" : ""}`} data-help={off ? "conversations.loggingDisabled" : "conversations.retentionHelp"}>
      <legend className="sr-only">{t("conversations.retention")}</legend>
      <div className="phx-retention-fields">
        {LIMITS.map(([field, , min, max, key, helpKey], index) => <div className="conversation-limit" key={field}>
          <label htmlFor={field}>{t(key)}</label>
          <BusyInput id={field} className="input" type="number" min={min} max={max} step={1} value={draft[index]} busy={saving} disabled={off}
            data-help={off ? "conversations.loggingDisabled" : helpKey}
            onChange={(e) => edit(index, e.target.value)}
            onBlur={(e) => commit(index, e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") commit(index, e.currentTarget.value); }} />
        </div>)}
      </div>
    </fieldset>
    {/* What the log takes now, under the limits that bound it (user, 2026-10-31). */}
    {!!storage?.max_bytes && <p className="conversation-secondary conversation-storage">{tRich("conversations.storage", { strong: (chunk) => <strong>{chunk}</strong> },
      { used: localeNumber(Math.round(storage!.bytes / 10000) / 100), limit: localeNumber(storage!.max_bytes! / 1000000) })}</p>}
  </div>;
}
