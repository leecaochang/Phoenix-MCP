import { useEffect, useState } from "react";
import { BusyInput } from "./BusyButton";
import { ActionButton } from "../ui/ActionButton";
import type { GlobalSettings } from "../types";
import { api } from "../api";
import { localeNumber } from "../i18n/format";
import { t } from "../i18n";
import { SettingLabel, helpRow } from "../ui/SettingLabel";

interface Props {
  settings: GlobalSettings;
  saving: boolean;
  onChange: (key: keyof GlobalSettings, value: boolean | number) => void;
  onSave: (patch: Partial<GlobalSettings>) => Promise<unknown>;
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
  const discard = () => setDraft([String(entries), String(days), String(mb)]);
  return <div className="phx-settings-form">
    <div className="toggle-row" {...helpRow(t("conversations.enableHelp"))}>
      <SettingLabel help={t("conversations.enableHelp")}><span>{t("conversations.enable")}</span></SettingLabel>
      <label className="toggle-switch">
        <BusyInput type="checkbox" aria-label={t("conversations.enable")} data-help="conversations.privacyHelp" checked={settings.conversation_log_enabled ?? false}
          busy={saving} disabled={settings.disable_all_logging} onChange={e => onChange("conversation_log_enabled", e.target.checked)} />
        <span className="toggle-switch-track" />
      </label>
    </div>
    {settings.disable_all_logging && <p className="conversation-secondary">{t("conversations.loggingDisabled")}</p>}
    {!settings.log_entity_names && <p className="conversation-secondary">{t("conversations.omission.privacy")}</p>}
    {!!storage?.max_bytes && <p className="conversation-secondary">{t("conversations.storage", { used: localeNumber(Math.round(storage!.bytes / 10000) / 100), limit: localeNumber(storage!.max_bytes! / 1000000) })}</p>}
    {/* The three limits save together; how retention and privacy work is in Help, on hover or focus. */}
    <fieldset className="phx-retention" data-help="conversations.retentionHelp">
      <legend className="sr-only">{t("conversations.retention")}</legend>
      <div className="phx-retention-fields">
        {LIMITS.map(([field, , min, max, key], index) => <div className="conversation-limit" key={field}>
          <label htmlFor={field}>{t(key)}</label>
          <BusyInput id={field} className="input" type="number" min={min} max={max} step={1} value={draft[index]} busy={saving}
            data-help="conversations.retentionHelp"
            onChange={e => setDraft(previous => previous.map((value, i) => i === index ? e.target.value : value))} />
        </div>)}
      </div>
      <div className="phx-window-actions">
        <ActionButton icon="save" label={t("common.save")} className="btn btn-primary btn-sm" busy={saving} disabled={!valid || !changed}
          onClick={() => void onSave(Object.fromEntries(LIMITS.map(([field], index) => [field, Number(draft[index])])))} />
        <ActionButton icon="undo" label={t("settings.discard")} className="btn btn-outline btn-sm" busy={saving} disabled={!changed}
          onClick={discard} />
      </div>
    </fieldset>
  </div>;
}
