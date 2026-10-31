import userEvent from "@testing-library/user-event";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { GlobalSettings } from "../types";

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), remove: vi.fn(), probe: vi.fn(), models: vi.fn(), setModel: vi.fn(), refresh: vi.fn() }));
vi.mock("../api", () => ({ api: {
  getDecisionProviders: mocks.list,
  refreshAgentCliProvider: mocks.refresh,
  probeAgentCliProvider: mocks.probe,
  getAgentCliModels: mocks.models,
  setAgentCliProviderModel: mocks.setModel,
  createAgentCliProvider: mocks.create,
  deleteAgentCliProvider: mocks.remove,
} }));
import { DecisionProviderSettings } from "../components/DecisionProviderSettings";
const types = [
  { kind: "typesafe", label: "TypeSafe / JEV", fields: [{ id: "api_key", type: "secret", label_key: "settings.agentcliApiKey", required: true }] },
];
const settings = { decision_provider_id: null, decision_shadow_enabled: false, decision_timeout_ms: 5000 } as GlobalSettings;
beforeEach(() => {
  vi.resetAllMocks();
  mocks.list.mockResolvedValue({ instances: [], provider_types: types });
  mocks.probe.mockResolvedValue({ ok: true, models: ["jev-latest"] });
  mocks.models.mockResolvedValue({ models: ["jev-latest"] });
});

it("offers only local routing and TypeSafe / JEV", async () => {
  render(<DecisionProviderSettings settings={settings} onChange={vi.fn()} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  // With no account added, No Decision Provider is the only choice, and it is chosen (user, 2026-10-31).
  const none = screen.getByRole("radio", { name: "No decision provider" });
  expect(none).toBeChecked();
  expect(screen.getAllByRole("radio")).toHaveLength(1);
  expect(none.closest("[data-help-text]")?.getAttribute("data-help-text")).toMatch(/not sent to a decision provider/);
  expect(screen.queryByRole("checkbox", { name: "Use decision provider" })).toBeNull();
  const picker = screen.getByLabelText("Provider type");
  expect(within(picker).getAllByRole("option").map(option => option.getAttribute("value"))).toEqual(["", "typesafe"]);
  expect(mocks.probe).not.toHaveBeenCalled();
});

it("saves an hourly request limit and rejects empty or fractional drafts", async () => {
  mocks.list.mockResolvedValue({ instances: [{ id: "decision", name: "TypeSafe / JEV", model: "jev-latest" }], provider_types: types });
  const change = vi.fn();
  const props = { settings: { ...settings, decision_provider_id: "decision", decision_provider_default_id: "decision", decision_request_budget: 12 }, onChange: change, saving: false };
  const view = render(<DecisionProviderSettings {...props} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.click(screen.getByText("Connection Settings"));
  const input = screen.getByLabelText("Decision requests per hour (0 = unlimited)");
  for (const value of ["", "1.5", "-1", "100001"]) {
    fireEvent.change(input, { target: { value } });
    fireEvent.blur(input);
    expect(input).toHaveValue(12);
  }
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: "20" } });
  fireEvent.blur(input);
  expect(change).toHaveBeenCalledExactlyOnceWith("decision_request_budget", 20);
  view.rerender(<DecisionProviderSettings {...props} settings={{ ...props.settings, decision_request_budget: 20 }} saving />);
  expect(input).toHaveValue(20);
  expect(input).toHaveAttribute("aria-disabled", "true");
});

it("keeps remote observation opt-in and sends no completion on mount", async () => {
  const change = vi.fn();
  render(<DecisionProviderSettings settings={settings} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.change(screen.getByLabelText("Provider type"), { target: { value: "typesafe" } });
  expect(screen.getByRole("radio", { name: "No decision provider" })).toBeChecked();
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

const two = [
  { id: "a", kind: "typesafe", name: "TypeSafe / JEV (a)", model: "jev-latest" },
  { id: "b", kind: "typesafe", name: "TypeSafe / JEV (b)", model: "jev-latest" },
];

it("puts a chosen account in use, and No Decision Provider clears only the account in use", async () => {
  const change = vi.fn();
  mocks.list.mockResolvedValue({ instances: two, provider_types: types });
  const base = { ...settings, decision_provider_default_id: "b" };
  const view = render(<DecisionProviderSettings settings={base} onChange={change} saving={false} />);
  const b = await screen.findByRole("radio", { name: "TypeSafe / JEV (b)" });
  const none = screen.getByRole("radio", { name: "No decision provider" });
  // The stored default is not shown as in use.
  expect(none).toBeChecked();
  expect(b).not.toBeChecked();
  fireEvent.click(b);
  expect(change).toHaveBeenLastCalledWith("decision_provider_id", "b");
  view.rerender(<DecisionProviderSettings settings={{ ...base, decision_provider_id: "b" }} onChange={change} saving={false} />);
  expect(b).toBeChecked();
  expect(none).not.toBeChecked();
  fireEvent.click(none);
  // It clears only the account in use: the default is never sent as null.
  expect(change).toHaveBeenLastCalledWith("decision_provider_id", "");
  expect(change).not.toHaveBeenCalledWith("decision_provider_default_id", expect.anything());
});

it("keeps a default account stored while any exists, without putting it in use", async () => {
  const change = vi.fn();
  mocks.list.mockResolvedValue({ instances: two, provider_types: types });
  // Nothing stored yet: the first account becomes the default for the server's sake.
  render(<DecisionProviderSettings settings={settings} onChange={change} saving={false} />);
  await waitFor(() => expect(change).toHaveBeenCalledWith("decision_provider_default_id", "a"));
  expect(screen.getAllByRole("radio")).toHaveLength(3);
  expect(screen.getByRole("radio", { name: "No decision provider" })).toBeChecked();
  expect(change).not.toHaveBeenCalledWith("decision_provider_id", expect.anything());
});

it("discovers compatible models before saving and invalidates them when credentials change", async () => {
  render(<DecisionProviderSettings settings={settings} onChange={vi.fn()} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  fireEvent.change(screen.getByLabelText("Provider type"), { target: { value: "typesafe" } });
  expect(screen.queryByLabelText("Select default model:")).toBeNull();
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "first-key" } });
  fireEvent.click(screen.getByText("Validate"));
  const model = await screen.findByLabelText("Select default model:");
  expect(model.tagName).toBe("SELECT");
  expect(mocks.probe).toHaveBeenCalledWith("typesafe", { api_key: "first-key" });
  expect(screen.queryByText(/uses a small amount of API credit/)).toBeNull();
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "changed-key" } });
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
  expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
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
  fireEvent.change(screen.getByLabelText("Provider type"), { target: { value: "typesafe" } });
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

it("opens an account's options when it is chosen, and asks in place before removing it", async () => {
  mocks.list.mockResolvedValue({ instances: [{ id: "decision", name: "My decision account", model: "jev-latest" }], provider_types: types });
  mocks.remove.mockResolvedValue({});
  const change = vi.fn();
  render(<DecisionProviderSettings settings={{ ...settings, decision_provider_default_id: "decision" }} onChange={change} saving={false} />);
  const account = await screen.findByRole("radio", { name: "My decision account" });
  expect(account).not.toBeChecked();
  expect(screen.queryByLabelText("API key")).toBeNull();
  // Nothing is open until an account is chosen (user, 2026-10-31).
  expect(screen.queryByRole("button", { name: "Remove My decision account" })).toBeNull();
  fireEvent.click(account);
  expect(change).toHaveBeenCalledExactlyOnceWith("decision_provider_id", "decision");
  // The model is not repeated above the actions.
  expect(screen.queryByText(/Default model/)).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: "Remove My decision account" }));
  expect(mocks.remove).not.toHaveBeenCalled();
  const confirm = screen.getByRole("button", { name: "Confirm Remove My decision account" });
  expect(confirm).toHaveClass("btn-danger");
  expect(confirm.getAttribute("data-help-text")).toMatch(/If it is the decision provider in use/);
  expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
  fireEvent.click(confirm);
  await waitFor(() => expect(mocks.remove).toHaveBeenCalledWith("decision"));
});

it("closes an account's options when it is clicked again or anywhere else", async () => {
  mocks.list.mockResolvedValue({ instances: [{ id: "decision", name: "JEV", model: "jev-latest" }], provider_types: types });
  render(<DecisionProviderSettings settings={{ ...settings, decision_provider_id: "decision", decision_provider_default_id: "decision" }} onChange={vi.fn()} saving={false} />);
  const account = await screen.findByRole("radio", { name: "JEV" });
  fireEvent.click(account);
  await screen.findByRole("button", { name: "Change Model" });
  fireEvent.click(account);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Change Model" })).toBeNull());
  fireEvent.click(account);
  await screen.findByRole("button", { name: "Change Model" });
  fireEvent.pointerDown(document.body);
  expect(screen.getByRole("button", { name: "Change Model" })).toBeTruthy();
  fireEvent.click(document.body);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Change Model" })).toBeNull());
});

it("keeps connection timing optional and rejects invalid values", async () => {
  mocks.list.mockResolvedValue({ instances: [{ id: "decision", name: "TypeSafe / JEV", model: "jev-latest" }], provider_types: types });
  const change = vi.fn();
  render(<DecisionProviderSettings settings={{ ...settings, decision_provider_id: "decision", decision_provider_default_id: "decision" }} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  const timeout = screen.getByLabelText("Decision timeout (ms)");
  expect(timeout).not.toBeVisible();
  fireEvent.click(screen.getByText("Connection Settings"));
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
  fireEvent.click(await screen.findByRole("radio", { name: "JEV" }));
  fireEvent.click(await screen.findByRole("button", { name: "Change Model" }));
  await screen.findByRole("option", { name: "jev-latest" });
  fireEvent.change(screen.getByLabelText("Model for JEV"), { target: { value: "jev-latest" } });
  expect(mocks.setModel).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(mocks.setModel).toHaveBeenCalledWith("decision", "jev-latest"));
});


it("shows the account's actions, with Refresh Models beside the model and cancellable edits", async () => {
  mocks.list.mockResolvedValue({ instances: [{ id: "decision", name: "JEV", model: "jev-latest" }], provider_types: types });
  mocks.models.mockResolvedValue({ models: ["jev-latest", "jev-1.13.0"] });
  render(<DecisionProviderSettings settings={settings} onChange={vi.fn()} saving={false} />);
  fireEvent.click(await screen.findByRole("radio", { name: "JEV" }));
  await screen.findByRole("button", { name: "Change Model" });
  expect(screen.queryByLabelText("Model for JEV")).toBeNull();
  // A decision model has no options to check (user, 2026-10-31).
  expect(screen.queryByRole("button", { name: "Check Options" })).toBeNull();
  for (const name of ["Change Model", "Remove JEV"]) {
    const button = screen.getByRole("button", { name });
    expect(button).toHaveClass("phx-action");
    expect(button).toHaveAttribute("title", name);
    expect(button.querySelector("svg")).not.toBeNull();
  }
  expect(screen.queryByRole("button", { name: "Refresh Models" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Change Model" }));
  await screen.findByRole("option", { name: "jev-1.13.0" });
  expect(screen.getByRole("button", { name: "Refresh Models" }).closest(".agentcli-add-inline")?.querySelector("select")).toBeTruthy();
  for (const name of ["Save", "Cancel"]) {
    const button = screen.getByRole("button", { name });
    expect(button).toHaveClass("phx-action");
    expect(button).not.toHaveClass("btn-primary");
    expect(button.querySelector("svg")).not.toBeNull();
  }
  fireEvent.change(screen.getByLabelText("Model for JEV"), { target: { value: "jev-1.13.0" } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(mocks.setModel).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Change Model" })).toBeVisible();
});

it("refreshes the catalog without inference, explaining in Help what that checks", async () => {
  mocks.list.mockResolvedValue({ instances: [{ id: "decision", name: "JEV", model: "jev-latest" }], provider_types: types });
  mocks.refresh.mockResolvedValue({ models: ["jev-latest", "jev-1.13.0"], declared: false });
  render(<DecisionProviderSettings settings={settings} onChange={vi.fn()} saving={false} />);
  fireEvent.click(await screen.findByRole("radio", { name: "JEV" }));
  fireEvent.click(await screen.findByRole("button", { name: "Change Model" }));
  const refresh = await screen.findByRole("button", { name: "Refresh Models" });
  expect(refresh.getAttribute("data-help-text")).toMatch(/^Decision providers use fixed routing choices/);
  expect(screen.queryByText(/Decision providers use fixed routing choices/)).toBeNull();
  fireEvent.click(refresh);
  await screen.findByText(/Refreshed: 2 models/);
  expect(mocks.refresh).toHaveBeenCalledExactlyOnceWith("decision");
  expect(screen.getByRole("option", { name: "jev-1.13.0" })).toBeVisible();
});

it("preserves the model and draft after a failed save", async () => {
  mocks.list.mockResolvedValue({ instances: [{ id: "decision", name: "JEV", model: "jev-latest" }], provider_types: types });
  mocks.models.mockResolvedValue({ models: ["jev-latest", "jev-1.13.0"] });
  mocks.setModel.mockRejectedValue(new Error("Model check failed"));
  render(<DecisionProviderSettings settings={settings} onChange={vi.fn()} saving={false} />);
  fireEvent.click(await screen.findByRole("radio", { name: "JEV" }));
  fireEvent.click(await screen.findByRole("button", { name: "Change Model" }));
  await screen.findByRole("option", { name: "jev-1.13.0" });
  fireEvent.change(screen.getByLabelText("Model for JEV"), { target: { value: "jev-1.13.0" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Model for JEV")).toHaveValue("jev-1.13.0");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Change Model" })).toBeVisible();
});


it("opens saved accounts without contacting an offline decision server", async () => {
  mocks.list.mockResolvedValue({ instances: [
    { id: "remote", name: "JEV", model: "jev-latest" },
    { id: "second", name: "Second JEV account", model: "jev-1.13.0" },
  ], provider_types: types });
  mocks.models.mockRejectedValue(new Error("Connection failed."));
  const change = vi.fn();
  render(<DecisionProviderSettings settings={{ ...settings, decision_provider_id: "remote", decision_provider_default_id: "remote" }} onChange={change} saving={false} />);
  const jev = await screen.findByRole("radio", { name: "JEV" });
  await screen.findByRole("radio", { name: "Second JEV account" });
  expect(mocks.models).not.toHaveBeenCalled();
  expect(mocks.refresh).not.toHaveBeenCalled();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(jev).toBeChecked();
  expect(screen.getByRole("radio", { name: "No decision provider" })).not.toBeChecked();
  // Clicking the account in use opens its options without choosing anything again.
  fireEvent.click(jev);
  const row = jev.closest(".routing-choice") as HTMLElement;
  fireEvent.click(within(row).getByRole("button", { name: "Change Model" }));
  await screen.findByText("Connection failed.");
  expect(mocks.models).toHaveBeenCalledExactlyOnceWith("remote");
  expect(screen.getByLabelText("Model for JEV")).toHaveValue("jev-latest");
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(change).not.toHaveBeenCalled();
  mocks.models.mockResolvedValue({ models: ["jev-latest", "new-model"] });
  fireEvent.click(within(row).getByRole("button", { name: "Change Model" }));
  await screen.findByRole("option", { name: "new-model" });
  expect(screen.queryByRole("alert")).toBeNull();
});


it("shows an unsupported saved selection as no decision provider", async () => {
  const change = vi.fn();
  render(<DecisionProviderSettings settings={{ ...settings, decision_provider_id: "retired" }} onChange={change} saving={false} />);
  await screen.findByText("TypeSafe / JEV");
  expect(screen.getByRole("radio", { name: "No decision provider" })).toBeChecked();
  expect(screen.getAllByRole("radio")).toHaveLength(1);
  expect(change).not.toHaveBeenCalled();
});

it("does not claim the provider is off after a failed account read", async () => {
  mocks.list.mockRejectedValue(new Error("Offline"));
  render(<DecisionProviderSettings settings={{ ...settings, decision_provider_id: "decision" }} onChange={vi.fn()} saving={false} />);
  await screen.findByRole("alert");
  const none = screen.getByRole("radio", { name: "No decision provider" });
  expect(none).not.toBeChecked();
  expect(none).toBeDisabled();
});

it("hides setup after JEV is configured and retains account management", async () => {
  mocks.list.mockResolvedValue({ instances: [{ id: "decision", kind: "typesafe", name: "TypeSafe / JEV", model: "jev-latest" }], provider_types: types });
  render(<DecisionProviderSettings settings={{ ...settings, decision_provider_id: "decision", decision_provider_default_id: "decision" }} onChange={vi.fn()} saving={false} />);
  const account = await screen.findByRole("radio", { name: "TypeSafe / JEV" });
  expect(account).toBeChecked();
  expect(screen.getByRole("radio", { name: "No decision provider" })).not.toBeChecked();
  expect(screen.queryByLabelText("Provider type")).toBeNull();
  expect(screen.queryByRole("option", { name: "TypeSafe / JEV" })).toBeNull();
  fireEvent.click(account);
  expect(await screen.findByRole("button", { name: "Change Model" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Remove TypeSafe / JEV" })).toBeEnabled();
});

it("chooses with the keyboard like any radio group", async () => {
  mocks.list.mockResolvedValue({ instances: [
    { id: "one", name: "First account", model: "model" },
    { id: "two", name: "Second account", model: "model" },
  ], provider_types: types });
  const onChange = vi.fn();
  render(<DecisionProviderSettings settings={{ ...settings, decision_provider_default_id: "one" }} onChange={onChange} saving={false} />);
  await screen.findByRole("radio", { name: "First account" });
  const none = screen.getByRole("radio", { name: "No decision provider" });
  const user = userEvent.setup();
  none.focus();
  await user.keyboard("{ArrowDown}");
  // One group (user, 2026-10-31): moving to an account chooses it, and the default follows on the server.
  expect(onChange).toHaveBeenCalledExactlyOnceWith("decision_provider_id", "one");
  expect(onChange).not.toHaveBeenCalledWith("decision_provider_default_id", expect.anything());
});

it("keeps refresh focused while blocking duplicate provider actions", async () => {
  mocks.list.mockResolvedValue({ instances: [{ id: "decision", name: "JEV", model: "jev-latest" }], provider_types: types });
  let release!: (value: unknown) => void;
  mocks.refresh.mockReturnValue(new Promise(resolve => { release = resolve; }));
  render(<DecisionProviderSettings settings={settings} onChange={vi.fn()} saving={false} />);
  fireEvent.click(await screen.findByRole("radio", { name: "JEV" }));
  fireEvent.click(await screen.findByRole("button", { name: "Change Model" }));
  const button = await screen.findByRole("button", { name: "Refresh Models" });
  await waitFor(() => expect(button).not.toHaveAttribute("aria-disabled", "true"));
  await userEvent.click(button);
  expect(button).toHaveFocus();
  expect(button).not.toBeDisabled();
  expect(button).toHaveAttribute("aria-disabled", "true");
  await userEvent.keyboard("{Enter} ");
  fireEvent.click(button);
  // Saving waits for the refresh too.
  expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute("aria-disabled", "true");
  expect(mocks.refresh).toHaveBeenCalledExactlyOnceWith("decision");
  await act(async () => release({ models: ["jev-latest"], declared: false }));
  await screen.findByRole("button", { name: "Refresh Models" });
  expect(button).toHaveFocus();
  expect(button).not.toHaveAttribute("aria-disabled", "true");
});
