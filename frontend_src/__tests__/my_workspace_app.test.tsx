/**
 * My Workspace in the real panel: Add to My Workspace from a window's menu,
 * the second copy beside the original, Remove and Close, the empty hint, and
 * Agent Chat docking, both the panel's own Chat and the global one through its
 * bridge. Chat floats while My Workspace is not shown and returns to its
 * window when it is.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, waitFor } from "@testing-library/react";
import { setAgentCliOwner } from "../utils/agentcli_state";

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
    listAccessKeys: async () => [accessKey],
    getSettings: async () => settings,
    listApprovals: async () => ({ approvals: [], total: 3 }),
    getApproval: async () => approval,
    getEntityHints: async () => ({ entity_hints: {} }),
    // Agent Chat is on the toolbar only with a provider to talk to.
    getAgentCliProviders: async () => ({ instances: [{ id: "p1", kind: "anthropic", name: "Claude", model: "model-1" }], default_instance_id: "p1" }),
    listArchivedAccessKeys: async () => [],
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
const menuItem = (el: Panel, label: string) => [...sr(el).querySelectorAll<HTMLElement>('.phx-menu [role^="menuitem"]')].find((i) => i.textContent?.replace("\u2713", "").trim() === label);
const toast = (el: Panel) => sr(el).querySelector(".phx-toast-text")?.textContent ?? null;
/** A window's title-bar control in the workspace on screen, found by the window's title. */
function titleControl(el: Panel, title: string, selector: string): HTMLElement {
  const group = [...frame(el)!.querySelectorAll<HTMLElement>(".dv-groupview")].find((g) => g.querySelector(".phx-window-title-text")?.textContent === title);
  return group!.querySelector<HTMLElement>(selector)!;
}
const openWindowMenu = (el: Panel, title: string) => act(() => { titleControl(el, title, "[data-phx-window-menu]").click(); });
const stored = () => JSON.parse(localStorage.getItem("phx-ui:u1:desktop") ?? "{}") as { myWindows?: string[] };

function mountPanel(): Panel {
  const el = document.createElement("phoenix-mcp-panel") as Panel;
  document.body.appendChild(el);
  el.hass = { user: { id: "u1" } };
  return el;
}
function seed(record: Record<string, unknown>) {
  localStorage.setItem("phx-ui:u1:desktop", JSON.stringify({ schema: 1, savedAt: 1, workspaces: {}, ...record }));
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

describe("My Workspace", () => {
  it("starts empty with a hint on how to add windows", async () => {
    seed({ lastWorkspace: "my" });
    const el = mountPanel();
    await waitFor(() => expect(frame(el)).toHaveAttribute("data-workspace", "my"));
    await waitFor(() => expect(frame(el)!.querySelector(".phx-workspace-empty")?.textContent ?? "").toContain("Add a window with Add to My Workspace in its window menu"));
    // Only Help is open; Notifications stays closed, as in every workspace.
    expect([...frame(el)!.querySelectorAll(".phx-window-title-text")].map((e) => e.textContent)).toEqual(["Help"]);
    expect(toolbarButton(el, "My Workspace")).toHaveAttribute("aria-current", "page");
  });

  it("adds a window from its menu, shows a second copy there, and saves the choice", async () => {
    const el = mountPanel();
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    openWindowMenu(el, "Access Keys");
    act(() => { menuItem(el, "Add to My Workspace")!.click(); });
    expect(toast(el)).toBe("Access Keys added to My Workspace");
    // Added once: the item is unavailable afterwards.
    openWindowMenu(el, "Access Keys");
    expect(menuItem(el, "Add to My Workspace")).toHaveAttribute("aria-disabled", "true");
    act(() => { toolbarButton(el, "My Workspace").click(); });
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    expect(frame(el)).toHaveAttribute("data-workspace", "my");
    expect(sr(el).querySelectorAll('[data-phx-window="accessKeys"]')).toHaveLength(2);
    await waitFor(() => expect(stored().myWindows).toEqual(["accessKeys"]), { timeout: 4000 });
  });

  it("hides a closed window until Workspace Options reopens it, and Remove takes it away", async () => {
    seed({ lastWorkspace: "my", myWindows: ["accessKeys"] });
    const el = mountPanel();
    await waitFor(() => expect(visible(el, "accessKeys")?.textContent).toContain("shell-access-key"));
    act(() => { titleControl(el, "Access Keys", '[aria-label="Close"]').click(); });
    await waitFor(() => expect(visible(el, "accessKeys")).toBeNull());
    act(() => { sr(el).querySelector<HTMLButtonElement>('[aria-label="Workspace Options"]')!.click(); });
    const reopen = [...sr(el).querySelectorAll<HTMLElement>('.phx-menu [role="menuitem"]')].find((i) => i.textContent?.includes("Access Keys"));
    expect(reopen).toBeTruthy();
    act(() => { reopen!.click(); });
    await waitFor(() => expect(visible(el, "accessKeys")).toBeTruthy());
    openWindowMenu(el, "Access Keys");
    act(() => { menuItem(el, "Remove from My Workspace")!.click(); });
    expect(toast(el)).toBe("Access Keys removed from My Workspace");
    await waitFor(() => expect(visible(el, "accessKeys")).toBeNull());
    act(() => { sr(el).querySelector<HTMLButtonElement>('[aria-label="Workspace Options"]')!.click(); });
    expect([...sr(el).querySelectorAll('.phx-menu [role="menuitem"]')].some((i) => i.textContent?.includes("Access Keys"))).toBe(false);
    await waitFor(() => expect(stored().myWindows).toEqual([]), { timeout: 4000 });
    // The original stays in Access Keys.
    act(() => { toolbarButton(el, "Access Keys").click(); });
    await waitFor(() => expect(visible(el, "accessKeys")).toBeTruthy());
  });
});

describe("Agent Chat in My Workspace", () => {
  const chat = (el: Panel) => sr(el).querySelector<HTMLElement>(".agentcli-window");
  const chatMenu = (el: Panel, label: string) => {
    act(() => { fireEvent.click(chat(el)!.querySelector<HTMLButtonElement>('[aria-label="Options"]')!); });
    act(() => { menuItem(el, label)!.click(); });
  };

  it("docks the panel's own Chat, floats it elsewhere, brings it back, and floats it for good", async () => {
    const el = mountPanel();
    await waitFor(() => expect(toolbarButton(el, "Agent Chat")).toBeTruthy());
    act(() => { toolbarButton(el, "Agent Chat").click(); });
    await waitFor(() => expect(chat(el)).toBeTruthy());
    fireEvent.change(chat(el)!.querySelector("textarea")!, { target: { value: "draft for later" } });
    chatMenu(el, "Add to My Workspace");
    expect(toast(el)).toBe("Agent Chat added to My Workspace");
    // Added, but My Workspace is not shown: Chat still floats here.
    expect(chat(el)).not.toHaveClass("agentcli-docked");

    act(() => { toolbarButton(el, "My Workspace").click(); });
    await waitFor(() => expect(visible(el, "agentChat")?.querySelector(".agentcli-window")).toHaveClass("agentcli-docked"));
    expect(sr(el).querySelectorAll(".agentcli-window")).toHaveLength(1);
    expect(chat(el)!.querySelector("textarea")).toHaveValue("draft for later");

    act(() => { toolbarButton(el, "Logs").click(); });
    await waitFor(() => expect(chat(el)).not.toHaveClass("agentcli-docked"));
    expect(chat(el)!.closest("[data-phx-window]")).toBeNull();

    act(() => { toolbarButton(el, "My Workspace").click(); });
    await waitFor(() => expect(chat(el)).toHaveClass("agentcli-docked"));

    chatMenu(el, "Float Agent Chat");
    await waitFor(() => expect(visible(el, "agentChat")).toBeNull());
    expect(chat(el)).not.toHaveClass("agentcli-docked");
    expect(chat(el)!.querySelector("textarea")).toHaveValue("draft for later");
    await waitFor(() => expect(stored().myWindows).toEqual([]), { timeout: 4000 });
  });

  it("closing its window in My Workspace closes Chat, and the window offers to open it again", async () => {
    seed({ lastWorkspace: "my", myWindows: ["agentChat"] });
    const el = mountPanel();
    await waitFor(() => expect(chat(el)).toHaveClass("agentcli-docked"));
    act(() => { titleControl(el, "Agent Chat", '[aria-label="Close"]').click(); });
    await waitFor(() => expect(chat(el)).toBeNull());
    act(() => { sr(el).querySelector<HTMLButtonElement>('[aria-label="Workspace Options"]')!.click(); });
    const reopen = [...sr(el).querySelectorAll<HTMLElement>('.phx-menu [role="menuitem"]')].find((i) => i.textContent?.includes("Agent Chat"));
    act(() => { reopen!.click(); });
    // Reopened: the window shows Chat again.
    await waitFor(() => expect(chat(el)).toHaveClass("agentcli-docked"));
  });

  it("offers Chat no slot while the kill switch is on", async () => {
    const live = await apiMock.getSettings() as { kill_switch: boolean };
    live.kill_switch = true;
    try {
      const setDock = vi.fn();
      (window as unknown as Record<string, unknown>).__phxAgentChat = { ready: true, isVisible: () => false, open: vi.fn(), restore: vi.fn(), close: vi.fn(), setDock };
      seed({ lastWorkspace: "my", myWindows: ["agentChat"] });
      const el = mountPanel();
      await waitFor(() => expect(visible(el, "agentChat")?.textContent).toContain("Agent Chat is closed."));
      await waitFor(() => expect(setDock).toHaveBeenCalled());
      expect(setDock.mock.calls.every(([dock]) => dock === null || dock.slot === null)).toBe(true);
      expect(chat(el)).toBeNull();
    } finally {
      live.kill_switch = false;
    }
  });

  it("hands the slot to the global Chat through its bridge, and takes it back when My Workspace is left", async () => {
    const setDock = vi.fn();
    const close = vi.fn();
    (window as unknown as Record<string, unknown>).__phxAgentChat = { ready: true, isVisible: () => false, open: vi.fn(), restore: vi.fn(), close, setDock };
    seed({ lastWorkspace: "my", myWindows: ["agentChat"] });
    const el = mountPanel();
    await waitFor(() => expect(setDock).toHaveBeenLastCalledWith(expect.objectContaining({ docked: true, slot: expect.any(HTMLElement) })));
    // The global Chat draws into the slot; the panel renders no Chat of its own.
    expect(chat(el)).toBeNull();
    const slot = setDock.mock.lastCall![0].slot as HTMLElement;
    expect(slot.closest('[data-phx-window="agentChat"]')).toBeTruthy();

    act(() => { toolbarButton(el, "Logs").click(); });
    await waitFor(() => expect(setDock).toHaveBeenLastCalledWith(expect.objectContaining({ docked: true, slot: null })));

    act(() => { toolbarButton(el, "My Workspace").click(); });
    await waitFor(() => expect(setDock.mock.lastCall![0].slot).toBeInstanceOf(HTMLElement));
    act(() => { titleControl(el, "Agent Chat", '[aria-label="Close"]').click(); });
    expect(close).toHaveBeenCalled();
    // The toggle the bridge passes to Chat's menu floats it for good.
    act(() => { (setDock.mock.lastCall![0].toggle as () => void)(); });
    await waitFor(() => expect(setDock).toHaveBeenLastCalledWith(expect.objectContaining({ docked: false, slot: null })));
    el.remove();
    expect(setDock).toHaveBeenLastCalledWith(null);
  });
});
