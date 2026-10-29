/**
 * Behaviour: the selected key's windows keep their help in the Help window
 * (user, 2026-10-28). Persona and Capabilities show descriptions only in
 * icons-and-text mode and always name them for Help and screen readers, with
 * risky capabilities marked in icons-only mode; Key Settings has Guidance,
 * Agent Behavior and Rate Limiting with every explanation in Help, and
 * Assist exposure only for pass-through keys; Presets marks its states as
 * icon and words with buttons that follow the display mode; the Permissions
 * tree has no Reload button, follows Home Assistant's registries by itself,
 * and offers Filter; the hint editor keeps its scope help for Help.
 * Choosing another key keeps the one before on screen, inert, until it loads;
 * Custom brings back a key's own capabilities after another persona; the
 * confirmations are plain boxes; archived keys turn red only to confirm a
 * delete; the create form keeps its explanations in Help; and the key list
 * reads itself again quietly while shown.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { api } from "../api";
import { CAP_NAMES } from "../components/CapabilityMatrix";
import { DisplayModeContext, type DisplayMode } from "../ui/ActionButton";
import type { AccessKeyRecord } from "../types";
import { AccessKeyDetailProvider, CapabilitiesWindow, KeySettingsWindow, PermissionsWindow, PersonaWindow, PresetsWindow } from "../views/AccessKeyDetail";
import { ArchivedAccessKeyTable } from "../components/ArchivedAccessKeyTable";
import { AccessKeyCreateForm } from "../components/AccessKeyCreate";
import { AccessKeyListView } from "../views/AccessKeyList";
import { MenuRootContext } from "../ui/Menu";
import { PERSONA_CAP_DEFAULTS } from "../personas";

const { registry } = vi.hoisted(() => ({ registry: new Map<string, () => void>() }));

vi.mock("../api", () => ({
  api: {
    getAccessKey: vi.fn(),
    patchAccessKey: vi.fn(),
    getEntityTree: vi.fn(),
    getEntityHints: vi.fn(),
    listMesaProfiles: vi.fn(),
    listMesaDevices: vi.fn(),
    listMesaDomains: vi.fn(),
    getMesaVocabulary: vi.fn(),
    deleteArchivedAccessKey: vi.fn(),
    createAccessKey: vi.fn(),
    listArchivedAccessKeys: vi.fn(),
  },
  // Home Assistant's connection, recording which events the tree follows.
  currentHass: () => ({
    connection: {
      subscribeEvents: async (cb: () => void, event: string) => {
        registry.set(event, cb);
        return () => registry.delete(event);
      },
    },
  }),
}));

function accessKey(overrides: Partial<AccessKeyRecord> = {}): AccessKeyRecord {
  return {
    id: "k1", name: "kitchen_agent", created_at: "2026-01-01T00:00:00Z", created_by: "admin", expires_at: null, revoked: false,
    last_used_at: null, updated_at: null, pass_through: false, persona: "automation_builder", notes: null,
    confirm_inline_wait_seconds: 0, rate_limit_requests: 60, rate_limit_burst: 10,
    ...Object.fromEntries(CAP_NAMES.map((c) => [c, "deny"])),
    permissions: { domains: {}, devices: {}, entities: {} },
    presets: [], active_preset_id: null,
    ...overrides,
  } as AccessKeyRecord;
}

let current = accessKey();
const TREE = { light: { devices: {}, deviceless_entities: ["light.kitchen"], entity_details: { "light.kitchen": { friendly_name: "Kitchen", labels: [], area_id: null, area_name: null } } } };

beforeEach(() => {
  vi.clearAllMocks();
  registry.clear();
  current = accessKey();
  vi.mocked(api.getAccessKey).mockImplementation(async () => current);
  vi.mocked(api.getEntityTree).mockResolvedValue(TREE as never);
  vi.mocked(api.getEntityHints).mockResolvedValue({ entity_hints: {} } as never);
  vi.mocked(api.listMesaProfiles).mockResolvedValue({ profiles: [] } as never);
  vi.mocked(api.listMesaDevices).mockResolvedValue({ devices: [] } as never);
  vi.mocked(api.listMesaDomains).mockResolvedValue({ domains: [] } as never);
  vi.mocked(api.getMesaVocabulary).mockResolvedValue({ canonical_tags: [] } as never);
});
afterEach(() => { vi.useRealTimers(); });

function show(content: ReactNode, mode: DisplayMode = "icon-text") {
  return render(
    <DisplayModeContext.Provider value={mode}>
      <AccessKeyDetailProvider accessKeyId="k1" onDeselect={vi.fn()} onReveal={vi.fn()} presetsEnabled>
        {content}
      </AccessKeyDetailProvider>
    </DisplayModeContext.Provider>,
  );
}

describe("Persona", () => {
  it("names each persona's description for Help, showing it only with icons and text", async () => {
    const { container, unmount } = show(<PersonaWindow />);
    const builder = await screen.findByRole("button", { name: /Automation Builder/, pressed: true });
    expect(builder).toHaveAttribute("data-help", "personas.automation_builder.description");
    expect(builder.querySelector(".persona-card-desc")).not.toBeNull();
    // The window's own explanation is in Help, not above the buttons.
    expect(container.querySelector(".persona-picker")).toHaveAttribute("data-help", "help.persona");
    expect(container.querySelector(".persona-helper")).toBeNull();
    unmount();

    show(<PersonaWindow />, "icons");
    const compact = await screen.findByRole("button", { name: /Automation Builder/, pressed: true });
    expect(compact.querySelector(".persona-card-desc")).toBeNull();
    // Still read out with the button, and still a large button.
    expect(compact.querySelector(".sr-only")?.textContent).toBeTruthy();
    expect(compact.closest(".persona-grid")).toHaveClass("is-compact");
  });

  it("keeps Home Companion's setup note in view while it is chosen", async () => {
    current = accessKey({ persona: "home_companion" });
    const { container } = show(<PersonaWindow />);
    await screen.findByRole("button", { name: /Home Companion/, pressed: true });
    expect(container.querySelector(".persona-helper")?.textContent).toContain("Voice settings");
  });
});

describe("Capabilities", () => {
  it("hides descriptions in icons-only mode and marks the four risky capabilities", async () => {
    const { container, unmount } = show(<CapabilitiesWindow />);
    await screen.findByRole("group", { name: "Manage automations" });
    expect(container.querySelectorAll(".cap-row-desc").length).toBe(CAP_NAMES.length);
    expect(container.querySelector(".cap-row-warning")).toBeNull();
    unmount();

    const icons = show(<CapabilitiesWindow />, "icons");
    await screen.findByRole("group", { name: "Manage automations" });
    expect(icons.container.querySelector(".cap-row-desc")).toBeNull();
    const warned = [...icons.container.querySelectorAll(".cap-row-warning")].map((icon) => icon.closest(".cap-row-label")!.getAttribute("data-help"));
    expect(warned.sort()).toEqual([
      "caps.cap_automation_write.description", "caps.cap_esphome_flash.description", "caps.cap_script_write.description", "caps.cap_yaml_edit.description",
    ]);
  });
});

describe("Key Settings", () => {
  it("shows Guidance, Agent Behavior and Rate Limiting with every explanation in Help", async () => {
    const { container } = show(<KeySettingsWindow />);
    await screen.findByRole("heading", { name: "Guidance" });
    expect(screen.getAllByRole("heading").map((h) => h.textContent)).toEqual(["Guidance", "Agent Behavior", "Rate Limiting"]);
    expect(container.querySelectorAll(".toggle-label small")).toHaveLength(0);
    expect(container.querySelector(".access-key-notes-help")).toBeNull();
    const guidance = screen.getByRole("textbox", { name: /Notes for agents/ });
    expect(guidance).toHaveAttribute("data-help", "help.guidance");
    // Who receives it is part of its Help text, not a line under the box (user, 2026-10-28).
    expect(guidance.getAttribute("aria-description")).toContain("Never put passwords or other secrets here.");
    expect(screen.getByRole("combobox", { name: /inline approval wait/i })).toHaveAttribute("data-help", "help.inlineWait");
    expect(screen.getByRole("checkbox", { name: "Always announce all tools" })).toHaveAttribute("data-help", "accessKeys.announceAllHelp");
    // The labels say 0 turns rate limiting off; no separate line repeats it.
    expect(screen.queryByText(/Rate limiting is disabled/)).toBeNull();
    // Assist exposure only matters for pass-through keys.
    expect(screen.queryByRole("checkbox", { name: "Limit to Assist-exposed entities" })).toBeNull();
  });

  it("offers Assist exposure on a pass-through key", async () => {
    current = accessKey({ pass_through: true });
    show(<KeySettingsWindow />);
    expect(await screen.findByRole("checkbox", { name: "Limit to Assist-exposed entities" })).toBeInTheDocument();
  });
});

describe("Presets", () => {
  const preset = (id: string, name: string) => ({
    id, name, created_at: null, caps: Object.fromEntries(CAP_NAMES.map((c) => [c, "deny"])), persona: "automation_builder",
    permissions: { domains: {}, devices: {}, entities: {} }, pass_through: false, use_assist_exposure: false, announce_all_tools: false,
    confirm_inline_wait_seconds: 0, rate_limit_requests: 60, rate_limit_burst: 10,
  });

  it("marks the active preset as icon and words, with buttons that follow the display mode", async () => {
    current = accessKey({ presets: [preset("p1", "Daily"), preset("p2", "Away")] as never, active_preset_id: "p1" });
    const { container } = show(<PresetsWindow />, "icons");
    const away = await screen.findByRole("button", { name: "Delete preset Away" });
    expect(away).toHaveClass("is-icon-only");
    expect(screen.getByRole("button", { name: "Switch To" })).toHaveClass("is-icon-only");
    expect(container.querySelector(".preset-state-active")?.textContent).toBe("Active");
    expect(container.querySelector(".badge")).toBeNull();
    // How switching works is in Help.
    expect(container.querySelector(".preset-hint")).toBeNull();
    expect(container.querySelector(".preset-list")).toHaveAttribute("data-help", "accessKeys.presetHint");
  });
});

describe("Permissions tree", () => {
  it("has no Reload button, offers Filter, and follows Home Assistant's registries quietly", async () => {
    show(<PermissionsWindow />);
    const tree = await screen.findByText("light");
    expect(screen.queryByRole("button", { name: /Reload/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Filter" })).toBeInTheDocument();
    await waitFor(() => expect([...registry.keys()].sort()).toEqual(["area_registry_updated", "device_registry_updated", "entity_registry_updated", "label_registry_updated"]));
    vi.useFakeTimers();
    const before = vi.mocked(api.getEntityTree).mock.calls.length;
    // A burst of changes is read once, after it settles, without the tree giving way to a spinner.
    act(() => { registry.get("entity_registry_updated")!(); registry.get("label_registry_updated")!(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(999); });
    expect(api.getEntityTree).toHaveBeenCalledTimes(before);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(api.getEntityTree).toHaveBeenCalledTimes(before + 1);
    expect(tree).toBeInTheDocument();
  });

  it("keeps the hint editor's scope explanation and caution for Help", async () => {
    current = accessKey({ permissions: { domains: {}, devices: {}, entities: { "light.kitchen": { state: "READ" } } } as never });
    const { container } = show(<PermissionsWindow />);
    fireEvent.click(await screen.findByText("light"));
    fireEvent.click(await screen.findByRole("button", { name: /hint/i }));
    const editor = await screen.findByRole("group", { name: /Entity hint/i });
    expect(editor.querySelector(".hint-scope-note")).toBeNull();
    expect(within(editor).getByRole("checkbox")).toHaveAttribute("data-help", "perms.hintNoteGlobal");
    expect(container.querySelector(".tree-hint-caution")).toBeNull();
    expect(within(editor).getByRole("textbox")).toHaveAttribute("data-help", "perms.hintCaution");
  });
});

describe("Choosing another key", () => {
  it("keeps the key before on screen, inert, until the chosen one loads", async () => {
    const view = (id: string) => (
      <AccessKeyDetailProvider accessKeyId={id} onDeselect={vi.fn()} presetsEnabled><PersonaWindow /></AccessKeyDetailProvider>
    );
    const { container, rerender } = render(view("k1"));
    await screen.findByRole("button", { name: /Automation Builder/, pressed: true });
    let arrive: (k: AccessKeyRecord) => void = () => {};
    vi.mocked(api.getAccessKey).mockImplementation(() => new Promise((resolve) => { arrive = resolve; }));
    rerender(view("k2"));
    // No loading state in between: the first key's personas stay, and cannot be used.
    expect(container.querySelector(".phx-loading, .spinner")).toBeNull();
    const held = screen.getByRole("button", { name: /Automation Builder/, pressed: true });
    expect(held.closest(".phx-window-pad")).toHaveAttribute("inert");
    await act(async () => { arrive(accessKey({ id: "k2", persona: "read_only" })); });
    const chosen = await screen.findByRole("button", { name: /Read-Only Observer/, pressed: true });
    expect(chosen.closest(".phx-window-pad")).not.toHaveAttribute("inert");
  });
});

describe("Custom persona", () => {
  it("brings back the key's own capabilities after another persona was tried", async () => {
    const mine = { cap_config_read: "allow", cap_automation_write: "confirm" } as const;
    current = accessKey({ persona: "custom", ...mine });
    vi.mocked(api.patchAccessKey).mockImplementation(async (_id, body) => {
      current = { ...current, ...(body as Partial<AccessKeyRecord>) };
      return current;
    });
    show(<PersonaWindow />);
    fireEvent.click(await screen.findByRole("button", { name: /Automation Builder/ }));
    await screen.findByRole("button", { name: /Automation Builder/, pressed: true });
    expect(api.patchAccessKey).toHaveBeenLastCalledWith("k1", { persona: "automation_builder", ...PERSONA_CAP_DEFAULTS.automation_builder });
    fireEvent.click(screen.getByRole("button", { name: /Custom/ }));
    await screen.findByRole("button", { name: /Custom/, pressed: true });
    const body = vi.mocked(api.patchAccessKey).mock.lastCall![1] as Record<string, string>;
    expect(body).toMatchObject({ persona: "custom", ...mine, cap_yaml_edit: "deny" });
    expect(Object.keys(body).filter((k) => k.startsWith("cap_")).sort()).toEqual([...CAP_NAMES].sort());
  });
});

describe("Confirmations", () => {
  it("asks before switching a pass-through key to scoped, in a plain box with an icon on Cancel", async () => {
    current = accessKey({ pass_through: true });
    const { container } = show(<PermissionsWindow />);
    fireEvent.click(await screen.findByRole("button", { name: "Switch to Scoped Mode" }));
    const confirm = screen.getByRole("group", { name: "Switch to Scoped Mode" });
    expect(confirm.querySelector(".phx-inline-confirm-title")?.textContent).toBe("Switch to Scoped Mode");
    expect(confirm.querySelector(".phx-inline-confirm-title svg")).toBeNull();
    expect(within(confirm).getByRole("button", { name: "Cancel" }).querySelector("svg")).not.toBeNull();
    expect(container.querySelector(".pass-through-convert-card")).toBeNull();
    fireEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Switch to Scoped Mode" })).toBeNull();
    expect(api.patchAccessKey).not.toHaveBeenCalled();
  });

  it("offers Clear All with an eraser, red only once it asks to confirm", async () => {
    const { container } = show(<PermissionsWindow />);
    const clear = await screen.findByRole("button", { name: "Clear All" });
    expect(clear).not.toHaveClass("btn-danger");
    fireEvent.click(clear);
    const confirm = container.querySelector(".phx-inline-confirm.is-danger")!;
    expect(within(confirm as HTMLElement).getByRole("button", { name: "Clear All" })).toHaveClass("btn-danger");
  });
});

describe("Archived keys", () => {
  const archived = [{ id: "a1", name: "old_agent", revoked: true, created_at: "2026-01-01T00:00:00Z", revoked_at: "2026-02-01T00:00:00Z", last_used_at: null }];

  it("turns Delete Permanently red to confirm, with Cancel beside it", async () => {
    vi.mocked(api.deleteArchivedAccessKey).mockResolvedValue(undefined as never);
    const onDeleted = vi.fn();
    render(<ArchivedAccessKeyTable accessKeys={archived as never} onDeleted={onDeleted} />);
    const remove = screen.getByRole("button", { name: "Delete Permanently" });
    expect(remove).not.toHaveClass("btn-danger");
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    fireEvent.click(remove);
    expect(api.deleteArchivedAccessKey).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Confirm Delete" })).toHaveClass("btn-danger");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Delete Permanently" })).not.toHaveClass("btn-danger");
    fireEvent.click(screen.getByRole("button", { name: "Delete Permanently" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm Delete" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith("a1"));
    expect(api.deleteArchivedAccessKey).toHaveBeenCalledWith("a1");
  });

  it("cancels the confirmation on a click anywhere else, not on one inside it (user, 2026-10-28)", () => {
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    try {
      render(<ArchivedAccessKeyTable accessKeys={archived as never} onDeleted={vi.fn()} />);
      fireEvent.click(screen.getByRole("button", { name: "Delete Permanently" }));
      fireEvent.pointerDown(screen.getByRole("button", { name: "Confirm Delete" }));
      expect(screen.getByRole("button", { name: "Confirm Delete" })).toBeInTheDocument();
      fireEvent.pointerDown(outside);
      expect(screen.queryByRole("button", { name: "Confirm Delete" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
      expect(api.deleteArchivedAccessKey).not.toHaveBeenCalled();
    } finally {
      outside.remove();
    }
  });
});

describe("Create Access Key", () => {
  it("keeps the pass-through explanation for Help and its fields on one row", () => {
    const { container } = render(<AccessKeyCreateForm existingNames={[]} onCreated={vi.fn()} onClose={vi.fn()} onOpenSettings={vi.fn()} />);
    expect(container.querySelector("small")).toBeNull();
    const pt = screen.getByRole("checkbox", { name: /^Pass-through mode$/i });
    expect(pt.closest(".toggle-row")).toHaveAttribute("data-help", "accessKeys.passThroughModeHelp");
    expect(pt).toHaveAttribute("aria-description", expect.stringContaining("Bypass the permission tree"));
    const row = container.querySelector(".access-key-create-fields")!;
    for (const name of ["Name (required)", "Expiry", "Requests per minute (0 = disabled)", "Burst per second"]) {
      expect(row.contains(screen.getByLabelText(name))).toBe(true);
    }
    fireEvent.click(pt);
    expect(container.querySelector(".amber-block")).not.toBeNull();
  });
});

describe("Key list", () => {
  it("reads itself again quietly while shown, with no Refresh tool", async () => {
    vi.useFakeTimers();
    const onUpdate = vi.fn();
    render(
      <AccessKeyListView accessKeys={[accessKey()]} loading error={null} onRefresh={vi.fn()} onUpdate={onUpdate} onOpenDetail={vi.fn()}
        onLaunchWizard={vi.fn()} showCreate={false} onOpenCreate={vi.fn()} onCloseCreate={vi.fn()} onOpenSettings={vi.fn()} />,
    );
    // Reading again keeps the table: only a first load shows the loading state.
    expect(screen.getByRole("grid")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Refresh/ })).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(29_999); });
    expect(onUpdate).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(onUpdate).toHaveBeenCalledTimes(1);
  });
});

describe("New key dialog", () => {
  it("sits over the whole workspace, not inside the Access Keys window, in the UI2 style", async () => {
    vi.mocked(api.createAccessKey).mockResolvedValue({ ...accessKey({ id: "k9", name: "new_agent" }), access_key: "phx_secret" } as never);
    const shell = document.createElement("div");
    shell.className = "phx-shell";
    document.body.appendChild(shell);
    try {
      const { container } = render(
        <MenuRootContext.Provider value={shell}>
          <div className="phx-window"><AccessKeyCreateForm existingNames={[]} onCreated={vi.fn()} onClose={vi.fn()} onOpenSettings={vi.fn()} /></div>
        </MenuRootContext.Provider>,
      );
      fireEvent.change(screen.getByLabelText("Name (required)"), { target: { value: "new_agent" } });
      fireEvent.click(screen.getByRole("button", { name: "Create" }));
      const dialog = await screen.findByRole("dialog");
      expect(shell.contains(dialog)).toBe(true);
      expect(container.contains(dialog)).toBe(false);
      expect(dialog.querySelector(".modal-title")?.textContent).toBe("Access Key Created: new_agent");
      // Plain rows with an icon each; no coloured banners anywhere in it.
      const choices = [...dialog.querySelectorAll(".post-create-choice")];
      expect(choices).toHaveLength(3);
      for (const choice of choices) expect(choice.querySelector("svg")).not.toBeNull();
      fireEvent.click(within(dialog).getByRole("button", { name: /Show the Access Key/ }));
      expect(within(dialog).getByText("phx_secret")).toBeInTheDocument();
      expect(dialog.querySelector(".banner")).toBeNull();
      expect(within(dialog).getByRole("button", { name: "Back" }).querySelector("svg")).not.toBeNull();
      expect(within(dialog).getByRole("button", { name: "Done" }).querySelector("svg")).not.toBeNull();
    } finally {
      shell.remove();
    }
  });
});
