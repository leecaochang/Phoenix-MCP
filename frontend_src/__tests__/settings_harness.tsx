/**
 * Renders the Settings workspace as the shell does: its six windows around
 * their shared state, each in a `data-phx-window` frame inside one root, so
 * reveals and Find Setting can find their settings. Windows offer their tools
 * to `registry` as they do in the shell, and `state` follows the shared
 * state. Mock `../api` with the names in `settings_api.ts` and call
 * `mockSettingsApi()` first.
 */

import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import { vi } from "vitest";
import { api } from "../api";
import { SettingsProvider, useSettings, type SettingsState } from "../shell/settings";
import type { SettingsWindowId } from "../shell/settingsIndex";
import { ToolRegistry, ToolRegistryContext, WindowPlaceContext } from "../shell/tools";
import type { GlobalSettings } from "../types";
import {
  AgentChatSettingsWindow, AiProvidersWindow, GeneralSettingsWindow, HomeAssistantWindow, LogsSettingsWindow, SafetyWindow,
} from "../views/SettingsWindows";

export const BASE_SETTINGS = {
  kill_switch: false, mesa_mode: "advisory", access_key_presets_enabled: false, mesa_inject_enabled: true,
  disable_all_logging: false, log_allowed: true, log_denied: true, log_rate_limited: true, log_entity_names: true, log_client_ip: true,
  audit_flush_interval: 0, audit_log_maxlen: 1000, notify_on_approval: true, notify_on_rate_limit: false,
  conversation_log_enabled: true, conversation_log_max_entries: 100, conversation_log_max_days: 7, conversation_log_max_mb: 25,
  agentcli_routing_mode: "off", agentcli_scrollback_lines: 200, agentcli_max_iterations: 10, agentcli_global: false,
  agentcli_conversation_style: "direct", agentcli_detail_level: "balanced", agentcli_home_focused: false,
  provider_catalog_refresh_enabled: true, provider_catalog_refresh_hours: 24,
  voice_agent_enabled: false, voice_agent_pipeline_supported: true, ai_task_supported: true, assist_api_supported: true,
} as unknown as GlobalSettings;

/** Nothing configured: no keys, no provider accounts, nothing recorded. */
export function mockSettingsApi(): void {
  vi.mocked(api.getInfo).mockResolvedValue({ version: "9.9.9", min_ha_version: "2026.7.0", github_url: "https://example.invalid/repo" });
  vi.mocked(api.getSettings).mockResolvedValue(BASE_SETTINGS);
  vi.mocked(api.getConversations).mockResolvedValue({ entries: [], total: 0, enabled: true, available: true, bytes: 0, max_bytes: 25000000 } as never);
  vi.mocked(api.listAccessKeys).mockResolvedValue([]);
  vi.mocked(api.getAgentCliProviders).mockResolvedValue({ instances: [], provider_types: [] } as never);
  vi.mocked(api.getAgentCliModels).mockResolvedValue({ models: [] } as never);
  vi.mocked(api.getAiEvaluatePreferred).mockResolvedValue({ entity_id: null, evaluate_entity_id: null, evaluate_name: null, is_preferred: false });
  vi.mocked(api.getAiTaskPreferred).mockResolvedValue({ supported: false, entity_id: null, gen_data_entity_id: null, gen_data_name: null, is_preferred: false });
  vi.mocked(api.getDecisionProviders).mockResolvedValue({ instances: [], provider_types: [] } as never);
}

const WINDOWS: [SettingsWindowId, string, () => React.ReactElement | null][] = [
  ["settingsProviders", "AI Providers", AiProvidersWindow],
  ["settingsChat", "Agent Chat", AgentChatSettingsWindow],
  ["settingsHome", "Home Assistant", HomeAssistantWindow],
  ["settingsSafety", "Safety", SafetyWindow],
  ["settingsLogs", "Logs and Notifications", LogsSettingsWindow],
  ["settingsGeneral", "General", GeneralSettingsWindow],
];

/** Hands the shared state to the test. */
function Probe({ into }: { into: { current: SettingsState | null } }) {
  into.current = useSettings();
  return null;
}

function Harness({ initial, onSettingsChange, registry, revealWindow, onNavigate, onLanguageChange, state }: {
  state: { current: SettingsState | null };
  initial: GlobalSettings | null;
  onSettingsChange: (s: GlobalSettings) => void;
  registry: ToolRegistry;
  revealWindow?: (id: SettingsWindowId) => void;
  onNavigate?: () => void;
  onLanguageChange: (language: string) => void;
}) {
  const [settings, setSettings] = useState(initial);
  const [root, setRoot] = useState<HTMLDivElement | null>(null);
  return (
    <ToolRegistryContext.Provider value={registry}>
      <SettingsProvider settings={settings} onSettingsChange={(s) => { onSettingsChange(s); setSettings(s); }} theme="auto" onThemeChange={() => {}}
        language="auto" onLanguageChange={onLanguageChange} root={root} revealWindow={revealWindow} onNavigate={onNavigate}>
        <Probe into={state} />
        <div ref={setRoot}>
          {WINDOWS.map(([id, name, Window]) => (
            <WindowPlaceContext.Provider key={id} value={{ workspace: "settings", window: id }}>
              <section aria-label={`${name} window`} data-phx-window={id}><Window /></section>
            </WindowPlaceContext.Provider>
          ))}
        </div>
      </SettingsProvider>
    </ToolRegistryContext.Provider>
  );
}

export function renderSettings({ settings = BASE_SETTINGS, revealWindow, onNavigate }: {
  settings?: GlobalSettings | null;
  revealWindow?: (id: SettingsWindowId) => void;
  onNavigate?: () => void;
} = {}) {
  const onSettingsChange = vi.fn();
  const onLanguageChange = vi.fn();
  const registry = new ToolRegistry();
  const state: { current: SettingsState | null } = { current: null };
  const view = render(<Harness initial={settings} onSettingsChange={onSettingsChange} registry={registry} state={state}
    revealWindow={revealWindow} onNavigate={onNavigate} onLanguageChange={onLanguageChange} />);
  return { ...view, onSettingsChange, onLanguageChange, registry, state };
}

export const WINDOW_ORDER = WINDOWS.map(([id]) => id);
/** The tools the windows offer, as the toolbar would show them. */
export const toolsFor = (registry: ToolRegistry) => registry.groupsFor("settings", WINDOW_ORDER, "Settings");

export const settingsWindow = (name: string) => within(screen.getByRole("region", { name: `${name} window` }));
