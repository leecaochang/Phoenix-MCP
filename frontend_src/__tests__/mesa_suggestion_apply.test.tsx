/**
 * MESA findings explain validation limits, in the Advisory and Orphaned
 * Profiles windows that show only while something is undismissed;
 * suggestions preserve concurrent edits; every scope names its target.
 */
import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { api, ApiError, setHass } from "../api";
import type { MesaFindings } from "../shell/mesa";
import { EMPTY_ISSUES as empty, advisoryWindow, diagnosticsWindow, mockEmptyMesa, orphansWindow, profilesWindow, renderMesa, rowsIn, suggestedWindow } from "./mesa_harness";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  const names = [
    "listMesaProfiles", "getMesaIssues", "listMesaDomains", "listMesaIntegrations", "listMesaAreas", "listMesaDevices",
    "getEntityTree", "getMesaVocabulary", "getMesaIntegrationOptions", "getMesaDeviceOptions",
    "putMesaDomain", "putMesaProfile", "getMesaProfile", "clearMesaOrphans", "dismissMesaSuggestion", "restoreMesaSuggestions",
  ];
  return { ...actual, api: Object.fromEntries(names.map((n) => [n, vi.fn()])) };
});

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  setHass({ user: { id: "test-admin" } });
  mockEmptyMesa();
});

const refresh = () => fireEvent.click(profilesWindow().getByRole("button", { name: "Refresh MESA" }));
const ADVISORY = "This is advisory only; no action is required.";

it.each(["entity", "domain"] as const)("does not replace a %s profile created after the suggestion loaded", async (scope) => {
  const subject = scope === "entity" ? "lock.front" : "lock";
  vi.mocked(api.getMesaIssues).mockResolvedValueOnce({ ...empty, suggestions: [{ key: "suggestion", signal: "naked_risky", scope, subject_id: subject, suggested_mode: "prohibited", reason: "Physical boundary", evidence: {} }] }).mockResolvedValue(empty);
  const put = scope === "entity" ? api.putMesaProfile : api.putMesaDomain;
  vi.mocked(put).mockRejectedValue(new ApiError(412, "already_exists", "A profile already exists."));
  renderMesa();
  await waitFor(() => expect(rowsIn("Suggested Profiles")).toHaveLength(1));
  fireEvent.click(rowsIn("Suggested Profiles")[0]);
  fireEvent.click(suggestedWindow().getByRole("button", { name: "Apply" }));
  await waitFor(() => expect(put).toHaveBeenCalledExactlyOnceWith(subject, expect.any(Object), true));
  expect(await suggestedWindow().findByText("A profile already exists.")).toBeVisible();
  expect(api.getMesaIssues).toHaveBeenCalledTimes(2);
  expect(rowsIn("Suggested Profiles")).toHaveLength(0);
  expect(suggestedWindow().getByRole("button", { name: "Apply" })).toBeDisabled();
});

it("combines template inspection notices and leaves automation details collapsed", async () => {
  const recommendation = "templates or blueprint inputs require host expansion; reference coverage is incomplete";
  const issues = Array.from({ length: 23 }, (_, index) => ({
    entity_id: "", declared_value: "unknown", automation_id: `template_automation_${index}`,
    role: "configuration", severity: "warning", recommendation,
  }));
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, issues: [...issues, issues[0]], orphans: ["sensor.retained_profile"] });
  const { container } = renderMesa();
  const advisory = advisoryWindow();
  const summary = await advisory.findByText("23 automations could only be partially inspected because they contain templates or use blueprints.");
  expect(summary.closest(".banner")).toHaveClass("banner-info");
  expect(advisory.getAllByText(ADVISORY)).toHaveLength(1);
  expect(orphansWindow().getAllByText(ADVISORY)).toHaveLength(1);
  expect(advisory.queryByText(recommendation)).toBeNull();
  expect(advisory.queryByText("Warning:")).toBeNull();
  const details = advisory.getByText("View Automations").closest("details")!;
  expect(details).not.toHaveAttribute("open");
  expect(within(details).getAllByRole("listitem", { hidden: true })).toHaveLength(23);
  fireEvent.click(advisory.getByText("View Automations"));
  expect(details).toHaveAttribute("open");
  expect(advisory.getByText("template_automation_0")).toBeVisible();
  expect(container.querySelectorAll("code:empty")).toHaveLength(0);
  expect(orphansWindow().getByText(/sensor.retained_profile/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /Clear all orphaned profiles/ })).toBeNull();
  expect(advisory.getByRole("button", { name: "Dismiss inspection notice permanently" })).toBeVisible();
  expect(orphansWindow().getByRole("button", { name: "Dismiss orphaned-profile notice permanently" })).toBeVisible();
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
  const { container } = renderMesa();
  const advisory = advisoryWindow();
  const error = await advisory.findByText("automation configuration cannot be inspected (MesaValidationError)");
  expect(within(error.closest("li")!).getByText("Error:")).toBeVisible();
  const row = advisory.getByText(conflict).closest("li")!;
  expect(row).toHaveTextContent("input_boolean.mode declared none but appears in automation mode_changed (trigger)");
  expect(within(row).getByText("Error:")).toBeVisible();
  expect(container.querySelector("img")).toBeNull();
  expect(advisory.queryByText(ADVISORY)).toBeNull();
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
  renderMesa();
  const advisory = advisoryWindow();
  expect(await advisory.findByText("MESA Validation Findings: 3")).toBeVisible();
  expect(advisory.getAllByText(knownReason)).toHaveLength(2);
  expect(advisory.getAllByText("Warning:")).toHaveLength(2);
  expect(advisory.getByText("Error:")).toBeVisible();
  expect(advisory.queryByText("View Automations")).toBeNull();
  expect(advisory.queryByText(ADVISORY)).toBeNull();
});

it.each(["orphans", "orphan_devices", "orphan_areas", "orphan_integrations"] as const)("treats %s as optional advisory cleanup", async (key) => {
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, [key]: ["retained_profile"] });
  renderMesa();
  const profile = await orphansWindow().findByText(/retained_profile/);
  const notice = profile.closest<HTMLElement>(".banner")!;
  expect(notice).toHaveClass("banner-info");
  expect(within(notice).getByText(ADVISORY)).toBeVisible();
  expect(api.clearMesaOrphans).not.toHaveBeenCalled();
});

it("shows Orphaned Profiles and Advisory only while they hold undismissed findings", async () => {
  const findings: Array<MesaFindings | null> = [];
  const inspection = { entity_id: "", declared_value: "unknown", automation_id: "templated", role: "configuration", severity: "warning", recommendation: "templates or blueprint inputs require host expansion; reference coverage is incomplete" };
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, issues: [inspection], orphans: ["sensor.kept"] });
  renderMesa({ onFindings: (f) => findings.push(f) });
  await waitFor(() => expect(findings[findings.length - 1]).toEqual({ orphans: true, advisory: true }));
  expect(findings[0]).toBeNull();
  fireEvent.click(orphansWindow().getByRole("button", { name: "Dismiss orphaned-profile notice permanently" }));
  await waitFor(() => expect(findings[findings.length - 1]).toEqual({ orphans: false, advisory: true }));
  fireEvent.click(advisoryWindow().getByRole("button", { name: "Dismiss inspection notice permanently" }));
  await waitFor(() => expect(findings[findings.length - 1]).toEqual({ orphans: false, advisory: false }));
  // Diagnostics still lists every finding, dismissed or not, without dismissing anything.
  expect(diagnosticsWindow().getByText(/sensor.kept/)).toBeVisible();
  expect(diagnosticsWindow().getByText("View Automations")).toBeInTheDocument();
  expect(diagnosticsWindow().queryByRole("button", { name: /Dismiss/ })).toBeNull();
});

it("opens an orphaned profile in Profile Editor so it can be reviewed and removed", async () => {
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphan_devices: ["gone-device"] });
  vi.mocked(api.getMesaProfile).mockResolvedValue({ stored: null } as never);
  renderMesa();
  fireEvent.click(await orphansWindow().findByRole("button", { name: "gone-device" }));
  expect(await screen.findByRole("region", { name: "Profile Editor window" })).toHaveTextContent("gone-device");
});

it("permanently dismisses orphan findings across refresh, remount, subsets and reordering without deleting profiles", async () => {
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphans: ["sensor.kept_a", "sensor.kept_b"] });
  const first = renderMesa();
  fireEvent.click(await orphansWindow().findByRole("button", { name: "Dismiss orphaned-profile notice permanently" }));
  expect(orphansWindow().queryByText(/sensor.kept_a/)).toBeNull();
  refresh();
  await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalledTimes(2));
  expect(orphansWindow().queryByText(/sensor.kept_a/)).toBeNull();
  first.unmount();
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphans: ["sensor.kept_b", "sensor.kept_a"] });
  const second = renderMesa();
  await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalledTimes(3));
  expect(orphansWindow().queryByRole("button", { name: "Dismiss orphaned-profile notice permanently" })).toBeNull();
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphans: ["sensor.kept_b"] });
  refresh();
  await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalledTimes(4));
  expect(orphansWindow().queryByText(/sensor.kept_b/)).toBeNull();
  second.unmount();
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphans: ["sensor.new_finding"] });
  renderMesa();
  expect(await orphansWindow().findByText(/sensor.new_finding/)).toBeVisible();
  expect(api.clearMesaOrphans).not.toHaveBeenCalled();
  expect(api.putMesaProfile).not.toHaveBeenCalled();
});

it("keeps dismissals scoped to the current authenticated account", async () => {
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphans: ["sensor.retained"] });
  const first = renderMesa();
  fireEvent.click(await orphansWindow().findByRole("button", { name: "Dismiss orphaned-profile notice permanently" }));
  first.unmount();
  setHass({ user: { id: "another-admin" } });
  const second = renderMesa();
  expect(await orphansWindow().findByText(/sensor.retained/)).toBeVisible();
  second.unmount();
  setHass({ user: { id: "test-admin" } });
  renderMesa();
  await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalledTimes(3));
  expect(orphansWindow().queryByText(/sensor.retained/)).toBeNull();
});

it("dismisses inspection and validation notices independently and resurfaces changed validation", async () => {
  const inspection = { entity_id: "", declared_value: "unknown", automation_id: "templated", role: "configuration", severity: "warning", recommendation: "templates or blueprint inputs require host expansion; reference coverage is incomplete" };
  const validation = { ...inspection, automation_id: "conflict", recommendation: "Review this finding." };
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, issues: [inspection, validation] });
  const first = renderMesa();
  fireEvent.click(await advisoryWindow().findByRole("button", { name: "Dismiss inspection notice permanently" }));
  expect(advisoryWindow().queryByText("View Automations")).toBeNull();
  expect(advisoryWindow().getByText(validation.recommendation)).toBeVisible();
  fireEvent.click(advisoryWindow().getByRole("button", { name: "Dismiss validation notice permanently" }));
  first.unmount();
  const second = renderMesa();
  await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalledTimes(2));
  expect(advisoryWindow().queryByText(validation.recommendation)).toBeNull();
  expect(advisoryWindow().queryByText("View Automations")).toBeNull();
  second.unmount();
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, issues: [{ ...validation, severity: "error" }] });
  renderMesa();
  expect(await advisoryWindow().findByText(validation.recommendation)).toBeVisible();
  expect(advisoryWindow().getByText("Error:")).toBeVisible();
});

it("still allows dismissal when browser storage is unavailable or malformed", async () => {
  vi.mocked(api.getMesaIssues).mockResolvedValue({ ...empty, orphans: ["sensor.retained"] });
  localStorage.setItem("phx-mesa-dismissed:test-admin", "null");
  const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Unavailable"); });
  try {
    renderMesa();
    fireEvent.click(await orphansWindow().findByRole("button", { name: "Dismiss orphaned-profile notice permanently" }));
    expect(orphansWindow().queryByText(/sensor.retained/)).toBeNull();
  } finally { write.mockRestore(); }
});

it("names every profile scope and suggestion by its familiar name, with the exact ID", async () => {
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
  renderMesa();
  await waitFor(() => expect(rowsIn("Profiles").map((r) => r.querySelector(".mesa-profile-name")?.textContent)).toEqual(
    expect.arrayContaining(["Office fan", "Office device", "Office", "Test integration", "fan"]),
  ));
  for (const [name, id] of [["Office fan", "fan.office"], ["Office device", "device-office"], ["Office", "office"], ["Test integration", "test_integration"]]) {
    const row = rowsIn("Profiles").find((r) => r.querySelector(".mesa-profile-name")?.textContent === name)!;
    expect(within(row).getByText(id).tagName).toBe("CODE");
    expect(row).toHaveTextContent("Confirm");
  }
  // A domain's key is its name, so it is not repeated.
  const domain = rowsIn("Profiles").find((r) => r.querySelector(".mesa-profile-name")?.textContent === "fan")!;
  expect(domain.querySelector("code")).toBeNull();
  const suggestion = rowsIn("Suggested Profiles")[0];
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
  const { container } = renderMesa();
  const advisory = advisoryWindow();
  await advisory.findByText("View Automations");
  fireEvent.click(advisory.getByText("View Automations"));
  const details = advisory.getByText("View Automations").closest("details")!;
  expect(within(details).getByText(hostileName).tagName).toBe("STRONG");
  expect(within(details).getByText("1740000000000").tagName).toBe("CODE");
  const row = advisory.getByText("Review declaration.").closest("li")!;
  expect(within(row).getByText("Office fan").tagName).toBe("STRONG");
  expect(within(row).getByText(hostileName).tagName).toBe("STRONG");
  expect(within(row).getByText("fan.office")).toHaveClass("mesa-identity-secondary");
  expect(advisory.getByText("missing_automation")).not.toHaveClass("mesa-identity-secondary");
  expect(container.querySelector("img")).toBeNull();
});
