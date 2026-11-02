import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PersonaPicker } from "../components/PersonaPicker";
import { t } from "../i18n";
import { api } from "../api";
import type { AccessKeyRecord, PermissionTree } from "../types";
import { Notifications, NotificationsContext } from "../ui/notify";
import { PermissionSummary } from "../components/PermissionSummary";
import { companionCap, companionLabel, type CompanionDomains } from "../utils/companion_reach";
vi.mock("../api", () => ({ api: { patchAccessKey: vi.fn() }, currentHass: () => null }));

const REACH: CompanionDomains = {
  control_domains: ["light", "switch"],
  read_domains: ["binary_sensor", "sensor"],
  sensor_classes: ["temperature"],
};
const STATES = {
  "light.desk": {},
  "sensor.temp": { attributes: { device_class: "temperature" } },
  "sensor.co2": { attributes: { device_class: "carbon_dioxide" } },
  "lock.door": {},
};

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
    expect(companion).toHaveTextContent(t("personas.home_companion.description"));
    expect(companion).not.toHaveTextContent("Experimental");
    expect(companion.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("says so when choosing it turns pass-through off", async () => {
    const accessKey = { id: "test", name: "kitchen", persona: "custom", pass_through: true, announce_all_tools: false } as AccessKeyRecord;
    vi.mocked(api.patchAccessKey).mockResolvedValue({ ...accessKey, persona: "home_companion", pass_through: false } as AccessKeyRecord);
    const center = new Notifications();
    render(<NotificationsContext.Provider value={center}><PersonaPicker accessKey={accessKey} onUpdate={vi.fn()} /></NotificationsContext.Provider>);
    fireEvent.click(screen.getByRole("button", { name: /Home Companion/ }));
    await waitFor(() => expect(center.history[0]?.text).toBe("Home Companion turned off pass-through and announcing all tools for kitchen."));
  });
});

describe("Home Companion reach", () => {
  it("mirrors the resolver: live state, its domains, its sensor classes, Write as Read in a read domain", () => {
    expect(companionCap("light.desk", REACH, STATES)).toBe("control");
    expect(companionCap("sensor.temp", REACH, STATES)).toBe("read");
    expect(companionCap("sensor.co2", REACH, STATES)).toBeNull();
    expect(companionCap("lock.door", REACH, STATES)).toBeNull();
    expect(companionCap("light.unloaded", REACH, STATES)).toBeNull();
    expect(companionLabel("WRITE", "read")).toBe("READ");
    expect(companionLabel("DENY", null)).toBe("NO_ACCESS");
    expect(companionLabel("WRITE", "control")).toBe("WRITE");
  });

  it("Permissions Summary shows what a Home Companion key really gets from each rule", () => {
    const permissions = {
      domains: { lock: { state: "GREEN" }, binary_sensor: { state: "GREEN" }, light: { state: "GREEN" } },
      devices: {}, entities: {},
    } as unknown as PermissionTree;
    render(<PermissionSummary permissions={permissions} companion={REACH} />);
    const row = (domain: string) => screen.getByRole("row", { name: new RegExp(`\\b${domain}\\b`) }).textContent;
    expect(row("lock")).toMatch(/NO ACCESS$/);
    expect(row("binary_sensor")).toMatch(/Read$/);
    expect(row("light")).toMatch(/Write$/);
  });
});
