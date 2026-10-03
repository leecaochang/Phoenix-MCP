import { useEffect, useState } from "react";
import { BusyButton, BusyInput } from "./BusyButton";
import type { GlobalSettings } from "../types";
import { t } from "../i18n";

interface Props {
  settings: GlobalSettings;
  saving: boolean;
  onChange: (key: keyof GlobalSettings, value: boolean | number) => void;
}

const LIMITS = [
  ["conversation_log_max_entries", 100, 1, 2000, "conversations.limit.entries"],
  ["conversation_log_max_days", 7, 1, 365, "conversations.limit.days"],
  ["conversation_log_max_mb", 25, 1, 250, "conversations.limit.mb"],
] as const;

function Limit({ field, value, min, max, label, saving, onChange }: {
  field: keyof GlobalSettings; value: number; min: number; max: number; label: string;
  saving: boolean; onChange: Props["onChange"];
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const valid = /^\d+$/.test(draft) && Number(draft) >= min && Number(draft) <= max;
  return <div className="conversation-limit">
    <label htmlFor={field}>{label}</label>
    <BusyInput id={field} className="input" type="number" min={min} max={max} step={1} value={draft} busy={saving}
      onChange={e => setDraft(e.target.value)} />
    <BusyButton className="btn btn-ghost" busy={saving} disabled={!valid || Number(draft) === value}
      onClick={() => onChange(field, Number(draft))}>{t("common.save")}</BusyButton>
  </div>;
}

export function ConversationSettings({ settings, saving, onChange }: Props) {
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
    <p className="conversation-secondary">{t("conversations.retentionHelp")}</p>
    {LIMITS.map(([field, fallback, min, max, key]) => <Limit key={field} field={field} value={settings[field] ?? fallback}
      min={min} max={max} label={t(key)} saving={saving} onChange={onChange} />)}
    <p className="conversation-secondary">{t("conversations.privacyHelp")}</p>
  </div>;
}
