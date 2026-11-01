import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, screen, waitFor } from "@testing-library/react";

const listAccessKeys = vi.fn();
const getAgentCliProviders = vi.fn();
const getAgentCliModels = vi.fn();
const createVoiceAgentPipeline = vi.fn();
const deleteVoiceAgentPipeline = vi.fn();
const setVoiceAgentPreferred = vi.fn();

vi.mock("../api", () => ({
  api: {
    listAccessKeys: (...a: unknown[]) => listAccessKeys(...a),
    getAgentCliProviders: (...a: unknown[]) => getAgentCliProviders(...a),
    getAgentCliModels: (...a: unknown[]) => getAgentCliModels(...a),
    createVoiceAgentPipeline: (...a: unknown[]) => createVoiceAgentPipeline(...a),
    deleteVoiceAgentPipeline: (...a: unknown[]) => deleteVoiceAgentPipeline(...a),
    setVoiceAgentPreferred: (...a: unknown[]) => setVoiceAgentPreferred(...a),
  },
}));

const notify = vi.fn();
vi.mock("../ui/notify", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../ui/notify")>()),
  useNotify: () => notify,
}));

import { VoiceAgentSettings } from "../components/VoiceAgentSettings";
import type { GlobalSettings } from "../types";

function settings(over: Partial<GlobalSettings> = {}): GlobalSettings {
  return {
    voice_agent_enabled: false,
    voice_agent_access_key_id: null,
    voice_agent_provider_id: null,
    voice_agent_model: null,
    voice_agent_pipeline_id: null,
    voice_agent_pipeline_supported: true,
    ...(over as object),
  } as unknown as GlobalSettings;
}

const FULL = {
  voice_agent_enabled: true,
  voice_agent_access_key_id: "tok-1",
  voice_agent_provider_id: "i1",
  voice_agent_model: "claude-opus-4-8",
};

function renderCard(s: GlobalSettings, onChange = vi.fn(), onRefresh = vi.fn().mockResolvedValue(undefined)) {
  render(<VoiceAgentSettings settings={s} onChange={onChange} saving={false} onRefresh={onRefresh} />);
  return { onChange, onRefresh };
}

const checkbox = (name: string) => screen.getByRole("checkbox", { name }) as HTMLInputElement;
const ASSIST = "Assist setup";
const PREFERRED = "Make Phoenix MCP my preferred assistant";

describe("VoiceAgentSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listAccessKeys.mockResolvedValue([{ id: "tok-1", name: "voice-access-key" }]);
    getAgentCliProviders.mockResolvedValue({ instances: [{ id: "i1", kind: "claude", name: "Claude", model: "claude-opus-4-8" }] });
    getAgentCliModels.mockResolvedValue({ models: ["claude-opus-4-8", "claude-sonnet-5"] });
    createVoiceAgentPipeline.mockResolvedValue({ pipeline_id: "pl1", name: "Phoenix MCP", preferred: true });
    deleteVoiceAgentPipeline.mockResolvedValue({ ok: true });
    setVoiceAgentPreferred.mockResolvedValue({ pipeline_id: "pl1", preferred: true });
  });

  it("renders the settings and toggles enable", async () => {
    const { onChange } = renderCard(settings());
    fireEvent.click(screen.getByLabelText("Enable Phoenix MCP voice agent"));
    expect(onChange).toHaveBeenCalledWith("voice_agent_enabled", true);
    await waitFor(() => expect(listAccessKeys).toHaveBeenCalled());
  });

  it("populates access keys and writes the access key selection", async () => {
    const { onChange } = renderCard(settings());
    await waitFor(() => expect(screen.getByRole("option", { name: "voice-access-key" })).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Voice agent access key"), { target: { value: "tok-1" } });
    expect(onChange).toHaveBeenCalledWith("voice_agent_access_key_id", "tok-1");
  });

  it("places select help above all three dropdowns without stacking the switch", () => {
    renderCard(settings());
    for (const label of ["Voice agent access key", "Voice agent provider account", "Voice agent model"]) {
      expect(screen.getByLabelText(label).closest(".toggle-row")).toHaveClass("toggle-row-stacked-control");
    }
    expect(screen.getByLabelText("Enable Phoenix MCP voice agent").closest(".toggle-row"))
      .not.toHaveClass("toggle-row-stacked-control");
  });

  it("renders and saves voice conversation behavior controls", () => {
    const { onChange } = renderCard(settings());
    fireEvent.change(screen.getByLabelText("Voice Agent conversation style"), {
      target: { value: "lively" },
    });
    fireEvent.change(screen.getByLabelText("Voice Agent detail level"), {
      target: { value: "balanced" },
    });
    fireEvent.click(screen.getByLabelText("Voice Agent Home-focused mode"));
    expect(onChange).toHaveBeenCalledWith("voice_agent_conversation_style", "lively");
    expect(onChange).toHaveBeenCalledWith("voice_agent_detail_level", "balanced");
    expect(onChange).toHaveBeenCalledWith("voice_agent_home_focused", true);
  });

  it("model select is disabled until a provider is chosen, then loads models", async () => {
    const { rerender } = render(<VoiceAgentSettings settings={settings()} onChange={vi.fn()} saving={false} />);
    expect((screen.getByLabelText("Voice agent model") as HTMLSelectElement).disabled).toBe(true);

    rerender(<VoiceAgentSettings settings={settings({ voice_agent_provider_id: "i1" })} onChange={vi.fn()} saving={false} />);
    await waitFor(() => expect(getAgentCliModels).toHaveBeenCalledWith("i1"));
    await waitFor(() => expect(screen.getByRole("option", { name: "claude-sonnet-5" })).toBeTruthy());
    expect((screen.getByLabelText("Voice agent model") as HTMLSelectElement).disabled).toBe(false);
  });

  it("disables provider + model selects when no provider accounts exist", async () => {
    getAgentCliProviders.mockResolvedValue({ instances: [] });
    renderCard(settings());
    await waitFor(() => expect(getAgentCliProviders).toHaveBeenCalled());
    await waitFor(() => expect((screen.getByLabelText("Voice agent provider account") as HTMLSelectElement).disabled).toBe(true));
    expect((screen.getByLabelText("Voice agent model") as HTMLSelectElement).disabled).toBe(true);
  });

  it("refetches provider accounts when the providers-changed event fires", async () => {
    renderCard(settings());
    await waitFor(() => expect(getAgentCliProviders).toHaveBeenCalledTimes(1));
    window.dispatchEvent(new CustomEvent("phx-agentcli-providers-changed"));
    await waitFor(() => expect(getAgentCliProviders).toHaveBeenCalledTimes(2));
  });

  it("applies enabling directly, without offering a setup dialog", async () => {
    const { onChange } = renderCard(settings());
    fireEvent.click(screen.getByLabelText("Enable Phoenix MCP voice agent"));
    expect(onChange).toHaveBeenCalledExactlyOnceWith("voice_agent_enabled", true);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(createVoiceAgentPipeline).not.toHaveBeenCalled();
  });

  it("sets up the assistant from its own switch, without making it preferred", async () => {
    const { onRefresh } = renderCard(settings(FULL));
    const assist = checkbox(ASSIST);
    expect(assist).not.toBeChecked();
    expect(assist).toBeEnabled();
    // Preferred needs the assistant.
    expect(checkbox(PREFERRED)).toBeDisabled();
    fireEvent.click(assist);
    await waitFor(() => expect(createVoiceAgentPipeline).toHaveBeenCalledExactlyOnceWith(false));
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps assistant setup unavailable until fully configured", () => {
    renderCard(settings({ voice_agent_enabled: true }));  // enabled but no access key/provider/model
    expect(checkbox(ASSIST)).toBeDisabled();
    expect(screen.getByText("Select an access key, provider, and model first.")).toBeTruthy();
  });

  it("removes the assistant directly when its switch is turned off", async () => {
    const { onRefresh } = renderCard(settings({ ...FULL, voice_agent_pipeline_id: "pl1" }));
    expect(checkbox(ASSIST)).toBeChecked();
    fireEvent.click(checkbox(ASSIST));
    await waitFor(() => expect(deleteVoiceAgentPipeline).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
    expect(createVoiceAgentPipeline).not.toHaveBeenCalled();
    expect(notify).toHaveBeenCalledWith("Phoenix MCP assistant removed from Home Assistant");
  });

  it("does not report the assistant removed when Home Assistant keeps it", async () => {
    deleteVoiceAgentPipeline.mockResolvedValue({ ok: true, kept: true });
    const { onRefresh } = renderCard(settings({ ...FULL, voice_agent_pipeline_id: "pl1" }));
    fireEvent.click(checkbox(ASSIST));
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).not.toHaveBeenCalledWith("Phoenix MCP assistant removed from Home Assistant");
  });

  it("shows Home Assistant's actual preference and sets or clears it, keeping the assistant", async () => {
    const { onRefresh } = renderCard(settings({ ...FULL, voice_agent_pipeline_id: "pl1", voice_agent_pipeline_preferred: false }));
    expect(checkbox(PREFERRED)).not.toBeChecked();
    expect(checkbox(PREFERRED)).toBeEnabled();
    fireEvent.click(checkbox(PREFERRED));
    await waitFor(() => expect(setVoiceAgentPreferred).toHaveBeenCalledExactlyOnceWith(true));
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    // The switch follows the settings it was given, not the click.
    expect(checkbox(PREFERRED)).not.toBeChecked();
  });

  it("clears the preference without removing the assistant", async () => {
    renderCard(settings({ ...FULL, voice_agent_pipeline_id: "pl1", voice_agent_pipeline_preferred: true }));
    expect(checkbox(PREFERRED)).toBeChecked();
    fireEvent.click(checkbox(PREFERRED));
    await waitFor(() => expect(setVoiceAgentPreferred).toHaveBeenCalledExactlyOnceWith(false));
    expect(deleteVoiceAgentPipeline).not.toHaveBeenCalled();
  });

  it("does not claim a preference without the assistant", () => {
    renderCard(settings({ ...FULL, voice_agent_pipeline_id: null, voice_agent_pipeline_preferred: true }));
    expect(checkbox(PREFERRED)).not.toBeChecked();
    expect(checkbox(PREFERRED)).toBeDisabled();
  });

  it("reports a refused preference change and still shows the saved state", async () => {
    setVoiceAgentPreferred.mockRejectedValueOnce(new Error("The Phoenix MCP assistant is the only one, so Home Assistant keeps it preferred."));
    const { onRefresh } = renderCard(settings({ ...FULL, voice_agent_pipeline_id: "pl1", voice_agent_pipeline_preferred: true }));
    fireEvent.click(checkbox(PREFERRED));
    expect(await screen.findByRole("alert")).toHaveTextContent("only one, so Home Assistant keeps it preferred");
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
    expect(checkbox(PREFERRED)).toBeChecked();
  });

  it("hides assistant setup and preference when the HA seam is unsupported", () => {
    renderCard(settings({ ...FULL, voice_agent_pipeline_supported: false }));
    expect(screen.queryByRole("checkbox", { name: ASSIST })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: PREFERRED })).toBeNull();
  });

  it("disables directly when an assistant exists, then reads the settings again", async () => {
    const onChange = vi.fn().mockResolvedValue(true);
    const { onRefresh } = renderCard(settings({ ...FULL, voice_agent_pipeline_id: "pl1" }), onChange);
    fireEvent.click(screen.getByLabelText("Enable Phoenix MCP voice agent"));  // toggle off
    expect(onChange).toHaveBeenCalledExactlyOnceWith("voice_agent_enabled", false);
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
  });

  it("does not read the settings again when disabling failed", async () => {
    const onChange = vi.fn().mockResolvedValue(false);
    const { onRefresh } = renderCard(settings({ ...FULL, voice_agent_pipeline_id: "pl1" }), onChange);
    fireEvent.click(screen.getByLabelText("Enable Phoenix MCP voice agent"));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it("does not expose internal decision review for any access key", async () => {
    listAccessKeys.mockResolvedValue([{ id: "tok-1", name: "Home access key", persona: "home_companion" },
      { id: "tok-2", name: "Ordinary access key", persona: "voice_assistant" }]);
    const { rerender } = render(<VoiceAgentSettings settings={settings({ voice_agent_access_key_id: "tok-1", voice_agent_tool_routing_mode: "observe" })} onChange={vi.fn()} saving={false} />);
    await screen.findByRole("option", { name: "Home access key" });
    expect(screen.queryByLabelText("Tool selection review")).toBeNull();
    rerender(<VoiceAgentSettings settings={settings({ voice_agent_access_key_id: "tok-2", voice_agent_tool_routing_mode: "observe" })} onChange={vi.fn()} saving={false} />);
    expect(screen.queryByLabelText("Tool selection review")).toBeNull();
  });

});

describe("VoiceAgentSettings layout", () => {
  it("puts routing statistics at the bottom of the tab", async () => {
    listAccessKeys.mockResolvedValue([{ id: "tok-1", name: "voice-access-key" }]);
    getAgentCliProviders.mockResolvedValue({ instances: [] });
    const { container } = render(<VoiceAgentSettings settings={settings(FULL)} onChange={vi.fn()} saving={false} />);
    await screen.findByText("Routing Statistics");
    const stats = container.querySelector("details") as HTMLElement;
    expect(stats.textContent).toContain("Routing Statistics");
    expect((stats.closest(".phx-settings-form") as HTMLElement).lastElementChild).toBe(stats);
  });
});
