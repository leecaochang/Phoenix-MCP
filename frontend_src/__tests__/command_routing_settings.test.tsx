import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { GlobalSettings } from "../types";
vi.mock("../components/DecisionProviderSettings", () => ({ DecisionProviderSettings: () => <div>Accounts</div> }));
import { CommandRoutingSettings } from "../components/CommandRoutingSettings";

it("shows two independent switches without experiment or diagnostic controls", () => {
  const change = vi.fn();
  render(<CommandRoutingSettings settings={{} as GlobalSettings} onChange={change} saving={false} />);
  expect(screen.getByRole("heading", { name: "Command routing" })).toBeVisible();
  expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(screen.queryByText("Test a command")).toBeNull();
  expect(screen.queryByText("Automatic routing languages")).toBeNull();
  expect(screen.getByLabelText("Enable for Voice")).not.toBeChecked();
  expect(screen.getByLabelText("Enable for Chat")).not.toBeChecked();
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByLabelText("Enable for Voice"));
  expect(change).toHaveBeenCalledExactlyOnceWith("voice_agent_routing_mode", "auto");
  fireEvent.click(screen.getByLabelText("Enable for Chat"));
  expect(change).toHaveBeenLastCalledWith("agentcli_routing_mode", "auto");
});

it("turns routing off and locks controls during a save", () => {
  const change = vi.fn();
  const settings = { voice_agent_routing_mode: "auto", agentcli_routing_mode: "auto" } as GlobalSettings;
  const view = render(<CommandRoutingSettings settings={settings} onChange={change} saving={false} />);
  fireEvent.click(screen.getByLabelText("Enable for Chat"));
  expect(change).toHaveBeenCalledExactlyOnceWith("agentcli_routing_mode", "off");
  view.rerender(<CommandRoutingSettings settings={settings} onChange={change} saving />);
  for (const toggle of screen.getAllByRole("checkbox")) {
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
  }
});
