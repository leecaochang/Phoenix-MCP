/**
 * Every setting Find Setting can reveal: the label a reader searches for,
 * the Settings window it lives in, the Home Assistant tab or folded section
 * around it, and how to find its control there. A test renders every window
 * and checks each entry still resolves to a control.
 */

export type SettingsWindowId = "settingsProviders" | "settingsChat" | "settingsHome" | "settingsSafety" | "settingsLogs" | "settingsGeneral";
export type HomeTab = "voice" | "aiTask" | "assist";

export const HOME_TABS: readonly HomeTab[] = ["voice", "aiTask", "assist"];
export const HOME_TAB_LABEL_KEYS: Record<HomeTab, string> = { voice: "settings.voiceCard", aiTask: "settings.aiTaskCard", assist: "settings.assistCard" };

export const SETTINGS_WINDOW_TITLE_KEYS: Record<SettingsWindowId, string> = {
  settingsProviders: "workspace.aiProviders",
  settingsChat: "settings.agentcliCard",
  settingsHome: "workspace.homeAssistant",
  settingsSafety: "workspace.safety",
  settingsLogs: "workspace.logsNotifications",
  settingsGeneral: "workspace.general",
};

/** An accessible name: a catalog key, with parameters that are catalog keys themselves. */
export type AriaName = [key: string, params?: Record<string, string>];

export interface SettingEntry {
  id: string;
  /** Catalog key of the label shown in the results and searched. */
  labelKey: string;
  window: SettingsWindowId;
  tab?: HomeTab;
  /** How to find the control: its accessible name, or a selector inside the window. */
  aria?: AriaName;
  selector?: string;
  /** Present only in some states (a provider in use, say); the window is still revealed. */
  optional?: boolean;
}

const chat = { surface: "settings.agentcliCard" };
const voice = { surface: "settings.voiceCard" };
const aiTask = { surface: "settings.aiTaskCard" };

export const SETTING_INDEX: readonly SettingEntry[] = [
  { id: "providerAccounts", labelKey: "settings.providersCard", window: "settingsProviders", selector: '[data-settings-anchor="providers"]' },
  { id: "providerAdd", labelKey: "settings.addAccount", window: "settingsProviders", aria: ["settings.agentcliProviderType"] },
  { id: "providerChecks", labelKey: "settings.providerChecksLabel", window: "settingsProviders", aria: ["settings.providerChecksLabel"] },
  { id: "providerChecksHours", labelKey: "settings.providerChecksHoursLabel", window: "settingsProviders", aria: ["settings.providerChecksHoursLabel"] },
  { id: "decisionProvider", labelKey: "routing.providerSection", window: "settingsProviders", selector: 'input[name="decision-account"]' },
  { id: "decisionTimeout", labelKey: "routing.decisionBudget", window: "settingsProviders", selector: '[data-setting="routing-decision-timeout"]', optional: true },
  { id: "decisionRequestLimit", labelKey: "routing.requestLimit", window: "settingsProviders", selector: '[data-setting="routing-request-limit"]', optional: true },

  { id: "chatCommands", labelKey: "routing.enableChat", window: "settingsChat", selector: '[data-setting="routing-chat"]' },
  { id: "chatGlobal", labelKey: "settings.agentcliGlobalLabel", window: "settingsChat", aria: ["settings.agentcliGlobalLabel"] },
  { id: "chatMemory", labelKey: "settings.agentcliScrollbackLabel", window: "settingsChat", aria: ["settings.agentcliScrollbackLabel"] },
  { id: "chatSteps", labelKey: "settings.agentcliStepsLabel", window: "settingsChat", aria: ["settings.agentcliStepsLabel"] },
  { id: "chatStyle", labelKey: "settings.conversationStyleAria", window: "settingsChat", aria: ["settings.conversationStyleAria", chat] },
  { id: "chatDetail", labelKey: "settings.detailLevelAria", window: "settingsChat", aria: ["settings.detailLevelAria", chat] },
  { id: "chatHomeFocused", labelKey: "settings.homeFocusedAria", window: "settingsChat", aria: ["settings.homeFocusedAria", chat] },
  { id: "chatStatistics", labelKey: "routingStats.title", window: "settingsChat", selector: ".routing-statistics-open button, .routing-statistics > summary" },

  { id: "voiceEnable", labelKey: "settings.voiceEnableLabel", window: "settingsHome", tab: "voice", aria: ["settings.voiceEnableLabel"] },
  { id: "voiceAssistant", labelKey: "settings.voiceAssistSetup", window: "settingsHome", tab: "voice", aria: ["settings.voiceAssistSetup"] },
  { id: "voicePreferred", labelKey: "settings.voicePreferredLabel", window: "settingsHome", tab: "voice", aria: ["settings.voicePreferredLabel"] },
  { id: "voiceAccessKey", labelKey: "settings.voiceAccessKeyAria", window: "settingsHome", tab: "voice", aria: ["settings.voiceAccessKeyAria"] },
  { id: "voiceProvider", labelKey: "settings.voiceProviderAria", window: "settingsHome", tab: "voice", aria: ["settings.voiceProviderAria"] },
  { id: "voiceModel", labelKey: "settings.voiceModelAria", window: "settingsHome", tab: "voice", aria: ["settings.voiceModelAria"] },
  { id: "voiceCommands", labelKey: "routing.enableVoice", window: "settingsHome", tab: "voice", selector: '[data-setting="routing-voice"]', optional: true },
  { id: "voiceStyle", labelKey: "settings.conversationStyleAria", window: "settingsHome", tab: "voice", aria: ["settings.conversationStyleAria", voice] },
  { id: "voiceDetail", labelKey: "settings.detailLevelAria", window: "settingsHome", tab: "voice", aria: ["settings.detailLevelAria", voice] },
  { id: "voiceHomeFocused", labelKey: "settings.homeFocusedAria", window: "settingsHome", tab: "voice", aria: ["settings.homeFocusedAria", voice] },
  { id: "voiceStatistics", labelKey: "routingStats.title", window: "settingsHome", tab: "voice", selector: ".routing-statistics-open button, .routing-statistics > summary" },

  { id: "aiTaskEnable", labelKey: "settings.aiTaskEnableLabel", window: "settingsHome", tab: "aiTask", aria: ["settings.aiTaskEnableLabel"] },
  { id: "aiTaskDefault", labelKey: "settings.aiTaskDefaultLabel", window: "settingsHome", tab: "aiTask", aria: ["settings.aiTaskDefaultLabel"], optional: true },
  { id: "aiTaskAccessKey", labelKey: "settings.aiTaskAccessKeyAria", window: "settingsHome", tab: "aiTask", aria: ["settings.aiTaskAccessKeyAria"] },
  { id: "aiTaskProvider", labelKey: "settings.aiTaskProviderAria", window: "settingsHome", tab: "aiTask", aria: ["settings.aiTaskProviderAria"] },
  { id: "aiTaskModel", labelKey: "settings.aiTaskModelAria", window: "settingsHome", tab: "aiTask", aria: ["settings.aiTaskModelAria"] },
  { id: "aiTaskStyle", labelKey: "settings.conversationStyleAria", window: "settingsHome", tab: "aiTask", aria: ["settings.conversationStyleAria", aiTask] },
  { id: "aiTaskDetail", labelKey: "settings.detailLevelAria", window: "settingsHome", tab: "aiTask", aria: ["settings.detailLevelAria", aiTask] },

  { id: "assistAccessKey", labelKey: "settings.assistBoundAccessKeyAria", window: "settingsHome", tab: "assist", aria: ["settings.assistBoundAccessKeyAria"] },

  { id: "killSwitch", labelKey: "settings.killSwitchCard", window: "settingsSafety", selector: "#phx-kill-switch-card input" },
  { id: "mesaMode", labelKey: "settings.mesaModeLabel", window: "settingsSafety", aria: ["settings.mesaModeAria"] },
  { id: "wipeCore", labelKey: "settings.wipeCoreLabel", window: "settingsSafety", aria: ["settings.wipeCoreLabel"] },
  { id: "wipeProviders", labelKey: "settings.wipeProvidersLabel", window: "settingsSafety", aria: ["settings.wipeProvidersLabel"] },
  { id: "wipeMesa", labelKey: "settings.wipeMesaLabel", window: "settingsSafety", aria: ["settings.wipeMesaLabel"] },

  { id: "logEnable", labelKey: "settings.logEnableLabel", window: "settingsLogs", aria: ["settings.logEnableLabel"] },
  { id: "logAllowed", labelKey: "settings.logAllowedLabel", window: "settingsLogs", aria: ["settings.logAllowedLabel"] },
  { id: "logDenied", labelKey: "settings.logDeniedLabel", window: "settingsLogs", aria: ["settings.logDeniedLabel"] },
  { id: "logRateLimited", labelKey: "settings.logRateLimitedLabel", window: "settingsLogs", aria: ["settings.logRateLimitedLabel"] },
  { id: "logFlush", labelKey: "settings.flushIntervalLabel", window: "settingsLogs", aria: ["settings.flushIntervalLabel"] },
  { id: "logMaxEntries", labelKey: "settings.maxLogEntriesLabel", window: "settingsLogs", aria: ["settings.maxLogEntriesLabel"] },
  { id: "conversationLog", labelKey: "conversations.enable", window: "settingsLogs", aria: ["conversations.enable"] },
  { id: "conversationEntries", labelKey: "conversations.limit.entries", window: "settingsLogs", selector: '[data-setting="conversation_log_max_entries"]' },
  { id: "conversationDays", labelKey: "conversations.limit.days", window: "settingsLogs", selector: '[data-setting="conversation_log_max_days"]' },
  { id: "conversationSize", labelKey: "conversations.limit.mb", window: "settingsLogs", selector: '[data-setting="conversation_log_max_mb"]' },
  { id: "logEntityIds", labelKey: "settings.logEntityIdsLabel", window: "settingsLogs", aria: ["settings.logEntityIdsLabel"] },
  { id: "logClientIp", labelKey: "settings.logClientIpLabel", window: "settingsLogs", aria: ["settings.logClientIpLabel"] },
  { id: "notifyApproval", labelKey: "settings.notifyApprovalLabel", window: "settingsLogs", aria: ["settings.notifyApprovalLabel"] },
  { id: "notifyRateLimit", labelKey: "settings.notifyRateLimitLabel", window: "settingsLogs", aria: ["settings.notifyRateLimitLabel"] },

  { id: "language", labelKey: "settings.languageLabel", window: "settingsGeneral", aria: ["settings.languageLabel"] },
  { id: "theme", labelKey: "settings.themeLabel", window: "settingsGeneral", selector: ".theme-toggle button" },
  { id: "colorTheme", labelKey: "settings.colorThemeLabel", window: "settingsGeneral", aria: ["settings.colorThemeLabel"] },
  { id: "presets", labelKey: "settings.presetsLabel", window: "settingsGeneral", aria: ["settings.presetsLabel"] },
  { id: "inject", labelKey: "settings.injectLabel", window: "settingsGeneral", aria: ["settings.injectLabel"] },
  { id: "about", labelKey: "settings.about", window: "settingsGeneral", selector: '[data-settings-anchor="about"]' },
];
