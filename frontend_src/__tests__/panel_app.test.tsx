import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, waitFor } from "@testing-library/react";
import { setAgentCliOwner, patchDurable as patchAgentCliDurable } from "../utils/agentcli_state";

// Lets the REAL PhoenixApp tree render (with a mocked api) so the app-level
// effects are covered: access key loading propagating into the list, the
// pending-approval count badge, and #approvals deep-linking. The custom-element
// shell is covered separately, with react-dom/client stubbed.

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
    getAgentCliProviders: async () => ({ instances: [], default_instance_id: null }),
    listArchivedAccessKeys: async () => [],
  };
  return { apiMock };
});

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: { ...actual.api, ...apiMock } };
});

await import("../index");

function mountPanel(): HTMLElement & { hass: unknown; narrow: boolean } {
  const el = document.createElement("phoenix-mcp-panel") as HTMLElement & { hass: unknown; narrow: boolean };
  document.body.appendChild(el);
  el.hass = { user: { id: "u1" } };  // render is a no-op until hass is set
  return el;
}

describe("phoenix-mcp-panel full app shell", () => {
  beforeEach(() => {
    setAgentCliOwner("u1");
    try { localStorage.clear(); } catch { /* ignore */ }
    window.history.replaceState(null, "", "/phoenix-mcp");
  });

  afterEach(() => {
    document.querySelectorAll("phoenix-mcp-panel").forEach((el) => el.remove());
    delete (window as unknown as Record<string, unknown>).__phxAgentChat;
  });

  it("loads access keys through the real tree and shows them in the list", async () => {
    const el = mountPanel();
    await waitFor(() => {
      expect(el.shadowRoot!.textContent).toContain("shell-access-key");
    });
  });

  it("renders the pending-approval count badge from listApprovals", async () => {
    const el = mountPanel();
    await waitFor(() => {
      const badge = el.shadowRoot!.querySelector(".phx-toolbar-badge");
      expect(badge?.textContent).toBe("3");
    });
    const approvals = [...el.shadowRoot!.querySelectorAll(".phx-toolbar-button")].find((b) => b.textContent?.startsWith("Approvals"));
    expect(approvals).toHaveAttribute("aria-label", "Approvals (3 pending)");
  });

  it("puts Home Assistant's menu button in the toolbar on narrow screens", async () => {
    const el = mountPanel();
    act(() => { el.narrow = true; });
    await waitFor(() => {
      const toolbar = el.shadowRoot!.querySelector(".phx-toolbar-main");
      expect(toolbar?.firstElementChild?.tagName).toBe("HA-MENU-BUTTON");
    });
  });

  it("opens the Access Keys workspace with its window by default", async () => {
    const el = mountPanel();
    await waitFor(() => {
      expect(el.shadowRoot!.querySelector(".phx-toolbar-button.is-current")?.textContent).toBe("Access Keys");
      expect(el.shadowRoot!.querySelector('[data-phx-window="accessKeys"]')).toBeTruthy();
    });
  });

  it("resumes the workspace saved for this user, and maps the old Logs tabs onto Logs", async () => {
    localStorage.setItem("phx-tab", "conversations");
    const el = mountPanel();
    await waitFor(() => {
      expect(el.shadowRoot!.querySelector(".phx-toolbar-button.is-current")?.textContent).toBe("Logs");
    });
  });

  it("switches workspaces from the toolbar and keeps the previous one mounted", async () => {
    const el = mountPanel();
    await waitFor(() => expect(el.shadowRoot!.querySelector('[data-phx-window="accessKeys"]')).toBeTruthy());
    const logs = [...el.shadowRoot!.querySelectorAll<HTMLButtonElement>(".phx-toolbar-button")].find((b) => b.textContent === "Logs")!;
    act(() => { logs.click(); });
    await waitFor(() => {
      expect(el.shadowRoot!.querySelector('[data-phx-window="auditLog"]')).toBeTruthy();
      expect(el.shadowRoot!.querySelector('[data-phx-window="conversationLog"]')).toBeTruthy();
    });
    // The Access Keys workspace is hidden, not unmounted, so its drafts survive.
    expect(el.shadowRoot!.querySelector('[data-workspace="accessKeys"]')).toHaveAttribute("hidden");
    expect(el.shadowRoot!.querySelector('[data-phx-window="accessKeys"]')).toBeTruthy();
  });

  it("deep-links to the Approvals workspace from the #approvals hash", async () => {
    window.location.hash = "#approvals";
    const el = mountPanel();
    await waitFor(() => {
      expect(el.shadowRoot!.querySelector(".phx-toolbar-button.is-current")?.textContent).toMatch(/^Approvals/);
      expect(el.shadowRoot!.querySelector('[data-phx-window="approvals"]')).toBeTruthy();
    });
  });

  it("keeps legacy approval hash links opening their specific record", async () => {
    window.location.hash = "#approvals/ap-9";
    const el = mountPanel();
    await waitFor(() => {
      expect(el.shadowRoot!.querySelector("[role='dialog']")).toBeTruthy();
    });
  });

  it("does not re-summon an already-visible global chat when the panel mounts", async () => {
    const open = vi.fn();
    (window as unknown as Record<string, unknown>).__phxAgentChat = {
      ready: true,
      isVisible: () => true,
      open,
    };
    patchAgentCliDurable({ open: true });
    // Home Assistant routes custom-panel subpaths through the panel's route.
    // The Phoenix element mounts only after that navigation has completed.
    window.history.replaceState(null, "", "/phoenix-mcp/approvals/ap-9");

    const el = mountPanel();
    await waitFor(() => {
      expect(el.shadowRoot!.querySelector("[role='dialog']")).toBeTruthy();
    });
    expect(open).not.toHaveBeenCalled();
  });

  it("restores rather than re-centers a hidden global chat after a reload", async () => {
    const open = vi.fn();
    const restore = vi.fn();
    (window as unknown as Record<string, unknown>).__phxAgentChat = {
      ready: true,
      isVisible: () => false,
      open,
      restore,
    };
    patchAgentCliDurable({ open: true, pos: { x: 123, y: 234 } });

    mountPanel();
    await waitFor(() => expect(restore).toHaveBeenCalledOnce());
    expect(open).not.toHaveBeenCalled();
  });

  describe("arrangements in Home Assistant's per-user storage", () => {
    /** A fake connection holding one stored value per key, like frontend/*_user_data. */
    function userData(initial: Record<string, unknown> = {}) {
      const values: Record<string, unknown> = { ...initial };
      const sets: Array<{ key: string; value: Record<string, unknown> }> = [];
      const connection = {
        sendMessagePromise: vi.fn(async (msg: { type: string; key: string; value?: unknown }) => {
          if (msg.type === "frontend/set_user_data") { values[msg.key] = msg.value; sets.push({ key: msg.key, value: msg.value as Record<string, unknown> }); }
          return msg.type === "frontend/get_user_data" ? { value: values[msg.key] } : undefined;
        }),
        subscribeMessage: vi.fn(async (cb: (e: { value: unknown }) => void, msg: { key: string }) => { cb({ value: values[msg.key] }); return () => undefined; }),
      };
      return { connection, sets };
    }
    function mountWith(connection: unknown) {
      const el = document.createElement("phoenix-mcp-panel") as HTMLElement & { hass: unknown };
      document.body.appendChild(el);
      el.hass = { user: { id: "u1" }, connection };
      return el;
    }
    const menuItem = (el: HTMLElement, label: string) => [...el.shadowRoot!.querySelectorAll<HTMLElement>('.phx-menu [role="menuitem"]')].find((i) => i.textContent === label);
    const openOptions = (el: HTMLElement) => act(() => { el.shadowRoot!.querySelector<HTMLButtonElement>('[aria-label="Workspace Options"]')!.click(); });

    it("adopts a newer arrangement saved from another computer", async () => {
      const toolbar = { dock: "top", x: 16, y: 12, collapsed: false, toolsShown: true, mode: "icon-text" };
      const { connection } = userData({ "phoenix_mcp_ui:desktop": { schema: 1, savedAt: 1, rev: 4, writer: "other", toolbar, workspaces: {} } });
      const el = mountWith(connection);
      await waitFor(() => expect(el.shadowRoot!.querySelector(".phx-toolbar")).toHaveAttribute("data-mode", "icon-text"));
      expect(JSON.parse(localStorage.getItem("phx-ui:u1:desktop")!).rev).toBe(4);
    });

    it("sends this browser's arrangement when Home Assistant has none yet", async () => {
      const { connection, sets } = userData();
      mountWith(connection);
      await waitFor(() => expect(sets.length).toBeGreaterThan(0), { timeout: 4000 });
      expect(sets[0].key).toBe("phoenix_mcp_ui:desktop");
      expect(sets[0].value.rev).toBe(1);
    });

    it("saves My Default to Home Assistant at once, and offers Restore My Default only afterwards", async () => {
      const { connection, sets } = userData();
      const el = mountWith(connection);
      await waitFor(() => expect(el.shadowRoot!.querySelector('[data-phx-window="accessKeys"]')).toBeTruthy());
      openOptions(el);
      expect(menuItem(el, "Restore My Default")).toHaveAttribute("aria-disabled", "true");
      act(() => { menuItem(el, "Save as My Default")!.click(); });
      await waitFor(() => expect(sets.some((s) => (s.value.myDefaults as Record<string, unknown> | undefined)?.accessKeys)).toBe(true));
      expect(el.shadowRoot!.querySelector(".phx-toast-text")?.textContent).toBe("Saved this arrangement as your default for Access Keys.");
      openOptions(el);
      expect(menuItem(el, "Restore My Default")).not.toHaveAttribute("aria-disabled");
    });

    it("Restore Panel Default Settings asks first, then resets the toolbar and keeps My Defaults", async () => {
      const toolbar = { dock: "left", x: 16, y: 12, collapsed: false, toolsShown: true, mode: "icon-text" };
      const saved = { schema: 1, savedAt: 1, rev: 2, toolbar, workspaces: {}, myDefaults: { logs: { dock: {}, folds: { folded: [], collapsed: {} }, closed: {} } } };
      const { connection, sets } = userData({ "phoenix_mcp_ui:desktop": saved });
      const el = mountWith(connection);
      await waitFor(() => expect(el.shadowRoot!.querySelector(".phx-toolbar")).toHaveAttribute("data-dock", "left"));
      openOptions(el);
      act(() => { menuItem(el, "Restore Panel Default Settings")!.click(); });
      const confirm = el.shadowRoot!.querySelector(".phx-shell-confirm")!;
      expect(confirm).toHaveAttribute("role", "alertdialog");
      expect(el.shadowRoot!.querySelector(".phx-toolbar")).toHaveAttribute("data-dock", "left");
      act(() => { [...confirm.querySelectorAll("button")].find((b) => b.textContent === "Restore Defaults")!.click(); });
      await waitFor(() => expect(el.shadowRoot!.querySelector(".phx-toolbar")).toHaveAttribute("data-dock", "top"));
      expect(el.shadowRoot!.querySelector(".phx-toolbar")).toHaveAttribute("data-mode", "icons");
      await waitFor(() => expect(sets.length).toBeGreaterThan(0), { timeout: 4000 });
      const last = sets[sets.length - 1].value;
      expect((last.toolbar as { dock: string }).dock).toBe("top");
      expect(last.myDefaults).toEqual(saved.myDefaults);
    });
  });
});
