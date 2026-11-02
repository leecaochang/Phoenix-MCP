import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, screen, waitFor } from "@testing-library/react";

const listAccessKeys = vi.fn();
const getAgentCliProviders = vi.fn();
const getAgentCliModels = vi.fn();
const getAiTaskPreferred = vi.fn();
const setAiTaskPreferred = vi.fn();
const clearAiTaskPreferred = vi.fn();

vi.mock("../api", () => ({
  api: {
    listAccessKeys: (...a: unknown[]) => listAccessKeys(...a),
    getAgentCliProviders: (...a: unknown[]) => getAgentCliProviders(...a),
    getAgentCliModels: (...a: unknown[]) => getAgentCliModels(...a),
    getAiTaskPreferred: (...a: unknown[]) => getAiTaskPreferred(...a),
    setAiTaskPreferred: (...a: unknown[]) => setAiTaskPreferred(...a),
    clearAiTaskPreferred: (...a: unknown[]) => clearAiTaskPreferred(...a),
  },
}));

function prefStatus(over: Record<string, unknown> = {}) {
  return {
    supported: true, entity_id: "ai_task.phoenix_mcp_ai_task",
    gen_data_entity_id: null, gen_data_name: null, is_preferred: false, ...over,
  };
}

import { Notifications, NotificationsContext } from "../ui/notify";
import { AiTaskSettings } from "../components/AiTaskSettings";
import type { GlobalSettings } from "../types";
import { t } from "../i18n";

function settings(over: Partial<GlobalSettings> = {}): GlobalSettings {
  return {
    ai_task_enabled: false,
    ai_task_access_key_id: null,
    ai_task_provider_id: null,
    ai_task_model: null,
    ai_task_supported: true,
    ...(over as object),
  } as unknown as GlobalSettings;
}

function renderCard(s: GlobalSettings, onChange = vi.fn()) {
  const center = new Notifications();
  render(<NotificationsContext.Provider value={center}><AiTaskSettings settings={s} onChange={onChange} saving={false} /></NotificationsContext.Provider>);
  return { onChange, center };
}

const DEFAULT = "Default AI Task";
const defaultSwitch = () => screen.findByRole("checkbox", { name: DEFAULT }) as Promise<HTMLInputElement>;

describe("AiTaskSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listAccessKeys.mockResolvedValue([{ id: "tok-1", name: "ai-access-key" }]);
    getAgentCliProviders.mockResolvedValue({ instances: [{ id: "i1", kind: "claude", name: "Claude", model: "claude-opus-4-8" }] });
    getAgentCliModels.mockResolvedValue({ models: ["claude-opus-4-8"] });
    getAiTaskPreferred.mockResolvedValue(prefStatus());
    setAiTaskPreferred.mockResolvedValue(prefStatus({ is_preferred: true, gen_data_entity_id: "ai_task.phoenix_mcp_ai_task" }));
    clearAiTaskPreferred.mockResolvedValue(prefStatus());
  });

  it("renders and toggles enable", async () => {
    const { onChange } = renderCard(settings());
    fireEvent.click(screen.getByLabelText("Enable Phoenix MCP AI Task"));
    expect(onChange).toHaveBeenCalledWith("ai_task_enabled", true);
    await waitFor(() => expect(listAccessKeys).toHaveBeenCalled());
  });

  it("writes the access key and provider selections", async () => {
    const { onChange } = renderCard(settings());
    await waitFor(() => expect(screen.getByRole("option", { name: "ai-access-key" })).toBeTruthy());
    fireEvent.change(screen.getByLabelText("AI Task access key"), { target: { value: "tok-1" } });
    expect(onChange).toHaveBeenCalledWith("ai_task_access_key_id", "tok-1");
    fireEvent.change(screen.getByLabelText("AI Task provider account"), { target: { value: "i1" } });
    expect(onChange).toHaveBeenCalledWith("ai_task_provider_id", "i1");
  });

  it("places select help above all three dropdowns without stacking the switch", () => {
    renderCard(settings());
    for (const label of ["AI Task access key", "AI Task provider account", "AI Task model"]) {
      expect(screen.getByLabelText(label).closest(".toggle-row")).toHaveClass("toggle-row-stacked-control");
    }
    expect(screen.getByLabelText("Enable Phoenix MCP AI Task").closest(".toggle-row"))
      .not.toHaveClass("toggle-row-stacked-control");
  });

  it("renders style and detail without a Home-focused control", () => {
    const { onChange } = renderCard(settings());
    fireEvent.change(screen.getByLabelText("AI Task conversation style"), {
      target: { value: "technical" },
    });
    fireEvent.change(screen.getByLabelText("AI Task detail level"), {
      target: { value: "detailed" },
    });
    expect(onChange).toHaveBeenCalledWith("ai_task_conversation_style", "technical");
    expect(onChange).toHaveBeenCalledWith("ai_task_detail_level", "detailed");
    expect(screen.queryByLabelText("AI Task Home-focused mode")).toBeNull();
    expect(screen.getByText(t("settings.aiTaskBehaviorFreeTextOnly"), { exact: false })).toBeInTheDocument();
  });

  it("model select is gated on a provider", async () => {
    const { rerender } = render(<AiTaskSettings settings={settings()} onChange={vi.fn()} saving={false} />);
    expect((screen.getByLabelText("AI Task model") as HTMLSelectElement).disabled).toBe(true);
    rerender(<AiTaskSettings settings={settings({ ai_task_provider_id: "i1" })} onChange={vi.fn()} saving={false} />);
    await waitFor(() => expect(getAgentCliModels).toHaveBeenCalledWith("i1"));
    expect((screen.getByLabelText("AI Task model") as HTMLSelectElement).disabled).toBe(false);
  });

  it("disables everything when the HA seam is unsupported", () => {
    renderCard(settings({ ai_task_supported: false }));
    expect((screen.getByLabelText("Enable Phoenix MCP AI Task") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("AI Task access key") as HTMLSelectElement).disabled).toBe(true);
  });

  it("offers Use as Default once the entity exists and it is not yet the default", async () => {
    renderCard(settings());
    const box = await defaultSwitch();
    await waitFor(() => expect(box).toBeEnabled());
    expect(box).not.toBeChecked();
  });

  it("keeps Use as Default unavailable until the entity exists", async () => {
    getAiTaskPreferred.mockResolvedValue(prefStatus({ entity_id: null }));
    renderCard(settings());
    expect(await defaultSwitch()).toBeDisabled();
    expect(screen.getByText("Enable and fully configure the AI Task first.")).toBeTruthy();
  });

  it("sets the default directly and names the default it replaced", async () => {
    getAiTaskPreferred.mockResolvedValue(
      prefStatus({ gen_data_entity_id: "ai_task.claude_ai_task", gen_data_name: "Claude AI Task" }),
    );
    const { center } = renderCard(settings());
    const box = await defaultSwitch();
    await waitFor(() => expect(box).toBeEnabled());
    fireEvent.click(box);
    await waitFor(() => expect(setAiTaskPreferred).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(box).toBeChecked());
    expect(center.toast?.text).toBe("Phoenix MCP AI Task is now the default, replacing Claude AI Task");
  });

  it("clears the default directly when Phoenix MCP is the default", async () => {
    getAiTaskPreferred.mockResolvedValue(
      prefStatus({ is_preferred: true, gen_data_entity_id: "ai_task.phoenix_mcp_ai_task" }),
    );
    const { center } = renderCard(settings());
    const box = await defaultSwitch();
    await waitFor(() => expect(box).toBeChecked());
    fireEvent.click(box);
    await waitFor(() => expect(clearAiTaskPreferred).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(box).not.toBeChecked());
    expect(center.toast?.text).toBe("Phoenix MCP AI Task is no longer the default");
  });

  it("reports a failed default change and reads the status again", async () => {
    setAiTaskPreferred.mockRejectedValueOnce(new Error("Home Assistant refused"));
    renderCard(settings());
    const box = await defaultSwitch();
    await waitFor(() => expect(box).toBeEnabled());
    fireEvent.click(box);
    expect(await screen.findByRole("alert")).toHaveTextContent("Home Assistant refused");
    await waitFor(() => expect(getAiTaskPreferred).toHaveBeenCalledTimes(2));
    expect(box).not.toBeChecked();
  });

  it("hides Use as Default when the preferences seam is unavailable", async () => {
    getAiTaskPreferred.mockResolvedValue(prefStatus({ supported: false, entity_id: null }));
    renderCard(settings());
    await waitFor(() => expect(getAiTaskPreferred).toHaveBeenCalled());
    expect(screen.queryByRole("checkbox", { name: DEFAULT })).toBeNull();
  });

  it("enables directly, without offering a default dialog", async () => {
    const { onChange } = renderCard(settings());
    await defaultSwitch();  // pref loaded
    fireEvent.click(screen.getByLabelText("Enable Phoenix MCP AI Task"));
    expect(onChange).toHaveBeenCalledExactlyOnceWith("ai_task_enabled", true);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(setAiTaskPreferred).not.toHaveBeenCalled();
  });

  it("disables directly when Phoenix MCP is the default, and says the default went with it", async () => {
    getAiTaskPreferred.mockResolvedValue(prefStatus({ is_preferred: true, gen_data_entity_id: "ai_task.phoenix_mcp_ai_task" }));
    const onChange = vi.fn().mockResolvedValue(true);
    const { center } = renderCard(settings({ ai_task_enabled: true }), onChange);
    await waitFor(async () => expect(await defaultSwitch()).toBeChecked());
    fireEvent.click(screen.getByLabelText("Enable Phoenix MCP AI Task"));  // toggle off
    expect(onChange).toHaveBeenCalledExactlyOnceWith("ai_task_enabled", false);
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(center.toast?.text).toBe("AI Task disabled, and it is no longer the default"));
  });

  it("does not expose internal decision review for any access key", async () => {
    listAccessKeys.mockResolvedValue([{ id: "tok-1", name: "Home access key", persona: "home_companion" },
      { id: "tok-2", name: "Ordinary access key", persona: "voice_assistant" }]);
    const { rerender } = render(<AiTaskSettings settings={settings({ ai_task_access_key_id: "tok-1", ai_task_tool_routing_mode: "observe" })} onChange={vi.fn()} saving={false} />);
    await screen.findByRole("option", { name: "Home access key" });
    expect(screen.queryByLabelText("Tool selection review")).toBeNull();
    rerender(<AiTaskSettings settings={settings({ ai_task_access_key_id: "tok-2", ai_task_tool_routing_mode: "observe" })} onChange={vi.fn()} saving={false} />);
    expect(screen.queryByLabelText("Tool selection review")).toBeNull();
  });

});
