import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";
import { ConfigurationRecovery } from "../components/ConfigurationRecovery";
import { api } from "../api";

vi.mock("../api", () => ({ api: { listConfigurationRecovery: vi.fn(), configurationRecoveryAction: vi.fn() } }));
const record = { id: "r", resource: "config_entry:example", phase: "reviewed", active: false,
  created_at: "2026-09-25T00:00:00Z", before: { snapshot_type: "template_helper" }, source: null };
function open(container: HTMLElement, value = true) {
  const details = container.querySelector("details")!;
  details.open = value;
  fireEvent(details, new Event("toggle"));
}

describe("configuration recovery", () => {
  beforeEach(() => vi.resetAllMocks());
  it("does not show an empty success state when loading fails", async () => {
    vi.mocked(api.listConfigurationRecovery).mockRejectedValue(new Error("offline"));
    const view = render(<ConfigurationRecovery />);
    open(view.container);
    await view.findByRole("alert");
    expect(view.queryByText("No recovery records.")).toBeNull();
  });
  it("requires side-specific confirmation and preserves mutation ownership across reopen", async () => {
    vi.mocked(api.listConfigurationRecovery).mockResolvedValue({ records: [record] });
    let finish!: (v: {}) => void;
    vi.mocked(api.configurationRecoveryAction).mockReturnValue(new Promise(resolve => { finish = resolve; }));
    const view = render(<ConfigurationRecovery />);
    open(view.container);
    fireEvent.click(await view.findByText("Restore safety copy"));
    expect(api.configurationRecoveryAction).not.toHaveBeenCalled();
    expect(view.getByText("Safety copy:")).toBeTruthy();
    fireEvent.click(view.getByText("Confirm"));
    await waitFor(() => expect(api.configurationRecoveryAction).toHaveBeenCalledWith("r", "restore", "before"));
    const before = vi.mocked(api.listConfigurationRecovery).mock.calls.length;
    open(view.container, false); open(view.container);
    expect(api.listConfigurationRecovery).toHaveBeenCalledTimes(before);
    expect((view.getByText("Inspect current state") as HTMLButtonElement).disabled).toBe(true);
    finish({});
    await waitFor(() => expect((view.getByText("Inspect current state") as HTMLButtonElement).disabled).toBe(false));
  });
});
