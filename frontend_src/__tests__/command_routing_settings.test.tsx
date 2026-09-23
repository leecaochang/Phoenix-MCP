import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { GlobalSettings } from "../types";

const preview = vi.fn();
vi.mock("../api", () => ({ api: {
  getDecisionProviders: () => Promise.resolve({ instances: [], provider_types: [] }),
  listTokens: () => Promise.resolve([{ id: "t", name: "Voice" }]),
  previewCommandRoute: (...args: unknown[]) => preview(...args),
} }));
import { CommandRoutingSettings } from "../components/CommandRoutingSettings";

const settings = { voice_agent_token_id: "t", command_routing_timeout_ms: 500 } as GlobalSettings;
const candidate = {
  route: "local_candidate", reason: "supported", executed: false, elapsed_ms: 12,
  proposal: { intent: "HassTurnOn", entity_ids: ["light.desk"] },
  voice_observations: { observations: 3, candidates: 2 },
};
beforeEach(() => { preview.mockReset(); });

it("previews explicitly and changes Voice routing only on request", async () => {
  const onChange = vi.fn();
  render(<CommandRoutingSettings settings={settings} onChange={onChange} saving={false} />);
  await screen.findByText("Voice");
  expect(preview).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Voice command routing")).toHaveValue("off");
  fireEvent.change(screen.getByLabelText("Voice command routing"), { target: { value: "local_shadow" } });
  expect(onChange).toHaveBeenCalledWith("voice_agent_routing_mode", "local_shadow");
  fireEvent.change(screen.getByLabelText("Command in English"), { target: { value: "turn on desk lamp" } });
  preview.mockResolvedValue(candidate);
  fireEvent.click(screen.getByText("Test command"));
  await screen.findByText("Local candidate found");
  expect(preview).toHaveBeenCalledWith("t", "turn on desk lamp", false, false);
  expect(screen.getByText(/Nothing was executed/)).toBeTruthy();
});

it("supports opting into local execution while previews remain inert", async () => {
  const onChange = vi.fn();
  render(<CommandRoutingSettings settings={{ ...settings, voice_agent_routing_mode: "local" }} onChange={onChange} saving={false} />);
  await screen.findByText("Voice");
  expect(screen.getByLabelText("Voice command routing")).toHaveValue("local");
  expect(screen.getByText(/Run locally can operate devices/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Voice command routing"), { target: { value: "off" } });
  expect(onChange).toHaveBeenCalledWith("voice_agent_routing_mode", "off");
  expect(preview).not.toHaveBeenCalled();
});

it("discards a preview when its command is edited", async () => {
  let resolve!: (value: unknown) => void;
  preview.mockReturnValue(new Promise(done => { resolve = done; }));
  render(<CommandRoutingSettings settings={settings} onChange={vi.fn()} saving={false} />);
  await screen.findByText("Voice");
  fireEvent.change(screen.getByLabelText("Command in English"), { target: { value: "old" } });
  fireEvent.click(screen.getByText("Test command"));
  fireEvent.change(screen.getByLabelText("Command in English"), { target: { value: "new" } });
  await act(async () => resolve(candidate));
  expect(screen.queryByText("Local candidate found")).toBeNull();
});

it.each(["no_match", "unsupported", "timeout"])("explains %s without implying an execution approval", async reason => {
  preview.mockResolvedValue({ ...candidate, route: "provider", reason, proposal: null });
  render(<CommandRoutingSettings settings={settings} onChange={vi.fn()} saving={false} />);
  await screen.findByText("Voice");
  fireEvent.change(screen.getByLabelText("Command in English"), { target: { value: "turn on desk lamp" } });
  fireEvent.click(screen.getByText("Test command"));
  await screen.findByText("Use the selected provider");
  expect(screen.queryByText(/execution-time permission/)).toBeNull();
  expect(screen.getByText(/Nothing was executed/)).toBeTruthy();
  if (reason === "no_match") {
    expect(screen.getByText(/exact name or alias/)).toBeTruthy();
  } else {
    expect(screen.getByText(/No local candidate/)).toBeTruthy();
  }
});

it("shows recoverable errors and rejects invalid budgets", async () => {
  const onChange = vi.fn();
  preview.mockRejectedValue(new Error("network"));
  render(<CommandRoutingSettings settings={settings} onChange={onChange} saving={false} />);
  await screen.findByText("Voice");
  const budget = screen.getByLabelText("Recognition budget (ms)");
  fireEvent.change(budget, { target: { value: "1" } });
  fireEvent.blur(budget);
  expect(onChange).not.toHaveBeenCalled();
  expect(budget).toHaveValue(500);
  fireEvent.change(screen.getByLabelText("Command in English"), { target: { value: "hello" } });
  fireEvent.click(screen.getByText("Test command"));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("try again"));
});

it("requires an explicit choice before sending a preview to the decision account", async () => {
  preview.mockResolvedValue({ ...candidate, route: "provider", proposal: null, reason: "no_match",
    decision_shadow: { reason: "observed", decision: { choice: "light_on", confidence: 0.8 }, executed: false, elapsed_ms: 100 },
  });
  render(<CommandRoutingSettings settings={{ ...settings, decision_provider_id: "decision" }} onChange={vi.fn()} saving={false} />);
  await screen.findByText("Voice");
  const compare = screen.getByLabelText(/Also compare local misses/);
  expect(compare).not.toBeChecked();
  fireEvent.click(compare);
  fireEvent.change(screen.getByLabelText("Command in English"), { target: { value: "light up the desk" } });
  fireEvent.click(screen.getByText("Test command"));
  await screen.findByText(/Decision results are observations only/);
  expect(preview).toHaveBeenCalledWith("t", "light up the desk", true, false);
  expect(screen.getByText(/Nothing was executed/)).toBeTruthy();
});

it("asks separately before sharing target metadata and shows the inert selection", async () => {
  preview.mockResolvedValue({ ...candidate, route: "provider", proposal: null, reason: "no_match",
    decision_shadow: { reason: "observed", decision: { choice: "light_on", confidence: 0.8 }, executed: false, elapsed_ms: 600,
      target_selection: { reason: "target_selected", entity_id: "light.desk", elapsed_ms: 300 } },
  });
  const props = { settings: { ...settings, decision_provider_id: "decision" }, onChange: vi.fn(), saving: false };
  const { rerender } = render(<CommandRoutingSettings {...props} />);
  await screen.findByText("Voice");
  expect(screen.queryByLabelText(/Also preview a single target/)).toBeNull();
  fireEvent.click(screen.getByLabelText(/Also compare local misses/));
  const targets = screen.getByLabelText(/Also preview a single target/);
  expect(targets).not.toBeChecked();
  expect(screen.getByText(/No state values or other attributes are sent/)).toBeTruthy();
  fireEvent.click(targets);
  fireEvent.change(screen.getByLabelText("Command in English"), { target: { value: "light up my desk" } });
  fireEvent.click(screen.getByText("Test command"));
  await screen.findByText("Suggested target:");
  expect(screen.getByText("light.desk")).toBeTruthy();
  expect(screen.getByText(/Target selection is a preview only/)).toBeTruthy();
  expect(preview).toHaveBeenCalledWith("t", "light up my desk", true, true);
  rerender(<CommandRoutingSettings {...props} settings={{ ...props.settings, decision_provider_id: "new" }} />);
  expect(screen.queryByText("Suggested target:")).toBeNull();
  expect(screen.getByLabelText(/Also compare local misses/)).not.toBeChecked();
  fireEvent.click(screen.getByLabelText(/Also compare local misses/));
  expect(screen.getByLabelText(/Also preview a single target/)).not.toBeChecked();
});

it("discards target results if metadata sharing is disabled during a request", async () => {
  let resolve!: (value: unknown) => void;
  preview.mockReturnValue(new Promise(done => { resolve = done; }));
  render(<CommandRoutingSettings settings={{ ...settings, decision_provider_id: "decision" }} onChange={vi.fn()} saving={false} />);
  await screen.findByText("Voice");
  fireEvent.click(screen.getByLabelText(/Also compare local misses/));
  fireEvent.click(screen.getByLabelText(/Also preview a single target/));
  fireEvent.change(screen.getByLabelText("Command in English"), { target: { value: "light up my desk" } });
  fireEvent.click(screen.getByText("Test command"));
  fireEvent.click(screen.getByLabelText(/Also compare local misses/));
  await act(async () => resolve({ ...candidate, decision_shadow: { target_selection: { entity_id: "light.desk" } } }));
  expect(screen.queryByText("Suggested target:")).toBeNull();
  fireEvent.click(screen.getByLabelText(/Also compare local misses/));
  expect(screen.getByLabelText(/Also preview a single target/)).not.toBeChecked();
});

it("requires an account for hybrid mode without enabling it automatically", async () => {
  const onChange = vi.fn();
  const view = render(<CommandRoutingSettings settings={settings} onChange={onChange} saving={false} />);
  await screen.findByText("Voice");
  expect(screen.getByRole("option", { name: "Run locally, then use the decision provider" })).toBeDisabled();
  view.rerender(<CommandRoutingSettings settings={{ ...settings, decision_provider_id: "decision" }} onChange={onChange} saving={false} />);
  expect(screen.getByRole("option", { name: "Run locally, then use the decision provider" })).not.toBeDisabled();
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Voice command routing"), { target: { value: "hybrid" } });
  expect(onChange).toHaveBeenCalledWith("voice_agent_routing_mode", "hybrid");
  expect(preview).not.toHaveBeenCalled();
});

it("shows a zero brightness proposal without implying execution or no remote usage", async () => {
  preview.mockResolvedValue({ ...candidate, route: "provider", proposal: null, reason: "no_match",
    decision_shadow: { reason: "observed", elapsed_ms: 600, executed: false,
      target_selection: { reason: "target_selected", entity_id: "light.desk", elapsed_ms: 300,
        proposal: { intent: "HassLightSet", entity_ids: ["light.desk"], brightness_pct: 0 } } },
  });
  render(<CommandRoutingSettings settings={{ ...settings, decision_provider_id: "decision", voice_agent_routing_mode: "hybrid" }} onChange={vi.fn()} saving={false} />);
  await screen.findByText("Voice");
  fireEvent.click(screen.getByLabelText(/Also compare local misses/));
  fireEvent.click(screen.getByLabelText(/Also preview a single target/));
  fireEvent.change(screen.getByLabelText("Command in English"), { target: { value: "zero brightness" } });
  fireEvent.click(screen.getByText("Test command"));
  await screen.findByText("Suggested brightness: 0%");
  expect(screen.queryByText(/Nothing was executed or sent to a provider/)).toBeNull();
  expect(screen.getByText(/Nothing was executed or queued for approval/)).toBeTruthy();
  expect(preview).toHaveBeenCalledWith("t", "zero brightness", true, true);
});
