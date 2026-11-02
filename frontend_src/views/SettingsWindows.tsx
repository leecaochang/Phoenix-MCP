/**
 * The Settings workspace's windows, around the shared state in
 * `shell/settings.tsx`: AI Providers (provider accounts, model catalog checks
 * and the Decision Provider), Agent Chat, Home Assistant (Voice Agent, AI Task
 * and Assist Tool Provider on local tabs), Safety (kill switch, MESA mode and
 * Data Management), Logs and Notifications, and General. Every window offers
 * Find Setting on the toolbar; a change that fails is reported in the window
 * where it was made.
 */

import { useContext, useEffect, useState, useSyncExternalStore, type KeyboardEvent, type ReactNode } from "react";
import { AgentCliSettings } from "../components/AgentCliSettings";
import { AiTaskSettings } from "../components/AiTaskSettings";
import { AssistBridgeSettings } from "../components/AssistBridgeSettings";
import { BusyInput, BusySelect } from "../components/BusyButton";
import { Loading } from "../components/common";
import { ConversationSettings } from "../components/ConversationSettings";
import { DecisionProviderSettings } from "../components/DecisionProviderSettings";
import { KillSwitch } from "../components/KillSwitch";
import { LlmProviderSettings } from "../components/LlmProviderSettings";
import { AUDIT_TOGGLES, LoggingSettings, PRIVACY_TOGGLES } from "../components/LoggingSettings";
import { NotificationSettings } from "../components/NotificationSettings";
import { RoutingStatistics, RoutingStatisticsContent } from "../components/RoutingStatistics";
import { VoiceAgentSettings } from "../components/VoiceAgentSettings";
import { DEFAULT_WIPE_SCOPES, WIPE_SCOPES, WipeConfirmModal, type WipeScopes } from "../components/WipeConfirmModal";
import { LANGUAGES, LANGUAGE_AUTO, t, tn } from "../i18n";
import { tPlain } from "../i18n/rich";
import { injectSessionBlocked, subscribeInjectCompatibility } from "../inject/compatibility";
import { useFindSettingTool, useSettings, useSettingsChange, type Theme } from "../shell/settings";
import { HOME_TAB_LABEL_KEYS, HOME_TABS, SETTINGS_WINDOW_TITLE_KEYS, type HomeTab, type SettingsWindowId } from "../shell/settingsIndex";
import { WindowPlaceContext, useToolbarTools, type Tool } from "../shell/tools";
import { ControllerContext } from "../shell/dock/TitleBar";
import { useWindowToggle } from "../shell/windowTools";
import { ActionButton } from "../ui/ActionButton";
import { PALETTES, readPalette, setPalette, subscribeTheme, type Palette } from "../ui/theme";
import { SettingLabel, helpRow } from "../ui/SettingLabel";
import { JS_BUILD } from "../version";

// The theme values stay lowercase internally (they are the stored setting), so
// the button captions are a lookup rather than a capitalization of the value.
const THEME_LABEL_KEYS: Record<Theme, string> = {
  light: "settings.themeLight",
  auto: "settings.themeAuto",
  dark: "settings.themeDark",
};

const PALETTE_LABEL_KEYS: Record<Palette, string> = {
  spartan: "settings.colorTheme.spartan",
  wayfinding: "settings.colorTheme.wayfinding",
  framed: "settings.colorTheme.framed",
  mist: "settings.colorTheme.mist",
  office: "settings.colorTheme.office",
  aubergine: "settings.colorTheme.aubergine",
};

/** Offer Find Setting, and any tools of the window's own, on the toolbar. */
function useSettingsTools(window: SettingsWindowId, own: Tool[] = []): void {
  // Find Setting belongs to Settings; a Settings window floating in Logs offers no tools of its own.
  const inSettings = (useContext(WindowPlaceContext)?.workspace ?? "settings") === "settings";
  const find = useFindSettingTool();
  useToolbarTools(inSettings ? [{ id: window, label: t(SETTINGS_WINDOW_TITLE_KEYS[window]), tools: [...(find ? [find] : []), ...own] }] : []);
}

/** A window's frame: the settings once loaded, and the error of a change made here. */
function SettingsBody({ error, children }: { error: string | null; children: ReactNode }) {
  return (
    <div className="phx-window-pad phx-settings">
      {error && <div className="banner banner-error" role="alert">{error}</div>}
      {children}
    </div>
  );
}

/**
 * A section folded into its title until opened; Find Setting opens it to reach a setting inside.
 * Its title names its Guide section; the settings inside name their own.
 */
function Disclosure({ title, guide, help, children }: { title: string; guide: string; help?: string; children: ReactNode }) {
  return (
    <details className="phx-disclosure">
      <summary data-guide={guide} data-help={help}>{title}</summary>
      <div className="phx-disclosure-body">{children}</div>
    </details>
  );
}

export function AiProvidersWindow() {
  const { settings, saving, change, error } = useSettingsChange();
  const s = useSettings();
  // Adding an account opens on request, from the toolbar or Find Setting (user, 2026-10-31).
  const [adding, setAdding] = useState(false);
  const revealedAdd = s?.revealed?.id === "providerAdd" ? s.revealed.n : 0;
  useEffect(() => { if (revealedAdd) setAdding(true); }, [revealedAdd]);
  useSettingsTools("settingsProviders", [
    { id: "addAccount", label: t("settings.addAccount"), icon: "add", help: "settings.addAccountHelp", pressed: adding, onSelect: () => setAdding((open) => !open) },
  ]);
  if (!settings) return <Loading />;
  return (
    <SettingsBody error={error}>
      <LlmProviderSettings settings={settings} onChange={change} saving={saving} adding={adding} onAddingChange={setAdding} />
      <Disclosure title={t("routing.providerSection")} guide="window.settingsProviders#decision-provider" help="routing.providerSectionHelp">
        <DecisionProviderSettings settings={settings} onChange={change} saving={saving} />
      </Disclosure>
    </SettingsBody>
  );
}

export function AgentChatSettingsWindow() {
  const { settings, saving, change, error } = useSettingsChange();
  useSettingsTools("settingsChat");
  if (!settings) return <Loading />;
  return (
    <SettingsBody error={error}>
      <AgentCliSettings
        routingMode={settings.agentcli_routing_mode}
        onRoutingChange={(value) => change("agentcli_routing_mode", value)}
        scrollback={settings.agentcli_scrollback_lines}
        onScrollbackChange={(n) => change("agentcli_scrollback_lines", n)}
        maxIterations={settings.agentcli_max_iterations}
        onMaxIterationsChange={(n) => change("agentcli_max_iterations", n)}
        globalVisible={settings.agentcli_global}
        onGlobalChange={(v) => change("agentcli_global", v)}
        conversationStyle={settings.agentcli_conversation_style}
        onConversationStyleChange={(v) => change("agentcli_conversation_style", v)}
        detailLevel={settings.agentcli_detail_level}
        onDetailLevelChange={(v) => change("agentcli_detail_level", v)}
        homeFocused={settings.agentcli_home_focused}
        onHomeFocusedChange={(v) => change("agentcli_home_focused", v)}
        saving={saving}
        statistics={<RoutingStatisticsButton surface="chat" />}
      />
    </SettingsBody>
  );
}

/** Each surface's routing counts float over Settings in a window of their own. */
const STATISTICS_WINDOW = { chat: "chatRoutingStatistics", voice: "voiceRoutingStatistics" } as const;

/**
 * Show Routing Statistics: opens and closes the surface's counts floating over the
 * workspace (user, 2026-10-31). Where the workspace has no such window, as outside
 * the shell, the counts unfold in place instead.
 */
function RoutingStatisticsButton({ surface }: { surface: "voice" | "chat" }) {
  const id = STATISTICS_WINDOW[surface];
  const toggle = useWindowToggle(id);
  const controller = useContext(ControllerContext);
  if (!toggle || !controller?.windows.includes(id)) return <RoutingStatistics surface={surface} />;
  return (
    <div className="phx-window-actions phx-window-actions-end routing-statistics-open">
      <ActionButton icon="statistics" label={t("routingStats.show")} className="btn btn-outline btn-sm"
        help="routingStats.showHelp" pressed={toggle.open} onClick={toggle.toggle} />
    </div>
  );
}

export function ChatRoutingStatisticsWindow() {
  return <div className="phx-window-pad"><RoutingStatisticsContent surface="chat" /></div>;
}

export function VoiceRoutingStatisticsWindow() {
  return <div className="phx-window-pad"><RoutingStatisticsContent surface="voice" /></div>;
}

const tabId = (tab: HomeTab) => `phx-home-tab-${tab}`;
/** What each tab is for, shown in Help over the tab and its empty space. */
const HOME_TAB_HELP_KEYS: Record<HomeTab, string> = { voice: "settings.voiceIntro", aiTask: "settings.aiTaskIntro", assist: "settings.assistIntro" };
const panelId = (tab: HomeTab) => `phx-home-panel-${tab}`;
/** Each tab's section on the Home Assistant page of the Guide. */
const HOME_TAB_GUIDE: Record<HomeTab, string> = { voice: "window.settingsHome#voice-agent", aiTask: "window.settingsHome#ai-task", assist: "window.settingsHome#assist-tool-provider" };

/** Voice Agent, AI Task and Assist Tool Provider on local tabs; each tab keeps its state while another is shown. */
export function HomeAssistantWindow() {
  const s = useSettings();
  const { settings, saving, change, saveForResult, error } = useSettingsChange();
  useSettingsTools("settingsHome");
  if (!s || !settings) return <Loading />;
  const current = s.homeTab;
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const at = HOME_TABS.indexOf(current);
    const next = e.key === "ArrowRight" ? HOME_TABS[(at + 1) % HOME_TABS.length]
      : e.key === "ArrowLeft" ? HOME_TABS[(at + HOME_TABS.length - 1) % HOME_TABS.length]
        : e.key === "Home" ? HOME_TABS[0] : e.key === "End" ? HOME_TABS[HOME_TABS.length - 1] : null;
    if (!next) return;
    e.preventDefault();
    s.setHomeTab(next);
    (e.currentTarget.parentElement?.querySelector(`#${tabId(next)}`) as HTMLElement | null)?.focus();
  };
  return (
    <SettingsBody error={error}>
      <div className="phx-local-tabs" role="tablist" aria-label={t("workspace.homeAssistant")}>
        {HOME_TABS.map((tab) => (
          <button key={tab} id={tabId(tab)} type="button" role="tab" className="phx-local-tab" aria-selected={tab === current} data-guide={HOME_TAB_GUIDE[tab]}
            data-help-text={tPlain(HOME_TAB_HELP_KEYS[tab])}
            aria-controls={panelId(tab)} tabIndex={tab === current ? 0 : -1} onClick={() => s.setHomeTab(tab)} onKeyDown={onKeyDown}>
            {t(HOME_TAB_LABEL_KEYS[tab])}
          </button>
        ))}
      </div>
      <div id={panelId("voice")} role="tabpanel" aria-labelledby={tabId("voice")} hidden={current !== "voice"}>
        <VoiceAgentSettings settings={settings} onChange={change} onSave={saveForResult} saving={saving} onRefresh={s.refresh}
          statistics={<RoutingStatisticsButton surface="voice" />} />
      </div>
      <div id={panelId("aiTask")} role="tabpanel" aria-labelledby={tabId("aiTask")} hidden={current !== "aiTask"}>
        <AiTaskSettings settings={settings} onChange={change} saving={saving} />
      </div>
      <div id={panelId("assist")} role="tabpanel" aria-labelledby={tabId("assist")} hidden={current !== "assist"}>
        <AssistBridgeSettings settings={settings} onChange={change} saving={saving} />
      </div>
    </SettingsBody>
  );
}

export function SafetyWindow() {
  const { settings, saving, change, error } = useSettingsChange();
  const [scopes, setScopes] = useState<WipeScopes>(DEFAULT_WIPE_SCOPES);
  const [showWipe, setShowWipe] = useState(false);
  useSettingsTools("settingsSafety");
  if (!settings) return <Loading />;
  const nothingSelected = !scopes.core && !scopes.providers && !scopes.mesa;
  // The warning about Enforced without profiles applies only while Enforced is chosen.
  const mesaModeHelp = settings.mesa_mode === "enforced"
    ? `${t("settings.mesaModeHelp")}${t("common.sentenceSeparator")}${t("settings.mesaEnforcedWarning")}`
    : t("settings.mesaModeHelp");
  return (
    <SettingsBody error={error}>
      <div id="phx-kill-switch-card" className="phx-kill-switch">
        <KillSwitch settings={settings} onToggle={(value) => change("kill_switch", value)} saving={saving} />
      </div>
      {/* What the modes do, and what Enforced means without profiles, is in Help (user, 2026-10-31). */}
      <div className="toggle-row toggle-row-stacked-control" {...helpRow(mesaModeHelp)}>
        <SettingLabel help={mesaModeHelp}><span>{t("settings.mesaModeLabel")}</span></SettingLabel>
        <BusySelect aria-label={t("settings.mesaModeAria")} className="input input-auto" value={settings.mesa_mode} busy={saving}
          onChange={(e) => change("mesa_mode", e.target.value)}>
          <option value="off">{t("settings.mesaModeOff")}</option>
          <option value="advisory">{t("settings.mesaModeAdvisory")}</option>
          <option value="enforced">{t("settings.mesaModeEnforced")}</option>
        </BusySelect>
      </div>
      <Disclosure title={t("settings.dataCard")} guide="window.settingsSafety#data-management" help="settings.dataCardHelp">
        {/* The choices form one group, without rules between them (user, 2026-10-31). */}
        <div className="phx-setting-checks">
          {WIPE_SCOPES.map((scope) => (
            <div key={scope.key} className="toggle-row" {...helpRow(t(scope.helpKey))}>
              <SettingLabel help={t(scope.helpKey)}><span>{t(scope.labelKey)}</span></SettingLabel>
              <label className="toggle-switch">
                <input type="checkbox" aria-label={t(scope.labelKey)} checked={scopes[scope.key]}
                  onChange={(e) => setScopes((prev) => ({ ...prev, [scope.key]: e.target.checked }))} />
                <span className="toggle-switch-track" />
              </label>
            </div>
          ))}
        </div>
        <div className="phx-window-actions phx-window-actions-end">
          <ActionButton icon="delete" label={t("settings.wipeSubmit")} className="btn btn-danger btn-sm" disabled={nothingSelected}
            help="settings.wipeSubmitHelp" onClick={() => setShowWipe(true)} />
        </div>
      </Disclosure>
      {showWipe && <WipeConfirmModal scopes={scopes} onWiped={() => window.location.reload()} onClose={() => setShowWipe(false)} />}
    </SettingsBody>
  );
}

export function LogsSettingsWindow() {
  const { settings, saving, change, error } = useSettingsChange();
  useSettingsTools("settingsLogs");
  if (!settings) return <Loading />;
  const greyed = settings.disable_all_logging ? " toggle-row-greyed" : "";
  return (
    <SettingsBody error={error}>
      <div className="phx-settings-columns">
        <section aria-labelledby="phx-settings-audit">
          <h3 id="phx-settings-audit" className="phx-section-title">{t("shell.tabAudit")}</h3>
          <LoggingSettings settings={settings} onToggle={change} saving={saving} only={AUDIT_TOGGLES} />
          <div className={`toggle-row toggle-row-stacked-control${greyed}`} {...helpRow(t("settings.flushIntervalHelp"))}>
            <SettingLabel help={t("settings.flushIntervalHelp")}><span>{t("settings.flushIntervalLabel")}</span></SettingLabel>
            <BusySelect aria-label={t("settings.flushIntervalLabel")} className="input input-auto" value={settings.audit_flush_interval}
              busy={saving} disabled={settings.disable_all_logging} onChange={(e) => change("audit_flush_interval", Number(e.target.value))}>
              <option value={0}>{t("common.never")}</option>
              {[5, 10, 15, 30, 60].map((n) => <option key={n} value={n}>{tn("settings.flushEveryMinutes", n)}</option>)}
            </BusySelect>
          </div>
          <div className={`toggle-row toggle-row-stacked-control${greyed}`} {...helpRow(t("settings.maxLogEntriesHelp"))}>
            <SettingLabel help={t("settings.maxLogEntriesHelp")}><span>{t("settings.maxLogEntriesLabel")}</span></SettingLabel>
            <BusySelect aria-label={t("settings.maxLogEntriesLabel")} className="input input-auto" value={settings.audit_log_maxlen}
              busy={saving} disabled={settings.disable_all_logging} onChange={(e) => change("audit_log_maxlen", Number(e.target.value))}>
              <option value={100}>100</option>
              <option value={1000}>1,000</option>
              <option value={5000}>5,000</option>
              <option value={10000}>10,000</option>
            </BusySelect>
          </div>
        </section>
        {/* The Conversation Log's settings fold under its name (user, 2026-10-31). */}
        <Disclosure title={t("conversations.title")} guide="window.settingsLogs#conversation-log" help="conversations.settingsHelp">
          <ConversationSettings settings={settings} saving={saving} onChange={change} />
        </Disclosure>
      </div>
      <Disclosure title={t("settings.privacySection")} guide="window.settingsLogs#privacy" help="settings.privacySectionHelp">
        <LoggingSettings settings={settings} onToggle={change} saving={saving} only={PRIVACY_TOGGLES} />
      </Disclosure>
      <Disclosure title={t("settings.haNotifications")} guide="window.settingsLogs#notifications" help="settings.haNotificationsHelp">
        <NotificationSettings settings={settings} onToggle={change} saving={saving} />
      </Disclosure>
    </SettingsBody>
  );
}

export function GeneralSettingsWindow() {
  const s = useSettings();
  const palette = useSyncExternalStore(subscribeTheme, readPalette);
  const { settings, saving, change, error } = useSettingsChange();
  const injectBlocked = useSyncExternalStore(subscribeInjectCompatibility, injectSessionBlocked);
  useSettingsTools("settingsGeneral");
  if (!s || !settings) return <Loading />;
  const info = s.info;
  return (
    <SettingsBody error={error}>
      <div className="toggle-row toggle-row-stacked-control" {...helpRow(t("settings.languageHelp"))}>
        <SettingLabel help={t("settings.languageHelp")}><span>{t("settings.languageLabel")}</span></SettingLabel>
        <select aria-label={t("settings.languageLabel")} className="input input-auto" value={s.language} onChange={(e) => s.onLanguageChange(e.target.value)}>
          {/* Each language is named in itself, so someone who cannot read
              the current UI language can still find their own. */}
          <option value={LANGUAGE_AUTO}>{t("settings.languageAuto")}</option>
          {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.endonym}</option>)}
        </select>
      </div>
      <div className="toggle-row" {...helpRow(t("settings.themeHelp"))}>
        <SettingLabel help={t("settings.themeHelp")}><span>{t("settings.themeLabel")}</span></SettingLabel>
        <div className="theme-toggle">
          {(["light", "auto", "dark"] as Theme[]).map((mode) => (
            <button key={mode} type="button" className={`theme-toggle-btn${s.theme === mode ? " active" : ""}`} aria-pressed={s.theme === mode}
              onClick={() => s.onThemeChange(mode)}>
              {t(THEME_LABEL_KEYS[mode])}
            </button>
          ))}
        </div>
      </div>
      <div className="toggle-row toggle-row-stacked-control" {...helpRow(t("settings.colorThemeHelp"))}>
        <SettingLabel help={t("settings.colorThemeHelp")}><span>{t("settings.colorThemeLabel")}</span></SettingLabel>
        <select aria-label={t("settings.colorThemeLabel")} className="input input-auto" value={palette} onChange={(e) => setPalette(e.target.value as Palette)}>
          {PALETTES.map((p) => <option key={p} value={p}>{t(PALETTE_LABEL_KEYS[p])}</option>)}
        </select>
      </div>
      {/* A rule sets the presets and profile buttons apart; no heading names them (user, 2026-10-31). */}
      <hr className="settings-divider" />
      {/* The two switches form one group, without a rule between them (user, 2026-10-31). */}
      <div className="phx-setting-checks">
        <div className="toggle-row" {...helpRow(t("settings.presetsHelp"))}>
          <SettingLabel help={t("settings.presetsHelp")}><span>{t("settings.presetsLabel")}</span></SettingLabel>
          <label className={`toggle-switch${saving ? " disabled" : ""}`}>
            <BusyInput type="checkbox" aria-label={t("settings.presetsLabel")} checked={settings.access_key_presets_enabled} busy={saving}
              onChange={(e) => change("access_key_presets_enabled", e.target.checked)} />
            <span className="toggle-switch-track" />
          </label>
        </div>
        <div className="toggle-row" {...helpRow(t("settings.injectHelp"))}>
          {/* Why the switch is off stays in the window; what it does is in Help. */}
          <SettingLabel help={injectBlocked ? null : t("settings.injectHelp")} helpId="phx-inject-help">
            <span>{t("settings.injectLabel")}</span>
            {injectBlocked && <small id="phx-inject-help">{t("settings.injectUnavailable")}</small>}
          </SettingLabel>
          <label className={`toggle-switch${saving || injectBlocked ? " disabled" : ""}`}>
            <BusyInput type="checkbox" aria-label={t("settings.injectLabel")} aria-describedby="phx-inject-help"
              checked={settings.mesa_inject_enabled && !injectBlocked} busy={saving} disabled={injectBlocked}
              onChange={(e) => change("mesa_inject_enabled", e.target.checked)} />
            <span className="toggle-switch-track" />
          </label>
        </div>
      </div>
      <details className="phx-disclosure">
        <summary data-settings-anchor="about" data-help="settings.aboutHelp">{t("settings.about")}</summary>
        <div className="phx-disclosure-body settings-info-list" data-help-text={tPlain("settings.infoStorageNote")}>
          <div><strong>{t("settings.infoVersionLabel")}</strong> {info?.version ?? "..."}</div>
          <div><strong>{t("settings.infoJsBuildLabel")}</strong> {JS_BUILD}</div>
          <div><strong>{t("settings.infoMinHaLabel")}</strong> {info?.minHaVersion ?? "..."}</div>
          <div>
            <a href={info?.githubUrl ?? "#"} target="_blank" rel="noopener noreferrer" className="settings-info-link" data-help="settings.githubHelp">{t("settings.infoGithubLink")}</a>
          </div>
        </div>
      </details>
    </SettingsBody>
  );
}
