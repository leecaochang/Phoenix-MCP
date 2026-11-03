/**
 * The logging master switch (user, 2026-11-03): stored as disable_all_logging,
 * shown as Enable logging, on by default. Turning it off stores the flag as
 * true and greys the other logging switches.
 */

import { it, expect, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { LoggingSettings } from "../components/LoggingSettings";
import type { GlobalSettings } from "../types";

const SETTINGS = { disable_all_logging: false, log_allowed: true, log_denied: true, log_rate_limited: true, log_entity_names: true, log_client_ip: true } as GlobalSettings;

it("shows Enable logging on by default, and turning it off stores disable_all_logging as true", () => {
  const onToggle = vi.fn();
  const view = render(<LoggingSettings settings={SETTINGS} saving={false} onToggle={onToggle} />);
  const master = view.getByLabelText("Enable logging") as HTMLInputElement;
  expect(master.checked).toBe(true);
  expect(view.queryByLabelText(/Disable all logging/)).toBeNull();
  fireEvent.click(master);
  expect(onToggle).toHaveBeenCalledWith("disable_all_logging", true);
});

it("shows it off while logging is disabled, greys the rest, and turning it on stores the flag as false", () => {
  const onToggle = vi.fn();
  const view = render(<LoggingSettings settings={{ ...SETTINGS, disable_all_logging: true }} saving={false} onToggle={onToggle} />);
  const master = view.getByLabelText("Enable logging") as HTMLInputElement;
  expect(master.checked).toBe(false);
  expect(view.getByLabelText("Log allowed requests")).toBeDisabled();
  fireEvent.click(master);
  expect(onToggle).toHaveBeenCalledWith("disable_all_logging", false);
});
