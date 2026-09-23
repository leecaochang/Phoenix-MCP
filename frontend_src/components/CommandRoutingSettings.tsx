import { DecisionProviderSettings } from "./DecisionProviderSettings";
import type { GlobalSettings } from "../types";
import { t } from "../i18n";

export function CommandRoutingSettings({ settings, onChange, saving }: {
  settings: GlobalSettings;
  onChange: (key: keyof GlobalSettings, value: string | number | boolean | string[]) => void;
  saving: boolean;
}) {
  return <section className="card command-routing-settings" aria-labelledby="command-routing-title">
    <h3 className="card-header" id="command-routing-title">{t("routing.title")}</h3>
    <p className="agentcli-settings-hint">{t("routing.intro")}</p>
    {([
      ["voice_agent_routing_mode", "routing.enableVoice"],
      ["agentcli_routing_mode", "routing.enableChat"],
    ] as const).map(([key, label]) => <div className="toggle-row" key={key}>
      <label htmlFor={`routing-${key}`}>{t(label)}</label>
      <label className="toggle-switch">
        <input id={`routing-${key}`} type="checkbox" disabled={saving}
          checked={["auto", "local", "hybrid"].includes(settings[key] ?? "off")}
          onChange={event => onChange(key, event.target.checked ? "auto" : "off")} />
        <span className="toggle-switch-track" />
      </label>
    </div>)}
    <p className="agentcli-settings-hint">{t("routing.languageHint")}</p>
    <DecisionProviderSettings settings={settings} onChange={onChange} saving={saving} />
  </section>;
}
