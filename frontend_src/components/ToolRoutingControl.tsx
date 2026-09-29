import type { ToolRoutingMode } from "../types";
import { t } from "../i18n";

export function ToolRoutingControl({ surface, mode = "off", hasAccount, qualifiedLanguages = [], saving, onChange }: {
  surface: "chat" | "voice" | "ai_task";
  mode?: ToolRoutingMode;
  hasAccount: boolean;
  qualifiedLanguages?: string[];
  saving: boolean;
  onChange: (mode: ToolRoutingMode) => void;
}) {
  const id = `tool-routing-${surface}`;
  return <div className="toggle-row toggle-row-stacked-control">
    <div className="toggle-label">
      <label htmlFor={id}>{t("toolRouting.label")}</label>
      <small id={`${id}-help`}>{t("toolRouting.help")}</small>
      <small id={`${id}-availability`}>{!hasAccount ? t("toolRouting.accountRequired")
        : qualifiedLanguages.length ? t("toolRouting.qualified", { languages: qualifiedLanguages.join(t("common.listSeparator")) })
        : t("toolRouting.unqualified")}</small>
    </div>
    <select id={id} className="input input-auto" value={mode} disabled={saving}
      aria-describedby={`${id}-help ${id}-availability`}
      onChange={event => onChange(event.target.value as ToolRoutingMode)}>
      <option value="off">{t("toolRouting.off")}</option>
      <option value="observe" disabled={!hasAccount}>{t("toolRouting.observe")}</option>
      <option value="advise" disabled={!hasAccount || !qualifiedLanguages.length}>{t("toolRouting.advise")}</option>
    </select>
  </div>;
}
