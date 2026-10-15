import { describe, it, expect, vi } from "vitest";
import {
  formatDate, formatDateTime, accessKeyStatus, copyToClipboard, HIGH_RISK_DOMAINS,
  friendlyToolName, friendlyResource,
} from "../utils";
import type { AccessKeyRecord } from "../types";

function makeAccessKey(overrides: Partial<AccessKeyRecord> = {}): AccessKeyRecord {
  return {
    id: "tok-1",
    name: "test-access-key",
    created_at: "2025-01-01T00:00:00Z",
    created_by: "user1",
    expires_at: null,
    revoked: false,
    last_used_at: null,
    updated_at: null,
    pass_through: false,
    persona: "custom",
    notes: null,
    confirm_inline_wait_seconds: 0,
    rate_limit_requests: 60,
    rate_limit_burst: 10,
    cap_automation_write: "deny",
    cap_script_write: "deny",
    cap_blueprint_write: "deny",
    cap_log_read: "deny",
    cap_log_control: "deny",
    cap_config_read: "deny",
    cap_template_render: "deny",
    cap_restart: "deny",
    cap_physical_control: "deny",
    cap_service_response: "deny",
    cap_broadcast: "deny",
    cap_search: "deny",
    cap_registry_read: "deny",
    cap_traces: "deny",
    cap_diagnostics: "deny",
    cap_scene_write: "deny",
    cap_helper_write: "deny",
    cap_calendar_write: "deny",
    cap_integration_write: "deny",
    cap_integration_reconfigure: "deny",
    cap_lovelace_write: "deny",
    cap_registry_write: "deny",
    cap_radio_write: "deny",
    cap_energy_write: "deny",
    cap_dashboard_resources: "deny",
    cap_backup: "deny",
    cap_filesystem: "deny",
    cap_yaml_edit: "deny",
    cap_esphome_yaml: "deny",
    cap_esphome_flash: "deny",
    cap_camera_read: "deny",
    permissions: { domains: {}, devices: {}, entities: {} },
    presets: [],
    active_preset_id: null,
    ...overrides,
  };
}

describe("formatDate", () => {
  it("returns 'Never' for null", () => {
    expect(formatDate(null)).toBe("Never");
  });

  it("returns a locale date string for a valid ISO string", () => {
    const result = formatDate("2025-01-15T12:00:00Z");
    expect(result).toBeTruthy();
    expect(result).not.toBe("Never");
  });
});

describe("formatDateTime", () => {
  it("returns 'Never' for null", () => {
    expect(formatDateTime(null)).toBe("Never");
  });

  it("returns a locale datetime string for a valid ISO string", () => {
    const result = formatDateTime("2025-01-15T12:30:00Z");
    expect(result).toBeTruthy();
    expect(result).not.toBe("Never");
  });
});

describe("accessKeyStatus", () => {
  it("returns 'Active' for a valid non-revoked access key without expiry", () => {
    expect(accessKeyStatus(makeAccessKey())).toBe("Active");
  });

  it("returns 'Revoked' for a revoked access key", () => {
    expect(accessKeyStatus(makeAccessKey({ revoked: true }))).toBe("Revoked");
  });

  it("returns 'Expired' for an access key with a past expiry date", () => {
    expect(accessKeyStatus(makeAccessKey({ expires_at: "2020-01-01T00:00:00Z" }))).toBe("Expired");
  });

  it("returns 'Active' for an access key with a future expiry date", () => {
    expect(accessKeyStatus(makeAccessKey({ expires_at: "2099-12-31T23:59:59Z" }))).toBe("Active");
  });

  it("returns 'Revoked' even if also expired (revoked takes priority)", () => {
    expect(accessKeyStatus(makeAccessKey({ revoked: true, expires_at: "2020-01-01T00:00:00Z" }))).toBe("Revoked");
  });
});

describe("copyToClipboard", () => {
  it("uses navigator.clipboard.writeText when available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    await copyToClipboard("test-value");
    expect(writeText).toHaveBeenCalledWith("test-value");
  });

  it("falls back to textarea when clipboard API is unavailable", async () => {
    Object.assign(navigator, { clipboard: undefined });
    const execCommand = vi.fn().mockReturnValue(true);
    document.execCommand = execCommand;

    await copyToClipboard("fallback-value");
    expect(execCommand).toHaveBeenCalledWith("copy");
  });

  it("falls back when the Clipboard API rejects", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("insecure context"));
    Object.assign(navigator, { clipboard: { writeText } });
    const execCommand = vi.fn().mockReturnValue(true);
    document.execCommand = execCommand;

    await copyToClipboard("rejected-value");

    expect(writeText).toHaveBeenCalledWith("rejected-value");
    expect(execCommand).toHaveBeenCalledWith("copy");
  });

  it("reports when neither clipboard mechanism is available", async () => {
    Object.assign(navigator, { clipboard: undefined });
    document.execCommand = vi.fn().mockReturnValue(false);

    await expect(copyToClipboard("unavailable-value")).rejects.toThrow("Clipboard unavailable");
  });
});

describe("friendlyToolName", () => {
  it("maps the MESA-approved executor sentinel to a readable label", () => {
    expect(friendlyToolName("call_service_mesa_approved")).toBe("MESA-gated action");
  });

  it("passes a real tool name through unchanged", () => {
    expect(friendlyToolName("create_automation")).toBe("create_automation");
  });
});

describe("friendlyResource", () => {
  it("humanizes an embedded MESA sentinel in an approval resource string", () => {
    expect(friendlyResource("approval:call_service_mesa_approved:appr_abc123"))
      .toBe("approval:MESA-gated action:appr_abc123");
  });

  it("passes a normal approval resource string through unchanged", () => {
    expect(friendlyResource("approval:create_automation:appr_abc123"))
      .toBe("approval:create_automation:appr_abc123");
  });

  it("passes a non-approval resource string through unchanged", () => {
    expect(friendlyResource("light.kitchen")).toBe("light.kitchen");
  });
});

describe("HIGH_RISK_DOMAINS", () => {
  it("contains expected domains", () => {
    expect(HIGH_RISK_DOMAINS.has("homeassistant")).toBe(true);
    expect(HIGH_RISK_DOMAINS.has("backup")).toBe(true);
    expect(HIGH_RISK_DOMAINS.has("mqtt")).toBe(true);
  });

  it("does not contain non-risky domains", () => {
    expect(HIGH_RISK_DOMAINS.has("light")).toBe(false);
    expect(HIGH_RISK_DOMAINS.has("sensor")).toBe(false);
  });
});

it.each([true, false])("restores shadow-root focus after fallback copy (success=%s)", async success => {
  Object.assign(navigator, { clipboard: undefined });
  document.execCommand = vi.fn().mockReturnValue(success);
  const host = document.createElement("div");
  document.body.append(host);
  const shadow = host.attachShadow({ mode: "open" });
  const button = document.createElement("button");
  shadow.append(button);
  button.focus();
  if (success) await copyToClipboard("text");
  else await expect(copyToClipboard("text")).rejects.toThrow("Clipboard unavailable");
  expect(shadow.activeElement).toBe(button);
  host.remove();
});
