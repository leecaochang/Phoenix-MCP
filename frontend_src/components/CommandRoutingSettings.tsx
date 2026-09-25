import { DocsHelpLink } from "./common";
import { DecisionProviderSettings } from "./DecisionProviderSettings";
import type { GlobalSettings } from "../types";
import { t } from "../i18n";

export function CommandRoutingSettings({ settings, onChange, saving }: {
  settings: GlobalSettings;
  onChange: (key: keyof GlobalSettings, value: string | number | boolean | string[]) => void;
  saving: boolean;
}) {
  return <section className="card command-routing-settings" aria-labelledby="command-routing-title">
    <h3 className="card-header" id="command-routing-title">{t("routing.providerSection")}<DocsHelpLink path="operations.html#command-routing" label={t("routing.providerSection")} /></h3>
    <DecisionProviderSettings settings={settings} onChange={onChange} saving={saving} />
  </section>;
}
