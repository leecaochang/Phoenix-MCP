import { BusyInput } from "./BusyButton";
import { t } from "../i18n";
import { SettingLabel, helpRow } from "../ui/SettingLabel";

export function CommandRoutingToggle({ surface, mode, onChange, saving }: {
  surface: "voice" | "chat";
  mode: string | undefined;
  onChange: (mode: "auto" | "off") => void;
  saving: boolean;
}) {
  const id = `routing-${surface}`;
  return <div className="toggle-row" {...helpRow(t("routing.commandHelp"))}>
    <SettingLabel help={t("routing.commandHelp")} helpId={`${id}-help`}>
      <label htmlFor={id}>{t(surface === "voice" ? "routing.enableVoice" : "routing.enableChat")}</label>
    </SettingLabel>
    <label className="toggle-switch">
      <BusyInput id={id} type="checkbox" aria-describedby={`${id}-help`} busy={saving}
        checked={["auto", "local", "hybrid"].includes(mode ?? "off")}
        onChange={event => onChange(event.target.checked ? "auto" : "off")} />
      <span className="toggle-switch-track" />
    </label>
  </div>;
}
