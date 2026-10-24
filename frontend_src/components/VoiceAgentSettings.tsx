import { BusyInput, BusySelect } from "./BusyButton";
import { RoutingStatistics } from "./RoutingStatistics";
import { CommandRoutingToggle } from "./CommandRoutingToggle";
import { useEffect, useState } from "react";
import type { GlobalSettings, AccessKeyRecord, AgentCliInstance } from "../types";
import { api } from "../api";
import { t } from "../i18n";
import { useNotify } from "../ui/notify";
import { ConversationBehaviorControls } from "./ConversationBehaviorControls";

// Voice Agent settings. Registers Phoenix MCP as a Home Assistant conversation agent
// so "Phoenix MCP" appears in Settings > Voice assistants and runs Phoenix MCP's own model loop on
// the chosen access key's scope. Needs an access key (scope), an Agent Chat provider account,
// and a model. Independent of the Assist tool provider.
//
// Three adjacent switches apply directly, as accepted for Settings: the agent itself, the
// Assist assistant Phoenix MCP creates for it, and whether that assistant is Home
// Assistant's preferred one. Each needs the one before it, and turning one off clears
// what depends on it: the backend removes the tracked assistant when the agent is
// disabled or its access key or provider goes away, and removing the assistant ends its
// preference. Clearing the preference keeps the assistant. The kill switch leaves the
// assistant in place (the agent stays registered but declines).
export function VoiceAgentSettings({
  settings,
  onChange,
  saving,
  onRefresh,
}: {
  settings: GlobalSettings;
  /** Save a setting; false when it failed (the window reports why). */
  onChange: (key: keyof GlobalSettings, value: boolean | string) => Promise<boolean> | void;
  saving: boolean;
  /** Read the settings again; otherwise a refresh is asked for by event. */
  onRefresh?: () => Promise<void>;
}) {
  const notify = useNotify();
  const [accessKeys, setAccessKeys] = useState<AccessKeyRecord[]>([]);
  const [instances, setInstances] = useState<AgentCliInstance[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const enabled = !!settings.voice_agent_enabled;
  const providerId = settings.voice_agent_provider_id ?? "";
  const accessKeyId = settings.voice_agent_access_key_id ?? "";
  const model = settings.voice_agent_model ?? "";
  const pipelineId = settings.voice_agent_pipeline_id ?? "";
  const pipelineSupported = settings.voice_agent_pipeline_supported ?? true;
  const preferred = !!pipelineId && !!settings.voice_agent_pipeline_preferred;
  const noProviders = instances.length === 0;
  const fullyConfigured = !!(enabled && accessKeyId && providerId && model);
  const companion = accessKeys.some(accessKey => accessKey.id === accessKeyId && accessKey.persona === "home_companion");

  useEffect(() => {
    api.listAccessKeys().then(setAccessKeys).catch(() => setAccessKeys([]));
    const loadProviders = () =>
      api.getAgentCliProviders().then((r) => setInstances(r.instances)).catch(() => setInstances([]));
    loadProviders();
    // Reload when a provider account is added or removed in AI Providers, so
    // the dropdowns here never show a stale (or deleted) account.
    window.addEventListener("phx-agentcli-providers-changed", loadProviders);
    return () => window.removeEventListener("phx-agentcli-providers-changed", loadProviders);
  }, []);

  // Load the chosen provider's models so the model dropdown can be populated.
  useEffect(() => {
    if (!providerId) { setModels([]); return; }
    let cancelled = false;
    api.getAgentCliModels(providerId)
      .then((r) => { if (!cancelled) setModels(r.models); })
      .catch(() => { if (!cancelled) setModels([]); });
    return () => { cancelled = true; };
  }, [providerId]);

  // Re-read settings so a created or removed assistant, or a changed preference, shows its real state.
  const refreshSettings = async () => {
    if (onRefresh) await onRefresh();
    else window.dispatchEvent(new Event("phx-settings-refresh"));
  };

  async function setEnabled(on: boolean) {
    const hadAssistant = !!pipelineId;
    if ((await onChange("voice_agent_enabled", on)) === false) return;
    if (!on && hadAssistant) await refreshSettings();
    notify(on ? t("settings.voiceEnabledToast") : hadAssistant ? t("settings.voiceDisabledRemovedToast") : t("settings.voiceDisabledToast"));
  }

  /** Run one Home Assistant change, then show the settings it left. */
  async function apply(run: () => Promise<unknown>, done: string, failed: string) {
    setBusy(true);
    setError(null);
    try {
      await run();
      notify(done);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : failed);
    } finally {
      await refreshSettings();
      setBusy(false);
    }
  }
  const setAssistant = (on: boolean) => apply(
    () => (on ? api.createVoiceAgentPipeline(false) : api.deleteVoiceAgentPipeline()),
    on ? t("settings.voiceAssistantAddedToast") : t("settings.voiceAssistantRemovedToast"),
    on ? t("settings.voiceSetUpFailed") : t("settings.voiceRemoveFailed"),
  );
  const setPreferred = (on: boolean) => apply(
    () => api.setVoiceAgentPreferred(on),
    on ? t("settings.voicePreferredSetToast") : t("settings.voicePreferredClearedToast"),
    t("settings.voicePreferredFailed"),
  );

  return (
    <div className="phx-settings-form">
      <p className="settings-info-note phx-settings-intro">
        {t("settings.voiceIntro")}
      </p>
      <div className="phx-setting-checks">
        <div className="toggle-row">
          <div className="toggle-label">
            <span>{t("settings.voiceEnableLabel")}</span>
            <small>{t("settings.voiceEnableHelp")}</small>
          </div>
          <label className="toggle-switch">
            <BusyInput
              type="checkbox"
              aria-label={t("settings.voiceEnableLabel")}
              data-help={pipelineId ? "help.settingsVoiceDisable" : undefined}
              checked={enabled}
              busy={saving || busy}
              onChange={(e) => void setEnabled(e.target.checked)}
            />
            <span className="toggle-switch-track" />
          </label>
        </div>
        {pipelineSupported && (
          <div className={`toggle-row${!pipelineId && !fullyConfigured ? " toggle-row-greyed" : ""}`}>
            <div className="toggle-label">
              <span>{t("settings.voiceAssistSetup")}</span>
              <small>
                {pipelineId
                  ? t("settings.voiceAssistSetupCreated")
                  : fullyConfigured ? t("settings.voiceAssistSetupNone") : t("settings.voiceConfigureFirst")}
              </small>
            </div>
            <label className="toggle-switch">
              <BusyInput
                type="checkbox"
                aria-label={t("settings.voiceAssistSetup")}
                data-help={pipelineId ? "help.settingsVoiceRemoveAssistant" : undefined}
                checked={!!pipelineId}
                busy={saving || busy} disabled={!pipelineId && !fullyConfigured}
                onChange={(e) => void setAssistant(e.target.checked)}
              />
              <span className="toggle-switch-track" />
            </label>
          </div>
        )}
        {pipelineSupported && (
          <div className={`toggle-row${!pipelineId ? " toggle-row-greyed" : ""}`}>
            <div className="toggle-label">
              <span>{t("settings.voicePreferredLabel")}</span>
              <small>{t("settings.voicePreferredHelp")}</small>
            </div>
            <label className="toggle-switch">
              <BusyInput
                type="checkbox"
                aria-label={t("settings.voicePreferredLabel")}
                data-help={preferred ? "help.settingsVoiceClearPreferred" : undefined}
                checked={preferred}
                busy={saving || busy} disabled={!pipelineId}
                onChange={(e) => void setPreferred(e.target.checked)}
              />
              <span className="toggle-switch-track" />
            </label>
          </div>
        )}
      </div>
      {error && <div className="banner banner-error" role="alert">{error}</div>}

      <div className="toggle-row toggle-row-stacked-control">
        <div className="toggle-label">
          <span>{t("settings.accessKey")}</span>
          <small>{t("settings.voiceAccessKeyHelp")}</small>
        </div>
        <BusySelect
          aria-label={t("settings.voiceAccessKeyAria")}
          className="input input-auto"
          value={accessKeyId}
          busy={saving}
          onChange={(e) => onChange("voice_agent_access_key_id", e.target.value)}
        >
          <option value="">{t("settings.selectAccessKey")}</option>
          {accessKeys.map((tok) => (
            <option key={tok.id} value={tok.id}>{tok.name}</option>
          ))}
        </BusySelect>
      </div>

      <div className="toggle-row toggle-row-stacked-control">
        <div className="toggle-label">
          <span>{t("settings.providerAccount")}</span>
          <small>{instances.length === 0 ? t("settings.addProviderFirst") : t("settings.voiceProviderHelp")}</small>
        </div>
        <BusySelect
          aria-label={t("settings.voiceProviderAria")}
          className="input input-auto"
          value={providerId}
          busy={saving} disabled={noProviders}
          onChange={(e) => onChange("voice_agent_provider_id", e.target.value)}
        >
          <option value="">{t("settings.selectProvider")}</option>
          {instances.map((i) => (
            <option key={i.id} value={i.id}>{i.name}</option>
          ))}
        </BusySelect>
      </div>

      <div className="toggle-row toggle-row-stacked-control">
        <div className="toggle-label">
          <span>{t("settings.model")}</span>
          <small>{t("settings.voiceModelHelp")}</small>
        </div>
        <BusySelect
          aria-label={t("settings.voiceModelAria")}
          className="input input-auto"
          value={model}
          busy={saving} disabled={!providerId || noProviders}
          onChange={(e) => onChange("voice_agent_model", e.target.value)}
        >
          <option value="">{t("settings.selectModel")}</option>
          {models.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </BusySelect>
      </div>

      {!companion && <CommandRoutingToggle surface="voice" mode={settings.voice_agent_routing_mode}
        onChange={value => onChange("voice_agent_routing_mode", value)} saving={saving} />}

      <ConversationBehaviorControls
        surface={t("settings.voiceCard")}
        style={settings.voice_agent_conversation_style ?? "direct"}
        detail={settings.voice_agent_detail_level ?? "concise"}
        homeFocused={settings.voice_agent_home_focused ?? false}
        saving={saving}
        onStyleChange={(value) => onChange("voice_agent_conversation_style", value)}
        onDetailChange={(value) => onChange("voice_agent_detail_level", value)}
        onHomeFocusedChange={(value) => onChange("voice_agent_home_focused", value)}
      />

      <RoutingStatistics surface="voice" />
    </div>
  );
}
