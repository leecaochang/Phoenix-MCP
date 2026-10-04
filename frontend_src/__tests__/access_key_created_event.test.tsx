/** Creating an access key must announce itself on the window.
 *
 * Every host caches the access key list: the panel shell gates its header "Agent
 * Chat" button on a non-empty list and hands the panel-hosted chat window its
 * `accessKeys` prop, and the floating window (a separate bundle) refetches only on
 * `phx-access-keys-changed`. Without the event a first access key stays invisible to both
 * surfaces until a page reload, and the chat window's prune-dead-selection
 * effect discards the just-created access key id.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent, screen, waitFor } from "@testing-library/react";

const createAccessKey = vi.fn();
const patchAccessKey = vi.fn();

vi.mock("../api", () => ({
  api: {
    getEntityTree: vi.fn().mockResolvedValue({}),
    getSettings: vi.fn().mockResolvedValue({ mesa_mode: "advisory" }),
    createAccessKey: (...a: unknown[]) => createAccessKey(...a),
    patchAccessKey: (...a: unknown[]) => patchAccessKey(...a),
  },
}));

vi.mock("../components/EntityTree", () => ({ EntityTree: () => null }));

import { OnboardingWizard } from "../views/OnboardingWizard";
import { AccessKeyCreateModal } from "../components/AccessKeyCreateModal";

const RECORD = {
  id: "t1",
  name: "wizard-access-key",
  permissions: { domains: {}, devices: {}, entities: {} },
};

describe("access key creation announces phx-access-keys-changed", () => {
  let fired: number;
  const bump = () => { fired += 1; };

  beforeEach(() => {
    vi.clearAllMocks();
    fired = 0;
    window.addEventListener("phx-access-keys-changed", bump);
    createAccessKey.mockResolvedValue({ access_key: "phx_" + "a".repeat(64), ...RECORD });
    patchAccessKey.mockResolvedValue(RECORD);
  });

  afterEach(() => {
    window.removeEventListener("phx-access-keys-changed", bump);
  });

  it("dispatches once the access key exists, so every host refreshes its list", async () => {
    render(
      <AccessKeyCreateModal
        existingNames={[]}
        onCreated={() => {}}
        onClose={() => {}}
        onOpenSettings={() => {}}
      />,
    );

    expect(screen.getByPlaceholderText("my_access_key")).toHaveValue("");
    fireEvent.change(screen.getByLabelText(/name \(required\)/i), {
      target: { value: "wizard-access-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(createAccessKey).toHaveBeenCalled());
    await waitFor(() => expect(fired).toBe(1));
  });

  it("uses the access-key example when creating from the wizard", async () => {
    render(<OnboardingWizard onCancel={() => {}} onFinish={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    const input = screen.getByPlaceholderText("my_access_key");
    expect(input).toHaveValue("my_access_key");
    expect(screen.queryByDisplayValue("test_access_key")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create access key" }));
    await waitFor(() => expect(createAccessKey).toHaveBeenCalledWith({
      name: "my_access_key", expires_at: undefined, pass_through: false,
    }));
    await waitFor(() => expect(fired).toBe(1));
  });

});
