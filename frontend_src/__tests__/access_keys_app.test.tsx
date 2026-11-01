/**
 * Access Keys in the real panel (user, 2026-10-28): while no key is chosen the
 * key's windows are hidden in place and only the list shows; choosing a key
 * shows them, and the choice survives a trip to another workspace. Reset This
 * Workspace also hides the archived keys again, and an edit to the chosen key
 * shows in the list at once.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, fireEvent, waitFor } from "@testing-library/react";
import { setAgentCliOwner } from "../utils/agentcli_state";
import { WORKSPACES, windowAvailable } from "../shell/workspaces";

vi.mock("../phoenix-mcp-panel.css?inline", () => ({ default: "" }));
vi.mock("../shell/dock/dock.css?inline", () => ({ default: "" }));
vi.mock("dockview-react/dist/styles/dockview.css?inline", () => ({ default: "" }));

const { apiMock } = vi.hoisted(() => {
  const CAPS = [
    "cap_config_read", "cap_camera_read", "cap_template_render", "cap_log_read", "cap_log_control", "cap_search",
    "cap_registry_read", "cap_traces", "cap_diagnostics", "cap_broadcast",
    "cap_service_response", "cap_automation_write", "cap_script_write",
    "cap_scene_write", "cap_helper_write", "cap_physical_control", "cap_restart",
    "cap_integration_write", "cap_integration_reconfigure", "cap_lovelace_write", "cap_registry_write", "cap_radio_write",
    "cap_backup", "cap_filesystem", "cap_yaml_edit",
  ];
  const accessKey = {
    id: "tok-1", name: "shell-access-key", created_at: new Date().toISOString(),
    created_by: "admin", expires_at: null, revoked: false, last_used_at: null,
    updated_at: null, pass_through: false, announce_all_tools: false,
    confirm_inline_wait_seconds: 0,
    persona: "custom", rate_limit_requests: 60, rate_limit_burst: 10,
    permissions: { domains: {}, devices: {}, entities: {} },
    ...Object.fromEntries(CAPS.map((c) => [c, "deny"])),
  };
  const approval = {
    id: "ap-9", access_key_id: accessKey.id, access_key_name: accessKey.name,
    cap_name: "cap_restart", tool_name: "restart_ha", args: {},
    status: "pending", created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    request_id: "req-9", client_ip: "browser",
  };
  const settings = {
    kill_switch: false, disable_all_logging: false, log_allowed: true,
    log_denied: true, log_rate_limited: true, log_entity_names: true,
    log_client_ip: true, notify_on_rate_limit: false, notify_on_approval: true,
    audit_flush_interval: 15, audit_log_maxlen: 10000, mesa_mode: "advisory",
    agentcli_global: true,
  };
  const apiMock = {
    listAccessKeys: vi.fn(async () => [{ ...accessKey }]),
    getAccessKey: async () => ({ ...accessKey }),
    patchAccessKey: async (_id: string, body: Record<string, unknown>) => {
      Object.assign(accessKey, body, { updated_at: new Date().toISOString() });
      delete (accessKey as Record<string, unknown>).confirm_pass_through;
      return { ...accessKey };
    },
    getSettings: async () => settings,
    listApprovals: async () => ({ approvals: [], total: 3 }),
    getApproval: async () => approval,
    getEntityHints: async () => ({ entity_hints: {} }),
    // Agent Chat is on the toolbar only with a provider to talk to.
    getAgentCliProviders: async () => ({ instances: [{ id: "p1", kind: "anthropic", name: "Claude", model: "model-1" }], default_instance_id: "p1" }),
    // One branch, so the tree has a triangle to open.
    getEntityTree: async () => ({ sensor: { devices: {}, deviceless_entities: ["sensor.outdoor"],
      entity_details: { "sensor.outdoor": { entity_id: "sensor.outdoor", friendly_name: "Outdoor temperature", device_id: null, area_id: null, area_name: null, labels: [] } } } }),
    createAccessKey: vi.fn(),
    listArchivedAccessKeys: async () => [{ id: "arch-1", name: "old_agent", revoked: true, created_at: "2026-01-01T00:00:00Z", revoked_at: "2026-02-01T00:00:00Z", last_used_at: null }],
  };
  return { apiMock };
});

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: { ...actual.api, ...apiMock } };
});

await import("../index");



type Panel = HTMLElement & { hass: unknown };
const sr = (el: Panel) => el.shadowRoot!;
const frame = (el: Panel) => sr(el).querySelector<HTMLElement>(".phx-workspace-frame:not([hidden])");
/** A window's content in the workspace on screen. */
const visible = (el: Panel, id: string) => frame(el)?.querySelector(`[data-phx-window="${id}"]`) ?? null;
const toolbarButton = (el: Panel, title: string) => [...sr(el).querySelectorAll<HTMLButtonElement>(".phx-toolbar-button")].find((b) => b.getAttribute("title") === title)!;

function mountPanel(): Panel {
  const el = document.createElement("phoenix-mcp-panel") as Panel;
  document.body.appendChild(el);
  el.hass = { user: { id: "u1" } };
  return el;
}

beforeEach(() => {
  setAgentCliOwner("u1");
  try { localStorage.clear(); sessionStorage.clear(); } catch { /* ignore */ }
  window.history.replaceState(null, "", "/phoenix-mcp");
});
afterEach(() => {
  document.querySelectorAll("phoenix-mcp-panel").forEach((el) => el.remove());
  delete (window as unknown as Record<string, unknown>).__phxAgentChat;
});

/** Whether a window's place in the grid is shown: Dockview hides a view by dropping its "visible" class. */
const shown = (el: Panel, title: string) => {
  const group = [...frame(el)!.querySelectorAll<HTMLElement>(".dv-groupview")].find((g) => g.querySelector(".phx-window-title-text")?.textContent === title);
  return !!group?.closest(".dv-view")?.classList.contains("visible");
};
const keyRow = (el: Panel) => visible(el, "accessKeys")!.querySelector<HTMLElement>('tr[data-row="tok-1"]')!;

describe("Access Keys", () => {
  it("shows only the list until a key is chosen, and keeps the choice across workspaces", async () => {
    const el = mountPanel();
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    await waitFor(() => expect(shown(el, "Persona")).toBe(false));
    expect(shown(el, "Capabilities")).toBe(false);
    expect(shown(el, "Permissions Tree")).toBe(false);
    expect(shown(el, "Access Keys")).toBe(true);
    expect(shown(el, "Help")).toBe(true);
    act(() => { fireEvent.click(keyRow(el)); });
    await waitFor(() => expect(shown(el, "Persona")).toBe(true));
    expect(shown(el, "Permissions Tree")).toBe(true);
    expect(keyRow(el)).toHaveAttribute("aria-selected", "true");
    act(() => { toolbarButton(el, "Logs").click(); });
    await waitFor(() => expect(frame(el)).toHaveAttribute("data-workspace", "logs"));
    act(() => { toolbarButton(el, "Access Keys").click(); });
    await waitFor(() => expect(frame(el)).toHaveAttribute("data-workspace", "accessKeys"));
    expect(keyRow(el)).toHaveAttribute("aria-selected", "true");
    expect(shown(el, "Persona")).toBe(true);
  });

  it("gives the archived keys a floating window of their own, closed until Show Archived opens it (user, 2026-10-28)", async () => {
    const el = mountPanel();
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    const archivedTool = () => [...sr(el).querySelectorAll<HTMLButtonElement>(".phx-toolbar-tools button")].find((b) => /^(Show|Hide) Archived$/.test(b.getAttribute("aria-label") ?? ""));
    await waitFor(() => expect(archivedTool()).toBeDefined());
    // Closed by default, and the key list no longer carries the archived table.
    expect(visible(el, "archivedAccessKeys")).toBeNull();
    expect(visible(el, "accessKeys")!.querySelector(".access-keys-archived")).toBeNull();

    act(() => { archivedTool()!.click(); });
    await waitFor(() => expect(visible(el, "archivedAccessKeys")).not.toBeNull());
    expect(shown(el, "Archived Access Keys")).toBe(true);
    expect(archivedTool()).toHaveAttribute("aria-label", "Hide Archived");
    await waitFor(() => expect(visible(el, "archivedAccessKeys")!.textContent).toContain("old_agent"));

    // Closing it from its own title bar turns the tool off, and it opens again from there.
    const close = [...frame(el)!.querySelectorAll<HTMLElement>(".dv-groupview")]
      .find((g) => g.querySelector(".phx-window-title-text")?.textContent === "Archived Access Keys")!
      .querySelector<HTMLElement>('[aria-label="Close"]')!;
    act(() => { close.click(); });
    await waitFor(() => expect(visible(el, "archivedAccessKeys")).toBeNull());
    expect(archivedTool()).toHaveAttribute("aria-label", "Show Archived");
    act(() => { archivedTool()!.click(); });
    await waitFor(() => expect(visible(el, "archivedAccessKeys")).not.toBeNull());

    // It floats over the workspace rather than pushing the chosen key's windows down (user, 2026-10-31).
    const floating = [...frame(el)!.querySelectorAll<HTMLElement>(".phx-float-layer .phx-window-title-text")].map((t) => t.textContent);
    expect(floating).toContain("Archived Access Keys");
    const docked = [...frame(el)!.querySelectorAll<HTMLElement>(".phx-workspace .phx-window-title-text")].map((t) => t.textContent);
    expect(docked).not.toContain("Archived Access Keys");
  });

  it("keeps the archived window out of the phone stack and Workspace Options until Show Archived is on", () => {
    expect(windowAvailable("archivedAccessKeys", null, null)).toBe(false);
    expect(windowAvailable("archivedAccessKeys", null, null, true)).toBe(true);
    expect(WORKSPACES.accessKeys.toolWindows).toContain("archivedAccessKeys");
  });

  it("hides the archived keys again on Reset This Workspace", async () => {
    const el = mountPanel();
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    const archivedTool = () => [...sr(el).querySelectorAll<HTMLButtonElement>(".phx-toolbar-tools button")].find((b) => /^(Show|Hide) Archived$/.test(b.getAttribute("aria-label") ?? ""));
    await waitFor(() => expect(archivedTool()).toBeDefined());
    act(() => { archivedTool()!.click(); });
    await waitFor(() => expect(visible(el, "archivedAccessKeys")).not.toBeNull());
    act(() => { sr(el).querySelector<HTMLButtonElement>('[aria-label="Workspace Options"]')!.click(); });
    const reset = [...sr(el).querySelectorAll<HTMLElement>('.phx-menu [role^="menuitem"]')].find((i) => i.textContent?.trim() === "Reset This Workspace")!;
    act(() => { reset.click(); });
    await waitFor(() => expect(visible(el, "archivedAccessKeys")).toBeNull());
    expect(archivedTool()).toHaveAttribute("aria-label", "Show Archived");
  });

  it("leaves no key chosen on Reset This Workspace, so the tree starts folded again (user, 2026-10-31)", async () => {
    const el = mountPanel();
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    act(() => { fireEvent.click(keyRow(el)); });
    const branch = () => visible(el, "permissions")?.querySelector<HTMLButtonElement>("button.tree-expand") ?? null;
    await waitFor(() => expect(branch()).not.toBeNull());
    act(() => { branch()!.click(); });
    await waitFor(() => expect(branch()).toHaveAttribute("aria-expanded", "true"));
    act(() => { sr(el).querySelector<HTMLButtonElement>('[aria-label="Workspace Options"]')!.click(); });
    const reset = [...sr(el).querySelectorAll<HTMLElement>('.phx-menu [role^="menuitem"]')].find((i) => i.textContent?.trim() === "Reset This Workspace")!;
    act(() => { reset.click(); });
    await waitFor(() => expect(keyRow(el)).toHaveAttribute("aria-selected", "false"));
    await waitFor(() => expect(shown(el, "Persona")).toBe(false));
    act(() => { fireEvent.click(keyRow(el)); });
    await waitFor(() => expect(branch()).toHaveAttribute("aria-expanded", "false"));
  });

  it("renames a key from a double-click on its name (user, 2026-10-31)", async () => {
    const el = mountPanel();
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    const name = () => keyRow(el).querySelector<HTMLElement>(".access-key-name")!;
    act(() => { fireEvent.click(keyRow(el)); });
    act(() => { fireEvent.doubleClick(name()); });
    const field = await waitFor(() => {
      const input = keyRow(el).querySelector<HTMLInputElement>("input.access-key-name-input");
      expect(input).not.toBeNull();
      return input!;
    });
    expect(field.value).toBe("shell-access-key");
  });

  it("offers the hidden archived keys in Workspace Options, opening them as Show Archived does (user, 2026-10-31)", async () => {
    const el = mountPanel();
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    const archivedTool = () => [...sr(el).querySelectorAll<HTMLButtonElement>(".phx-toolbar-tools button")].find((b) => /^(Show|Hide) Archived$/.test(b.getAttribute("aria-label") ?? ""));
    await waitFor(() => expect(archivedTool()).toBeDefined());
    const entry = () => [...sr(el).querySelectorAll<HTMLElement>('.phx-menu [role^="menuitem"]')].find((i) => i.textContent?.trim() === "Show Archived Access Keys");
    act(() => { sr(el).querySelector<HTMLButtonElement>('[aria-label="Workspace Options"]')!.click(); });
    act(() => { entry()!.click(); });
    await waitFor(() => expect(visible(el, "archivedAccessKeys")).not.toBeNull());
    expect(archivedTool()).toHaveAttribute("aria-label", "Hide Archived");
    act(() => { sr(el).querySelector<HTMLButtonElement>('[aria-label="Workspace Options"]')!.click(); });
    expect(entry()).toBeUndefined();
  });

  it("opens and closes Create Access Key from its tool, and dismisses it on a click elsewhere (user, 2026-10-31)", async () => {
    const el = mountPanel();
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    const create = () => [...sr(el).querySelectorAll<HTMLButtonElement>(".phx-toolbar-tools button")].find((b) => b.getAttribute("aria-label") === "Create Access Key");
    await waitFor(() => expect(create()).toBeDefined());
    const form = () => visible(el, "accessKeys")!.querySelector("section.phx-inline-form");
    act(() => { create()!.click(); });
    await waitFor(() => expect(form()).not.toBeNull());
    await waitFor(() => expect(create()).toHaveAttribute("aria-pressed", "true"));
    // A click inside keeps it; the tool closes it and opens it again.
    act(() => { fireEvent.click(form()!.querySelector("input")!); });
    expect(form()).not.toBeNull();
    act(() => { create()!.click(); });
    await waitFor(() => expect(form()).toBeNull());
    await waitFor(() => expect(create()).toHaveAttribute("aria-pressed", "false"));
    act(() => { create()!.click(); });
    await waitFor(() => expect(form()).not.toBeNull());
    act(() => { fireEvent.click(keyRow(el)); });
    await waitFor(() => expect(form()).toBeNull());
  });

  it("keeps Create Access Key open from its tool while the key is being created (user, 2026-10-31)", async () => {
    let finish: (value: unknown) => void = () => {};
    apiMock.createAccessKey.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const el = mountPanel();
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    const create = () => [...sr(el).querySelectorAll<HTMLButtonElement>(".phx-toolbar-tools button")].find((b) => b.getAttribute("aria-label") === "Create Access Key");
    await waitFor(() => expect(create()).toBeDefined());
    const form = () => visible(el, "accessKeys")!.querySelector("section.phx-inline-form");
    act(() => { create()!.click(); });
    await waitFor(() => expect(form()).not.toBeNull());
    const name = form()!.querySelector<HTMLInputElement>("#access-key-name-input")!;
    act(() => { fireEvent.change(name, { target: { value: "new_agent" } }); });
    const submit = [...form()!.querySelectorAll<HTMLButtonElement>("button")].find((b) => (b.getAttribute("aria-label") ?? b.textContent?.trim()) === "Create")!;
    act(() => { fireEvent.click(submit); });
    await waitFor(() => expect(apiMock.createAccessKey).toHaveBeenCalled());
    act(() => { create()!.click(); });
    expect(form()).not.toBeNull();
    await act(async () => { finish({ ...{ id: "k9", name: "new_agent" }, access_key: "phx_" + "c".repeat(64) }); });
  });

  it("hides the archived keys when an active key is chosen (user, 2026-10-28)", async () => {
    const el = mountPanel();
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    const archivedTool = () => [...sr(el).querySelectorAll<HTMLButtonElement>(".phx-toolbar-tools button")].find((b) => /^(Show|Hide) Archived$/.test(b.getAttribute("aria-label") ?? ""));
    await waitFor(() => expect(archivedTool()).toBeDefined());
    act(() => { archivedTool()!.click(); });
    await waitFor(() => expect(visible(el, "archivedAccessKeys")).not.toBeNull());
    act(() => { fireEvent.click(keyRow(el)); });
    await waitFor(() => expect(visible(el, "archivedAccessKeys")).toBeNull());
    expect(keyRow(el)).toHaveAttribute("aria-selected", "true");
    expect(archivedTool()).toHaveAttribute("aria-label", "Show Archived");
  });

  it("shows Enable Pass-Through in the list's Mode at once, with no banner above the list", async () => {
    const el = mountPanel();
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    act(() => { fireEvent.click(keyRow(el)); });
    const keyActions = () => [...sr(el).querySelectorAll<HTMLButtonElement>(".phx-toolbar-tools button")].find((b) => b.getAttribute("aria-label") === "Key Actions");
    await waitFor(() => expect(keyActions()?.disabled).toBe(false));
    act(() => { keyActions()!.click(); });
    const item = [...sr(el).querySelectorAll<HTMLElement>('.phx-menu [role="menuitem"]')].find((i) => i.textContent?.trim() === "Enable Pass-Through")!;
    act(() => { item.click(); });
    const confirm = await waitFor(() => visible(el, "accessKeys")!.querySelector<HTMLElement>(".phx-inline-confirm")!);
    act(() => { fireEvent.click(confirm.querySelector<HTMLInputElement>('input[type="checkbox"]')!); });
    const reads = apiMock.listAccessKeys.mock.calls.length;
    act(() => { confirm.querySelector<HTMLButtonElement>(".phx-inline-confirm-actions button")!.click(); });
    await waitFor(() => expect(keyRow(el).textContent).toContain("Pass-Through"));
    expect(apiMock.listAccessKeys.mock.calls.length).toBeGreaterThan(reads);
    expect(visible(el, "accessKeys")!.querySelector(".pass-through-header-banner")).toBeNull();
  });
});
