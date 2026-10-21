/**
 * The Access Keys workspace: the key list on top selects a key, and the
 * windows below share that key's state through one provider. Key actions keep
 * their confirmations, and the list's buttons move to the toolbar in the shell.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { api } from "../api";
import { CAP_NAMES } from "../components/CapabilityMatrix";
import { ToolRegistry, ToolRegistryContext, WindowPlaceContext } from "../shell/tools";
import type { AccessKeyRecord } from "../types";
import {
  AccessKeyDetailProvider, AccessKeyNotices, PermissionSummaryWindow, PersonaWindow, useAccessKeyDetail, useKeyActionsTool,
} from "../views/AccessKeyDetail";
import { AccessKeyListView } from "../views/AccessKeyList";

vi.mock("../api", () => ({
  api: {
    getAccessKey: vi.fn(),
    patchAccessKey: vi.fn(),
    revokeAccessKey: vi.fn(),
    getEntityTree: vi.fn(),
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
const tool = (registry: ToolRegistry, group: string, id: string) => registry.latest("accessKeys", group, id);

describe("Access Keys workspace", () => {
  it("asks for a selection, then fills the windows below the list without leaving it", async () => {
    const registry = new ToolRegistry();
    render(<Workspace registry={registry} />);
    expect(persona().getByText("Select an access key to see its settings.")).toBeInTheDocument();
    expect(tool(registry, "selectedKey", "keyActions")).toBeUndefined();

    fireEvent.click(screen.getByRole("button", { name: "Edit access key kitchen_agent" }));
    await waitFor(() => expect(persona().getByRole("button", { name: /Automation Builder/, pressed: true })).toBeInTheDocument());
    const row = screen.getByRole("button", { name: "Edit access key kitchen_agent" }).closest("tr")!;
    expect(row).toHaveClass("is-selected");
    expect(row).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Edit access key hall_agent" })).toBeInTheDocument();
    expect(tool(registry, "selectedKey", "keyActions")?.label).toBe("Key Actions");

    // Another key starts from that key's own state.
    fireEvent.click(screen.getByRole("button", { name: "Edit access key hall_agent" }));
    await waitFor(() => expect(persona().getByRole("button", { name: /Read-Only Observer/, pressed: true })).toBeInTheDocument());
    expect(api.getAccessKey).toHaveBeenLastCalledWith("k2");
  });

  it("moves the list's buttons to the toolbar inside the shell", () => {
    const registry = new ToolRegistry();
    render(<Workspace registry={registry} />);
    expect(screen.queryByRole("button", { name: "Create Access Key" })).toBeNull();
    const labels = registry.groupsFor("accessKeys", ["accessKeys"])[0].tools.map((t) => t.label);
    expect(labels).toEqual(["Set Up an Agent", "Show Archived", "Create Access Key", "Refresh Access Keys"]);
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
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(api.patchAccessKey).not.toHaveBeenCalled();
    fireEvent.change(again, { target: { value: "kitchen_helper" } });
    fireEvent.keyDown(again, { key: "Enter" });
    await waitFor(() => expect(api.patchAccessKey).toHaveBeenCalledWith("k1", { name: "kitchen_helper" }));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Access key name (editable)" })).toBeNull());
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
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Revoke Access Key" });
    expect(confirm).toBeDisabled();
    expect(api.revokeAccessKey).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("checkbox"));
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
      expect(screen.getByText(/This is a pass-through access key/)).toBeInTheDocument();
    } finally {
      KEYS.pop();
    }
  });

  it("a rule picked in Permission Summary brings the tree into view", async () => {
    const registry = new ToolRegistry();
    const onReveal = vi.fn();
    render(<Workspace registry={registry} initial="k1" onReveal={onReveal} />);
    const summary = within(screen.getByRole("region", { name: "Summary window" }));
    fireEvent.click(await summary.findByRole("button", { name: "light.kitchen" }));
    expect(onReveal).toHaveBeenCalledOnce();
  });
});
