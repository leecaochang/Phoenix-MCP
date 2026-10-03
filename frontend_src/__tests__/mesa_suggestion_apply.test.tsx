/** MESA findings explain validation limits; suggestions preserve concurrent edits. */
import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api, ApiError } from "../api";
import { MesaView } from "../views/MesaView";
import type { MesaIssuesResponse } from "../types";

vi.mock("../api", async importOriginal => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: {
    listMesaProfiles: vi.fn(), getMesaIssues: vi.fn(), listMesaDomains: vi.fn(),
    listMesaIntegrations: vi.fn(), listMesaAreas: vi.fn(), listMesaDevices: vi.fn(),
    getEntityTree: vi.fn(), getMesaVocabulary: vi.fn(), getMesaIntegrationOptions: vi.fn(),
    getMesaDeviceOptions: vi.fn(), putMesaDomain: vi.fn(), putMesaProfile: vi.fn(),
  } };
});
const empty: MesaIssuesResponse = { issues: [], orphans: [], orphan_devices: [], orphan_areas: [], orphan_integrations: [], suggestions: [], dismissed_suggestions: [] };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listMesaProfiles).mockResolvedValue({ profiles: [], total_matched: 0, has_more: false, next_cursor: null });
  vi.mocked(api.listMesaDomains).mockResolvedValue({ domains: [] });
  vi.mocked(api.listMesaIntegrations).mockResolvedValue({ integrations: [] });
  vi.mocked(api.listMesaAreas).mockResolvedValue({ areas: [] });
  vi.mocked(api.listMesaDevices).mockResolvedValue({ devices: [] });
  vi.mocked(api.getEntityTree).mockResolvedValue({});
  vi.mocked(api.getMesaVocabulary).mockResolvedValue({ canonical_tags: [], canonical_roots: [] });
  vi.mocked(api.getMesaIntegrationOptions).mockResolvedValue({ integrations: [] });
  vi.mocked(api.getMesaDeviceOptions).mockResolvedValue({ devices: [] });
});

it.each(["entity", "domain"] as const)("does not replace a %s profile created after the suggestion loaded", async scope => {
  const subject = scope === "entity" ? "lock.front" : "lock";
  vi.mocked(api.getMesaIssues).mockResolvedValueOnce({ ...empty, suggestions: [{ key: "suggestion", signal: "naked_risky", scope, subject_id: subject, suggested_mode: "prohibited", reason: "Physical boundary", evidence: {} }] }).mockResolvedValue(empty);
  const put = scope === "entity" ? api.putMesaProfile : api.putMesaDomain;
  vi.mocked(put).mockRejectedValue(new ApiError(412, "already_exists", "A profile already exists."));
  render(<MesaView />);
  fireEvent.click(await screen.findByRole("button", { name: "Apply" }));
  await waitFor(() => expect(put).toHaveBeenCalledExactlyOnceWith(subject, expect.any(Object), true));
  expect(await screen.findByText("A profile already exists.")).toBeVisible();
  expect(api.getMesaIssues).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
});

it("shows template inspection warnings with their explanation instead of a profile conflict", async () => {
  const recommendation = "templates or blueprint inputs require host expansion; reference coverage is incomplete";
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, issues: [{
    entity_id: "", declared_value: "unknown", automation_id: "template_automation",
    role: "configuration", severity: "warning", recommendation,
  }], orphans: ["sensor.retained_profile"] });
  const { container } = render(<MesaView />);
  expect(await screen.findByText(recommendation)).toBeVisible();
  const row = screen.getByText(recommendation).closest("li")!;
  expect(within(row).getByText("Warning:")).toBeVisible();
  expect(row).toHaveTextContent("Automation template_automation");
  expect(row).not.toHaveTextContent("declared unknown");
  expect(row).not.toHaveTextContent("(configuration)");
  expect(container.querySelectorAll("code:empty")).toHaveLength(0);
  expect(screen.getByText(/sensor.retained_profile/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Clear all orphaned profiles" })).toBeVisible();
});

it("distinguishes inspection errors and keeps entity conflicts and recommendations visible as text", async () => {
  const conflict = "Change the declaration to 'likely'. <img src=x onerror=alert(1)>";
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, issues: [{
    entity_id: "", declared_value: "unknown", automation_id: "uninspectable",
    role: "configuration", severity: "error", recommendation: "automation configuration cannot be inspected (MesaValidationError)",
  }, {
    entity_id: "input_boolean.mode", declared_value: "none", automation_id: "mode_changed",
    role: "trigger", severity: "error", recommendation: conflict,
  }] });
  const { container } = render(<MesaView />);
  const error = await screen.findByText("automation configuration cannot be inspected (MesaValidationError)");
  expect(within(error.closest("li")!).getByText("Error:")).toBeVisible();
  const row = screen.getByText(conflict).closest("li")!;
  expect(row).toHaveTextContent("input_boolean.mode declared none but appears in automation mode_changed (trigger)");
  expect(within(row).getByText("Error:")).toBeVisible();
  expect(container.querySelector("img")).toBeNull();
});
