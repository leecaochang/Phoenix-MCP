/**
 * The setup wizard keeps its original steps with three changes: Install
 * Phoenix leads the stepper as a completed step, the choice between Agent
 * Chat and an external app is step 2 and where the wizard opens, and every
 * Back goes to the step before it in the new order. Each step reports itself
 * for the Guide.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { PermissionTree } from "../types";

const mocks = vi.hoisted(() => ({
  getEntityTree: vi.fn(),
  getSettings: vi.fn(),
  createAccessKey: vi.fn(),
  patchAccessKey: vi.fn(),
  getAccessKeyConnection: vi.fn(),
  getAgentCliProviders: vi.fn(),
  listAccessKeys: vi.fn().mockResolvedValue([]),
}));

vi.mock("../api", () => ({ api: mocks, ApiError: class extends Error {} }));
vi.mock("../components/EntityTree", () => ({
  EntityTree: ({ onPermissionsChange }: { onPermissionsChange: (tree: PermissionTree) => void }) => (
    <button type="button" onClick={() => onPermissionsChange({ domains: {}, devices: {}, entities: { "light.desk": { state: "GREEN", hint: null } } })}>Grant desk</button>
  ),
}));
vi.mock("../components/ConnectInstructions", () => ({
  ConnectInstructions: () => <div>Connection instructions</div>,
  CopyCodeBox: () => null,
}));

import { OnboardingWizard } from "../views/OnboardingWizard";
import { WizardGuideContext, type WizardGuideStep } from "../shell/guide";
import { DisplayModeContext } from "../ui/ActionButton";
import { getDurable, setSessionTurns, __resetAgentCliState } from "../utils/agentcli_state";

const RECORD = { id: "k1", name: "my_access_key", permissions: { domains: {}, devices: {}, entities: {} } };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getEntityTree.mockResolvedValue({
    light: { devices: {}, deviceless_entities: ["light.desk"], entity_details: { "light.desk": { entity_id: "light.desk", friendly_name: "Desk", device_id: null, area_id: null, area_name: null, labels: [] } } },
  });
  mocks.getSettings.mockResolvedValue({ mesa_mode: "advisory" });
  mocks.createAccessKey.mockResolvedValue({ access_key: "phx_" + "a".repeat(64), ...RECORD });
  mocks.patchAccessKey.mockResolvedValue(RECORD);
  mocks.getAccessKeyConnection.mockResolvedValue({ request_count: 0 });
  mocks.getAgentCliProviders.mockResolvedValue({ instances: [], provider_types: [] });
});

const stepper = () => screen.getByRole("list", { name: "Setup progress" });
const stepLabels = () => within(stepper()).getAllByRole("listitem").map((li) => li.querySelector(".wizard-step-label")!.textContent);
const current = () => within(stepper()).getAllByRole("listitem").find((li) => li.getAttribute("aria-current") === "step")!.querySelector(".wizard-step-label")!.textContent;
const heading = () => screen.getByRole("heading", { level: 3 }).textContent;
const press = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));

/** From the choice to the Access step, creating the key on the way. */
async function toAccess(path: "Agent Chat Using HA (Recommended)" | "Connect an External App") {
  press(new RegExp(`^${path.replace(/[()]/g, "\\$&")}`));
  press("Next");
  press("Create Access Key");
  await waitFor(() => expect(heading()).toBe("Grant Access to One Light"));
}

describe("the setup wizard", () => {
  it("opens on the choice, after Install Phoenix shown as completed", () => {
    render(<OnboardingWizard onCancel={vi.fn()} onFinish={vi.fn()} />);
    expect(heading()).toBe("Choose How to Connect");
    expect(stepLabels()).toEqual(["Install Phoenix", "Choose", "Persona", "Access Key", "Access", "Connect", "Test", "Done"]);
    expect(current()).toBe("Choose");
    const install = within(stepper()).getAllByRole("listitem")[0];
    expect(install).toHaveClass("wizard-step-done");
    expect(within(install).getByText("Completed")).toHaveClass("sr-only");
    // The first step offers Cancel, as the original first step did.
    const onCancel = vi.fn();
    render(<OnboardingWizard onCancel={onCancel} onFinish={vi.fn()} />);
    fireEvent.click(screen.getAllByRole("button", { name: "Cancel" })[1]);
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("goes back to the step before each one on the external app path", async () => {
    render(<OnboardingWizard onCancel={vi.fn()} onFinish={vi.fn()} />);
    press(/^Connect an External App/);
    expect(heading()).toBe("Choose a Persona");
    expect(current()).toBe("Persona");
    press("Back");
    expect(heading()).toBe("Choose How to Connect");
    expect(screen.getByRole("button", { name: /Connect an External App/ })).toHaveAttribute("aria-pressed", "true");

    press(/^Connect an External App/);
    press("Next");
    expect(heading()).toBe("Name Your Test Access Key");
    press("Back");
    expect(heading()).toBe("Choose a Persona");
    press("Next");
    press("Create Access Key");
    await waitFor(() => expect(heading()).toBe("Grant Access to One Light"));
    expect(mocks.createAccessKey).toHaveBeenCalledOnce();

    press("Back");
    expect(heading()).toBe("Name Your Test Access Key");
    press("Next");
    await waitFor(() => expect(heading()).toBe("Grant Access to One Light"));
    expect(mocks.createAccessKey).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    press("Grant desk");
    press("Next");
    expect(heading()).toBe("Connect Phoenix MCP to Your AI Agent");
    expect(current()).toBe("Connect");

    press("Back");
    expect(heading()).toBe("Grant Access to One Light");
    press("Next");
    press("Test Connection");
    expect(heading()).toBe("Test the Connection");
    press("Back");
    expect(heading()).toBe("Connect Phoenix MCP to Your AI Agent");
    press("Connect Later");
    expect(heading()).toBe("You're all set");
    expect(current()).toBe("Done");
  });

  it("takes the Agent Chat path from Access to the provider, whose Back returns to Access", async () => {
    render(<OnboardingWizard onCancel={vi.fn()} onFinish={vi.fn()} />);
    await toAccess("Agent Chat Using HA (Recommended)");
    expect(stepLabels()).toEqual(["Install Phoenix", "Choose", "Persona", "Access Key", "Access", "Provider", "Try"]);
    press("Grant desk");
    press("Next");
    expect(heading()).toBe("Set Up Your Model Provider");
    expect(current()).toBe("Provider");
    press("Back");
    expect(heading()).toBe("Grant Access to One Light");
  });

  it("shows every button's icon and text, even with the toolbar showing icons only (user, 2026-10-28)", async () => {
    mocks.getAgentCliProviders.mockResolvedValue({ instances: [{ id: "inst1", name: "My Claude", model: "claude" }], provider_types: [] });
    render(<DisplayModeContext.Provider value="icons"><OnboardingWizard onCancel={vi.fn()} onFinish={vi.fn()} /></DisplayModeContext.Provider>);
    const allLabelled = () => {
      for (const b of document.querySelectorAll<HTMLButtonElement>(".wizard-actions button, .wizard-choice, .agentcli-settings-form-actions button")) {
        expect(b.querySelector("svg"), b.textContent ?? "").not.toBeNull();
        expect(b.textContent?.trim(), b.outerHTML).not.toBe("");
      }
    };
    allLabelled();
    await toAccess("Agent Chat Using HA (Recommended)");
    allLabelled();
    press("Grant desk");
    press("Next");
    await screen.findByRole("button", { name: "Try Now" });
    allLabelled();
  });

  it("opens Agent Chat for the test with an empty conversation (user, 2026-10-28)", async () => {
    __resetAgentCliState();
    setSessionTurns([{ entries: [{ kind: "user", text: "old", ts: 1 }], messages: [], lines: 1 }]);
    mocks.getAgentCliProviders.mockResolvedValue({ instances: [{ id: "inst1", name: "My Claude", model: "claude" }], provider_types: [] });
    const opened = vi.fn();
    window.addEventListener("phx-open-agentcli", opened);
    try {
      render(<OnboardingWizard onCancel={vi.fn()} onFinish={vi.fn()} />);
      await toAccess("Agent Chat Using HA (Recommended)");
      press("Grant desk");
      press("Next");
      fireEvent.click(await screen.findByRole("button", { name: "Try Now" }));
      expect(opened).toHaveBeenCalledOnce();
      const durable = getDurable();
      expect(durable.freshConversation).toBe(true);
      expect(durable.prefill).toBe("Which lights in my home can you see?");
      expect(durable.instanceId).toBe("inst1");
    } finally {
      window.removeEventListener("phx-open-agentcli", opened);
      __resetAgentCliState();
    }
  });

  it("reports each step with the part its Guide topic explains, and its end", async () => {
    const report = vi.fn<(step: WizardGuideStep | null) => void>();
    const view = render(
      <WizardGuideContext.Provider value={{ report, guideBeside: true }}>
        <OnboardingWizard onCancel={vi.fn()} onFinish={vi.fn()} />
      </WizardGuideContext.Provider>,
    );
    expect(report).toHaveBeenCalledTimes(1);
    const [first] = report.mock.calls[0];
    expect(first?.step).toBe("choose");
    expect(first?.part).toHaveClass("wizard-choice-row");
    expect(first?.content).toHaveClass("wizard-body");
    // The wizard makes room for the Guide beside it.
    expect(document.querySelector(".wizard-root")).toHaveClass("is-guided");

    press(/^Connect an External App/);
    const [second] = report.mock.calls[1];
    expect(second?.step).toBe("persona");
    expect(second?.part.querySelector(".persona-picker, [class*=persona]")).not.toBeNull();

    act(() => view.unmount());
    expect(report).toHaveBeenLastCalledWith(null);
  });
});
