import { BusyInput, BusySelect } from "./BusyButton";
import { useEffect, useState } from "react";
import type { GlobalSettings, AccessKeyRecord, AgentCliInstance, AiTaskPreferredStatus } from "../types";
import { api } from "../api";
import { t } from "../i18n";
import { SettingLabel, helpRow } from "../ui/SettingLabel";
import { useNotify } from "../ui/notify";
import { ConversationBehaviorControls } from "./ConversationBehaviorControls";

// AI Task settings. Registers Phoenix MCP as a Home Assistant AI Task entity ("Phoenix MCP AI
// Task") so ai_task.generate_data can target Phoenix MCP and run its own model on the chosen
// access key's scope, with MESA safety, approvals, and audit. Independent of the Voice
// Agent and the Assist tool provider; needs an access key, an Agent Chat provider account, a model.
//
// Enable and Use as Default apply directly, as accepted for Settings. Home Assistant has one
// default data-generation entity, so making Phoenix MCP the default replaces another one and
// the toast names it; disabling the AI Task also clears its default (backend).
export function AiTaskSettings({
  settings,
  onChange,
  saving,
}: {
  settings: GlobalSettings;
  /** Save a setting; false when it failed (the window reports why). */
  onChange: (key: keyof GlobalSettings, value: boolean | string) => Promise<boolean> | void;
  saving: boolean;
}) {
  const notify = useNotify();
  const [accessKeys, setAccessKeys] = useState<AccessKeyRecord[]>([]);
  const [instances, setInstances] = useState<AgentCliInstance[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [pref, setPref] = useState<AiTaskPreferredStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const supported = settings.ai_task_supported !== false;
  const enabled = !!settings.ai_task_enabled;
  const providerId = settings.ai_task_provider_id ?? "";
  const accessKeyId = settings.ai_task_access_key_id ?? "";
  const model = settings.ai_task_model ?? "";
  const noProviders = instances.length === 0;

  useEffect(() => {
    api.listAccessKeys().then(setAccessKeys).catch(() => setAccessKeys([]));
    const loadProviders = () =>
      api.getAgentCliProviders().then((r) => setInstances(r.instances)).catch(() => setInstances([]));
    loadProviders();
    window.addEventListener("phx-agentcli-providers-changed", loadProviders);
    return () => window.removeEventListener("phx-agentcli-providers-changed", loadProviders);
  }, []);

  useEffect(() => {
    if (!providerId) { setModels([]); return; }
    let cancelled = false;
    api.getAgentCliModels(providerId)
      .then((r) => { if (!cancelled) setModels(r.models); })
      .catch(() => { if (!cancelled) setModels([]); });
    return () => { cancelled = true; };
  }, [providerId]);

  // The AI Task entity (and thus its eligibility to be the default) exists only when
  // fully configured, so re-read the default data-generation entity whenever the
  // configuration changes, not just on mount.
  const loadPref = () => {
    if (!supported) { setPref(null); return; }
    api.getAiTaskPreferred().then(setPref).catch(() => setPref(null));
  };
  useEffect(() => {
    loadPref();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [supported, enabled, accessKeyId, providerId, model]);

  async function setEnabled(on: boolean) {
    const wasDefault = !!pref?.is_preferred;
    if ((await onChange("ai_task_enabled", on)) === false) return;
    notify(on ? t("settings.aiTaskEnabledToast") : wasDefault ? t("settings.aiTaskDisabledDefaultToast") : t("settings.aiTaskDisabledToast"));
  }

  async function setDefault(on: boolean) {
    // Home Assistant has one default; name the one this replaces, read before the change.
    const replaced = on && pref?.gen_data_entity_id && !pref.is_preferred ? (pref.gen_data_name ?? pref.gen_data_entity_id) : null;
    setBusy(true);
    setError(null);
    try {
      setPref(await (on ? api.setAiTaskPreferred() : api.clearAiTaskPreferred()));
      notify(on
        ? (replaced ? t("settings.aiTaskDefaultReplacedToast", { name: replaced }) : t("settings.aiTaskDefaultSetToast"))
        : t("settings.aiTaskDefaultClearedToast"));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("settings.aiTaskSetDefaultFailed"));
      loadPref();
    } finally {
      setBusy(false);
    }
  }

  const defaultReady = !!pref?.entity_id;
  return (
    <div className="phx-settings-form" data-help-text={t("settings.aiTaskIntro")}>
      <div className="phx-setting-checks">
        <div className="toggle-row" {...helpRow(t("settings.aiTaskEnableHelp"))}>
          <SettingLabel help={t("settings.aiTaskEnableHelp")}>
            <span>{t("settings.aiTaskEnableLabel")}</span>
            {!supported && <small>{t("settings.aiTaskUnsupported")}</small>}
          </SettingLabel>
          <label className={`toggle-switch${saving || !supported ? " disabled" : ""}`}>
            <BusyInput
              type="checkbox"
              aria-label={t("settings.aiTaskEnableLabel")}
              data-help={pref?.is_preferred ? "help.settingsAiTaskDisable" : undefined}
              checked={enabled}
              busy={saving || busy} disabled={!supported}
              onChange={(e) => void setEnabled(e.target.checked)}
            />
            <span className="toggle-switch-track" />
          </label>
        </div>
        {supported && (pref?.supported ?? false) && (
          <div className={`toggle-row${!defaultReady && !pref?.is_preferred ? " toggle-row-greyed" : ""}`}
            {...helpRow(t(pref?.is_preferred ? "settings.aiTaskDefaultOn" : "settings.aiTaskDefaultOff"))}>
            <SettingLabel help={t(pref?.is_preferred ? "settings.aiTaskDefaultOn" : "settings.aiTaskDefaultOff")}>
              <span>{t("settings.aiTaskDefaultLabel")}</span>
              {!pref?.is_preferred && !defaultReady && <small>{t("settings.aiTaskConfigureFirst")}</small>}
            </SettingLabel>
            <label className="toggle-switch">
              <BusyInput
                type="checkbox"
                aria-label={t("settings.aiTaskDefaultLabel")}
                data-help={!pref?.is_preferred && pref?.gen_data_entity_id ? "help.settingsAiTaskReplaceDefault" : undefined}
                checked={!!pref?.is_preferred}
                busy={saving || busy} disabled={!defaultReady && !pref?.is_preferred}
                onChange={(e) => void setDefault(e.target.checked)}
              />
              <span className="toggle-switch-track" />
            </label>
          </div>
        )}
      </div>
      {error && <div className="banner banner-error" role="alert">{error}</div>}

      <div className="toggle-row toggle-row-stacked-control" {...helpRow(t("settings.aiTaskAccessKeyHelp"))}>
        <SettingLabel help={t("settings.aiTaskAccessKeyHelp")}><span>{t("settings.accessKey")}</span></SettingLabel>
        <BusySelect
          aria-label={t("settings.aiTaskAccessKeyAria")}
          className="input input-auto"
          value={accessKeyId}
          busy={saving} disabled={!supported}
          onChange={(e) => onChange("ai_task_access_key_id", e.target.value)}
        >
          <option value="">{t("settings.selectAccessKey")}</option>
          {accessKeys.map((tok) => (
            <option key={tok.id} value={tok.id}>{tok.name}</option>
          ))}
        </BusySelect>
      </div>

      <div className="toggle-row toggle-row-stacked-control" {...helpRow(t("settings.aiTaskProviderHelp"))}>
        <SettingLabel help={t("settings.aiTaskProviderHelp")}>
          <span>{t("settings.providerAccount")}</span>
          {noProviders && <small>{t("settings.addProviderFirst")}</small>}
        </SettingLabel>
        <BusySelect
          aria-label={t("settings.aiTaskProviderAria")}
          className="input input-auto"
          value={providerId}
          busy={saving} disabled={noProviders || !supported}
          onChange={(e) => onChange("ai_task_provider_id", e.target.value)}
        >
          <option value="">{t("settings.selectProvider")}</option>
          {instances.map((i) => (
            <option key={i.id} value={i.id}>{i.name}</option>
          ))}
        </BusySelect>
      </div>

      <div className="toggle-row toggle-row-stacked-control" {...helpRow(t("settings.aiTaskModelHelp"))}>
        <SettingLabel help={t("settings.aiTaskModelHelp")}><span>{t("settings.model")}</span></SettingLabel>
        <BusySelect
          aria-label={t("settings.aiTaskModelAria")}
          className="input input-auto"
          value={model}
          busy={saving} disabled={!providerId || noProviders || !supported}
          onChange={(e) => onChange("ai_task_model", e.target.value)}
        >
          <option value="">{t("settings.selectModel")}</option>
          {models.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </BusySelect>
      </div>

      <ConversationBehaviorControls
        surface={t("settings.aiTaskCard")}
        style={settings.ai_task_conversation_style ?? "direct"}
        detail={settings.ai_task_detail_level ?? "balanced"}
        saving={saving || !supported}
        onStyleChange={(value) => onChange("ai_task_conversation_style", value)}
        onDetailChange={(value) => onChange("ai_task_detail_level", value)}
        aiTaskFreeTextOnly
      />
    </div>
  );
}
