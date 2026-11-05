import { useId } from "react";
import { BusyInput } from "./BusyButton";
import { t } from "../i18n";
import { SettingLabel, helpRow } from "../ui/SettingLabel";

export function CommandRoutingToggle({ surface, mode, onChange, saving, divider = true }: {
  surface: "voice" | "chat";
  mode: string | undefined;
  onChange: (mode: "auto" | "off") => void;
  saving: boolean;
  /** The rule under the row; Agent Chat's settings go on without one. */
  divider?: boolean;
}) {
  // Its window can show in two workspaces at once; Find Setting finds it by `data-setting`.
  const id = `${useId()}routing-${surface}`;
  return <div className={divider ? "toggle-row" : "toggle-row toggle-row-flat"} {...helpRow(t("routing.commandHelp"))}>
    <SettingLabel help={t("routing.commandHelp")} helpId={`${id}-help`}>
      <label htmlFor={id}>{t(surface === "voice" ? "routing.enableVoice" : "routing.enableChat")}</label>
    </SettingLabel>
    <label className="toggle-switch">
      <BusyInput id={id} data-setting={`routing-${surface}`} type="checkbox" aria-describedby={`${id}-help`} busy={saving}
        checked={["auto", "local", "hybrid"].includes(mode ?? "off")}
        onChange={event => onChange(event.target.checked ? "auto" : "off")} />
      <span className="toggle-switch-track" />
    </label>
  </div>;
}
