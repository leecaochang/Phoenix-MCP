import { BusyInput } from "./BusyButton";
import type { GlobalSettings } from "../types";
import { t } from "../i18n";
import { SettingLabel, helpRow } from "../ui/SettingLabel";

interface Props {
  settings: GlobalSettings;
  onToggle: (value: boolean) => void;
  saving: boolean;
}

export function KillSwitch({ settings, onToggle, saving }: Props) {
  const active = settings.kill_switch;
  const help = active ? t("settings.killSwitchActiveHelp") : t("settings.killSwitchInactiveHelp");

  return (
    <div className={active ? "kill-switch-active" : ""}>
      <div className="toggle-row toggle-row-plain" {...helpRow(help)}>
        <SettingLabel help={help}>
          <span className={active ? "kill-switch-label-active" : "kill-switch-label"}>
            {active ? t("settings.killSwitchActiveLabel") : t("settings.killSwitchDisableLabel")}
          </span>
        </SettingLabel>
        <label className={`toggle-switch toggle-switch-danger${saving ? " disabled" : ""}`}>
          <BusyInput
            type="checkbox"
            aria-label={active ? t("settings.killSwitchActiveAria") : t("settings.killSwitchDisableLabel")}
            checked={active}
            busy={saving}
            onChange={(e) => onToggle(e.target.checked)}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
    </div>
  );
}
