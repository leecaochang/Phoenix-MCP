import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, fireEvent, screen, waitFor } from "@testing-library/react";

const getAgentCliProviders = vi.fn();
const probeAgentCliProvider = vi.fn();
const createAgentCliProvider = vi.fn();
const deleteAgentCliProvider = vi.fn();
const getAgentCliModels = vi.fn();
const setAgentCliProviderModel = vi.fn();
const setAgentCliProviderAuto = vi.fn();
const probeAgentCliCapabilities = vi.fn();
const refreshAgentCliProvider = vi.fn();
const localizedApiMessage = vi.fn((
  message: string,
  key?: string,
  _params?: Record<string, string | number>,
) => key ? "本地化连接错误" : message);

vi.mock("../api", () => ({
  localizedApiMessage: (...a: [string, string?, Record<string, string | number>?]) => localizedApiMessage(...a),
  api: {
    getAgentCliProviders: async (...a: unknown[]) => {
      const response = await getAgentCliProviders(...a);
      return { ...response, provider_types: response.provider_types ?? instances().provider_types };
    },
    probeAgentCliProvider: (...a: unknown[]) => probeAgentCliProvider(...a),
    createAgentCliProvider: (...a: unknown[]) => createAgentCliProvider(...a),
    deleteAgentCliProvider: (...a: unknown[]) => deleteAgentCliProvider(...a),
    getAgentCliModels: (...a: unknown[]) => getAgentCliModels(...a),
    setAgentCliProviderModel: (...a: unknown[]) => setAgentCliProviderModel(...a),
    setAgentCliProviderAuto: (...a: unknown[]) => setAgentCliProviderAuto(...a),
    probeAgentCliCapabilities: (...a: unknown[]) => probeAgentCliCapabilities(...a),
    refreshAgentCliProvider: (...a: unknown[]) => refreshAgentCliProvider(...a),
  },
}));

import { AgentCliSettings } from "../components/AgentCliSettings";
import { LlmProviderSettings } from "../components/LlmProviderSettings";
import type { GlobalSettings } from "../types";

function instances() {
  return {
    instances: [{ id: "i1", kind: "deepseek", name: "DeepSeek", model: "deepseek-v4-flash" }],
    provider_types: [
      { kind: "mimo", label: "Xiaomi MiMo", label_key: "settings.providerMiMo",
        fields: [{ id: "api_key", type: "secret", required: true,
          label_key: "settings.agentcliApiKey" }] },
      { kind: "tencent", label: "Tencent Cloud TokenHub", label_key: "settings.providerTencent", fields: [
        { id: "endpoint_id", type: "choice", required: true, label_key: "settings.agentcliSite", choices: [
          { value: "singapore", label: "Singapore", label_key: "settings.agentcliSingapore" },
          { value: "guangzhou", label: "Guangzhou", label_key: "settings.agentcliGuangzhou" },
          { value: "silicon_valley", label: "Silicon Valley", label_key: "settings.agentcliSiliconValley" },
        ] },
        { id: "api_key", type: "secret", required: true, label_key: "settings.agentcliApiKey" },
      ] },
      { kind: "claude", label: "Anthropic", label_key: "settings.providerAnthropic",
        fields: [{ id: "api_key", type: "secret", required: true,
          label_key: "settings.agentcliApiKey" }] },
      { kind: "ollama", label: "Ollama (local)", label_key: "settings.providerOllamaLocal",
        fields: [{ id: "base_url", type: "url", required: true,
          label_key: "settings.agentcliServerUrl", placeholder: "http://host:11434" }] },
      { kind: "zai", label: "Z.ai", label_key: "settings.providerZai", fields: [
        { id: "endpoint_id", type: "choice", required: true,
          label_key: "settings.agentcliZaiPlan", choices: [
            { value: "standard", label: "Standard API", label_key: "settings.agentcliZaiStandard" },
            { value: "coding", label: "Coding Plan", label_key: "settings.agentcliZaiCoding" },
          ] },
        { id: "api_key", type: "secret", required: true,
          label_key: "settings.agentcliApiKey" },
      ] },
      { kind: "qwen", label: "Alibaba Cloud Model Studio / Qwen",
        label_key: "settings.providerQwen", fields: [
          { id: "api_key", type: "secret", required: true,
            label_key: "settings.agentcliApiKey" },
          { id: "base_url", type: "url", required: true,
            label_key: "settings.agentcliBaseUrl", placeholder: "https://workspace.example/v1" },
        ] },
    ],
  };
}

async function beginAdd(kind = "claude") {
  const picker = await screen.findByLabelText("Provider type");
  fireEvent.change(picker, { target: { value: kind } });
}

const onSettingChange = vi.fn();

function renderCard(settings: Partial<GlobalSettings> = {}) {
  // The provider accounts and the chat behaviour are separate cards in the panel;
  // both render here so each test can reach whichever control it exercises.
  return render(
    <>
      <LlmProviderSettings settings={settings as GlobalSettings} onChange={onSettingChange} saving={false} />
      <AgentCliSettings
        routingMode="off"
        onRoutingChange={() => {}}
        scrollback={100}
        onScrollbackChange={() => {}}
        maxIterations={20}
        onMaxIterationsChange={() => {}}
        globalVisible={false}
        onGlobalChange={() => {}}
        conversationStyle="direct"
        onConversationStyleChange={() => {}}
        detailLevel="concise"
        onDetailLevelChange={() => {}}
        homeFocused={false}
        onHomeFocusedChange={() => {}}
        saving={false}
      />
    </>,
  );
}

describe("AgentCliSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getAgentCliProviders.mockResolvedValue(instances());
    probeAgentCliProvider.mockResolvedValue({ ok: true, models: ["claude-opus-4-8", "claude-haiku"] });
    createAgentCliProvider.mockResolvedValue({ instance: { id: "i2", kind: "claude", name: "Claude", model: "claude-opus-4-8" } });
    deleteAgentCliProvider.mockResolvedValue({ deleted: "i1" });
    getAgentCliModels.mockResolvedValue({ models: ["deepseek-v4-flash", "deepseek-v4-pro"] });
    setAgentCliProviderModel.mockResolvedValue({ instance: { id: "i1", model: "deepseek-v4-pro" } });
  });

  it("does not expose internal decision review", async () => {
    renderCard();
    await screen.findByLabelText("Provider type");
    expect(screen.queryByLabelText("Tool selection review")).toBeNull();
    expect(screen.getByLabelText("Handle chat commands directly")).toBeInTheDocument();
  });

  it("Validate with no API key shows an error and does not create", async () => {
    renderCard();
    await waitFor(() => expect(getAgentCliProviders).toHaveBeenCalled());
    await beginAdd();
    fireEvent.click(screen.getByText("Validate"));
    await waitFor(() => expect(screen.getByText(/Enter API key/i)).toBeInTheDocument());
    expect(createAgentCliProvider).not.toHaveBeenCalled();
  });

  it("Validate lists models and turns into Done, which creates the account", async () => {
    renderCard();
    await waitFor(() => expect(getAgentCliProviders).toHaveBeenCalled());
    await beginAdd();
    fireEvent.change(screen.getByPlaceholderText("API key"), { target: { value: "sk-abc" } });
    fireEvent.click(screen.getByText("Validate"));
    await waitFor(() => expect(probeAgentCliProvider).toHaveBeenCalledWith("claude", { api_key: "sk-abc" }));
    // Model dropdown appears; the button is now "Done".
    await waitFor(() => expect(screen.getByText(/Select default model/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText("Done"));
    await waitFor(() => expect(createAgentCliProvider).toHaveBeenCalled());
    expect(createAgentCliProvider.mock.calls[0][0]).toBe("claude");
  });

  it("shows a duplicate error before default-model selection", async () => {
    probeAgentCliProvider.mockRejectedValueOnce(new Error("This provider account is already configured."));
    renderCard();
    await waitFor(() => expect(getAgentCliProviders).toHaveBeenCalled());
    await beginAdd();
    fireEvent.change(screen.getByPlaceholderText("API key"), { target: { value: "sk-abc" } });
    fireEvent.click(screen.getByText("Validate"));

    await waitFor(() => expect(screen.getByText("This provider account is already configured.")).toBeInTheDocument());
    expect(screen.queryByText(/Select default model/i)).toBeNull();
    expect(createAgentCliProvider).not.toHaveBeenCalled();
  });

  it("localizes an HTTP-200 validation failure before rendering it", async () => {
    probeAgentCliProvider.mockResolvedValueOnce({
      ok: false,
      models: [],
      error: "Enter your API key.",
      message_key: "adminError.providerApiKeyRequired",
    });
    renderCard();
    await waitFor(() => expect(getAgentCliProviders).toHaveBeenCalled());
    await beginAdd();
    fireEvent.change(screen.getByPlaceholderText("API key"), { target: { value: "sk-invalid" } });
    fireEvent.click(screen.getByText("Validate"));

    expect(await screen.findByText("本地化连接错误")).toBeInTheDocument();
    expect(localizedApiMessage).toHaveBeenCalledWith(
      "Enter your API key.",
      "adminError.providerApiKeyRequired",
      undefined,
    );
  });

  it("Cancel abandons the add without creating", async () => {
    renderCard();
    await waitFor(() => expect(getAgentCliProviders).toHaveBeenCalled());
    await beginAdd();
    fireEvent.change(screen.getByPlaceholderText("API key"), { target: { value: "sk-abc" } });
    fireEvent.click(screen.getByText("Cancel"));
    await waitFor(() => expect(screen.queryByPlaceholderText("API key")).toBeNull());
    expect(createAgentCliProvider).not.toHaveBeenCalled();
  });

  it("uses the disabled placeholder as the first option and has no Add button", async () => {
    renderCard();
    const picker = await screen.findByLabelText("Provider type") as HTMLSelectElement;
    expect(picker.value).toBe("");
    expect(picker.options[0].textContent).toBe("Add new provider…");
    expect(picker.options[0].disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Add new provider…" })).toBeNull();
  });

  it("resets the picker on failed validation while retaining the form", async () => {
    renderCard();
    await beginAdd();
    const picker = screen.getByLabelText("Provider type") as HTMLSelectElement;
    fireEvent.click(screen.getByText("Validate"));
    expect(picker.value).toBe("");
    expect(screen.getByLabelText("API key")).toBeInTheDocument();
  });

  it("validates and saves the chosen Tencent site with its key", async () => {
    renderCard();
    await beginAdd("tencent");
    expect(screen.getByLabelText("Site")).toHaveValue("singapore");
    expect(screen.getByText(/Keys are site-specific/)).toBeInTheDocument();
    expect(document.querySelector(".agentcli-add-form img")).toBeNull();
    fireEvent.change(screen.getByLabelText("Site"), { target: { value: "silicon_valley" } });
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "t-key" } });
    fireEvent.click(screen.getByText("Validate"));
    await waitFor(() => expect(probeAgentCliProvider).toHaveBeenCalledWith("tencent", {
      endpoint_id: "silicon_valley", api_key: "t-key",
    }));
    fireEvent.click(await screen.findByText("Done"));
    await waitFor(() => expect(createAgentCliProvider).toHaveBeenCalledWith("tencent", expect.objectContaining({
      endpoint_id: "silicon_valley", api_key: "t-key",
    })));
  });

  it("shows MiMo pay-as-you-go guidance without a logo", async () => {
    renderCard();
    await beginAdd("mimo");
    expect(screen.getByText(/Use a pay-as-you-go Xiaomi MiMo API key/)).toBeInTheDocument();
    expect(document.querySelector(".agentcli-add-form img")).toBeNull();
    expect(screen.queryByLabelText("Site")).toBeNull();
  });

  it("serializes the Z.ai endpoint choice", async () => {
    renderCard();
    await beginAdd("zai");
    fireEvent.change(screen.getByLabelText("Z.ai plan"), { target: { value: "coding" } });
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "z-key" } });
    fireEvent.click(screen.getByText("Validate"));
    await waitFor(() => expect(probeAgentCliProvider).toHaveBeenCalledWith("zai", {
      endpoint_id: "coding", api_key: "z-key",
    }));
  });

  it("serializes the Qwen workspace URL", async () => {
    renderCard();
    await beginAdd("qwen");
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "q-key" } });
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://workspace.example/compatible-mode/v1" },
    });
    fireEvent.click(screen.getByText("Validate"));
    await waitFor(() => expect(probeAgentCliProvider).toHaveBeenCalledWith("qwen", {
      api_key: "q-key", base_url: "https://workspace.example/compatible-mode/v1",
    }));
  });

  it("Remove asks for confirmation before deleting the instance", async () => {
    renderCard();
    await waitFor(() => expect(getAgentCliProviders).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText(/^Remove /));
    await waitFor(() => expect(screen.getByText(/Remove provider/i)).toBeInTheDocument());
    expect(deleteAgentCliProvider).not.toHaveBeenCalled();
    const removeButtons = screen.getAllByText("Remove");
    fireEvent.click(removeButtons[removeButtons.length - 1]);
    await waitFor(() => expect(deleteAgentCliProvider).toHaveBeenCalledWith("i1"));
  });

  it("surfaces a failed removal and keeps the confirm dialog open", async () => {
    deleteAgentCliProvider.mockRejectedValueOnce(new Error("server unreachable"));
    renderCard();
    await waitFor(() => expect(getAgentCliProviders).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText(/^Remove /));
    await waitFor(() => expect(screen.getByText(/Remove provider/i)).toBeInTheDocument());
    const removeButtons = screen.getAllByText("Remove");
    fireEvent.click(removeButtons[removeButtons.length - 1]);
    // The error is shown and the dialog stays up (not silently closed as success).
    await waitFor(() => expect(screen.getByText("server unreachable")).toBeInTheDocument());
    expect(screen.getByText(/Remove provider/i)).toBeInTheDocument();
  });

  it("supports adding a second account of an existing kind (Ollama URL field)", async () => {
    renderCard();
    await waitFor(() => expect(getAgentCliProviders).toHaveBeenCalled());
    await beginAdd("ollama");
    // Ollama shows a base-URL field, not an API key field.
    expect(screen.getByPlaceholderText("http://host:11434")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("API key")).toBeNull();
  });

  it("the global-visibility toggle reports changes", async () => {
    const onGlobal = vi.fn();
    render(
      <AgentCliSettings
      routingMode="off"
      onRoutingChange={() => {}}
        scrollback={100}
        onScrollbackChange={() => {}}
        maxIterations={20}
        onMaxIterationsChange={() => {}}
        globalVisible={false}
        onGlobalChange={onGlobal}
        conversationStyle="direct"
        onConversationStyleChange={() => {}}
        detailLevel="concise"
        onDetailLevelChange={() => {}}
        homeFocused={false}
        onHomeFocusedChange={() => {}}
        saving={false}
      />,
    );
    fireEvent.click(screen.getByLabelText("Show Agent Chat throughout Home Assistant"));
    expect(onGlobal).toHaveBeenCalledWith(true);
  });

  it("renders and saves Agent Chat conversation behavior controls", async () => {
    const onStyle = vi.fn();
    const onDetail = vi.fn();
    const onFocus = vi.fn();
    render(
      <AgentCliSettings
      routingMode="off"
      onRoutingChange={() => {}}
        scrollback={100} onScrollbackChange={() => {}}
        maxIterations={20} onMaxIterationsChange={() => {}}
        globalVisible={false} onGlobalChange={() => {}}
        conversationStyle="direct" onConversationStyleChange={onStyle}
        detailLevel="concise" onDetailLevelChange={onDetail}
        homeFocused={false} onHomeFocusedChange={onFocus}
        saving={false}
      />,
    );
    fireEvent.change(screen.getByLabelText("Agent Chat conversation style"), {
      target: { value: "warm" },
    });
    fireEvent.change(screen.getByLabelText("Agent Chat detail level"), {
      target: { value: "detailed" },
    });
    fireEvent.click(screen.getByLabelText("Agent Chat Home-focused mode"));
    expect(onStyle).toHaveBeenCalledWith("warm");
    expect(onDetail).toHaveBeenCalledWith("detailed");
    expect(onFocus).toHaveBeenCalledWith(true);
    expect(screen.getByText(/Focus preference, not a security control/)).toBeInTheDocument();
  });

  it("chat memory auto-saves after a change without an explicit blur (spinner fix)", () => {
    vi.useFakeTimers();
    try {
      const onScroll = vi.fn();
      render(
        <AgentCliSettings
      routingMode="off"
      onRoutingChange={() => {}}
          scrollback={500}
          onScrollbackChange={onScroll}
          maxIterations={20}
          onMaxIterationsChange={() => {}}
          globalVisible={false}
          onGlobalChange={() => {}}
          conversationStyle="direct"
          onConversationStyleChange={() => {}}
          detailLevel="concise"
          onDetailLevelChange={() => {}}
          homeFocused={false}
          onHomeFocusedChange={() => {}}
          saving={false}
        />,
      );
      const input = screen.getByLabelText("Chat history line limit");
      fireEvent.change(input, { target: { value: "300" } });
      expect(onScroll).not.toHaveBeenCalled();  // debounced, nothing yet
      vi.advanceTimersByTime(700);
      expect(onScroll).toHaveBeenCalledWith(300);
    } finally {
      vi.useRealTimers();
    }
  });
});


// The default model can be changed after creation, and the provider's model list
// (a cheap authenticated GET) is fetched when the card is shown, so a retired model
// is flagged where the operator can act on it.
describe("Agent Chat card layout", () => {
  it("shows routing statistics last, after every other setting", async () => {
    const { container } = renderCard();
    await screen.findByLabelText("Check providers for new models");
    const stats = container.querySelector("details.routing-stats, details");
    expect(stats?.textContent).toContain("Routing statistics");
    const chat = stats!.closest(".card") as HTMLElement;
    // Nothing from the Agent Chat card follows the disclosure.
    expect(chat.lastElementChild).toBe(stats);
    // And the provider controls are a different card from the chat settings.
    expect(chat.textContent).not.toContain("Check providers for new models");
  });
});

describe("LLM providers card controls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getAgentCliProviders.mockResolvedValue(instances());
    getAgentCliModels.mockResolvedValue({ models: ["deepseek-v4-flash"] });
  });

  it("has provider checks on at 24 hours by default and reports changes", async () => {
    renderCard();
    const toggle = (await screen.findByLabelText("Check providers for new models")) as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    const hours = screen.getByLabelText("Hours between provider checks") as HTMLInputElement;
    expect(hours.value).toBe("24");
    expect(hours.disabled).toBe(false);
    fireEvent.click(toggle);
    expect(onSettingChange).toHaveBeenCalledWith("provider_catalog_refresh_enabled", false);
    fireEvent.change(hours, { target: { value: "48" } });
    fireEvent.blur(hours);
    expect(onSettingChange).toHaveBeenCalledWith("provider_catalog_refresh_hours", 48);
  });

  it("clamps the period to 6-720 hours and disables it when checks are off", async () => {
    renderCard({ provider_catalog_refresh_enabled: false, provider_catalog_refresh_hours: 72 });
    const hours = (await screen.findByLabelText("Hours between provider checks")) as HTMLInputElement;
    expect(hours.disabled).toBe(true);
    expect(hours.value).toBe("72");
  });

  it("clamps an out-of-range period", async () => {
    renderCard();
    const hours = (await screen.findByLabelText("Hours between provider checks")) as HTMLInputElement;
    fireEvent.change(hours, { target: { value: "9999" } });
    fireEvent.blur(hours);
    expect(onSettingChange).toHaveBeenCalledWith("provider_catalog_refresh_hours", 720);
    fireEvent.change(hours, { target: { value: "1" } });
    fireEvent.blur(hours);
    expect(onSettingChange).toHaveBeenCalledWith("provider_catalog_refresh_hours", 6);
  });
});

describe("AgentCliSettings default model", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getAgentCliProviders.mockResolvedValue(instances());
    getAgentCliModels.mockResolvedValue({ models: ["deepseek-v4-flash", "deepseek-v4-pro"] });
    setAgentCliProviderModel.mockResolvedValue({ instance: { id: "i1", model: "deepseek-v4-pro" } });
  });

  it("changes the default model without touching the credential", async () => {
    renderCard();
    fireEvent.click(await screen.findByLabelText("Change default model"));
    const select = await screen.findByLabelText("Select default model:");
    const editor = select.closest(".agentcli-settings-model-edit") as HTMLElement;
    expect(editor).toHaveClass("agentcli-settings-model-edit");
    expect(editor.querySelector(".agentcli-settings-model-actions")).toBeTruthy();
    expect(editor.querySelector("input[type='checkbox']")?.closest(".toggle-switch")).toBeTruthy();
    expect(editor.querySelector(".agentcli-settings-model-actions button")?.textContent).toBe("Save");
    fireEvent.change(select, { target: { value: "deepseek-v4-pro" } });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(setAgentCliProviderModel).toHaveBeenCalledWith("i1", "deepseek-v4-pro"));
    // Changing a model must never touch the account's credential, so neither
    // delete nor create may be called.
    expect(deleteAgentCliProvider).not.toHaveBeenCalled();
    expect(createAgentCliProvider).not.toHaveBeenCalled();
  });

  it("shows an automatic account as following the newest model", async () => {
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "deepseek", name: "DeepSeek", model: "deepseek-v4-pro", model_mode: "auto" }],
    });
    renderCard();
    expect(await screen.findByText("Default model: automatic, currently deepseek-v4-pro")).toBeTruthy();
    // An automatic account is never flagged as holding a retired model.
    expect(screen.queryByText(/no longer offers/)).toBeNull();
    fireEvent.click(await screen.findByLabelText("Change default model"));
    const select = (await screen.findByLabelText("Select default model:")) as HTMLSelectElement;
    expect(select.value).toBe("");
    // Nothing to save until the choice differs from what the account already does.
    expect(screen.getByText("Save").closest("button")).toBeDisabled();
  });

  it("switches a fixed account back to automatic without sending a model", async () => {
    setAgentCliProviderAuto.mockResolvedValue({ instance: { id: "i1", model: "deepseek-v4-pro", model_mode: "auto" } });
    renderCard();
    fireEvent.click(await screen.findByLabelText("Change default model"));
    const select = await screen.findByLabelText("Select default model:");
    fireEvent.change(select, { target: { value: "" } });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(setAgentCliProviderAuto).toHaveBeenCalledWith("i1"));
    expect(setAgentCliProviderModel).not.toHaveBeenCalled();
  });

  it("tells the operator when a chosen model was retired and replaced", async () => {
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "deepseek", name: "DeepSeek", model: "deepseek-flash",
        model_mode: "fixed", model_replaced_from: "deepseek-v4-flash" }],
    });
    getAgentCliModels.mockResolvedValue({ models: ["deepseek-flash", "deepseek-v4-pro"] });
    renderCard();
    expect(await screen.findByText(
      "deepseek-v4-flash is no longer offered by this provider, so this account is using deepseek-flash instead.",
    )).toBeTruthy();
  });

  it("warns when the configured model is no longer offered", async () => {
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "deepseek", name: "DeepSeek", model: "deepseek-chat" }],
    });
    renderCard();
    expect(await screen.findByText(/no longer offers deepseek-chat/)).toBeTruthy();
  });

  it("says nothing when the configured model is still offered", async () => {
    renderCard();
    await screen.findByLabelText("Change default model");
    await waitFor(() => expect(getAgentCliModels).toHaveBeenCalled());
    expect(screen.queryByText(/no longer offers/)).toBeNull();
  });

  it("says nothing when the provider could not be reached", async () => {
    // "Nobody checked" and "the model is gone" must not look the same. An
    // unreachable provider says nothing about the model, and reporting it as
    // retired sends the operator to fix an account that is fine.
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "deepseek", name: "DeepSeek", model: "deepseek-chat" }],
    });
    getAgentCliModels.mockRejectedValue(new Error("connection refused"));
    renderCard();
    await screen.findByLabelText("Change default model");
    await waitFor(() => expect(getAgentCliModels).toHaveBeenCalled());
    expect(screen.queryByText(/no longer offers/)).toBeNull();
  });

  it("says nothing when the provider reports an empty list", async () => {
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "deepseek", name: "DeepSeek", model: "deepseek-chat" }],
    });
    getAgentCliModels.mockResolvedValue({ models: [] });
    renderCard();
    await screen.findByLabelText("Change default model");
    await waitFor(() => expect(getAgentCliModels).toHaveBeenCalled());
    expect(screen.queryByText(/no longer offers/)).toBeNull();
  });

  it("shows the retired model but does not let it be picked again", async () => {
    // The retired model must be PRESENT, or a select whose value matches no
    // option silently displays some other model as though it were the configured
    // one. It must also be DISABLED, or the card offers the very model it has just
    // warned about.
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "ollama", name: "Ollama", model: "gone:8b" }],
    });
    getAgentCliModels.mockResolvedValue({ models: ["still-here:8b"] });
    renderCard();
    fireEvent.click(await screen.findByLabelText("Change default model"));
    const select = await screen.findByLabelText("Select default model:") as HTMLSelectElement;
    expect(select.value).toBe("gone:8b");
    const stale = [...select.options].find((o) => o.value === "gone:8b");
    expect(stale).toBeTruthy();
    expect(stale!.disabled).toBe(true);
    expect(stale!.text).toContain("no longer available");
    // Nothing changed, so there is nothing to save: Save would only re-commit
    // the dead model.
    expect((screen.getByText("Save") as HTMLButtonElement).disabled).toBe(true);
  });

  it("a model still on offer is selectable and saveable", async () => {
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "ollama", name: "Ollama", model: "a:8b" }],
    });
    getAgentCliModels.mockResolvedValue({ models: ["a:8b", "b:8b"] });
    renderCard();
    fireEvent.click(await screen.findByLabelText("Change default model"));
    const select = await screen.findByLabelText("Select default model:") as HTMLSelectElement;
    expect([...select.options].every((o) => !o.disabled)).toBe(true);
    fireEvent.change(select, { target: { value: "b:8b" } });
    expect((screen.getByText("Save") as HTMLButtonElement).disabled).toBe(false);
  });
});

// A banner means only "this account is broken, fix it"; anything else belongs on
// the option it describes.
describe("AgentCliSettings tool-calling capability", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setAgentCliProviderModel.mockResolvedValue({ instance: { id: "i1", model: "good" } });
  });

  function withCaps(model: string, models: string[], caps: Record<string, { tools: boolean }>) {
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "ollama", name: "Ollama", model, capabilities: caps }],
    });
    getAgentCliModels.mockResolvedValue({ models });
  }

  it("marks an unusable model on its own option instead of in a banner", async () => {
    withCaps("good", ["good", "notools"], { good: { tools: true }, notools: { tools: false } });
    renderCard();
    fireEvent.click(await screen.findByLabelText("Change default model"));
    const select = await screen.findByLabelText("Select default model:") as HTMLSelectElement;
    const bad = [...select.options].find((o) => o.value === "notools")!;
    expect(bad.disabled).toBe(true);
    expect(bad.text).toContain("no tool calling");
    expect([...select.options].find((o) => o.value === "good")!.disabled).toBe(false);
  });

  it("shows no banner when the account's own model is fine", async () => {
    // A banner naming unusable models the operator cannot select offers nothing
    // to fix and nothing to dismiss, so it must not appear.
    withCaps("good", ["good", "notools"], { good: { tools: true }, notools: { tools: false } });
    renderCard();
    await screen.findByLabelText("Change default model");
    await waitFor(() => expect(getAgentCliModels).toHaveBeenCalled());
    expect(screen.queryByText(/cannot call tools/)).toBeNull();
  });

  it("banners only when the account's OWN default cannot call tools", async () => {
    withCaps("notools", ["good", "notools"], { good: { tools: true }, notools: { tools: false } });
    renderCard();
    expect(await screen.findByText(/notools cannot call tools/)).toBeTruthy();
  });

  it("says nothing about a model whose capability was never declared", async () => {
    // Most providers publish nothing. An undeclared model is not an unusable one.
    withCaps("mystery", ["mystery"], {});
    renderCard();
    fireEvent.click(await screen.findByLabelText("Change default model"));
    const select = await screen.findByLabelText("Select default model:") as HTMLSelectElement;
    expect([...select.options].every((o) => !o.disabled)).toBe(true);
    expect(screen.queryByText(/cannot call tools/)).toBeNull();
  });
});

// A warning about a broken account is worth showing, but not worth showing
// forever with no way to acknowledge it.
describe("AgentCliSettings dismissible warnings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "ollama", name: "Ollama", model: "gone:8b" }],
    });
    getAgentCliModels.mockResolvedValue({ models: ["here:8b"] });
  });

  it("closes on the X and leaves a marker that brings it back", async () => {
    const { unmount } = renderCard();
    fireEvent.click(await screen.findByLabelText("Dismiss this warning"));
    await waitFor(() => expect(screen.queryByText(/no longer offers/)).toBeNull());

    const badge = screen.getByLabelText(/no longer offers gone:8b/);
    fireEvent.click(badge);
    expect(await screen.findByText(/no longer offers gone:8b/)).toBeTruthy();

    // Close it again and prove the dismissal SURVIVES a remount: an in-memory
    // one would return on the next card open.
    fireEvent.click(screen.getByLabelText("Dismiss this warning"));
    await waitFor(() => expect(screen.queryByText(/no longer offers/)).toBeNull());
    unmount();
    renderCard();
    await screen.findByLabelText("Change default model");
    expect(screen.queryByText(/no longer offers/)).toBeNull();
    expect(await screen.findByLabelText(/no longer offers gone:8b/)).toBeTruthy();
  });

  it("a dismissal does not cover a warning about a different model", async () => {
    // Dismissing acknowledges one statement about one model, not the whole
    // category: a NEW broken model must warn on its own.
    const { unmount } = renderCard();
    fireEvent.click(await screen.findByLabelText("Dismiss this warning"));
    await waitFor(() => expect(screen.queryByText(/no longer offers/)).toBeNull());
    unmount();

    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "ollama", name: "Ollama", model: "alsogone:8b" }],
    });
    renderCard();
    expect(await screen.findByText(/no longer offers alsogone:8b/)).toBeTruthy();
  });

  it("no marker when there is nothing wrong", async () => {
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "ollama", name: "Ollama", model: "here:8b" }],
    });
    renderCard();
    await screen.findByLabelText("Change default model");
    await waitFor(() => expect(getAgentCliModels).toHaveBeenCalled());
    expect(screen.queryByLabelText("Dismiss this warning")).toBeNull();
    expect(screen.queryByText(/no longer offers/)).toBeNull();
  });
});

// The probe is the only control on this card that spends money, which is why it
// has its own button and its own confirmation.
describe("AgentCliSettings capability probe", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "deepseek", name: "DeepSeek", model: "deepseek-v4-flash" }],
    });
    getAgentCliModels.mockResolvedValue({ models: ["deepseek-v4-flash"] });
    probeAgentCliCapabilities.mockResolvedValue({
      model: "deepseek-v4-flash", probed: { effort_levels: ["low", "high", "max"] },
      calls: 7, checked_at: "2026-08-04T00:00:00Z", effort_checkable: true, answered: true,
    });
  });

  it("asks before spending anything, and says so", async () => {
    renderCard();
    fireEvent.click(await screen.findByLabelText("Check options"));
    // The confirmation must name the cost: it is what separates this button
    // from the free refresh beside it.
    expect(await screen.findByText(/uses your own API credit/)).toBeTruthy();
    expect(probeAgentCliCapabilities).not.toHaveBeenCalled();
  });

  it("cancelling spends nothing", async () => {
    renderCard();
    fireEvent.click(await screen.findByLabelText("Check options"));
    fireEvent.click(await screen.findByText("Cancel"));
    await waitFor(() => expect(screen.queryByText(/uses your own API credit/)).toBeNull());
    expect(probeAgentCliCapabilities).not.toHaveBeenCalled();
  });

  it("reports the levels it established and how many calls it took", async () => {
    renderCard();
    fireEvent.click(await screen.findByLabelText("Check options"));
    fireEvent.click(await screen.findByText("Check now"));
    await waitFor(() => expect(probeAgentCliCapabilities).toHaveBeenCalledWith("i1"));
    expect(await screen.findByText(/Thinking levels accepted: Low, High, Max/)).toBeTruthy();
    expect(screen.getByText(/7 requests/)).toBeTruthy();
  });

  it("says plainly when nothing could be established", async () => {
    // Not a failure: a provider that ignores unknown parameters cannot be asked
    // this way, and reporting an error would send the operator debugging.
    probeAgentCliCapabilities.mockResolvedValue({
      model: "m", probed: {}, calls: 2, checked_at: "2026-08-04T00:00:00Z", effort_checkable: true, answered: true,
    });
    renderCard();
    fireEvent.click(await screen.findByLabelText("Check options"));
    fireEvent.click(await screen.findByText("Check now"));
    expect(await screen.findByText(/does not validate the options/)).toBeTruthy();
  });

  it("cannot be run on an account with no model chosen", async () => {
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "ollama", name: "Ollama", model: "" }],
    });
    renderCard();
    expect((await screen.findByLabelText("Check options") as HTMLButtonElement).disabled).toBe(true);
  });

  it("says there was nothing to check, not that the provider ignored us", async () => {
    // A backend whose thinking control is a boolean flag has no effort stage to
    // probe. That must not read as "this provider does not validate the options",
    // which would blame it for ignoring a question nobody asked.
    probeAgentCliCapabilities.mockResolvedValue({
      model: "gemma", probed: {}, calls: 1, checked_at: "2026-08-04T00:00:00Z",
      effort_checkable: false, answered: true,
    });
    renderCard();
    fireEvent.click(await screen.findByLabelText("Check options"));
    fireEvent.click(await screen.findByText("Check now"));
    expect(await screen.findByText(/no levels to check/)).toBeTruthy();
    expect(screen.queryByText(/does not validate the options/)).toBeNull();
  });

  it.each([
    ["effort_levels as a bare string", { model: "m", probed: { effort_levels: "high" }, calls: 2, checked_at: "x", effort_checkable: true, answered: true }],
    ["effort_levels holding non-strings", { model: "m", probed: { effort_levels: [1, 2] }, calls: 2, checked_at: "x", effort_checkable: true, answered: true }],
    ["effort_levels as an object", { model: "m", probed: { effort_levels: { a: 1 } }, calls: 2, checked_at: "x", effort_checkable: true, answered: true }],
  ])("survives %s inside an otherwise well-formed response", async (_label, response) => {
    // The container check passes here (`probed` is an object), but a string has
    // a truthy `.length` and no `.map`, which would throw in cardResultText during
    // render. A malformed levels list must read as "no levels reported", not as
    // a failure or a crash.
    probeAgentCliCapabilities.mockResolvedValue(response);
    renderCard();
    fireEvent.click(await screen.findByLabelText("Check options"));
    fireEvent.click(await screen.findByText("Check now"));
    await waitFor(() => expect(probeAgentCliCapabilities).toHaveBeenCalledWith("i1"));
    // Rendered an outcome rather than crashing, and the card is still usable.
    expect(await screen.findByText(/Checked with 2 requests/)).toBeTruthy();
    expect(screen.getByLabelText("Check options")).toBeTruthy();
  });

  it.each([
    ["a 204, which the client returns as undefined", undefined],
    ["a success that did not parse as JSON", { error: "parse_error", message: "OK" }],
    ["a response with no probed block", { calls: 3, answered: true }],
    ["a null probed block", { probed: null, calls: 1, answered: true }],
  ])("survives %s", async (_label, response) => {
    // A response the return type does not admit must read as a failed check
    // rather than take the card down. The card result is built synchronously
    // inside the try, not inside the setRefreshResult updater: React runs the
    // updater during the next render, where a throw is an unhandled render error.
    // Assert what renders, because such an error can leave every assertion
    // passing while vitest still exits non-zero.
    probeAgentCliCapabilities.mockResolvedValue(response);
    renderCard();
    fireEvent.click(await screen.findByLabelText("Check options"));
    fireEvent.click(await screen.findByText("Check now"));
    await waitFor(() => expect(probeAgentCliCapabilities).toHaveBeenCalledWith("i1"));
    expect(await screen.findByText("Connection failed.")).toBeTruthy();
    // Still mounted and still offering the action.
    expect(screen.getByLabelText("Check options")).toBeTruthy();
  });
});

// The actions are icon buttons, and an icon carries no name, so the accessible
// name is the only name there is.
describe("AgentCliSettings account actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    getAgentCliProviders.mockResolvedValue({
      instances: [{
        id: "i1", kind: "deepseek", name: "DeepSeek", model: "deepseek-v4-flash",
        capabilities_checked_at: "2026-08-04T00:00:00Z",
      }],
    });
    getAgentCliModels.mockResolvedValue({ models: ["deepseek-v4-flash"] });
  });

  it("every action has an accessible name, and the icon is never the label", async () => {
    renderCard();
    expect(await screen.findByLabelText("Change default model")).toBeTruthy();
    expect(screen.getByLabelText("Refresh models")).toBeTruthy();
    expect(screen.getByLabelText(/^Check options/)).toBeTruthy();
    // Naming the account matters here: with four identical squares, "Remove"
    // alone does not say what is about to be removed.
    expect(screen.getByLabelText("Remove DeepSeek")).toBeTruthy();
  });

  it("the last-checked time rides on the button it belongs to", async () => {
    // It is not a separate status line.
    renderCard();
    const probe = await screen.findByLabelText(/^Check options \(last checked/);
    expect(probe.getAttribute("title")).toBe(probe.getAttribute("aria-label"));
    expect(screen.queryByText(/Capabilities last checked/)).toBeNull();
  });

  it("a running action renames itself rather than only spinning", async () => {
    let release: (v: unknown) => void = () => {};
    refreshAgentCliProvider.mockReturnValue(new Promise((r) => { release = r; }));
    renderCard();
    const button = await screen.findByLabelText("Refresh models");
    button.focus();
    fireEvent.click(button);
    expect(button).toHaveFocus();
    expect(button).not.toBeDisabled();
    expect(button).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(button);
    expect(refreshAgentCliProvider).toHaveBeenCalledTimes(1);
    // An icon that merely spins announces nothing to a screen reader.
    expect(await screen.findByLabelText("Refreshing...")).toBeTruthy();
    await act(async () => release({ models: [], capabilities: {}, declared: false, checked_at: "x" }));
    await screen.findByLabelText("Refresh models");
    expect(button).toHaveFocus();
    expect(button).not.toHaveAttribute("aria-disabled", "true");
  });

  it("checks the new model's options as part of adding an account", async () => {
    // The check is offered while adding, so the options are known before the
    // first conversation.
    createAgentCliProvider.mockResolvedValue({ instance: { id: "new1", kind: "claude", name: "Anthropic", model: "claude-opus-4-8" } });
    probeAgentCliCapabilities.mockResolvedValue({
      model: "claude-opus-4-8", probed: {}, calls: 2, checked_at: "x", effort_checkable: true, answered: true,
    });
    renderCard();
    await beginAdd();
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-test" } });
    fireEvent.click(screen.getByText("Validate"));
    const probe = await screen.findByLabelText(/Check which options this model accepts/);
    expect(probe.closest(".toggle-switch")).toBeTruthy();
    expect(probe.closest(".agentcli-settings-model-actions")?.querySelector("button")?.textContent).toBe("Done");
    fireEvent.click(await screen.findByText("Done"));
    await waitFor(() => expect(probeAgentCliCapabilities).toHaveBeenCalledWith("new1"));
  });

  it("adding without the check spends nothing", async () => {
    createAgentCliProvider.mockResolvedValue({ instance: { id: "new1", kind: "claude", name: "Anthropic", model: "claude-opus-4-8" } });
    renderCard();
    await beginAdd();
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-test" } });
    fireEvent.click(screen.getByText("Validate"));
    fireEvent.click(await screen.findByLabelText(/Check which options this model accepts/));
    fireEvent.click(screen.getByText("Done"));
    await waitFor(() => expect(createAgentCliProvider).toHaveBeenCalled());
    expect(probeAgentCliCapabilities).not.toHaveBeenCalled();
  });

  it("a failed check does not fail the account", async () => {
    // The account is already stored and working; the card can retry.
    createAgentCliProvider.mockResolvedValue({ instance: { id: "new1", kind: "claude", name: "Anthropic", model: "claude-opus-4-8" } });
    probeAgentCliCapabilities.mockRejectedValue(new Error("rate limited"));
    renderCard();
    await beginAdd();
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-test" } });
    fireEvent.click(screen.getByText("Validate"));
    fireEvent.click(await screen.findByText("Done"));
    await waitFor(() => expect(createAgentCliProvider).toHaveBeenCalled());
    expect(screen.queryByText("rate limited")).toBeNull();
  });
});

describe("AgentCliSettings probe could not run", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "openrouter", name: "OpenRouter", model: "openai/gpt-5" }],
    });
    getAgentCliModels.mockResolvedValue({ models: ["openai/gpt-5"] });
  });

  it("says the provider declined, not that it does not validate options", async () => {
    // Every probe refused for an ACCOUNT reason (for example a key with no
    // credit). Reporting that as a finding about the model would blame the
    // provider for something it never got to answer.
    probeAgentCliCapabilities.mockResolvedValue({
      model: "openai/gpt-5", probed: {}, calls: 2, checked_at: "x",
      effort_checkable: false, answered: false,
    });
    renderCard();
    fireEvent.click(await screen.findByLabelText(/^Check options/));
    fireEvent.click(await screen.findByText("Check now"));
    expect(await screen.findByText(/declined every one/)).toBeTruthy();
    expect(screen.queryByText(/no levels to check/)).toBeNull();
  });

  it("offers the check when the default model is changed", async () => {
    // A different model has different options, so the offer belongs here too.
    setAgentCliProviderModel.mockResolvedValue({ instance: { id: "i1", model: "openai/gpt-5-mini" } });
    probeAgentCliCapabilities.mockResolvedValue({
      model: "openai/gpt-5-mini", probed: {}, calls: 2, checked_at: "x",
      effort_checkable: false, answered: true,
    });
    getAgentCliModels.mockResolvedValue({ models: ["openai/gpt-5", "openai/gpt-5-mini"] });
    renderCard();
    fireEvent.click(await screen.findByLabelText("Change default model"));
    fireEvent.change(await screen.findByLabelText("Select default model:"),
                     { target: { value: "openai/gpt-5-mini" } });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(probeAgentCliCapabilities).toHaveBeenCalledWith("i1"));
  });

  it("changing the model without the check spends nothing", async () => {
    setAgentCliProviderModel.mockResolvedValue({ instance: { id: "i1", model: "openai/gpt-5-mini" } });
    getAgentCliModels.mockResolvedValue({ models: ["openai/gpt-5", "openai/gpt-5-mini"] });
    renderCard();
    fireEvent.click(await screen.findByLabelText("Change default model"));
    fireEvent.click(await screen.findByLabelText(/Check which options this model accepts/));
    fireEvent.change(await screen.findByLabelText("Select default model:"),
                     { target: { value: "openai/gpt-5-mini" } });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(setAgentCliProviderModel).toHaveBeenCalled());
    expect(probeAgentCliCapabilities).not.toHaveBeenCalled();
  });
});

// Whoever asked for the check gets the answer: running it silently on the add and
// save paths would pay for an answer and never show it.
describe("AgentCliSettings reports every check", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "openrouter", name: "OpenRouter", model: "a/one" }],
    });
    getAgentCliModels.mockResolvedValue({ models: ["a/one", "a/two"] });
    setAgentCliProviderModel.mockResolvedValue({ instance: { id: "i1", model: "a/two" } });
    probeAgentCliCapabilities.mockResolvedValue({
      model: "a/two", probed: {}, calls: 1, checked_at: "x",
      effort_checkable: false, answered: true,
    });
  });

  it("shows the result after changing the default model", async () => {
    renderCard();
    fireEvent.click(await screen.findByLabelText("Change default model"));
    fireEvent.change(await screen.findByLabelText("Select default model:"), { target: { value: "a/two" } });
    fireEvent.click(screen.getByText("Save"));
    expect(await screen.findByText(/no levels to check/)).toBeTruthy();
  });

  it("shows the result after adding an account", async () => {
    const added = { id: "new1", kind: "claude", name: "Anthropic", model: "claude-opus-4-8" };
    createAgentCliProvider.mockResolvedValue({ instance: added });
    // The reload after Done returns the new account, as the server would; the
    // result has to land on ITS card, which only exists once it is listed.
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "openrouter", name: "OpenRouter", model: "a/one" }, added],
    });
    renderCard();
    await beginAdd();
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-test" } });
    fireEvent.click(screen.getByText("Validate"));
    fireEvent.click(await screen.findByText("Done"));
    expect(await screen.findByText(/no levels to check/)).toBeTruthy();
  });

  it("a failed check is reported rather than swallowed", async () => {
    probeAgentCliCapabilities.mockRejectedValue(new Error("rate limited"));
    renderCard();
    fireEvent.click(await screen.findByLabelText("Change default model"));
    fireEvent.change(await screen.findByLabelText("Select default model:"), { target: { value: "a/two" } });
    fireEvent.click(screen.getByText("Save"));
    // Reported, but the model change still stands: the two are separate.
    expect(await screen.findByText("rate limited")).toBeTruthy();
    await waitFor(() => expect(setAgentCliProviderModel).toHaveBeenCalled());
  });
});

// A check that writes new capabilities has to announce them: both chat-window
// hosts hold accounts as state and reload on this event.
describe("AgentCliSettings announces capability changes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    getAgentCliProviders.mockResolvedValue({
      instances: [{ id: "i1", kind: "openrouter", name: "OpenRouter", model: "a/one" }],
    });
    getAgentCliModels.mockResolvedValue({ models: ["a/one"] });
    probeAgentCliCapabilities.mockResolvedValue({
      model: "a/one", probed: { effort_levels: ["low", "high"] }, calls: 7,
      checked_at: "x", effort_checkable: true, answered: true,
    });
  });

  it("fires providers-changed after a manual check", async () => {
    const seen = vi.fn();
    window.addEventListener("phx-agentcli-providers-changed", seen);
    renderCard();
    fireEvent.click(await screen.findByLabelText(/^Check options/));
    fireEvent.click(await screen.findByText("Check now"));
    // Without this the chat window keeps its old accounts, so a Thinking control
    // the check just established never appears and the check looks broken.
    await waitFor(() => expect(seen).toHaveBeenCalled());
    window.removeEventListener("phx-agentcli-providers-changed", seen);
  });
});
