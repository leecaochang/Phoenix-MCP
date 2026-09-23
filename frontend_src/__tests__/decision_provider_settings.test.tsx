import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { GlobalSettings } from "../types";

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), remove: vi.fn() }));
vi.mock("../api", () => ({ api: {
  getDecisionProviders: mocks.list,
  createAgentCliProvider: mocks.create,
  deleteAgentCliProvider: mocks.remove,
} }));
import { DecisionProviderSettings } from "../components/DecisionProviderSettings";
const types = [
  { kind: "typesafe", label: "TypeSafe / JEV", fields: [{ id: "api_key", type: "secret", label_key: "settings.agentcliApiKey", required: true }] },
  { kind: "systemone", label: "System One compatible", fields: [
    { id: "base_url", type: "url", label_key: "settings.agentcliBaseUrl", required: true },
    { id: "api_key", type: "secret", label_key: "settings.agentcliApiKey", required: false },
  ] },
];
const settings = { decision_provider_id: null, decision_shadow_enabled: false, decision_timeout_ms: 5000 } as GlobalSettings;
beforeEach(() => {
  vi.resetAllMocks();
  mocks.list.mockResolvedValue({ instances: [], provider_types: types });
});

it("keeps remote observation opt-in and sends no completion on mount", async () => {
  const change = vi.fn();
  render(<DecisionProviderSettings settings={settings} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  expect(screen.getByLabelText("Observe unmatched English Voice requests")).not.toBeChecked();
  expect(screen.getByLabelText("Observe unmatched English Voice requests")).toBeDisabled();
  expect(mocks.create).not.toHaveBeenCalled();
  expect(change).not.toHaveBeenCalled();
});

it("saves through the shared account API and clears the write-only credential", async () => {
  const change = vi.fn();
  mocks.create.mockResolvedValue({ instance: { id: "decision" } });
  render(<DecisionProviderSettings settings={settings} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "private-key" } });
  fireEvent.click(screen.getByText("Check and save account"));
  await waitFor(() => expect(change).toHaveBeenCalledWith("decision_provider_id", "decision"));
  expect(mocks.create).toHaveBeenCalledWith("typesafe", { api_key: "private-key", model: "jev-latest" });
  expect(screen.getByLabelText("API key")).toHaveValue("");
  expect(change).not.toHaveBeenCalledWith("decision_shadow_enabled", true);
});

it("resets credentials and requires an explicit model when changing protocols", async () => {
  render(<DecisionProviderSettings settings={settings} onChange={vi.fn()} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "private-key" } });
  fireEvent.change(screen.getByLabelText("Decision service"), { target: { value: "systemone" } });
  expect(screen.getByLabelText("API key")).toHaveValue("");
  expect(screen.getByLabelText("Model")).toHaveValue("");
  expect(screen.getByText("Check and save account")).toBeDisabled();
});

it("does not publish an account selection after unmount", async () => {
  const change = vi.fn();
  let done!: (value: unknown) => void;
  mocks.create.mockReturnValue(new Promise(resolve => { done = resolve; }));
  const view = render(<DecisionProviderSettings settings={settings} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "key" } });
  fireEvent.click(screen.getByText("Check and save account"));
  view.unmount();
  await act(async () => done({ instance: { id: "late" } }));
  expect(change).not.toHaveBeenCalled();
});

it("pauses background observation in hybrid mode without overwriting its preference", async () => {
  const change = vi.fn();
  render(<DecisionProviderSettings settings={{ ...settings, decision_provider_id: "decision",
    voice_agent_routing_mode: "hybrid", decision_shadow_enabled: true }} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  expect(screen.getByLabelText("Observe unmatched English Voice requests")).toBeDisabled();
  expect(screen.getByLabelText("Observe unmatched English Voice requests")).toBeChecked();
  expect(screen.getByText(/Background observation is paused/)).toBeTruthy();
  expect(change).not.toHaveBeenCalled();
});
