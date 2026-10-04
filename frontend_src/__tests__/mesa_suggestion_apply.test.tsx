/** MESA findings explain validation limits; suggestions preserve concurrent edits. */
import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api, ApiError, setHass } from "../api";
import { MesaView } from "../views/MesaView";
import type { MesaIssuesResponse } from "../types";

vi.mock("../api", async importOriginal => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: {
    listMesaProfiles: vi.fn(), getMesaIssues: vi.fn(), listMesaDomains: vi.fn(),
    listMesaIntegrations: vi.fn(), listMesaAreas: vi.fn(), listMesaDevices: vi.fn(),
    getEntityTree: vi.fn(), getMesaVocabulary: vi.fn(), getMesaIntegrationOptions: vi.fn(),
    getMesaDeviceOptions: vi.fn(), putMesaDomain: vi.fn(), putMesaProfile: vi.fn(),
    clearMesaOrphans: vi.fn(),
  } };
});
const empty: MesaIssuesResponse = { issues: [], orphans: [], orphan_devices: [], orphan_areas: [], orphan_integrations: [], suggestions: [], dismissed_suggestions: [] };
beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  setHass({ user: { id: "test-admin" } });
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

it("combines template inspection notices and leaves automation details collapsed", async () => {
  const recommendation = "templates or blueprint inputs require host expansion; reference coverage is incomplete";
  const issues = Array.from({ length: 23 }, (_, index) => ({
    entity_id: "", declared_value: "unknown", automation_id: `template_automation_${index}`,
    role: "configuration", severity: "warning", recommendation,
  }));
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, issues: [...issues, issues[0]], orphans: ["sensor.retained_profile"] });
  const { container } = render(<MesaView />);
  const summary = await screen.findByText("23 automations could only be partially inspected because they contain templates or use blueprints.");
  expect(summary.closest(".banner")).toHaveClass("banner-info");
  expect(screen.getAllByText("This is advisory only; no action is required.")).toHaveLength(2);
  expect(screen.queryByText(recommendation)).toBeNull();
  expect(screen.queryByText("Warning:")).toBeNull();
  const details = screen.getByText("View automations").closest("details")!;
  expect(details).not.toHaveAttribute("open");
  expect(within(details).getAllByRole("listitem", { hidden: true })).toHaveLength(23);
  fireEvent.click(screen.getByText("View automations"));
  expect(details).toHaveAttribute("open");
  expect(screen.getByText("template_automation_0")).toBeVisible();
  expect(container.querySelectorAll("code:empty")).toHaveLength(0);
  expect(screen.getByText(/sensor.retained_profile/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /Clear all orphaned profiles/ })).toBeNull();
  expect(screen.getByRole("button", { name: "Dismiss inspection notice permanently" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Dismiss orphaned-profile notice permanently" })).toBeVisible();
  expect(api.clearMesaOrphans).not.toHaveBeenCalled();
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
  expect(screen.queryByText("This is advisory only; no action is required.")).toBeNull();
});

it("does not downgrade unknown configuration warnings or inspection errors", async () => {
  const knownReason = "templates or blueprint inputs require host expansion; reference coverage is incomplete";
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, issues: [{
    entity_id: "", declared_value: "unknown", automation_id: "failed_inspection",
    role: "configuration", severity: "error", recommendation: knownReason,
  }, {
    entity_id: "", declared_value: "unknown", automation_id: "future_warning",
    role: "configuration", severity: "warning", recommendation: "A new configuration finding.",
  }, {
    entity_id: "input_boolean.mode", declared_value: "none", automation_id: "mode_condition",
    role: "condition", severity: "warning", recommendation: knownReason,
  }] });
  render(<MesaView />);
  expect(await screen.findByText("MESA validation findings: 3")).toBeVisible();
  expect(screen.getAllByText(knownReason)).toHaveLength(2);
  expect(screen.getAllByText("Warning:")).toHaveLength(2);
  expect(screen.getByText("Error:")).toBeVisible();
  expect(screen.queryByText("View automations")).toBeNull();
  expect(screen.queryByText("This is advisory only; no action is required.")).toBeNull();
});

it.each(["orphans", "orphan_devices", "orphan_areas", "orphan_integrations"] as const)("treats %s as optional advisory cleanup", async key => {
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, [key]: ["retained_profile"] });
  render(<MesaView />);
  const profile = await screen.findByText(/retained_profile/);
  const notice = profile.closest<HTMLElement>(".banner")!;
  expect(notice).toHaveClass("banner-info");
  expect(within(notice).getByText("This is advisory only; no action is required.")).toBeVisible();
  expect(api.clearMesaOrphans).not.toHaveBeenCalled();
});

it("permanently dismisses orphan findings across refresh, remount, subsets and reordering without deleting profiles", async () => {
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphans: ["sensor.kept_a", "sensor.kept_b"] });
  const first = render(<MesaView />);
  fireEvent.click(await screen.findByRole("button", { name: "Dismiss orphaned-profile notice permanently" }));
  expect(screen.queryByText(/sensor.kept_a/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalledTimes(2));
  expect(screen.queryByText(/sensor.kept_a/)).toBeNull();
  first.unmount();
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphans: ["sensor.kept_b", "sensor.kept_a"] });
  const second = render(<MesaView />);
  await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalledTimes(3));
  expect(screen.queryByRole("button", { name: "Dismiss orphaned-profile notice permanently" })).toBeNull();
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphans: ["sensor.kept_b"] });
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalledTimes(4));
  expect(screen.queryByText(/sensor.kept_b/)).toBeNull();
  second.unmount();
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphans: ["sensor.new_finding"] });
  render(<MesaView />);
  expect(await screen.findByText(/sensor.new_finding/)).toBeVisible();
  expect(api.clearMesaOrphans).not.toHaveBeenCalled();
  expect(api.putMesaProfile).not.toHaveBeenCalled();
});

it("keeps dismissals scoped to the current authenticated account", async () => {
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphans: ["sensor.retained"] });
  const first = render(<MesaView />);
  fireEvent.click(await screen.findByRole("button", { name: "Dismiss orphaned-profile notice permanently" }));
  first.unmount();
  setHass({ user: { id: "another-admin" } });
  const second = render(<MesaView />);
  expect(await screen.findByText(/sensor.retained/)).toBeVisible();
  second.unmount();
  setHass({ user: { id: "test-admin" } });
  render(<MesaView />);
  await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalledTimes(3));
  expect(screen.queryByText(/sensor.retained/)).toBeNull();
});

it("dismisses inspection and validation cards independently and resurfaces changed validation", async () => {
  const inspection = { entity_id: "", declared_value: "unknown", automation_id: "templated", role: "configuration", severity: "warning", recommendation: "templates or blueprint inputs require host expansion; reference coverage is incomplete" };
  const validation = { ...inspection, automation_id: "conflict", recommendation: "Review this finding." };
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, issues: [inspection, validation] });
  const first = render(<MesaView />);
  fireEvent.click(await screen.findByRole("button", { name: "Dismiss inspection notice permanently" }));
  expect(screen.queryByText("View automations")).toBeNull();
  expect(screen.getByText(validation.recommendation)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Dismiss validation notice permanently" }));
  first.unmount();
  const second = render(<MesaView />);
  await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalledTimes(2));
  expect(screen.queryByText(validation.recommendation)).toBeNull();
  expect(screen.queryByText("View automations")).toBeNull();
  second.unmount();
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, issues: [{ ...validation, severity: "error" }] });
  render(<MesaView />);
  expect(await screen.findByText(validation.recommendation)).toBeVisible();
  expect(screen.getByText("Error:")).toBeVisible();
});

it("still allows dismissal when browser storage is unavailable or malformed", async () => {
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphans: ["sensor.retained"] });
  localStorage.setItem("phx-mesa-dismissed:test-admin", "null");
  const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Unavailable"); });
  try {
    render(<MesaView />);
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss orphaned-profile notice permanently" }));
    expect(screen.queryByText(/sensor.retained/)).toBeNull();
  } finally { write.mockRestore(); }
});

it("uses bold friendly names with IDs underneath for every profile scope and suggestions", async () => {
  const document = { semantic_profile: { operational_boundaries: { control_mode: "confirm" } } };
  const entity = { entity_id: "fan.office", friendly_name: "Office fan", device_id: "device-office", area_id: "office", area_name: "Office", labels: [] };
  vi.mocked(api.getEntityTree).mockResolvedValue({ fan: { devices: {}, deviceless_entities: [entity.entity_id], entity_details: { [entity.entity_id]: entity } } });
  vi.mocked(api.listMesaProfiles).mockResolvedValue({ profiles: [{ entity_id: "fan.office", document }], total_matched: 1, has_more: false, next_cursor: null });
  vi.mocked(api.listMesaDevices).mockResolvedValue({ devices: [{ device_id: "device-office", document }] });
  vi.mocked(api.listMesaAreas).mockResolvedValue({ areas: [{ area_id: "office", document }] });
  vi.mocked(api.listMesaIntegrations).mockResolvedValue({ integrations: [{ integration: "test_integration", document }] });
  vi.mocked(api.listMesaDomains).mockResolvedValue({ domains: [{ domain: "fan", document }] });
  vi.mocked(api.getMesaDeviceOptions).mockResolvedValue({ devices: [{ id: "device-office", name: "Office device" }] });
  vi.mocked(api.getMesaIntegrationOptions).mockResolvedValue({ integrations: [{ id: "test_integration", name: "Test integration" }] });
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, suggestions: [{ key: "fan-suggestion", signal: "naked_risky", scope: "entity", subject_id: "fan.office", suggested_mode: "confirm", reason: "Review this entity.", evidence: {} }] });
  render(<MesaView />);
  await screen.findByRole("button", { name: "Test integration test_integration" });
  for (const [name, id] of [["Office fan", "fan.office"], ["Office device", "device-office"], ["Office", "office"], ["Test integration", "test_integration"]]) {
    const button = screen.getByRole("button", { name: `${name} ${id}` });
    const identity = button.querySelector(".mesa-identity")!;
    expect(identity.children[0].tagName).toBe("STRONG");
    expect(identity.children[0]).toHaveTextContent(name);
    expect(identity.children[1].tagName).toBe("CODE");
    expect(identity.children[1]).toHaveTextContent(id);
    expect(button).toHaveClass("row-link-btn-stack");
  }
  const suggestion = screen.getByText("Review this entity.").closest<HTMLElement>(".mesa-suggest-row")!;
  expect(within(suggestion).getByText("Office fan").tagName).toBe("STRONG");
  expect(within(suggestion).getByText("fan.office").tagName).toBe("CODE");
});

it("resolves automation configuration IDs and renders friendly names in advisory details and validation", async () => {
  const hostileName = "Night fan <img src=x onerror=alert(1)>";
  setHass({ user: { id: "test-admin" }, states: {
    "automation.night_fan": { attributes: { id: "1740000000000", friendly_name: hostileName } },
    "fan.office": { attributes: { friendly_name: "Office fan" } },
  } });
  const inspection = { entity_id: "", declared_value: "unknown", automation_id: "1740000000000", role: "configuration", severity: "warning", recommendation: "templates or blueprint inputs require host expansion; reference coverage is incomplete" };
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, issues: [inspection, {
    entity_id: "fan.office", declared_value: "none", automation_id: "1740000000000", role: "trigger", severity: "error", recommendation: "Review declaration.",
  }, { ...inspection, automation_id: "missing_automation", recommendation: "Unknown finding." }] });
  const { container } = render(<MesaView />);
  await screen.findByText("View automations");
  fireEvent.click(screen.getByText("View automations"));
  const details = screen.getByText("View automations").closest("details")!;
  expect(within(details).getByText(hostileName).tagName).toBe("STRONG");
  expect(within(details).getByText("1740000000000").tagName).toBe("CODE");
  const row = screen.getByText("Review declaration.").closest("li")!;
  expect(within(row).getByText("Office fan").tagName).toBe("STRONG");
  expect(within(row).getByText(hostileName).tagName).toBe("STRONG");
  expect(within(row).getByText("fan.office")).toHaveClass("mesa-identity-secondary");
  expect(screen.getByText("missing_automation")).not.toHaveClass("mesa-identity-secondary");
  expect(container.querySelector("img")).toBeNull();
});
