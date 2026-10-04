import { useEffect, useState } from "react";
import { BusyButton, BusyInput } from "./BusyButton";
import type { GlobalSettings } from "../types";
import { api } from "../api";
import { localeNumber } from "../i18n/format";
import { t } from "../i18n";

interface Props {
  settings: GlobalSettings;
  saving: boolean;
  onChange: (key: keyof GlobalSettings, value: boolean | number) => void;
  onSave: (patch: Partial<GlobalSettings>) => Promise<void>;
}

const LIMITS = [
  ["conversation_log_max_entries", 100, 1, 2000, "conversations.limit.entries"],
  ["conversation_log_max_days", 7, 1, 365, "conversations.limit.days"],
  ["conversation_log_max_mb", 25, 1, 250, "conversations.limit.mb"],
] as const;

export function ConversationSettings({ settings, saving, onChange, onSave }: Props) {
  const [storage, setStorage] = useState<{ bytes: number; max_bytes?: number } | null>(null);
  useEffect(() => {
    let active = true;
    api.getConversations({ limit: 1 }).then(value => { if (active) setStorage(value); }).catch(() => { if (active) setStorage(null); });
    return () => { active = false; };
  }, [settings.conversation_log_max_entries, settings.conversation_log_max_days, settings.conversation_log_max_mb, saving]);
  const entries = settings.conversation_log_max_entries ?? 100;
  const days = settings.conversation_log_max_days ?? 7;
  const mb = settings.conversation_log_max_mb ?? 25;
  const [draft, setDraft] = useState([String(entries), String(days), String(mb)]);
  useEffect(() => setDraft([String(entries), String(days), String(mb)]), [entries, days, mb]);
  const valid = LIMITS.every(([, , min, max], index) => /^\d+$/.test(draft[index]) && Number(draft[index]) >= min && Number(draft[index]) <= max);
  const changed = draft.some((value, index) => Number(value) !== [entries, days, mb][index]);
  return <div className="card">
    <h3 className="card-header">{t("conversations.title")}</h3>
    <div className="toggle-row">
      <div className="toggle-label"><span>{t("conversations.enable")}</span><small>{t("conversations.enableHelp")}</small></div>
      <label className="toggle-switch">
        <BusyInput type="checkbox" aria-label={t("conversations.enable")} checked={settings.conversation_log_enabled ?? false}
          busy={saving} disabled={settings.disable_all_logging} onChange={e => onChange("conversation_log_enabled", e.target.checked)} />
        <span className="toggle-switch-track" />
      </label>
    </div>
    {settings.disable_all_logging && <p className="conversation-secondary">{t("conversations.loggingDisabled")}</p>}
    {!settings.log_entity_names && <p className="conversation-secondary">{t("conversations.omission.privacy")}</p>}
    {!!storage?.max_bytes && <p className="conversation-secondary">{t("conversations.storage", { used: localeNumber(Math.round(storage!.bytes / 10000) / 100), limit: localeNumber(storage!.max_bytes! / 1000000) })}</p>}
    <p className="conversation-secondary">{t("conversations.retentionHelp")}</p>
    {LIMITS.map(([field, , min, max, key], index) => <div className="conversation-limit" key={field}>
      <label htmlFor={field}>{t(key)}</label>
      <BusyInput id={field} className="input" type="number" min={min} max={max} step={1} value={draft[index]} busy={saving}
        onChange={e => setDraft(previous => previous.map((value, i) => i === index ? e.target.value : value))} />
    </div>)}
    <BusyButton className="btn btn-ghost" busy={saving} disabled={!valid || !changed}
      onClick={() => void onSave(Object.fromEntries(LIMITS.map(([field], index) => [field, Number(draft[index])])))}>{t("common.save")}</BusyButton>
    <p className="conversation-secondary">{t("conversations.privacyHelp")}</p>
  </div>;
}
