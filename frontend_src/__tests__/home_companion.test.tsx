import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PersonaPicker } from "../components/PersonaPicker";
import { api } from "../api";
import type { AccessKeyRecord } from "../types";
vi.mock("../api", () => ({ api: { patchAccessKey: vi.fn() } }));

describe("Home Companion setup", () => {
  it("applies the explicit persona and shows its enforced scope guidance", async () => {
    const accessKey = { id: "test", persona: "custom" } as AccessKeyRecord;
    const updated = { ...accessKey, persona: "home_companion" } as AccessKeyRecord;
    vi.mocked(api.patchAccessKey).mockResolvedValue(updated);
    const onUpdate = vi.fn();
    const view = render(<PersonaPicker accessKey={accessKey} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByRole("button", { name: /Home Companion/ }));
    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith(updated));
    expect(api.patchAccessKey).toHaveBeenCalledWith("test", expect.objectContaining({ persona: "home_companion", cap_filesystem: "deny", cap_physical_control: "confirm", cap_service_response: "deny" }));
    view.rerender(<PersonaPicker accessKey={updated} onUpdate={onUpdate} />);
    expect(screen.getByText(/Capabilities enable or disable access within the household catalog/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Home Companion/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("places the distinct task persona before Custom and describes its intended surfaces", () => {
    render(<PersonaPicker accessKey={{ id: "test", persona: "custom" } as AccessKeyRecord} onUpdate={vi.fn()} />);
    const buttons = screen.getAllByRole("button");
    const companion = screen.getByRole("button", { name: /Home Companion/ });
    expect(buttons[buttons.length - 2]).toBe(companion);
    expect(buttons[buttons.length - 1]).toHaveTextContent("Custom");
    expect(companion).toHaveTextContent("Designed for Agent Chat and Voice");
    expect(companion).not.toHaveTextContent("Experimental");
    expect(companion.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });
});
