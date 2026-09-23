import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { GlobalSettings } from "../types";

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), remove: vi.fn(), probe: vi.fn(), models: vi.fn(), setModel: vi.fn() }));
vi.mock("../api", () => ({ api: {
  getDecisionProviders: mocks.list,
  probeAgentCliProvider: mocks.probe,
  getAgentCliModels: mocks.models,
  setAgentCliProviderModel: mocks.setModel,
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
  mocks.probe.mockResolvedValue({ ok: true, models: ["jev-latest"] });
  mocks.models.mockResolvedValue({ models: ["jev-latest"] });
});

it("keeps remote observation opt-in and sends no completion on mount", async () => {
  const change = vi.fn();
  render(<DecisionProviderSettings settings={settings} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.change(screen.getByLabelText("Provider type"), { target: { value: "typesafe" } });
  expect(screen.getByLabelText("Local only (no decision provider)")).toBeChecked();
  expect(mocks.create).not.toHaveBeenCalled();
  expect(change).not.toHaveBeenCalled();
});

it.each([null, "previous"])("selects a newly saved account from %s without changing routing switches and clears the credential", async previous => {
  const change = vi.fn();
  mocks.create.mockResolvedValue({ instance: { id: "decision" } });
  render(<DecisionProviderSettings settings={{ ...settings, decision_provider_id: previous }} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.change(screen.getByLabelText("Provider type"), { target: { value: "typesafe" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "private-key" } });
  fireEvent.click(screen.getByText("Validate"));
  await screen.findByLabelText("Select default model:");
  fireEvent.click(screen.getByText("Done"));
  await waitFor(() => expect(screen.queryByLabelText("API key")).toBeNull());
  expect(mocks.create).toHaveBeenCalledWith("typesafe", { api_key: "private-key", model: "jev-latest" });
  expect(change).toHaveBeenCalledExactlyOnceWith("decision_provider_id", "decision");
});

it("keeps the selected account when creation fails", async () => {
  const change = vi.fn();
  mocks.create.mockRejectedValue(new Error("Could not save account"));
  render(<DecisionProviderSettings settings={settings} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.change(screen.getByLabelText("Provider type"), { target: { value: "typesafe" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "key" } });
  fireEvent.click(screen.getByText("Validate"));
  await screen.findByLabelText("Select default model:");
  fireEvent.click(screen.getByText("Done"));
  await screen.findByText("Could not save account");
  expect(change).not.toHaveBeenCalled();
});

it("preserves a newer manual choice while account creation is pending", async () => {
  const change = vi.fn();
  let done!: (value: unknown) => void;
  mocks.create.mockReturnValue(new Promise(resolve => { done = resolve; }));
  render(<DecisionProviderSettings settings={{ ...settings, decision_provider_id: "previous" }} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.change(screen.getByLabelText("Provider type"), { target: { value: "typesafe" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "key" } });
  fireEvent.click(screen.getByText("Validate"));
  await screen.findByLabelText("Select default model:");
  fireEvent.click(screen.getByText("Done"));
  fireEvent.click(screen.getByLabelText("Local only (no decision provider)"));
  await act(async () => done({ instance: { id: "new" } }));
  expect(change).toHaveBeenCalledExactlyOnceWith("decision_provider_id", "");
});

it("discovers compatible models before saving and invalidates them when credentials change", async () => {
  render(<DecisionProviderSettings settings={settings} onChange={vi.fn()} saving={false} />);
  await screen.findByText("System One compatible");
  fireEvent.change(screen.getByLabelText("Provider type"), { target: { value: "systemone" } });
  expect(screen.queryByLabelText("Select default model:")).toBeNull();
  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "http://local:8000/v1" } });
  fireEvent.click(screen.getByText("Validate"));
  const model = await screen.findByLabelText("Select default model:");
  expect(model.tagName).toBe("SELECT");
  expect(mocks.probe).toHaveBeenCalledWith("systemone", { base_url: "http://local:8000/v1", api_key: "" });
  expect(screen.queryByText(/uses a small amount of API credit/)).toBeNull();
  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "http://different:8000/v1" } });
  expect(screen.queryByLabelText("Select default model:")).toBeNull();
  expect(screen.queryByText("Done")).toBeNull();
});

it("blocks saving when the server advertises no models", async () => {
  mocks.probe.mockResolvedValue({ ok: true, models: [] });
  render(<DecisionProviderSettings settings={settings} onChange={vi.fn()} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.change(screen.getByLabelText("Provider type"), { target: { value: "typesafe" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "key" } });
  fireEvent.click(screen.getByText("Validate"));
  await screen.findByLabelText("Select default model:");
  expect(screen.getByText("Done")).toBeDisabled();
  expect(mocks.create).not.toHaveBeenCalled();
});

it("discards a model catalog after the user cancels validation", async () => {
  let done!: (value: unknown) => void;
  mocks.probe.mockReturnValue(new Promise(resolve => { done = resolve; }));
  render(<DecisionProviderSettings settings={settings} onChange={vi.fn()} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.change(screen.getByLabelText("Provider type"), { target: { value: "typesafe" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "key" } });
  fireEvent.click(screen.getByText("Validate"));
  fireEvent.click(screen.getByText("Cancel"));
  fireEvent.change(screen.getByLabelText("Provider type"), { target: { value: "systemone" } });
  await act(async () => done({ ok: true, models: ["wrong-provider-model"] }));
  expect(screen.queryByText("wrong-provider-model")).toBeNull();
  expect(screen.queryByText("Done")).toBeNull();
});

it("does not publish an account selection after unmount", async () => {
  const change = vi.fn();
  let done!: (value: unknown) => void;
  mocks.create.mockReturnValue(new Promise(resolve => { done = resolve; }));
  const view = render(<DecisionProviderSettings settings={settings} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.change(screen.getByLabelText("Provider type"), { target: { value: "typesafe" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "key" } });
  fireEvent.click(screen.getByText("Validate"));
  await screen.findByLabelText("Select default model:");
  fireEvent.click(screen.getByText("Done"));
  view.unmount();
  await act(async () => done({ instance: { id: "late" } }));
  expect(change).not.toHaveBeenCalled();
});

it("makes account selection explicit and deletion available on each account row", async () => {
  mocks.list.mockResolvedValue({ instances: [{ id: "decision", name: "My decision account", model: "jev-latest" }], provider_types: types });
  mocks.remove.mockResolvedValue({});
  const change = vi.fn();
  render(<DecisionProviderSettings settings={settings} onChange={change} saving={false} />);
  const account = await screen.findByRole("radio", { name: "My decision account" });
  expect(account).not.toBeChecked();
  expect(screen.queryByLabelText("API key")).toBeNull();
  fireEvent.click(account);
  expect(change).toHaveBeenCalledExactlyOnceWith("decision_provider_id", "decision");
  fireEvent.click(screen.getByRole("button", { name: "Delete My decision account" }));
  await waitFor(() => expect(mocks.remove).toHaveBeenCalledWith("decision"));
});

it("keeps connection timing optional and rejects invalid values", async () => {
  const change = vi.fn();
  render(<DecisionProviderSettings settings={{ ...settings, decision_provider_id: "decision" }} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  const timeout = screen.getByLabelText("Decision timeout (ms)");
  expect(timeout).not.toBeVisible();
  fireEvent.click(screen.getByText("Connection settings"));
  expect(timeout).toBeVisible();
  fireEvent.change(timeout, { target: { value: "12000" } });
  fireEvent.blur(timeout);
  expect(change).toHaveBeenCalledExactlyOnceWith("decision_timeout_ms", 12000);
  fireEvent.change(timeout, { target: { value: "0" } });
  fireEvent.blur(timeout);
  expect(timeout).toHaveValue(5000);
  expect(change).toHaveBeenCalledTimes(1);
});


it("repairs an existing unavailable model through the fetched picker", async () => {
  mocks.list.mockResolvedValue({ instances: [{ id: "decision", name: "JEV", model: "abcdefg" }], provider_types: types });
  mocks.setModel.mockResolvedValue({ instance: { id: "decision", model: "jev-latest" } });
  render(<DecisionProviderSettings settings={settings} onChange={vi.fn()} saving={false} />);
  await screen.findByText("abcdefg (no longer available)");
  fireEvent.change(screen.getByLabelText("Model for JEV"), { target: { value: "jev-latest" } });
  await waitFor(() => expect(mocks.setModel).toHaveBeenCalledWith("decision", "jev-latest"));
});
