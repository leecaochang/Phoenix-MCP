/**
 * The Settings workspace: changes keep their ordering across windows (a read
 * started mid-change never overwrites a change's answer, and saving always
 * clears), a failed change is reported in the window where it was made, every
 * Find Setting entry still finds its control, a reveal opens the window's tab
 * or folded section and focuses the setting, Find Setting searches and picks
 * from the toolbar, and Data Management chooses what a wipe deletes before the
 * typed confirmation.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { api } from "../api";
import { hasMessage } from "../i18n";
import { blockInjectSession, startInjectSession } from "../inject/compatibility";
import { findControl } from "../shell/settings";
import { SETTING_INDEX } from "../shell/settingsIndex";
import type { GlobalSettings } from "../types";
import { BASE_SETTINGS, WINDOW_ORDER, mockSettingsApi, renderSettings, settingsWindow, toolsFor } from "./settings_harness";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  const { SETTINGS_API } = await import("./settings_api");
  return { ...actual, api: Object.fromEntries(SETTINGS_API.map((n) => [n, vi.fn()])) };
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const killSwitch = () => screen.getByRole("checkbox", { name: "Disable all Phoenix MCP endpoints" });

beforeEach(() => {
  vi.resetAllMocks();
  mockSettingsApi();
});
afterEach(() => {
  delete (window as unknown as Record<string, unknown>).__phxMesaInjectCompatibility;
});

describe("changes across Settings windows", () => {
  it("shows the session-disabled toggle off without changing the saved setting", () => {
    startInjectSession("1.0.1");
    const { onSettingsChange } = renderSettings();
    const toggle = screen.getByRole("checkbox", { name: "In-context profile buttons" });
    const kill = killSwitch();
    expect(toggle).toBeChecked();
    act(() => blockInjectSession("1.0.1"));
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeDisabled();
    expect(kill).not.toBeDisabled();
    fireEvent.click(toggle);
    expect(api.patchSettings).not.toHaveBeenCalled();
    expect(onSettingsChange).not.toHaveBeenCalled();
    act(() => startInjectSession("1.0.2"));
    expect(toggle).toBeChecked();
    expect(toggle).not.toBeDisabled();
  });

  it("never sends in-context profile buttons while the injector is blocked, whoever asks", async () => {
    vi.mocked(api.patchSettings).mockImplementation(async (patch) => ({ ...BASE_SETTINGS, ...patch }) as GlobalSettings);
    startInjectSession("1.0.1");
    blockInjectSession("1.0.1");
    const { state } = renderSettings();
    expect(await act(() => state.current!.patch({ mesa_inject_enabled: true }))).toBeNull();
    expect(api.patchSettings).not.toHaveBeenCalled();
    await act(() => state.current!.patch({ mesa_inject_enabled: true, access_key_presets_enabled: true }));
    expect(api.patchSettings).toHaveBeenCalledExactlyOnceWith({ access_key_presets_enabled: true });
  });

  it("reads a failure detected before Settings was opened", () => {
    startInjectSession("1.0.1");
    blockInjectSession("1.0.1");
    renderSettings();
    const toggle = screen.getByRole("checkbox", { name: "In-context profile buttons" });
    expect(toggle).toBeDisabled();
    expect(toggle).not.toBeChecked();
  });

  it("discards an older refresh response that finishes last", async () => {
    const stale = deferred<GlobalSettings>();
    const current = deferred<GlobalSettings>();
    vi.mocked(api.getSettings).mockReturnValueOnce(stale.promise).mockReturnValueOnce(current.promise);
    const { onSettingsChange } = renderSettings();

    window.dispatchEvent(new CustomEvent("phx-settings-refresh"));
    window.dispatchEvent(new CustomEvent("phx-settings-refresh"));
    await waitFor(() => expect(api.getSettings).toHaveBeenCalledTimes(2));

    const latest = { ...BASE_SETTINGS, mesa_mode: "enforced" } as GlobalSettings;
    current.resolve(latest);
    await waitFor(() => expect(onSettingsChange).toHaveBeenCalledWith(latest));

    stale.resolve({ ...BASE_SETTINGS, kill_switch: true } as GlobalSettings);
    await waitFor(() => expect(onSettingsChange).toHaveBeenCalledTimes(1));
  });

  it("queues a refresh behind a change in flight and always clears saving", async () => {
    const mutation = deferred<GlobalSettings>();
    const refresh = deferred<GlobalSettings>();
    vi.mocked(api.patchSettings).mockReturnValueOnce(mutation.promise);
    vi.mocked(api.getSettings).mockReturnValueOnce(refresh.promise);
    const { onSettingsChange } = renderSettings();
    const kill = killSwitch();

    fireEvent.click(kill);
    await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith({ kill_switch: true }));
    expect(kill).toHaveAttribute("aria-disabled", "true");
    // Every window's controls wait while a change is saved.
    expect(screen.getByRole("checkbox", { name: "Log allowed requests" })).toHaveAttribute("aria-disabled", "true");
    window.dispatchEvent(new CustomEvent("phx-settings-refresh"));
    expect(api.getSettings).not.toHaveBeenCalled();

    const patched = { ...BASE_SETTINGS, kill_switch: true } as GlobalSettings;
    mutation.resolve(patched);
    await waitFor(() => expect(onSettingsChange).toHaveBeenCalledWith(patched));
    await waitFor(() => expect(api.getSettings).toHaveBeenCalledTimes(1));
    expect(kill).not.toHaveAttribute("aria-disabled", "true");

    const refreshed = { ...patched, mesa_mode: "enforced" } as GlobalSettings;
    refresh.resolve(refreshed);
    await waitFor(() => expect(onSettingsChange).toHaveBeenLastCalledWith(refreshed));
  });

  it("discards a refresh that began before a change", async () => {
    const staleRefresh = deferred<GlobalSettings>();
    const mutation = deferred<GlobalSettings>();
    vi.mocked(api.getSettings).mockReturnValueOnce(staleRefresh.promise);
    vi.mocked(api.patchSettings).mockReturnValueOnce(mutation.promise);
    const { onSettingsChange } = renderSettings();
    const kill = killSwitch();

    window.dispatchEvent(new CustomEvent("phx-settings-refresh"));
    await waitFor(() => expect(api.getSettings).toHaveBeenCalledTimes(1));
    fireEvent.click(kill);
    await waitFor(() => expect(api.patchSettings).toHaveBeenCalledTimes(1));

    staleRefresh.resolve({ ...BASE_SETTINGS, kill_switch: false } as GlobalSettings);
    await Promise.resolve();
    expect(onSettingsChange).not.toHaveBeenCalled();

    const patched = { ...BASE_SETTINGS, kill_switch: true } as GlobalSettings;
    mutation.resolve(patched);
    await waitFor(() => expect(onSettingsChange).toHaveBeenCalledTimes(1));
    expect(onSettingsChange).toHaveBeenCalledWith(patched);
    expect(kill).not.toHaveAttribute("aria-disabled", "true");
  });

  it("clears saving even when a queued refresh fails", async () => {
    const mutation = deferred<GlobalSettings>();
    vi.mocked(api.patchSettings).mockReturnValueOnce(mutation.promise);
    vi.mocked(api.getSettings).mockRejectedValueOnce(new Error("refresh failed"));
    renderSettings();
    const kill = killSwitch();

    fireEvent.click(kill);
    await waitFor(() => expect(api.patchSettings).toHaveBeenCalledTimes(1));
    window.dispatchEvent(new CustomEvent("phx-settings-refresh"));
    mutation.resolve({ ...BASE_SETTINGS, kill_switch: true } as GlobalSettings);

    await waitFor(() => expect(api.getSettings).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(kill).not.toHaveAttribute("aria-disabled", "true"));
  });

  it("reports a failed change in the window where it was made, and shows the saved value", async () => {
    vi.mocked(api.patchSettings).mockRejectedValueOnce(new Error("Settings are read-only right now."));
    renderSettings();
    fireEvent.click(killSwitch());
    expect(await settingsWindow("Safety").findByRole("alert")).toHaveTextContent("Settings are read-only right now.");
    expect(settingsWindow("General").queryByRole("alert")).toBeNull();
    expect(killSwitch()).not.toBeChecked();
    // The failure asks for the saved settings again.
    await waitFor(() => expect(api.getSettings).toHaveBeenCalledTimes(1));
  });

  it("saves the three retention limits together, and Discard restores them", async () => {
    vi.mocked(api.patchSettings).mockImplementation(async (patch) => ({ ...BASE_SETTINGS, ...patch }) as GlobalSettings);
    renderSettings();
    const logs = settingsWindow("Logs and Notifications");
    const save = logs.getByRole("button", { name: "Save" });
    const discard = logs.getByRole("button", { name: "Discard" });
    expect(save).toBeDisabled();
    expect(discard).toBeDisabled();
    fireEvent.change(logs.getByLabelText("Maximum entries"), { target: { value: "300" } });
    fireEvent.click(discard);
    expect(logs.getByLabelText("Maximum entries")).toHaveValue(100);
    fireEvent.change(logs.getByLabelText("Maximum age (days)"), { target: { value: "30" } });
    fireEvent.click(save);
    await waitFor(() => expect(api.patchSettings).toHaveBeenCalledExactlyOnceWith({
      conversation_log_max_entries: 100, conversation_log_max_days: 30, conversation_log_max_mb: 25,
    }));
  });
});

describe("Find Setting index", () => {
  it("names every setting with a catalog label and a unique id", () => {
    const ids = SETTING_INDEX.map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(SETTING_INDEX.filter((e) => !hasMessage(e.labelKey)).map((e) => e.id)).toEqual([]);
    expect(SETTING_INDEX.filter((e) => !e.selector && !e.aria).map((e) => e.id)).toEqual([]);
  });

  it("finds every setting's control in its window, with every optional setting shown", async () => {
    vi.mocked(api.getDecisionProviders).mockResolvedValue({ instances: [{ id: "d1", kind: "jev", name: "JEV", model: "jev-latest" }], provider_types: [] } as never);
    vi.mocked(api.getAiTaskPreferred).mockResolvedValue({ supported: true, entity_id: "ai_task.phoenix_mcp_ai_task", gen_data_entity_id: null, gen_data_name: null, is_preferred: false });
    const { container } = renderSettings({ settings: {
      ...BASE_SETTINGS, decision_provider_id: "d1", decision_provider_default_id: "d1", voice_agent_pipeline_id: "pl1",
    } as GlobalSettings });
    await screen.findByRole("radio", { name: "JEV" });
    await screen.findByRole("checkbox", { name: "Default AI Task", hidden: true });
    const missing = SETTING_INDEX.filter((entry) => {
      const windowEl = container.querySelector(`[data-phx-window="${entry.window}"]`);
      return !windowEl || !findControl(windowEl, entry);
    }).map((e) => e.id);
    expect(missing).toEqual([]);
  });

  it("finds settings in the window they are indexed under, not another one", () => {
    const { container } = renderSettings();
    for (const entry of SETTING_INDEX.filter((e) => !e.optional)) {
      const elsewhere = WINDOW_ORDER.filter((id) => id !== entry.window)
        .filter((id) => { const el = container.querySelector(`[data-phx-window="${id}"]`); return !!el && !!findControl(el, entry); });
      // Shared rows (Routing Statistics, conversation style) exist per surface; the index names the right window.
      if (elsewhere.length) expect(entry.selector?.includes("routing-statistics") || entry.aria?.[1], entry.id).toBeTruthy();
    }
  });
});

describe("revealing a setting", () => {
  it("opens the folded section, focuses the setting and highlights its row", async () => {
    const revealWindow = vi.fn();
    const onNavigate = vi.fn();
    const { state } = renderSettings({ revealWindow, onNavigate });
    const privacy = screen.getByText("Privacy").closest("details")!;
    expect(privacy.open).toBe(false);
    act(() => state.current!.reveal("logEntityIds"));
    const box = screen.getByRole("checkbox", { name: "Log entity IDs" });
    await waitFor(() => expect(box).toHaveFocus());
    expect(privacy.open).toBe(true);
    expect(box.closest(".toggle-row")).toHaveClass("phx-setting-found");
    expect(onNavigate).toHaveBeenCalledTimes(1);
    expect(revealWindow).toHaveBeenCalledWith("settingsLogs");
  });

  it("selects the Home Assistant tab a setting is on", async () => {
    const { state } = renderSettings();
    const home = settingsWindow("Home Assistant");
    expect(home.getByRole("tab", { name: "Voice Agent" })).toHaveAttribute("aria-selected", "true");
    act(() => state.current!.reveal("aiTaskAccessKey"));
    await waitFor(() => expect(home.getByLabelText("AI Task access key")).toHaveFocus());
    expect(home.getByRole("tab", { name: "AI Task" })).toHaveAttribute("aria-selected", "true");
    expect(home.getByRole("tabpanel", { name: "AI Task" })).toBeVisible();
    expect(screen.getByLabelText("Voice agent model").closest('[role="tabpanel"]')).not.toBeVisible();
  });

  it("moves between Home Assistant tabs with the arrow keys", () => {
    renderSettings();
    const home = settingsWindow("Home Assistant");
    const voice = home.getByRole("tab", { name: "Voice Agent" });
    voice.focus();
    fireEvent.keyDown(voice, { key: "ArrowRight" });
    expect(home.getByRole("tab", { name: "AI Task" })).toHaveFocus();
    fireEvent.keyDown(home.getByRole("tab", { name: "AI Task" }), { key: "End" });
    expect(home.getByRole("tab", { name: "Assist Tool Provider" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(home.getByRole("tab", { name: "Assist Tool Provider" }), { key: "ArrowRight" });
    expect(voice).toHaveAttribute("aria-selected", "true");
  });

  it("opens the window alone when the setting is not shown in this state", async () => {
    const revealWindow = vi.fn();
    const { state } = renderSettings({ revealWindow });
    const before = document.activeElement;
    // The decision timeout shows only while a decision provider is in use.
    act(() => state.current!.reveal("decisionTimeout"));
    // It keeps looking for a while (the window may still be opening), then stops.
    await waitFor(() => expect(revealWindow).toHaveBeenCalledTimes(60), { timeout: 4000 });
    await new Promise((r) => setTimeout(r, 100));
    expect(revealWindow).toHaveBeenCalledTimes(60);
    expect(revealWindow).toHaveBeenCalledWith("settingsProviders");
    expect(document.activeElement).toBe(before);
  });
});

describe("Find Setting on the toolbar", () => {
  const findTool = (registry: Parameters<typeof toolsFor>[0]) => toolsFor(registry).flatMap((g) => g.tools).find((tool) => tool.id === "findSetting");

  it("shows once for every Settings window, searches, and opens the chosen setting", async () => {
    const { registry } = renderSettings();
    await waitFor(() => expect(findTool(registry)).toBeTruthy());
    expect(toolsFor(registry).flatMap((g) => g.tools).filter((tool) => tool.id === "findSetting")).toHaveLength(1);
    act(() => registry.runShared("settings", WINDOW_ORDER, "findSetting"));
    const dialog = await screen.findByRole("dialog", { name: "Find Setting" });
    const field = within(dialog).getByRole("searchbox", { name: "Find Setting" });
    expect(field).toHaveFocus();
    await waitFor(() => expect(findTool(registry)?.pressed).toBe(true));
    fireEvent.change(field, { target: { value: "kill switch" } });
    const results = within(dialog).getAllByRole("button");
    expect(results[0]).toHaveTextContent("Emergency Kill SwitchSafety");
    fireEvent.keyDown(field, { key: "Enter" });
    await waitFor(() => expect(killSwitch()).toHaveFocus());
    expect(screen.queryByRole("dialog", { name: "Find Setting" })).toBeNull();
  });

  it("names the window and tab of each result, and says when nothing matches", async () => {
    const { registry } = renderSettings();
    await waitFor(() => expect(findTool(registry)).toBeTruthy());
    act(() => registry.runShared("settings", WINDOW_ORDER, "findSetting"));
    const dialog = await screen.findByRole("dialog", { name: "Find Setting" });
    const field = within(dialog).getByRole("searchbox");
    fireEvent.change(field, { target: { value: "voice agent model" } });
    expect(within(dialog).getByRole("button", { name: /Voice agent model/ })).toHaveTextContent("Home Assistant, Voice Agent");
    fireEvent.change(field, { target: { value: "zzzz" } });
    expect(within(dialog).getByText("No matching settings")).toBeVisible();
    fireEvent.keyDown(field, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Find Setting" })).toBeNull();
  });

  it("moves through the results with the arrow keys and opens one with a click", async () => {
    const { registry } = renderSettings();
    await waitFor(() => expect(findTool(registry)).toBeTruthy());
    act(() => registry.runShared("settings", WINDOW_ORDER, "findSetting"));
    const dialog = await screen.findByRole("dialog", { name: "Find Setting" });
    const field = within(dialog).getByRole("searchbox");
    fireEvent.change(field, { target: { value: "log denied" } });
    fireEvent.keyDown(field, { key: "ArrowDown" });
    const result = within(dialog).getByRole("button", { name: /Log denied requests/ });
    expect(result).toHaveFocus();
    fireEvent.click(result);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Log denied requests" })).toHaveFocus());
  });

  it("opens the add-account picker from AI Providers' Add Account tool", async () => {
    vi.mocked(api.getAgentCliProviders).mockResolvedValue({ instances: [], provider_types: [{ kind: "claude", label: "Anthropic", fields: [] }] } as never);
    const { registry } = renderSettings();
    await waitFor(() => expect(settingsWindow("AI Providers").getAllByLabelText("Provider type")[0]).toBeEnabled());
    const add = await waitFor(() => {
      const tool = toolsFor(registry).flatMap((g) => g.tools).find((t) => t.id === "addAccount");
      expect(tool).toBeTruthy();
      return tool!;
    });
    act(() => add.onSelect!());
    await waitFor(() => expect(settingsWindow("AI Providers").getAllByLabelText("Provider type")[0]).toHaveFocus());
  });
});

describe("Safety and Data Management", () => {
  it("chooses what a wipe deletes in place, then asks for WIPE", async () => {
    vi.mocked(api.wipe).mockResolvedValue({} as never);
    renderSettings();
    const safety = settingsWindow("Safety");
    expect(safety.getByText("Data Management").closest("details")!.open).toBe(false);
    const core = safety.getByRole("checkbox", { name: "Delete core Phoenix MCP data" });
    const providers = safety.getByRole("checkbox", { name: "Delete provider accounts and keys" });
    const mesa = safety.getByRole("checkbox", { name: "Delete MESA data" });
    // The credential-bearing scopes start selected; authored MESA policy does not.
    expect(core).toBeChecked();
    expect(providers).toBeChecked();
    expect(mesa).not.toBeChecked();
    fireEvent.click(providers);
    fireEvent.click(safety.getByRole("button", { name: "Wipe Selected Data" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("list")).toHaveTextContent("Delete core Phoenix MCP data");
    expect(within(dialog).getByRole("list")).not.toHaveTextContent("Delete provider accounts and keys");
    const submit = within(dialog).getByRole("button", { name: "Wipe Selected Data" });
    expect(submit).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Type WIPE to confirm"), { target: { value: "WIPE" } });
    fireEvent.click(submit);
    await waitFor(() => expect(api.wipe).toHaveBeenCalledExactlyOnceWith({ wipe_core: true, wipe_providers: false, wipe_mesa: false }));
  });

  it("offers no wipe while nothing is selected", () => {
    renderSettings();
    const safety = settingsWindow("Safety");
    fireEvent.click(safety.getByRole("checkbox", { name: "Delete core Phoenix MCP data" }));
    fireEvent.click(safety.getByRole("checkbox", { name: "Delete provider accounts and keys" }));
    expect(safety.getByRole("button", { name: "Wipe Selected Data" })).toBeDisabled();
  });

  it("warns while MESA is enforced", () => {
    renderSettings({ settings: { ...BASE_SETTINGS, mesa_mode: "enforced" } as GlobalSettings });
    expect(settingsWindow("Safety").getByText(/use the built-in safety baseline/)).toBeVisible();
  });
});

describe("AI Providers", () => {
  it("keeps the Decision Provider and model catalog checks folded, as separate sections", async () => {
    renderSettings();
    const providers = settingsWindow("AI Providers");
    const decision = providers.getByText("Decision Provider").closest("details")!;
    const catalog = providers.getByText("Model Catalog Checks").closest("details")!;
    expect(decision.open).toBe(false);
    expect(catalog.open).toBe(false);
    expect(within(decision).getByRole("checkbox", { name: "Use decision provider" })).toBeInTheDocument();
    expect(within(catalog).getByRole("checkbox", { name: "Check providers for new models" })).toBeInTheDocument();
  });

  it("lists accounts in a table and shows the chosen one below it", async () => {
    vi.mocked(api.getAgentCliProviders).mockResolvedValue({
      instances: [
        { id: "a1", kind: "claude", name: "Work Anthropic", model: "claude-opus-5" },
        { id: "a2", kind: "ollama", name: "Kitchen Ollama", model: "qwen3", model_mode: "auto" },
      ],
      provider_types: [{ kind: "claude", label: "Anthropic", label_key: "settings.providerAnthropic", fields: [] }, { kind: "ollama", label: "Ollama", fields: [] }],
    } as never);
    renderSettings();
    const providers = settingsWindow("AI Providers");
    const table = await providers.findByRole("grid", { name: "LLM Providers" });
    await within(table).findByText("Kitchen Ollama");
    const rows = [...table.querySelectorAll<HTMLElement>("tbody tr[data-row]")];
    expect(rows.map((r) => [...r.querySelectorAll("td:not([aria-hidden])")].map((c) => c.textContent))).toEqual([
      ["Work Anthropic", "Anthropic", "claude-opus-5"],
      ["Kitchen Ollama", "Ollama", "qwen3 (automatic)"],
    ]);
    expect(providers.getByRole("region", { name: "Work Anthropic" })).toBeVisible();
    fireEvent.click(rows[1]);
    expect(providers.getByRole("region", { name: "Kitchen Ollama" })).toHaveTextContent("Default model: automatic, currently qwen3");
    expect(providers.queryByRole("region", { name: "Work Anthropic" })).toBeNull();
    expect(providers.getByRole("button", { name: "Remove Kitchen Ollama" })).toBeVisible();
  });
});
