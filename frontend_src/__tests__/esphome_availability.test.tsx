import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("../api", () => ({ api: {} }));

import { CapabilityMatrix } from "../components/CapabilityMatrix";
import { PersonaPicker } from "../components/PersonaPicker";
import type { AccessKeyRecord } from "../types";

function accessKey(over: Partial<AccessKeyRecord> = {}): AccessKeyRecord {
  return {
    id: "tok-1",
    name: "t",
    persona: "custom",
    pass_through: false,
    cap_esphome_yaml: "deny",
    cap_esphome_flash: "deny",
    ...(over as object),
  } as unknown as AccessKeyRecord;
}

// Both ESPHome capabilities carry the badge, so each message appears once per row.
const ESPHOME_CAP_ROWS = 2;

describe("ESPHome availability in the panel", () => {
  it("marks the capabilities when neither ESPHome nor the add-on is present", () => {
    render(
      <CapabilityMatrix
        accessKey={accessKey()}
        onUpdate={vi.fn()}
        esphome={{ integration: false, builder: false }}
      />,
    );
    expect(screen.getAllByText("ESPHome not detected")).toHaveLength(ESPHOME_CAP_ROWS);
  });

  it("names the add-on specifically when ESPHome itself is present", () => {
    render(
      <CapabilityMatrix
        accessKey={accessKey()}
        onUpdate={vi.fn()}
        esphome={{ integration: true, builder: false }}
      />,
    );
    expect(screen.getAllByText("Device Builder add-on not detected"))
      .toHaveLength(ESPHOME_CAP_ROWS);
  });

  it("shows nothing when the Device Builder is available", () => {
    render(
      <CapabilityMatrix
        accessKey={accessKey()}
        onUpdate={vi.fn()}
        esphome={{ integration: true, builder: true }}
      />,
    );
    expect(screen.queryByText(/not detected/)).toBeNull();
  });

  it("shows nothing while settings are still loading", () => {
    render(<CapabilityMatrix accessKey={accessKey()} onUpdate={vi.fn()} esphome={null} />);
    expect(screen.queryByText(/not detected/)).toBeNull();
  });

  it("keeps the capability settable so a token can be prepared in advance", () => {
    render(
      <CapabilityMatrix
        accessKey={accessKey()}
        onUpdate={vi.fn()}
        esphome={{ integration: false, builder: false }}
      />,
    );
    const group = screen.getByRole("group", { name: "ESPHome device YAML" });
    const radios = group.querySelectorAll("button");
    expect(radios.length).toBeGreaterThan(0);
    // Only the Confirm-unavailable mechanic may disable a mode; absence of
    // ESPHome must not.
    expect(Array.from(radios).every((r) => !(r as HTMLButtonElement).disabled)).toBe(true);
  });

  it("notes the missing system on the esphome persona card", () => {
    render(
      <PersonaPicker
        accessKey={accessKey()}
        onUpdate={vi.fn()}
        esphome={{ integration: false, builder: false }}
      />,
    );
    expect(screen.getByText("ESPHome was not detected on this system")).toBeTruthy();
  });

  it("leaves the persona card unannotated when ESPHome is present", () => {
    render(
      <PersonaPicker
        accessKey={accessKey()}
        onUpdate={vi.fn()}
        esphome={{ integration: true, builder: true }}
      />,
    );
    expect(screen.queryByText(/was not detected/)).toBeNull();
  });
});
