import { BusyInput, BusySelect } from "./BusyButton";
import { useEffect, useState, type ReactNode } from "react";
import type { GlobalSettings, AccessKeyRecord, AiEvaluatePreferredStatus } from "../types";
import { api } from "../api";
import { t } from "../i18n";
import { SettingLabel, helpRow } from "../ui/SettingLabel";
import { useNotify } from "../ui/notify";
import { RoutingStatistics } from "./RoutingStatistics";

// The opt-in Decisions entity: Home Assistant's Evaluate action, answered by the decision
// provider. It has its own access key, so turning it on never widens the AI Task's key.
// AiTaskSettings renders this only on a Home Assistant with the action and while a decision
// provider is in use, so it needs no disabled state of its own.
export function AiTaskDecisionsSection({
  settings,
  onChange,
  saving,
  accessKeys,
  statistics = <RoutingStatistics surface="ai_task" />,
}: {
  settings: GlobalSettings;
  /** Save a setting; false when it failed (the window reports why). */
  onChange: (key: keyof GlobalSettings, value: boolean | string) => Promise<boolean> | void;
  saving: boolean;
  accessKeys: AccessKeyRecord[];
  /** The Evaluate counts, or the control that opens them. */
  statistics?: ReactNode;
}) {
  const notify = useNotify();
  const [pref, setPref] = useState<AiEvaluatePreferredStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const enabled = !!settings.ai_task_decisions_enabled;
  const accessKeyId = settings.ai_task_decisions_access_key_id ?? "";
  const chosenKey = accessKeys.find((key) => key.id === accessKeyId);
  // Evaluate needs Allow, and pass-through opens up Deny but leaves Confirm as it is.
  const needsCapability = !!chosenKey && (chosenKey.pass_through
    ? chosenKey.cap_service_response === "confirm"
    : chosenKey.cap_service_response !== "allow");

  // The entity exists only while configured, so re-read the default when that changes.
  const loadPref = () => {
    api.getAiEvaluatePreferred().then(setPref).catch(() => setPref(null));
  };
  useEffect(() => {
    loadPref();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, accessKeyId, settings.decision_provider_id]);

  async function setEnabled(on: boolean) {
    if ((await onChange("ai_task_decisions_enabled", on)) === false) return;
    notify(t(on ? "settings.aiDecisionsEnabledToast" : "settings.aiDecisionsDisabledToast"));
  }

  async function setDefault(on: boolean) {
    // Home Assistant has one default; name the one this replaces, read before the change.
    const replaced = on && pref?.evaluate_entity_id && !pref.is_preferred ? (pref.evaluate_name ?? pref.evaluate_entity_id) : null;
    setBusy(true);
    setError(null);
    try {
      setPref(await (on ? api.setAiEvaluatePreferred() : api.clearAiEvaluatePreferred()));
      notify(on
        ? (replaced ? t("settings.aiDecisionsDefaultReplacedToast", { name: replaced }) : t("settings.aiDecisionsDefaultSetToast"))
        : t("settings.aiDecisionsDefaultClearedToast"));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("settings.aiDecisionsSetDefaultFailed"));
      loadPref();
    } finally {
      setBusy(false);
    }
  }

  const defaultReady = !!pref?.entity_id;
  const defaultLocked = !defaultReady && !pref?.is_preferred;
  // Why a control is unavailable belongs in Help beside what it does, not under its name.
  const defaultHelp = [
    t(pref?.is_preferred ? "settings.aiDecisionsDefaultOn" : "settings.aiDecisionsDefaultOff"),
    defaultLocked ? t("settings.aiDecisionsConfigureFirst", { setting: t("settings.aiDecisionsEnableLabel") }) : null,
  ].filter(Boolean).join(" ");
  const accessKeyHelp = [
    t("settings.aiDecisionsAccessKeyHelp"),
    needsCapability ? t("settings.aiDecisionsCapabilityNote", { capability: t("caps.cap_service_response.label"), mode: t("caps.modeAllow") }) : null,
  ].filter(Boolean).join(" ");
  return (
    <section className="phx-settings-section phx-ai-decisions" data-help-text={t("settings.aiDecisionsIntro")}>
      <h3 className="phx-section-title">{t("settings.aiDecisionsHeading")}</h3>
      <div className="phx-setting-checks">
        <div className="toggle-row" {...helpRow(t("settings.aiDecisionsEnableHelp"))}>
          <SettingLabel help={t("settings.aiDecisionsEnableHelp")}>
            <span>{t("settings.aiDecisionsEnableLabel")}</span>
          </SettingLabel>
          <label className={`toggle-switch${saving ? " disabled" : ""}`}>
            <BusyInput
              type="checkbox"
              aria-label={t("settings.aiDecisionsEnableLabel")}
              checked={enabled}
              busy={saving || busy}
              onChange={(e) => void setEnabled(e.target.checked)}
            />
            <span className="toggle-switch-track" />
          </label>
        </div>
        <div className={`toggle-row${defaultLocked ? " toggle-row-greyed" : ""}`} {...helpRow(defaultHelp)}>
          <SettingLabel help={defaultHelp}>
            <span>{t("settings.aiDecisionsDefaultLabel")}</span>
          </SettingLabel>
          <label className="toggle-switch">
            <BusyInput
              type="checkbox"
              aria-label={t("settings.aiDecisionsDefaultLabel")}
              data-help={!pref?.is_preferred && pref?.evaluate_entity_id ? "help.settingsAiDecisionsReplaceDefault" : undefined}
              checked={!!pref?.is_preferred}
              busy={saving || busy} disabled={defaultLocked}
              onChange={(e) => void setDefault(e.target.checked)}
            />
            <span className="toggle-switch-track" />
          </label>
        </div>
      </div>
      {error && <div className="banner banner-error" role="alert">{error}</div>}

      <div className="toggle-row toggle-row-stacked-control" {...helpRow(accessKeyHelp)}>
        <SettingLabel help={accessKeyHelp}>
          <span>{t("settings.accessKey")}</span>
        </SettingLabel>
        <BusySelect
          aria-label={t("settings.aiDecisionsAccessKeyAria")}
          className="input input-auto"
          value={accessKeyId}
          busy={saving}
          onChange={(e) => onChange("ai_task_decisions_access_key_id", e.target.value)}
        >
          <option value="">{t("settings.selectAccessKey")}</option>
          {accessKeys.map((key) => (
            <option key={key.id} value={key.id}>{key.name}</option>
          ))}
        </BusySelect>
      </div>
      {statistics}
    </section>
  );
}
