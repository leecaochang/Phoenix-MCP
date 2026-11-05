/**
 * The panel's one-time reads: a failure must not strand what waits on them.
 * Settings windows and the panel's Agent Chat depend on these, and nothing else
 * would ask again before a manual page reload.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

const api = vi.hoisted(() => ({
  getSettings: vi.fn(),
  listAccessKeys: vi.fn(),
  getAgentCliProviders: vi.fn(),
  listApprovals: vi.fn(async () => ({ approvals: [], total: 0 })),
  listAttention: vi.fn(async () => ({ items: [] })),
  getInfo: vi.fn(),
  postCardCatalog: vi.fn(),
}));
vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: { ...actual.api, ...api }, currentHass: () => null };
});

import { PanelDataProvider, usePanelData, type PanelData } from "../shell/PanelData";
import { SettingsProvider, useSettings } from "../shell/settings";
import { SafetyWindow } from "../views/SettingsWindows";
import { noteConnection, __resetConnectionState } from "../utils/connection";
import { BASE_SETTINGS } from "./settings_harness";

let panel: PanelData;
function Probe() {
  panel = usePanelData();
  return null;
}
function Version() {
  return <p data-testid="version">{useSettings()?.info?.version ?? "none"}</p>;
}
function SettingsWindowHost() {
  const data = usePanelData();
  return (
    <SettingsProvider settings={data.settings} onSettingsChange={data.setSettings} theme="auto" onThemeChange={() => {}} language="auto" onLanguageChange={() => {}}>
      <SafetyWindow />
      <Version />
    </SettingsProvider>
  );
}

async function mount(children: React.ReactNode = null) {
  render(
    <PanelDataProvider hass={null} narrow={false} theme="auto" onThemeChange={() => {}} language="auto" onLanguageChange={() => {}} initialWorkspace="settings">
      <Probe />
      {children}
    </PanelDataProvider>,
  );
  await act(async () => {});
}

async function homeAssistantReturns() {
  await act(async () => { noteConnection(false); });
  await act(async () => { noteConnection(true); });
}

beforeEach(() => {
  __resetConnectionState();
  vi.clearAllMocks();
  api.getSettings.mockResolvedValue(BASE_SETTINGS);
  api.listAccessKeys.mockResolvedValue([]);
  api.getAgentCliProviders.mockResolvedValue({ instances: [] });
  api.getInfo.mockResolvedValue({ version: "9.9.9", min_ha_version: "2026.7.0", github_url: "https://example.invalid/repo" });
});

describe("the settings read", () => {
  it("runs again when Home Assistant answers after failing at mount", async () => {
    api.getSettings.mockRejectedValueOnce(new Error("offline"));
    await mount();
    expect(panel.settings).toBeNull();
    await homeAssistantReturns();
    expect(panel.settings).toEqual(BASE_SETTINGS);
    expect(panel.settingsError).toBeNull();
  });

  it("is not repeated once the settings are here, so it cannot undo a save", async () => {
    await mount();
    api.getSettings.mockClear();
    await homeAssistantReturns();
    expect(api.getSettings).not.toHaveBeenCalled();
  });

  it("does not replace settings a save answered with while it ran", async () => {
    let answer: (value: unknown) => void = () => {};
    api.getSettings.mockRejectedValueOnce(new Error("offline"));
    await mount();
    api.getSettings.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
    await homeAssistantReturns();
    const saved = { ...BASE_SETTINGS, kill_switch: true };
    await act(async () => { panel.setSettings(saved); });
    await act(async () => { answer(BASE_SETTINGS); });
    expect(panel.settings).toEqual(saved);
  });

  it("says so in a Settings window, with a way to try again", async () => {
    api.getSettings.mockRejectedValueOnce(new Error("Service unavailable"));
    await mount(<SettingsWindowHost />);
    expect(screen.getByRole("alert").textContent).toBe("Service unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Try Again" }));
    await act(async () => {});
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.querySelector("#phx-kill-switch-card")).toBeTruthy();
  });

  it("keeps showing the wait while the read runs", async () => {
    api.getSettings.mockReturnValueOnce(new Promise(() => {}));
    await mount(<SettingsWindowHost />);
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("the version read", () => {
  it("runs again when Home Assistant answers", async () => {
    api.getInfo.mockRejectedValueOnce(new Error("offline"));
    await mount(<SettingsWindowHost />);
    expect(screen.getByTestId("version").textContent).toBe("none");
    await homeAssistantReturns();
    expect(screen.getByTestId("version").textContent).toBe("9.9.9");
  });
});

describe("the lists Agent Chat chooses from", () => {
  it("are loaded only when both have been read", async () => {
    let providers: (value: unknown) => void = () => {};
    api.getAgentCliProviders.mockReturnValueOnce(new Promise((resolve) => { providers = resolve; }));
    await mount();
    expect(panel.agentCliListsLoaded).toBe(false);
    await act(async () => { providers({ instances: [] }); });
    expect(panel.agentCliListsLoaded).toBe(true);
  });

  it("are read again when Home Assistant answers", async () => {
    api.listAccessKeys.mockRejectedValueOnce(new Error("offline"));
    api.getAgentCliProviders.mockRejectedValueOnce(new Error("offline"));
    await mount();
    expect(panel.agentCliListsLoaded).toBe(false);
    await homeAssistantReturns();
    expect(panel.agentCliListsLoaded).toBe(true);
  });

  it("are read again when Agent Chat is opened, for a failure that was not the connection", async () => {
    api.getAgentCliProviders.mockRejectedValueOnce(new Error("Service unavailable"));
    await mount();
    expect(panel.agentCliListsLoaded).toBe(false);
    await act(async () => { panel.openAgentCli(); });
    expect(panel.agentCliListsLoaded).toBe(true);
  });

  it("report a failed read until a new attempt starts", async () => {
    api.getAgentCliProviders.mockRejectedValueOnce(new Error("Service unavailable"));
    await mount();
    expect(panel.agentCliListFailed).toBe(true);
    await act(async () => { panel.openAgentCli(); });
    expect(panel.agentCliListFailed).toBe(false);
    expect(panel.agentCliListsLoaded).toBe(true);
  });

  it("stay loaded when a later read fails", async () => {
    await mount();
    api.listAccessKeys.mockRejectedValueOnce(new Error("offline"));
    await act(async () => { await panel.refreshAccessKeys(); });
    expect(panel.agentCliListsLoaded).toBe(true);
  });
});
