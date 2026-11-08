import { BusySelect } from "./BusyButton";
import type { GlobalSettings } from "../types";
import { useAccessKeyList } from "../utils/access_key_list";
import { t } from "../i18n";
import { tPlain } from "../i18n/rich";
import { SettingLabel, helpRow } from "../ui/SettingLabel";

// Assist tool-provider card ("Phoenix MCP (scoped)"). Binds ONE access key whose scoped tools
// Phoenix MCP hands to a conversation agent supplied by another integration (OpenAI,
// Anthropic, Ollama, and so on). Distinct from the Voice Agent card, which runs
// Phoenix MCP's own model. Install-wide singleton, so it is a Settings-level control (a
// bound-access-key dropdown), not a per-access-key toggle.
export function AssistBridgeSettings({
  settings,
  onChange,
  saving,
}: {
  settings: GlobalSettings;
  onChange: (key: keyof GlobalSettings, value: boolean | string) => void;
  saving: boolean;
}) {
  const accessKeys = useAccessKeyList();
  const supported = settings.assist_api_supported !== false;
  const boundId = settings.assist_bound_access_key_id ?? "";

  return (
    <div className="phx-settings-form" data-help-text={tPlain("settings.assistIntro")}>
      <div className="toggle-row toggle-row-stacked-control" {...helpRow(tPlain("settings.assistBoundHelp"))}>
        <SettingLabel help={tPlain("settings.assistBoundHelp")}>
          <span>{t("settings.assistBoundAccessKey")}</span>
          {!supported && <small><em>{t("settings.assistUnsupported")}</em></small>}
        </SettingLabel>
        <BusySelect
          aria-label={t("settings.assistBoundAccessKeyAria")}
          className="input input-auto"
          value={boundId}
          busy={saving} disabled={!supported}
          onChange={(e) => onChange("assist_bound_access_key_id", e.target.value)}
        >
          <option value="">{t("settings.assistNotBound")}</option>
          {accessKeys.map((tok) => (
            <option key={tok.id} value={tok.id}>{tok.name}</option>
          ))}
        </BusySelect>
      </div>
    </div>
  );
}
