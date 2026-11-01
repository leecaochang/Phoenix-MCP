---
{ "id": "workspace.settings", "title": "Settings", "order": 60, "keywords": ["options", "preferences", "configuration"], "covers": ["frontend_src/views/SettingsWindows.tsx", "frontend_src/shell/settingsIndex.ts"] }
---
Settings that apply to all of Phoenix, in six windows. Select a window, or one of its settings, to read about it here.

- [{{label:workspace.aiProviders}}](guide:window.settingsProviders): the AI accounts Phoenix uses, and the optional {{label:routing.providerSection}}.
- [{{label:settings.agentcliCard}}](guide:window.settingsChat): how Agent Chat behaves.
- [{{label:workspace.homeAssistant}}](guide:window.settingsHome): Phoenix in Home Assistant's voice, AI Task and Assist features.
- [{{label:workspace.safety}}](guide:window.settingsSafety): the {{label:settings.killSwitchCard}}, MESA enforcement and {{label:settings.dataCard}}.
- [{{label:workspace.logsNotifications}}](guide:window.settingsLogs): what's recorded, and when Home Assistant notifies you.
- [{{label:workspace.general}}](guide:window.settingsGeneral): language, theme, optional features and version.

To find a setting by name, use **{{label:workspace.findSetting}}** on the toolbar. It opens the window the setting is in.

Changes save automatically. Settings apply to every access key and every Home Assistant user, except {{label:settings.languageLabel}} and {{label:settings.themeLabel}}, which apply to this browser only.
