import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, waitFor } from "@testing-library/react";
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
      expect(el.shadowRoot!.querySelector('[data-phx-window="review"] .phx-review')).toBeTruthy();
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
      expect(el.shadowRoot!.querySelector('[data-phx-window="review"] .phx-review')).toBeTruthy();
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
      // Icons and text at the top: the fit rule runs, and each main-row control says when it gives up its text.
      await waitFor(() => expect(el.shadowRoot!.querySelector(".phx-toolbar")).toHaveAttribute("data-fit", "0"));
      const main = el.shadowRoot!.querySelector(".phx-toolbar-main")!;
      expect(main.querySelector('[aria-label="Workspace Options"]')).toHaveClass("phx-fit-1");
      expect(main.querySelector('[aria-label="Help"]')).toHaveClass("phx-fit-2");
      expect([...main.querySelectorAll(".phx-toolbar-nav button")].every((b) => b.classList.contains("phx-fit-4"))).toBe(true);
      // The toolbar options and the tools toggle are icons only and never part of the fit.
      expect(main.querySelector('[aria-label="Toolbar Options"]')?.className).not.toMatch(/phx-fit-/);
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

    it("Reset All Workspaces resets the toolbar at once and keeps My Defaults", async () => {
      const toolbar = { dock: "left", x: 16, y: 12, collapsed: false, toolsShown: true, mode: "icons" };
      const saved = { schema: 1, savedAt: 1, rev: 2, toolbar, workspaces: {}, myDefaults: { logs: { dock: {}, folds: { folded: [], collapsed: {} }, closed: {} } } };
      const { connection, sets } = userData({ "phoenix_mcp_ui:desktop": saved });
      const el = mountWith(connection);
      await waitFor(() => expect(el.shadowRoot!.querySelector(".phx-toolbar")).toHaveAttribute("data-dock", "left"));
      // At a side the toolbar does not need the fit rule.
      expect(el.shadowRoot!.querySelector(".phx-toolbar")).not.toHaveAttribute("data-fit");
      openOptions(el);
      act(() => { menuItem(el, "Reset All Workspaces")!.click(); });
      expect(el.shadowRoot!.querySelector('[role="alertdialog"]')).toBeNull();
      await waitFor(() => expect(el.shadowRoot!.querySelector(".phx-toolbar")).toHaveAttribute("data-dock", "top"));
      expect(el.shadowRoot!.querySelector(".phx-toolbar")).toHaveAttribute("data-mode", "icon-text");
      // Back across the top with its text, the fit rule applies again.
      expect(el.shadowRoot!.querySelector(".phx-toolbar")).toHaveAttribute("data-fit");
      await waitFor(() => expect(sets.length).toBeGreaterThan(0), { timeout: 4000 });
      const last = sets[sets.length - 1].value;
      expect((last.toolbar as { dock: string }).dock).toBe("top");
      expect(last.myDefaults).toEqual(saved.myDefaults);
    });

    it("moves a floating toolbar back inside the panel on load, so it is drawn in full", async () => {
      // jsdom has no layout: the panel is 1200 by 900 and the toolbar 300 by 100.
      const rect = vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
        const box = (w: number, h: number) => ({ x: 0, y: 0, left: 0, top: 0, width: w, height: h, right: w, bottom: h, toJSON: () => ({}) }) as DOMRect;
        if (this.classList.contains("phx-shell")) return box(1200, 900);
        if (this.classList.contains("phx-toolbar")) return box(300, 100);
        return box(0, 0);
      });
      try {
        const toolbar = { dock: "floating", x: 1150, y: 880, collapsed: false, toolsShown: true, mode: "icons", vertical: true };
        const { connection } = userData({ "phoenix_mcp_ui:desktop": { schema: 1, savedAt: 1, rev: 2, toolbar, workspaces: {} } });
        const el = mountWith(connection);
        const bar = () => el.shadowRoot!.querySelector<HTMLElement>(".phx-toolbar");
        await waitFor(() => expect(bar()).toHaveAttribute("data-dock", "floating"));
        // 8 px from the right and bottom edges: moved left and up, never shrunk.
        await waitFor(() => expect(bar()!.style.left).toBe("892px"));
        expect(bar()!.style.top).toBe("792px");
      } finally {
        rect.mockRestore();
      }
    });

    it("offers the toolbar menu from its own button and the grip; orientation only while floating; collapsed, only the grip", async () => {
      const { connection } = userData();
      const el = mountWith(connection);
      const bar = () => el.shadowRoot!.querySelector(".phx-toolbar")!;
      await waitFor(() => expect(bar()).toHaveAttribute("data-orient", "horizontal"));
      const items = () => [...el.shadowRoot!.querySelectorAll<HTMLElement>('.phx-menu [role^="menuitem"]')].map((i) => i.textContent!.replace("\u2713", "").trim());
      const pick = (label: string) => act(() => { [...el.shadowRoot!.querySelectorAll<HTMLElement>('.phx-menu [role^="menuitem"]')].find((i) => i.textContent!.replace("\u2713", "").trim() === label)!.click(); });
      const options = () => act(() => { el.shadowRoot!.querySelector<HTMLButtonElement>('.phx-toolbar-main [aria-label="Toolbar Options"]')!.click(); });
      const grip = () => act(() => { el.shadowRoot!.querySelector<HTMLButtonElement>('.phx-toolbar > [aria-label="Move Toolbar"]')!.click(); });
      // The grip sits outside the rows, beside both of them.
      expect(el.shadowRoot!.querySelector(".phx-toolbar-main [aria-label='Move Toolbar']")).toBeNull();
      // No separate collapse button: Collapse lives in the menu. Docked, no orientation choice.
      expect(el.shadowRoot!.querySelector('[aria-label="Collapse Toolbar"]')).toBeNull();
      options();
      expect(items()).toEqual(expect.arrayContaining(["Icons Only", "Icons and Text", "Dock at Left", "Float Toolbar", "Collapse Toolbar"]));
      expect(items()).not.toContain("Vertical");
      expect(items()).not.toContain("Horizontal");
      pick("Dock at Left");
      await waitFor(() => expect(bar()).toHaveAttribute("data-orient", "vertical"));
      options();
      expect(items()).not.toContain("Horizontal");
      pick("Float Toolbar");
      await waitFor(() => expect(bar()).toHaveAttribute("data-dock", "floating"));
      expect(bar()).toHaveAttribute("data-orient", "vertical");
      // Floating: the other orientation is offered, and Float Toolbar is not.
      options();
      expect(items()).toContain("Horizontal");
      expect(items()).not.toContain("Vertical");
      expect(items()).not.toContain("Float Toolbar");
      pick("Horizontal");
      await waitFor(() => expect(bar()).toHaveAttribute("data-orient", "horizontal"));
      expect(bar()).toHaveAttribute("data-dock", "floating");
      options();
      pick("Collapse Toolbar");
      await waitFor(() => expect(bar()).toHaveAttribute("data-collapsed"));
      // Collapsed, only the grip remains, and it holds Expand.
      expect(el.shadowRoot!.querySelector('[aria-label="Toolbar Options"]')).toBeNull();
      expect(el.shadowRoot!.querySelector(".phx-toolbar-body")).toBeNull();
      grip();
      expect(items()).toContain("Expand Toolbar");
    });

    it("puts the tools on the main row from its menu, back on two rows while they do not fit (user, 2026-11-01)", async () => {
      const { connection } = userData();
      const el = mountWith(connection);
      const bar = () => el.shadowRoot!.querySelector<HTMLElement>(".phx-toolbar")!;
      await waitFor(() => expect(el.shadowRoot!.querySelector(".phx-toolbar-tools")).not.toBeNull());
      // The tools always show: there is no button to hide them.
      expect(el.shadowRoot!.querySelector('[aria-label="Hide Tools"], [aria-label="Show Tools"]')).toBeNull();
      const single = () => [...el.shadowRoot!.querySelectorAll<HTMLElement>('.phx-menu [role="menuitemcheckbox"]')].find((i) => i.textContent!.replace("\u2713", "").trim() === "Single Row")!;
      const items = () => [...el.shadowRoot!.querySelectorAll<HTMLElement>('.phx-menu [role^="menuitem"]')].map((i) => i.textContent!.replace("\u2713", "").trim());
      const options = () => act(() => { el.shadowRoot!.querySelector<HTMLButtonElement>('.phx-toolbar-main [aria-label="Toolbar Options"]')!.click(); });
      expect(bar()).not.toHaveAttribute("data-rows");
      options();
      expect(single()).toHaveAttribute("aria-checked", "false");
      act(() => single().click());
      await waitFor(() => expect(bar()).toHaveAttribute("data-rows", "1"));
      options();
      expect(single()).toHaveAttribute("aria-checked", "true");
      act(() => single().click());
      await waitFor(() => expect(bar()).not.toHaveAttribute("data-rows"));
      // Too wide for one row: two rows until there is room, still checked.
      const body = el.shadowRoot!.querySelector<HTMLElement>(".phx-toolbar-body")!;
      Object.defineProperty(body, "scrollWidth", { configurable: true, value: 2000 });
      Object.defineProperty(body, "clientWidth", { configurable: true, value: 800 });
      try {
        options();
        act(() => single().click());
        options();
        expect(single()).toHaveAttribute("aria-checked", "true");
        expect(bar()).not.toHaveAttribute("data-rows");
        act(() => { fireEvent.keyDown(el.shadowRoot!.querySelector(".phx-menu")!, { key: "Escape" }); });
      } finally {
        delete (body as unknown as Record<string, unknown>).scrollWidth;
        delete (body as unknown as Record<string, unknown>).clientWidth;
      }
      // The Help menu holds Quick Help and the Guide; the Guide is the manual, so the website is gone.
      act(() => { el.shadowRoot!.querySelector<HTMLButtonElement>('.phx-toolbar [aria-label="Help"]')!.click(); });
      expect(items()).toEqual(["Quick Help", "Guide"]);
    });
  });
});
