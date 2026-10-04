import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, screen, waitFor } from "@testing-library/react";

const listAccessKeys = vi.fn();

vi.mock("../api", () => ({
  api: { listAccessKeys: (...a: unknown[]) => listAccessKeys(...a) },
}));

import { AssistBridgeSettings } from "../components/AssistBridgeSettings";
import type { GlobalSettings } from "../types";

function settings(over: Partial<GlobalSettings> = {}): GlobalSettings {
  return { assist_api_supported: true, assist_bound_access_key_id: null, ...(over as object) } as unknown as GlobalSettings;
}

describe("AssistBridgeSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listAccessKeys.mockResolvedValue([{ id: "tok-1", name: "voice-access-key" }]);
  });

  it("lists access keys and binds the selected one", async () => {
    const onChange = vi.fn();
    render(<AssistBridgeSettings settings={settings()} onChange={onChange} saving={false} />);
    await waitFor(() => expect(screen.getByRole("option", { name: "voice-access-key" })).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Assist bound access key"), { target: { value: "tok-1" } });
    expect(onChange).toHaveBeenCalledWith("assist_bound_access_key_id", "tok-1");
  });

  it("places its help above the access key dropdown", () => {
    render(<AssistBridgeSettings settings={settings()} onChange={vi.fn()} saving={false} />);
    expect(screen.getByLabelText("Assist bound access key").closest(".toggle-row"))
      .toHaveClass("toggle-row-stacked-control");
  });

  it("unbinds via the 'Not bound' option (empty string)", async () => {
    const onChange = vi.fn();
    render(<AssistBridgeSettings settings={settings({ assist_bound_access_key_id: "tok-1" })} onChange={onChange} saving={false} />);
    await waitFor(() => expect(listAccessKeys).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Assist bound access key"), { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith("assist_bound_access_key_id", "");
  });

  it("reflects the currently bound access key", async () => {
    render(<AssistBridgeSettings settings={settings({ assist_bound_access_key_id: "tok-1" })} onChange={vi.fn()} saving={false} />);
    await waitFor(() => expect((screen.getByLabelText("Assist bound access key") as HTMLSelectElement).value).toBe("tok-1"));
  });

  it("disables the dropdown when unsupported", () => {
    render(<AssistBridgeSettings settings={settings({ assist_api_supported: false })} onChange={vi.fn()} saving={false} />);
    expect((screen.getByLabelText("Assist bound access key") as HTMLSelectElement).disabled).toBe(true);
  });
});
