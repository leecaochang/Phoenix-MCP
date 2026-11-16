/**
 * Agent Chat checks a model's options when its dropdown picks one the account never checked, for
 * providers whose thinking levels exist only after a check.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, screen, waitFor } from "@testing-library/react";

const getAgentCliModels = vi.fn();
const probeAgentCliCapabilities = vi.fn();

vi.mock("../api", () => ({
  agentCliChat: vi.fn(),
  api: {
    getAgentCliModels: (...args: unknown[]) => getAgentCliModels(...args),
    probeAgentCliCapabilities: (...args: unknown[]) => probeAgentCliCapabilities(...args),
    getApproval: vi.fn(),
  },
}));

import { AgentCliWindow } from "../components/AgentCliWindow";
import { __resetAgentCliState } from "../utils/agentcli_state";
import type { AgentCliInstance, AccessKeyRecord } from "../types";

const KEY = { id: "t1", name: "alpha", persona: "custom" } as AccessKeyRecord;
const AZURE: AgentCliInstance = {
  id: "i-az", kind: "azure", name: "Microsoft Azure", model: "checked",
  capabilities: { checked: { effort_levels: ["none", "high", "max"] } },
};

function open(instance: AgentCliInstance) {
  return render(<AgentCliWindow accessKeys={[KEY]} instances={[instance]} memoryChars={960000}
                                initialAccessKeyId="t1" onClose={() => {}} />);
}

async function pick(model: string) {
  const select = await screen.findByLabelText("Model") as HTMLSelectElement;
  await waitFor(() => expect([...select.options].map((o) => o.value)).toContain(model));
  fireEvent.change(select, { target: { value: model } });
}

beforeEach(() => {
  vi.clearAllMocks();
  __resetAgentCliState();
  getAgentCliModels.mockResolvedValue({ models: ["checked", "fresh"] });
});

describe("Agent Chat checks an unchecked model on select", () => {
  it("checks a model the account never checked, once, and asks for the accounts again", async () => {
    let finish: (value: unknown) => void = () => {};
    probeAgentCliCapabilities.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const changed = vi.fn();
    window.addEventListener("phx-agentcli-providers-changed", changed);
    open(AZURE);

    await pick("fresh");
    await waitFor(() => expect(probeAgentCliCapabilities).toHaveBeenCalledWith("i-az", "fresh"));
    expect(await screen.findByText("Checking which options this model accepts...")).toHaveAttribute("role", "status");
    finish({ model: "fresh", probed: {} });
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));

    await pick("checked");
    await pick("fresh");
    expect(probeAgentCliCapabilities).toHaveBeenCalledTimes(1);
    window.removeEventListener("phx-agentcli-providers-changed", changed);
  });

  it("leaves a checked model, and a provider that declares its levels, alone", async () => {
    const first = open(AZURE);
    await pick("checked");
    first.unmount();
    open({ id: "i-gpt", kind: "chatgpt", name: "GPT", model: "checked" });
    await waitFor(() => expect(getAgentCliModels).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(probeAgentCliCapabilities).not.toHaveBeenCalled();
  });

  it("never checks the previous account's model while the new account's list loads", async () => {
    // Switching accounts keeps the old list and model for a moment; checking then would send
    // the previous provider's model id to this account.
    let listAzure: (value: unknown) => void = () => {};
    getAgentCliModels.mockImplementation((id: string) => (id === "i-az"
      ? new Promise((resolve) => { listAzure = resolve; })
      : Promise.resolve({ models: ["claude-x"] })));
    probeAgentCliCapabilities.mockResolvedValue({ model: "fresh", probed: {} });
    render(<AgentCliWindow accessKeys={[KEY]} memoryChars={960000} initialAccessKeyId="t1" onClose={() => {}}
                           instances={[{ id: "i-cl", kind: "claude", name: "Anthropic", model: "claude-x" }, AZURE]} />);
    await pick("claude-x");
    fireEvent.change(screen.getByLabelText("Provider account"), { target: { value: "i-az" } });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(probeAgentCliCapabilities).not.toHaveBeenCalled();

    listAzure({ models: ["checked", "fresh"] });
    await pick("fresh");
    await waitFor(() => expect(probeAgentCliCapabilities).toHaveBeenCalledWith("i-az", "fresh"));
    expect(probeAgentCliCapabilities).toHaveBeenCalledTimes(1);
  });
});
