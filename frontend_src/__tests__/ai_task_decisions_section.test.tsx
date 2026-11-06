import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, screen, waitFor } from "@testing-library/react";

const listAccessKeys = vi.fn();
const getAgentCliProviders = vi.fn();
const getAgentCliModels = vi.fn();
const getAiTaskPreferred = vi.fn();
const getAiEvaluatePreferred = vi.fn();
const setAiEvaluatePreferred = vi.fn();
const clearAiEvaluatePreferred = vi.fn();

vi.mock("../api", () => ({
  api: {
    listAccessKeys: (...a: unknown[]) => listAccessKeys(...a),
    getAgentCliProviders: (...a: unknown[]) => getAgentCliProviders(...a),
    getAgentCliModels: (...a: unknown[]) => getAgentCliModels(...a),
    getAiTaskPreferred: (...a: unknown[]) => getAiTaskPreferred(...a),
    getAiEvaluatePreferred: (...a: unknown[]) => getAiEvaluatePreferred(...a),
    setAiEvaluatePreferred: (...a: unknown[]) => setAiEvaluatePreferred(...a),
    clearAiEvaluatePreferred: (...a: unknown[]) => clearAiEvaluatePreferred(...a),
  },
}));

import { Notifications, NotificationsContext } from "../ui/notify";
import { AiTaskSettings } from "../components/AiTaskSettings";
import type { GlobalSettings } from "../types";
import { t } from "../i18n";

const ENTITY = "ai_task.phoenix_mcp_decisions";

function settings(over: Partial<GlobalSettings> = {}): GlobalSettings {
  return {
    ai_task_supported: true, ai_task_decisions_supported: true, decision_provider_id: "jev",
    ai_task_decisions_enabled: true, ai_task_decisions_access_key_id: "tok-1",
    ...(over as object),
  } as unknown as GlobalSettings;
}

function renderCard(s: GlobalSettings, onChange = vi.fn()) {
  const center = new Notifications();
  render(<NotificationsContext.Provider value={center}><AiTaskSettings settings={s} onChange={onChange} saving={false} /></NotificationsContext.Provider>);
  return { onChange, center };
}

const enableName = () => t("settings.aiDecisionsEnableLabel");
const defaultName = () => t("settings.aiDecisionsDefaultLabel");
const lastToast = (center: Notifications) => center.toast?.text;

describe("AiTaskDecisionsSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listAccessKeys.mockResolvedValue([{ id: "tok-1", name: "decisions", cap_service_response: "allow" }]);
    getAgentCliProviders.mockResolvedValue({ instances: [] });
    getAgentCliModels.mockResolvedValue({ models: [] });
    getAiTaskPreferred.mockResolvedValue({ supported: true, entity_id: null, gen_data_entity_id: null, gen_data_name: null, is_preferred: false });
    getAiEvaluatePreferred.mockResolvedValue({ entity_id: ENTITY, evaluate_entity_id: null, evaluate_name: null, is_preferred: false });
    setAiEvaluatePreferred.mockResolvedValue({ entity_id: ENTITY, evaluate_entity_id: ENTITY, evaluate_name: "Phoenix MCP Decisions", is_preferred: true });
    clearAiEvaluatePreferred.mockResolvedValue({ entity_id: ENTITY, evaluate_entity_id: null, evaluate_name: null, is_preferred: false });
  });

  it.each([
    ["an older Home Assistant", { ai_task_decisions_supported: false }],
    ["an unreported version", { ai_task_decisions_supported: undefined }],
    ["no decision provider in use", { decision_provider_id: null }],
  ])("is absent with %s", async (_name, over) => {
    renderCard(settings(over));
    await waitFor(() => expect(listAccessKeys).toHaveBeenCalled());
    expect(screen.queryByText(t("settings.aiDecisionsHeading"))).toBeNull();
    expect(screen.queryByRole("checkbox", { name: enableName() })).toBeNull();
    expect(getAiEvaluatePreferred).not.toHaveBeenCalled();
  });

  it("saves the switch and the access key", async () => {
    const { onChange } = renderCard(settings({ ai_task_decisions_enabled: false }));
    fireEvent.click(screen.getByRole("checkbox", { name: enableName() }));
    expect(onChange).toHaveBeenCalledWith("ai_task_decisions_enabled", true);
    const select = screen.getByRole("combobox", { name: t("settings.aiDecisionsAccessKeyAria") });
    await screen.findAllByRole("option", { name: "decisions" });
    fireEvent.change(select, { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith("ai_task_decisions_access_key_id", "");
  });

  it("makes Phoenix the default and clears it", async () => {
    const { center } = renderCard(settings());
    const toggle = await screen.findByRole("checkbox", { name: defaultName() }) as HTMLInputElement;
    await waitFor(() => expect(toggle.disabled).toBe(false));
    fireEvent.click(toggle);
    await waitFor(() => expect(setAiEvaluatePreferred).toHaveBeenCalled());
    await waitFor(() => expect(lastToast(center)).toBe(t("settings.aiDecisionsDefaultSetToast")));
    await waitFor(() => expect(toggle.checked).toBe(true));
    fireEvent.click(toggle);
    await waitFor(() => expect(clearAiEvaluatePreferred).toHaveBeenCalled());
  });

  it("names the default it replaces", async () => {
    getAiEvaluatePreferred.mockResolvedValue({ entity_id: ENTITY, evaluate_entity_id: "ai_task.other", evaluate_name: "Other", is_preferred: false });
    const { center } = renderCard(settings());
    const toggle = await screen.findByRole("checkbox", { name: defaultName() }) as HTMLInputElement;
    await waitFor(() => expect(toggle.getAttribute("data-help")).toBe("help.settingsAiDecisionsReplaceDefault"));
    fireEvent.click(toggle);
    await waitFor(() => expect(lastToast(center)).toBe(t("settings.aiDecisionsDefaultReplacedToast", { name: "Other" })));
  });

  it("cannot be made default before the entity exists", async () => {
    getAiEvaluatePreferred.mockResolvedValue({ entity_id: null, evaluate_entity_id: null, evaluate_name: null, is_preferred: false });
    renderCard(settings());
    expect(await screen.findByText(t("settings.aiDecisionsConfigureFirst"))).toBeTruthy();
    expect((screen.getByRole("checkbox", { name: defaultName() }) as HTMLInputElement).disabled).toBe(true);
  });

  it.each([
    ["deny", false, true], ["allow", false, false], ["deny", true, false],
  ])("capability %s with pass-through %s", async (mode, passThrough, shown) => {
    listAccessKeys.mockResolvedValue([{ id: "tok-1", name: "decisions", cap_service_response: mode, pass_through: passThrough }]);
    renderCard(settings());
    await screen.findAllByRole("option", { name: "decisions" });
    const note = t("settings.aiDecisionsCapabilityNote", { capability: t("caps.cap_service_response.label"), mode: t("caps.modeAllow") });
    expect(!!screen.queryByText(note)).toBe(shown);
  });
});
