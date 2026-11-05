/**
 * The Access Keys workspace: the key list on top selects a key, and the
 * windows below share that key's state through one provider. Key actions keep
 * their confirmations, now shown in place in the window they belong to, and
 * the list's buttons move to the toolbar in the shell.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { api } from "../api";
import { CAP_NAMES } from "../components/CapabilityMatrix";
import { ToolRegistry, ToolRegistryContext, WindowPlaceContext } from "../shell/tools";
import type { AccessKeyPreset, AccessKeyRecord } from "../types";
import {
  AccessKeyDetailProvider, AccessKeyNotices, PermissionSummaryWindow, PermissionsWindow, PersonaWindow, PresetsWindow, useAccessKeyDetail, useKeyActionsTool,
} from "../views/AccessKeyDetail";
import { AccessKeyListView } from "../views/AccessKeyList";
import { t } from "../i18n";
import { tPlain } from "../i18n/rich";

vi.mock("../api", () => ({
  // No Home Assistant connection: the tree does not follow the registries here.
  currentHass: () => null,
  api: {
    getAccessKey: vi.fn(),
    patchAccessKey: vi.fn(),
    revokeAccessKey: vi.fn(),
    rotateAccessKey: vi.fn(),
    setPermissions: vi.fn(),
    applyPreset: vi.fn(),
    getEntityTree: vi.fn(),
    getEntityHints: vi.fn(),
    listMesaProfiles: vi.fn(),
    listMesaDevices: vi.fn(),
    listMesaDomains: vi.fn(),
    getMesaVocabulary: vi.fn(),
    listArchivedAccessKeys: vi.fn(),
  },
}));

function accessKey(overrides: Partial<AccessKeyRecord> = {}): AccessKeyRecord {
  return {
    id: "k1",
    name: "kitchen_agent",
    created_at: "2026-01-01T00:00:00Z",
    created_by: "admin",
    expires_at: null,
    revoked: false,
    last_used_at: null,
    updated_at: null,
    pass_through: false,
    persona: "automation_builder",
    notes: null,
    confirm_inline_wait_seconds: 0,
    rate_limit_requests: 60,
    rate_limit_burst: 10,
    ...Object.fromEntries(CAP_NAMES.map((c) => [c, "deny"])),
    permissions: { domains: { light: { state: "WRITE" } }, devices: {}, entities: { "light.kitchen": { state: "READ" } } },
    presets: [],
    active_preset_id: null,
    ...overrides,
  } as AccessKeyRecord;
}

const KEYS = [accessKey(), accessKey({ id: "k2", name: "hall_agent", persona: "read_only" })];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getAccessKey).mockImplementation(async (id: string) => KEYS.find((k) => k.id === id)!);
  vi.mocked(api.getEntityTree).mockResolvedValue({} as never);
  vi.mocked(api.getEntityHints).mockResolvedValue({ entity_hints: {} } as never);
  vi.mocked(api.listMesaProfiles).mockResolvedValue({ profiles: [] } as never);
  vi.mocked(api.listMesaDevices).mockResolvedValue({ devices: [] } as never);
  vi.mocked(api.listMesaDomains).mockResolvedValue({ domains: [] } as never);
  vi.mocked(api.getMesaVocabulary).mockResolvedValue({ canonical_tags: [] } as never);
});

/** The list and two key windows, wired the way the shell wires them. */
function Workspace({ registry, onDeselect = vi.fn(), onReveal = vi.fn(), initial = null as string | null }: {
  registry: ToolRegistry; onDeselect?: () => void; onReveal?: () => void; initial?: string | null;
}) {
  const [selected, setSelected] = useState<string | null>(initial);
  return (
    <ToolRegistryContext.Provider value={registry}>
      <AccessKeyDetailProvider accessKeyId={selected} onDeselect={() => { setSelected(null); onDeselect(); }} onReveal={onReveal}>
        <WindowPlaceContext.Provider value={{ workspace: "accessKeys", window: "accessKeys" }}>
          <List selected={selected} onSelect={setSelected} />
        </WindowPlaceContext.Provider>
        <section aria-label="Persona window"><PersonaWindow /></section>
        <section aria-label="Summary window"><PermissionSummaryWindow /></section>
        <WindowPlaceContext.Provider value={{ workspace: "accessKeys", window: "permissions" }}>
          <section aria-label="Permissions window"><PermissionsWindow /></section>
        </WindowPlaceContext.Provider>
        <section aria-label="Presets window"><PresetsWindow /></section>
      </AccessKeyDetailProvider>
    </ToolRegistryContext.Provider>
  );
}

function List({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }) {
  const detail = useAccessKeyDetail();
  const keyActions = useKeyActionsTool();
  return (
    <AccessKeyListView
      accessKeys={KEYS} loading={false} error={null} showCreate={false}
      onRefresh={vi.fn()} onOpenDetail={onSelect} onLaunchWizard={vi.fn()} onOpenCreate={vi.fn()} onCloseCreate={vi.fn()} onOpenSettings={vi.fn()}
      selectedId={selected}
      renamingId={detail?.renaming ? detail.accessKeyId : null}
      onRenamed={(u) => detail?.applyUpdate(u)}
      onRenameEnd={() => detail?.setRenaming(false)}
      selectedKeyTools={keyActions}
      notices={<AccessKeyNotices />}
    />
  );
}

const persona = () => within(screen.getByRole("region", { name: "Persona window" }));
/** A key's row in the list, named by the key, its mode and status. */
const keyRow = (name: string) => screen.getByRole("row", { name: new RegExp(`^${name},`) });
const tool = (registry: ToolRegistry, group: string, id: string) => registry.latest("accessKeys", group, id);

/** Choose an entry of the selected key's Key Actions menu. */
async function keyAction(registry: ToolRegistry, id: string) {
  await waitFor(() => expect(tool(registry, "selectedKey", "keyActions")).toBeDefined());
  const entry = tool(registry, "selectedKey", "keyActions")!.menu!().find((e) => e !== "separator" && e.id === id) as { onSelect: () => void };
  act(() => entry.onSelect());
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe("Access Keys workspace", () => {
  it("asks for a selection, then fills the windows below the list without leaving it", async () => {
    const registry = new ToolRegistry();
    render(<Workspace registry={registry} />);
    expect(persona().getByText("Select an access key to see its settings.")).toBeInTheDocument();
    expect(tool(registry, "selectedKey", "keyActions")).toBeUndefined();

    fireEvent.click(keyRow("kitchen_agent"));
    await waitFor(() => expect(persona().getByRole("button", { name: /Automation Builder/, pressed: true })).toBeInTheDocument());
    const row = keyRow("kitchen_agent");
    expect(row).toHaveClass("is-selected");
    expect(row).toHaveAttribute("aria-selected", "true");
    expect(keyRow("hall_agent")).toBeInTheDocument();
    expect(tool(registry, "selectedKey", "keyActions")?.label).toBe("Key Actions");

    // Another key starts from that key's own state.
    fireEvent.click(keyRow("hall_agent"));
    await waitFor(() => expect(persona().getByRole("button", { name: /Read-Only Observer/, pressed: true })).toBeInTheDocument());
    expect(api.getAccessKey).toHaveBeenLastCalledWith("k2");
  });

  it("moves the list's buttons to the toolbar inside the shell", () => {
    const registry = new ToolRegistry();
    render(<Workspace registry={registry} />);
    expect(screen.queryByRole("button", { name: "Create Access Key" })).toBeNull();
    const labels = registry.groupsFor("accessKeys", ["accessKeys"])[0].tools.map((t) => t.label);
    // No Refresh: the list keeps itself up to date while shown.
    expect(labels).toEqual(["Set Up an Agent", "Show Archived", "Create Access Key"]);
  });

  it("renames in place from Key Actions: Escape cancels, Enter saves", async () => {
    const registry = new ToolRegistry();
    vi.mocked(api.patchAccessKey).mockImplementation(async (_id, body) => ({ ...KEYS[0], ...body }) as AccessKeyRecord);
    render(<Workspace registry={registry} initial="k1" />);
    await waitFor(() => expect(tool(registry, "selectedKey", "keyActions")).toBeDefined());
    const menu = () => tool(registry, "selectedKey", "keyActions")!.menu!();
    const pick = (id: string) => act(() => (menu().find((e) => e !== "separator" && e.id === id) as { onSelect: () => void }).onSelect());

    pick("rename");
    const field = await screen.findByRole("textbox", { name: "Access key name (editable)" });
    expect(field).toHaveFocus();
    fireEvent.keyDown(field, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Access key name (editable)" })).toBeNull());
    expect(api.patchAccessKey).not.toHaveBeenCalled();

    pick("rename");
    const again = await screen.findByRole("textbox", { name: "Access key name (editable)" });
    fireEvent.change(again, { target: { value: "no spaces allowed" } });
    fireEvent.keyDown(again, { key: "Enter" });
    // The refusal reads in a row of its own under the key's, not cut off inside it.
    const alert = await screen.findByRole("alert");
    expect(alert.closest("tr")).toHaveClass("phx-table-detail");
    expect(alert.closest("tr")!.previousElementSibling).toBe(keyRow("kitchen_agent"));
    expect(again).toHaveAttribute("aria-describedby", alert.id);
    expect(api.patchAccessKey).not.toHaveBeenCalled();
    fireEvent.change(again, { target: { value: "kitchen_helper" } });
    fireEvent.keyDown(again, { key: "Enter" });
    await waitFor(() => expect(api.patchAccessKey).toHaveBeenCalledWith("k1", { name: "kitchen_helper" }));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Access key name (editable)" })).toBeNull());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("offers pass-through only to a scoped key, and revokes only after confirmation", async () => {
    const registry = new ToolRegistry();
    const onDeselect = vi.fn();
    vi.mocked(api.revokeAccessKey).mockResolvedValue(undefined as never);
    render(<Workspace registry={registry} initial="k1" onDeselect={onDeselect} />);
    await waitFor(() => expect(tool(registry, "selectedKey", "keyActions")).toBeDefined());
    const entries = tool(registry, "selectedKey", "keyActions")!.menu!().filter((e) => e !== "separator") as Array<{ id: string; label: string; disabled?: boolean; onSelect: () => void }>;
    expect(entries.map((e) => e.label)).toEqual(["Rename", "Rotate", "Enable Pass-Through", "Revoke"]);
    expect(entries.find((e) => e.id === "passThrough")?.disabled).toBe(false);

    act(() => entries.find((e) => e.id === "revoke")!.onSelect());
    // Confirmed in place, above the key list, rather than in a dialog.
    expect(screen.queryByRole("dialog")).toBeNull();
    const inline = await screen.findByRole("group", { name: "Revoke Access Key" });
    const confirm = within(inline).getByRole("button", { name: "Revoke Access Key" });
    expect(confirm).toBeDisabled();
    // What revoking does reads in Help, for the box and its controls, not in it.
    // Not drawn in the box (Help shows it), but read with it by a screen reader.
    expect(within(inline).getByText(t("accessKeys.revokeBody"))).toHaveClass("sr-only");
    expect(inline).toHaveAttribute("data-help-text", t("accessKeys.revokeBody"));
    expect(inline).toHaveAttribute("data-help-controls");
    expect(within(inline).getByRole("checkbox")).toHaveFocus();
    expect(api.revokeAccessKey).not.toHaveBeenCalled();
    fireEvent.click(within(inline).getByRole("checkbox"));
    fireEvent.click(confirm);
    await waitFor(() => expect(api.revokeAccessKey).toHaveBeenCalledWith("k1"));
    await waitFor(() => expect(onDeselect).toHaveBeenCalledOnce());
    expect(persona().getByText("Select an access key to see its settings.")).toBeInTheDocument();
  });

  it("disables Enable Pass-Through for a pass-through key and says why the summary is empty", async () => {
    const registry = new ToolRegistry();
    KEYS.push(accessKey({ id: "k3", name: "open_agent", pass_through: true }));
    try {
      render(<Workspace registry={registry} initial="k3" />);
      await waitFor(() => expect(tool(registry, "selectedKey", "keyActions")).toBeDefined());
      const entries = tool(registry, "selectedKey", "keyActions")!.menu!().filter((e) => e !== "separator") as Array<{ id: string; disabled?: boolean }>;
      expect(entries.find((e) => e.id === "passThrough")?.disabled).toBe(true);
      expect(screen.getByText("Pass-through access keys don't use the permissions tree, so there are no rules to summarize.")).toBeInTheDocument();
      // What pass-through means is the Mode column's Help, not a banner above the list.
      expect(screen.queryByText(/This is a pass-through access key/)).toBeNull();
      expect(document.querySelector(".access-keys-list .pass-through-header-banner")).toBeNull();
      expect(document.querySelector('tr[data-row="k3"] [data-help="accessKeys.passThroughModeHelp"]')).not.toBeNull();
    } finally {
      KEYS.pop();
    }
  });

  it("a rule picked in Permission Summary brings the tree into view", async () => {
    const registry = new ToolRegistry();
    const onReveal = vi.fn();
    render(<Workspace registry={registry} initial="k1" onReveal={onReveal} />);
    const summary = within(screen.getByRole("region", { name: "Summary window" }));
    fireEvent.click(await summary.findByRole("row", { name: /light\.kitchen/ }));
    expect(onReveal).toHaveBeenCalledOnce();
  });

  it("rotates only after acknowledgement, then shows the new key once in a dialog", async () => {
    const registry = new ToolRegistry();
    vi.mocked(api.rotateAccessKey).mockResolvedValue({ ...KEYS[0], access_key: "phx_" + "b".repeat(64) } as never);
    render(<Workspace registry={registry} initial="k1" />);
    await keyAction(registry, "rotate");
    const inline = await screen.findByRole("group", { name: "Rotate Access Key" });
    const confirm = within(inline).getByRole("button", { name: "Rotate Access Key" });
    expect(confirm).toBeDisabled();
    // In Help, not in the box, and confirmed in the same gold as Enable Pass-Through.
    // Not drawn in the box (Help shows it), but read with it by a screen reader.
    expect(within(inline).getByText(t("accessKeys.rotateBody"))).toHaveClass("sr-only");
    expect(inline).toHaveAttribute("data-help-text", t("accessKeys.rotateBody"));
    expect(confirm).toHaveClass("btn-warning");
    fireEvent.click(within(inline).getByRole("checkbox"));
    fireEvent.click(confirm);
    await waitFor(() => expect(api.rotateAccessKey).toHaveBeenCalledWith("k1"));
    // The one-time secret stays in a dialog.
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Access Key Rotated: kitchen_agent")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Rotate Access Key" })).toBeNull();
  });

  it("Escape closes a confirmation, but a running action can be neither dismissed nor replaced", async () => {
    const registry = new ToolRegistry();
    const pending = deferred<AccessKeyRecord>();
    vi.mocked(api.patchAccessKey).mockReturnValue(pending.promise);
    render(<Workspace registry={registry} initial="k1" />);

    await keyAction(registry, "rotate");
    fireEvent.keyDown(await screen.findByRole("group", { name: "Rotate Access Key" }), { key: "Escape" });
    expect(screen.queryByRole("group", { name: "Rotate Access Key" })).toBeNull();
    expect(api.rotateAccessKey).not.toHaveBeenCalled();

    await keyAction(registry, "passThrough");
    const inline = await screen.findByRole("group", { name: "Enable Pass-Through Mode" });
    expect(within(inline).getByText(/read and control every entity/)).toHaveClass("sr-only");
    expect(inline).toHaveAttribute("data-help-text", tPlain("accessKeys.enablePtBody"));
    fireEvent.click(within(inline).getByRole("checkbox"));
    fireEvent.click(within(inline).getByRole("button", { name: "Enable Pass-Through" }));
    expect(api.patchAccessKey).toHaveBeenCalledWith("k1", { pass_through: true, confirm_pass_through: true });

    await keyAction(registry, "revoke");
    fireEvent.keyDown(inline, { key: "Escape" });
    expect(screen.getByRole("group", { name: "Enable Pass-Through Mode" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Revoke Access Key" })).toBeNull();

    await act(async () => pending.resolve({ ...KEYS[0], pass_through: true }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Enable Pass-Through Mode" })).toBeNull());
    expect(api.revokeAccessKey).not.toHaveBeenCalled();
  });

  it("confirms Clear All Permissions in the Permissions window and keeps it open with the reason when it fails", async () => {
    const registry = new ToolRegistry();
    vi.mocked(api.setPermissions)
      .mockRejectedValueOnce(new Error("Storage is busy."))
      .mockResolvedValueOnce({ domains: {}, devices: {}, entities: {} });
    render(<Workspace registry={registry} initial="k1" />);
    await waitFor(() => expect(tool(registry, "permissions", "clearAll")).toBeDefined());
    // Select By narrows the tree beside its filter; it is not a toolbar tool any more.
    expect(tool(registry, "permissions", "selectBy")).toBeUndefined();

    // The tool opens and closes the confirmation, showing pressed while it is open.
    const permissions = within(screen.getByRole("region", { name: "Permissions window" }));
    act(() => tool(registry, "permissions", "clearAll")!.onSelect!());
    await permissions.findByRole("group", { name: "Clear All Permissions" });
    await waitFor(() => expect(tool(registry, "permissions", "clearAll")!.pressed).toBe(true));
    act(() => tool(registry, "permissions", "clearAll")!.onSelect!());
    await waitFor(() => expect(permissions.queryByRole("group", { name: "Clear All Permissions" })).toBeNull());
    await waitFor(() => expect(tool(registry, "permissions", "clearAll")!.pressed).toBe(false));
    act(() => tool(registry, "permissions", "clearAll")!.onSelect!());
    const inline = await permissions.findByRole("group", { name: "Clear All Permissions" });
    fireEvent.click(within(inline).getByRole("button", { name: "Clear All" }));
    expect(await within(inline).findByRole("alert")).toHaveTextContent("Storage is busy.");
    expect(permissions.getByRole("group", { name: "Clear All Permissions" })).toBeInTheDocument();

    fireEvent.click(within(inline).getByRole("button", { name: "Clear All" }));
    await waitFor(() => expect(permissions.queryByRole("group", { name: "Clear All Permissions" })).toBeNull());
    expect(api.setPermissions).toHaveBeenCalledTimes(2);
    expect(api.setPermissions).toHaveBeenLastCalledWith("k1", { domains: {}, devices: {}, entities: {} });
  });

  it("confirms a preset under its row and asks for the pass-through acknowledgement it needs", async () => {
    const preset: AccessKeyPreset = {
      id: "p1", name: "Evening", created_at: null, persona: "automation_builder",
      caps: Object.fromEntries(CAP_NAMES.map((c) => [c, "deny"])) as AccessKeyPreset["caps"],
      permissions: { domains: {}, devices: {}, entities: {} },
      pass_through: true, use_assist_exposure: false, announce_all_tools: false,
      confirm_inline_wait_seconds: 0, rate_limit_requests: 60, rate_limit_burst: 10,
    };
    KEYS.push(accessKey({ id: "k4", name: "preset_agent", presets: [preset] }));
    vi.mocked(api.applyPreset).mockResolvedValue(KEYS[2]);
    try {
      const registry = new ToolRegistry();
      render(<Workspace registry={registry} initial="k4" />);
      const presets = within(screen.getByRole("region", { name: "Presets window" }));
      fireEvent.click(await presets.findByRole("button", { name: "Switch To" }));
      expect(screen.queryByRole("dialog")).toBeNull();
      const inline = presets.getByRole("group", { name: "Switch to Preset: Evening" });
      // It opens directly under the preset's row.
      expect(inline.previousElementSibling).toHaveClass("preset-row");
      const confirm = within(inline).getByRole("button", { name: "Switch" });
      expect(confirm).toBeDisabled();
      fireEvent.click(within(inline).getByRole("checkbox", { name: "I understand this preset enables pass-through" }));
      fireEvent.click(confirm);
      await waitFor(() => expect(api.applyPreset).toHaveBeenCalledWith("k4", "p1", true));
      await waitFor(() => expect(presets.queryByRole("group", { name: "Switch to Preset: Evening" })).toBeNull());
    } finally {
      KEYS.pop();
    }
  });

  it("opens Create Access Key in place, above the list, and Escape closes it", () => {
    const onCloseCreate = vi.fn();
    render(
      <AccessKeyListView
        accessKeys={KEYS} loading={false} error={null} showCreate
        onRefresh={vi.fn()} onOpenDetail={vi.fn()} onLaunchWizard={vi.fn()} onOpenCreate={vi.fn()} onCloseCreate={onCloseCreate} onOpenSettings={vi.fn()}
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    const form = screen.getByRole("region", { name: "Create Access Key" });
    expect(form.compareDocumentPosition(screen.getByRole("grid")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const name = within(form).getByLabelText("Name (required)");
    expect(name).toHaveFocus();
    fireEvent.keyDown(name, { key: "Escape" });
    expect(onCloseCreate).toHaveBeenCalledOnce();
  });
});
