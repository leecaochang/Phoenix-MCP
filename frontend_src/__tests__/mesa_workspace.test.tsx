/**
 * The MESA workspace's Profiles and Profile Editor: one list for all five
 * scopes with type, control and enforcement filters; a chosen profile opens
 * in the editor window; switching away from unsaved changes asks first;
 * Save shows the saved profile; Discard reverts; Delete confirms in place,
 * naming its target, in the window and in the injected modal alike.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api, setHass } from "../api";
import { windowAvailable } from "../shell/workspaces";
import { ProfileEditor } from "../views/MesaView";
import { advisoryWindow, editorWindow, mockEmptyMesa, profilesWindow, renderMesa, rowsIn, suggestedWindow } from "./mesa_harness";
import type { MesaIssuesResponse } from "../types";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  const names = [
    "listMesaProfiles", "getMesaIssues", "listMesaDomains", "listMesaIntegrations", "listMesaAreas", "listMesaDevices",
    "getEntityTree", "getMesaVocabulary", "getMesaIntegrationOptions", "getMesaDeviceOptions",
    "getMesaProfile", "getMesaDomain", "getMesaDevice", "putMesaProfile", "putMesaDomain", "deleteMesaProfile", "deleteMesaDomain",
  ];
  return { ...actual, api: Object.fromEntries(names.map((n) => [n, vi.fn()])) };
});

const doc = (mode: string, extra: Record<string, unknown> = {}) => ({ semantic_profile: { semantic_tags: ["security.entry"], operational_boundaries: { control_mode: mode, ...extra } } });
const detail = (mode: string) => ({ stored: doc(mode), effective: doc(mode), explanation: { explanation: [], warnings: [] } });

beforeEach(() => {
  vi.resetAllMocks();
  setHass({ user: { id: "test-admin" } });
  mockEmptyMesa();
  vi.mocked(api.getEntityTree).mockResolvedValue({
    lock: { devices: {}, deviceless_entities: ["lock.front"], entity_details: { "lock.front": { entity_id: "lock.front", friendly_name: "Front door", device_id: null, area_id: null, area_name: null, labels: [] } } },
    light: { devices: {}, deviceless_entities: ["light.desk"], entity_details: { "light.desk": { entity_id: "light.desk", friendly_name: "Desk lamp", device_id: null, area_id: null, area_name: null, labels: [] } } },
  });
  vi.mocked(api.listMesaProfiles).mockResolvedValue({ profiles: [
    { entity_id: "lock.front", document: doc("prohibited", { enforcement_mode: "enforced" }) },
    { entity_id: "light.desk", document: doc("autonomous") },
  ], total_matched: 2, has_more: false, next_cursor: null });
  vi.mocked(api.listMesaDevices).mockResolvedValue({ devices: [{ device_id: "dev-hall", document: doc("confirm") }] });
  vi.mocked(api.getMesaDeviceOptions).mockResolvedValue({ devices: [{ id: "dev-hall", name: "Hall hub" }] });
  vi.mocked(api.getMesaProfile).mockImplementation(async (key: string) => detail(key === "lock.front" ? "prohibited" : "autonomous") as never);
  vi.mocked(api.getMesaDevice).mockResolvedValue({ stored: doc("confirm") } as never);
});

/** The Name column of each row: ID, Name, Control Mode, Type, Actions (user, 2026-10-30). */
const nameCell = (r: HTMLElement) => r.querySelectorAll("td")[1]?.textContent ?? "";
const names = () => rowsIn("Profiles").map(nameCell);
const choose = (name: string) => fireEvent.click(rowsIn("Profiles").find((r) => nameCell(r) === name)!.querySelectorAll("td")[1]!);

describe("Profiles", () => {
  it("lists every scope together, filters by type, control mode (with counts) and enforcement, and searches", async () => {
    renderMesa();
    await waitFor(() => expect(names().sort()).toEqual(["Desk lamp", "Front door", "Hall hub"]));
    const win = profilesWindow();
    expect(win.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["ID", "Name", "Control Mode", "Type", "Actions"]);
    expect(win.queryByRole("columnheader", { name: /Tags/ })).toBeNull();

    fireEvent.change(win.getByRole("combobox", { name: "Type" }), { target: { value: "device" } });
    expect(names()).toEqual(["Hall hub"]);
    fireEvent.change(win.getByRole("combobox", { name: "Type" }), { target: { value: "" } });

    const control = win.getByRole("combobox", { name: "Control Mode" });
    expect([...control.querySelectorAll("option")].map((o) => o.textContent)).toContain("Prohibited (1)");
    fireEvent.change(control, { target: { value: "prohibited" } });
    expect(names()).toEqual(["Front door"]);
    fireEvent.change(control, { target: { value: "" } });

    fireEvent.change(win.getByRole("combobox", { name: "Enforcement" }), { target: { value: "advisory" } });
    expect(names().sort()).toEqual(["Desk lamp", "Hall hub"]);
    fireEvent.click(win.getByRole("button", { name: "Clear Filters" }));

    fireEvent.change(win.getByRole("searchbox", { name: "Search profiles" }), { target: { value: "hub" } });
    expect(names()).toEqual(["Hall hub"]);
    fireEvent.change(win.getByRole("searchbox", { name: "Search profiles" }), { target: { value: "lock.front" } });
    expect(names()).toEqual(["Front door"]);
    expect(rowsIn("Profiles")[0]).toHaveTextContent("Prohibited · Enforced");
  });

  it("opens a chosen profile in Profile Editor, named with its scope and exact ID", async () => {
    renderMesa();
    expect(await editorWindow().findByText("Select a profile to edit it, or add one.")).toBeTruthy();
    await waitFor(() => expect(names()).toContain("Hall hub"));
    choose("Hall hub");
    const heading = await editorWindow().findByRole("heading", { level: 3 });
    // The line names the scope and the exact id; the familiar name is the field below it.
    expect(heading).toHaveTextContent("Device");
    expect(within(heading).getByText("dev-hall").tagName).toBe("CODE");
    expect(heading).not.toHaveTextContent("Hall hub");
    expect(editorWindow().getByDisplayValue("Hall hub")).toBeDisabled();
    expect(api.getMesaDevice).toHaveBeenCalledWith("dev-hall");
    await waitFor(() => expect(editorWindow().getByLabelText(/Control mode/)).toHaveValue("confirm"));
    expect(rowsIn("Profiles").find((r) => r.getAttribute("aria-selected") === "true")).toHaveTextContent("Hall hub");
  });

  it("empties Profile Editor when the list is told to show other profiles", async () => {
    renderMesa();
    await waitFor(() => expect(names()).toContain("Front door"));
    choose("Front door");
    await waitFor(() => expect(editorWindow().getByLabelText(/Control mode/)).toHaveValue("prohibited"));
    // Showing other profiles leaves nothing chosen in the list (user, 2026-10-29).
    fireEvent.change(profilesWindow().getByRole("combobox", { name: "Type" }), { target: { value: "device" } });
    expect(await editorWindow().findByText("Select a profile to edit it, or add one.")).toBeTruthy();
  });

  it("opens another profile straight away, unsaved changes and all (user, 2026-10-30)", async () => {
    renderMesa();
    await waitFor(() => expect(names()).toContain("Front door"));
    choose("Front door");
    await waitFor(() => expect(editorWindow().getByLabelText(/Control mode/)).toHaveValue("prohibited"));
    fireEvent.change(editorWindow().getByLabelText(/Control mode/), { target: { value: "confirm" } });
    // Choosing the profile already open keeps the form as it is.
    choose("Front door");
    expect(editorWindow().getByLabelText(/Control mode/)).toHaveValue("confirm");

    // Another profile loads at once: nothing asks about the changes, and nothing was saved.
    choose("Desk lamp");
    await waitFor(() => expect(editorWindow().getByRole("heading", { level: 3 })).toHaveTextContent("light.desk"));
    expect(editorWindow().queryByRole("group", { name: "Discard changes?" })).toBeNull();
    expect(api.putMesaProfile).not.toHaveBeenCalled();
  });

  it("Discard Changes returns the form to the saved profile, and is unavailable while nothing changed", async () => {
    renderMesa();
    await waitFor(() => expect(names()).toContain("Front door"));
    choose("Front door");
    await waitFor(() => expect(editorWindow().getByLabelText(/Control mode/)).toHaveValue("prohibited"));
    const discard = editorWindow().getByRole("button", { name: "Discard Changes" });
    expect(discard).toHaveAttribute("aria-disabled", "true");
    fireEvent.change(editorWindow().getByLabelText(/Control mode/), { target: { value: "confirm" } });
    expect(discard).not.toHaveAttribute("aria-disabled", "true");
    fireEvent.click(discard);
    expect(editorWindow().getByLabelText(/Control mode/)).toHaveValue("prohibited");
  });

  it("adds a profile from the Add Profile menu, and Save shows it as saved", async () => {
    vi.mocked(api.putMesaDomain).mockResolvedValue({} as never);
    vi.mocked(api.getMesaDomain).mockResolvedValue({ stored: doc("confirm") } as never);
    renderMesa();
    await waitFor(() => expect(names()).toContain("Front door"));
    fireEvent.click(profilesWindow().getByRole("button", { name: "Add Profile" }));
    expect(screen.getAllByRole("menuitem").map((i) => i.textContent)).toEqual([
      "Add Entity Profile", "Add Device Profile", "Add Area Profile", "Add Integration Profile", "Add Domain Profile",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Add Domain Profile" }));
    expect(editorWindow().getByRole("heading", { level: 3 })).toHaveTextContent("Add Domain Profile");
    // Delete is offered but does nothing until there is a profile to delete (user, 2026-10-30).
    expect(editorWindow().getByRole("button", { name: "Delete" })).toBeDisabled();
    fireEvent.change(editorWindow().getByRole("combobox", { name: /Domain/ }), { target: { value: "lock" } });
    fireEvent.change(editorWindow().getByLabelText(/Control mode/), { target: { value: "confirm" } });
    fireEvent.click(editorWindow().getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putMesaDomain).toHaveBeenCalledWith("lock", expect.any(Object), true));
    await waitFor(() => expect(editorWindow().getByRole("button", { name: "Delete" })).not.toBeDisabled());
    expect(editorWindow().getByRole("heading", { level: 3 })).toHaveTextContent("lock");
    expect(api.getMesaDomain).toHaveBeenCalledWith("lock");
    expect(api.listMesaProfiles).toHaveBeenCalledTimes(2);
  });

  it("does not offer a new profile a target that already has one", async () => {
    vi.mocked(api.listMesaDomains).mockResolvedValue({ domains: [{ domain: "lock", document: doc("confirm") }] } as never);
    renderMesa();
    await waitFor(() => expect(names()).toContain("Front door"));
    const add = (item: string) => {
      fireEvent.click(profilesWindow().getByRole("button", { name: "Add Profile" }));
      fireEvent.click(screen.getByRole("menuitem", { name: item }));
    };
    const offered = (label: RegExp, text: string) => {
      fireEvent.change(editorWindow().getByRole("combobox", { name: label }), { target: { value: text } });
      const list = editorWindow().queryByRole("listbox");
      return list ? within(list).getAllByRole("option").map((o) => o.querySelector("code")?.textContent ?? o.textContent) : [];
    };
    add("Add Domain Profile");
    expect(offered(/Domain/, "l")).toEqual(["light"]);
    // Typed in full, a taken target is refused rather than saved over.
    offered(/Domain/, "lock");
    expect(editorWindow().getByRole("button", { name: "Save" })).toHaveAttribute("aria-disabled", "true");
    add("Add Entity Profile");
    // Both entities have profiles; the editor's own entity picker offers neither.
    expect(offered(/Entity/, "e")).toEqual([]);
  });

  it("deletes only after an inline confirmation naming the target, then empties the editor", async () => {
    vi.mocked(api.deleteMesaProfile).mockResolvedValue(undefined as never);
    renderMesa();
    await waitFor(() => expect(names()).toContain("Front door"));
    choose("Front door");
    await waitFor(() => expect(editorWindow().getByLabelText(/Control mode/)).toHaveValue("prohibited"));
    fireEvent.click(editorWindow().getByRole("button", { name: "Delete" }));
    const confirm = editorWindow().getByRole("group", { name: "Delete this entity profile?" });
    // What it falls back to reads in Help rather than inside the box (user, 2026-10-30).
    expect(confirm.getAttribute("data-help-text")).toContain('The "Front door" entity will fall back');
    expect(api.deleteMesaProfile).not.toHaveBeenCalled();
    fireEvent.click(within(confirm).getByRole("button", { name: "Delete Entity Profile" }));
    await waitFor(() => expect(api.deleteMesaProfile).toHaveBeenCalledWith("lock.front"));
    expect(await editorWindow().findByText("Select a profile to edit it, or add one.")).toBeTruthy();
  });
});

describe("the injected modal editor", () => {
  it("also confirms an entity profile's deletion first", async () => {
    vi.mocked(api.deleteMesaProfile).mockResolvedValue(undefined as never);
    const onClose = vi.fn();
    render(<ProfileEditor scope="entity" profileKey="lock.front" isNew={false} entityTree={{}} canonicalTags={[]} onClose={onClose} onSaved={() => {}} lockedKey />);
    await waitFor(() => expect(screen.getByLabelText(/Control mode/)).toHaveValue("prohibited"));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(api.deleteMesaProfile).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole("group", { name: "Delete this entity profile?" })).getByRole("button", { name: "Delete Entity Profile" }));
    await waitFor(() => expect(api.deleteMesaProfile).toHaveBeenCalledWith("lock.front"));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("loading the findings", () => {
  const profile = { entity_id: "light.kept", document: doc("prohibited") };
  const pending = <T,>() => {
    let resolve!: (value: T) => void, reject!: (reason: Error) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
  };
  const issues = (orphans: string[]): MesaIssuesResponse => ({ issues: [], orphans, orphan_devices: [], orphan_areas: [], orphan_integrations: [], suggestions: [], dismissed_suggestions: [] });
  /** The server's two reads: what it holds, which is quick, and a scan, which is not. */
  function holdReads() {
    const held = pending<MesaIssuesResponse>();
    const scan = pending<MesaIssuesResponse>();
    vi.mocked(api.getMesaIssues).mockImplementation((refresh) => (refresh ? scan.promise : held.promise));
    held.promise.catch(() => null);
    vi.mocked(api.listMesaProfiles).mockResolvedValue({ profiles: [profile], total_matched: 1, has_more: false, next_cursor: null });
    return { held, scan };
  }

  it("lists the profiles without waiting for the scan, and claims no findings until they are known", async () => {
    const { held, scan } = holdReads();
    const onFindings = vi.fn();
    renderMesa({ onFindings });
    expect(await profilesWindow().findByText("light.kept")).toBeVisible();
    expect(advisoryWindow().queryByText(/No advisories right now/)).toBeNull();
    expect(suggestedWindow().queryByText(/No suggestions right now/)).toBeNull();
    expect(advisoryWindow().getByRole("status")).toBeVisible();
    expect(onFindings).not.toHaveBeenCalledWith({ advisory: false });

    held.resolve(issues([]));
    scan.resolve(issues([]));
    expect(await advisoryWindow().findByText(/No advisories right now/)).toBeVisible();
    expect(await suggestedWindow().findByText(/No suggestions right now/)).toBeVisible();
    await waitFor(() => expect(onFindings).toHaveBeenLastCalledWith({ advisory: false }));
  });

  it("shows the findings the server holds while the scan runs, then what the scan found", async () => {
    const { held, scan } = holdReads();
    renderMesa();
    held.resolve(issues(["sensor.held"]));
    expect(await advisoryWindow().findByText(/sensor.held/)).toBeVisible();
    scan.resolve(issues(["sensor.scanned"]));
    expect(await advisoryWindow().findByText(/sensor.scanned/)).toBeVisible();
    expect(advisoryWindow().queryByText(/sensor.held/)).toBeNull();
  });

  it("does not let the held findings replace a scan that finished first", async () => {
    const { held, scan } = holdReads();
    renderMesa();
    scan.resolve(issues(["sensor.scanned"]));
    expect(await advisoryWindow().findByText(/sensor.scanned/)).toBeVisible();
    held.resolve(issues(["sensor.held"]));
    await new Promise((r) => setTimeout(r, 20));
    expect(advisoryWindow().queryByText(/sensor.held/)).toBeNull();
  });

  it("says why when the findings cannot be read, rather than that there are none", async () => {
    const { held, scan } = holdReads();
    renderMesa();
    held.reject(new Error("No reply"));
    scan.reject(new Error("No reply"));
    expect(await advisoryWindow().findByText("No reply")).toBeVisible();
    expect(advisoryWindow().queryByText(/No advisories right now/)).toBeNull();
    expect(await profilesWindow().findByText("light.kept")).toBeVisible();
  });
});

describe("conditional MESA windows", () => {
  it("are unknown until MESA has loaded, then follow its findings; other windows are always allowed", () => {
    expect(windowAvailable("mesaAdvisory", null, null)).toBeNull();
    expect(windowAvailable("mesaAdvisory", null, { advisory: true })).toBe(true);
    expect(windowAvailable("mesaAdvisory", null, { advisory: false })).toBe(false);
    expect(windowAvailable("mesaProfiles", null, null)).toBe(true);
  });
});
