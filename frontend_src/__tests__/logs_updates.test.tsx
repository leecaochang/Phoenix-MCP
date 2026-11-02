/**
 * The logs update themselves: Audit Log reads its newest page every five
 * seconds while visible (adding only unseen entries above further loaded
 * pages), both logs stop while Pause Updates is on and their window is hidden,
 * Refresh is offered only while paused, and one shared Pause Updates and
 * Log Settings serve both logs. Log Settings floats the workspace's Logs and
 * Notifications window and closes it again.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { api } from "../api";
import { ToolRegistry, ToolRegistryContext, WindowPlaceContext } from "../shell/tools";
import { ControllerContext } from "../shell/dock/TitleBar";
import type { WorkspaceController } from "../shell/dock/controller";
import { WindowVisibleContext } from "../shell/visibility";
import type { AuditEntry, ConversationListResponse } from "../types";
import { AuditView } from "../views/AuditView";
import { AuditEntryDetail } from "../components/AuditEntry";
import { ConversationsView } from "../views/ConversationsView";

vi.mock("../api", () => ({ api: { getAudit: vi.fn(), getConversations: vi.fn() } }));

function entry(n: number): AuditEntry {
  return {
    request_id: `r${n}`, timestamp: new Date(Date.UTC(2026, 0, 1, 0, 0, n)).toISOString(), access_key_id: "t1", access_key_name: "key",
    method: "tools/call", resource: `res-${n}`, outcome: "allowed", client_ip: "127.0.0.1", pass_through: false,
  };
}
const list = (): ConversationListResponse => ({ entries: [], total: 0, enabled: true, available: true, bytes: 0 });

function inLogs(children: ReactNode, registry: ToolRegistry, window: string, visible = true) {
  return (
    <ToolRegistryContext.Provider value={registry}>
      <WindowPlaceContext.Provider value={{ workspace: "logs", window }}>
        <WindowVisibleContext.Provider value={visible}>{children}</WindowVisibleContext.Provider>
      </WindowPlaceContext.Provider>
    </ToolRegistryContext.Provider>
  );
}

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
const wait = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getConversations).mockImplementation(async () => list());
});
afterEach(() => { vi.useRealTimers(); });

describe("Audit Log updates", () => {
  it("reads new entries every five seconds, not while paused, and Refresh still works", async () => {
    vi.useFakeTimers();
    vi.mocked(api.getAudit).mockResolvedValue({ entries: [entry(1)], total: 1 });
    const registry = new ToolRegistry();
    render(inLogs(<AuditView accessKeys={[]} />, registry, "auditLog"));
    await flush();
    expect(api.getAudit).toHaveBeenCalledTimes(1);

    vi.mocked(api.getAudit).mockResolvedValue({ entries: [entry(2), entry(1)], total: 2 });
    await wait(5000);
    expect(api.getAudit).toHaveBeenCalledTimes(2);
    expect(api.getAudit).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 100, offset: 0 }));
    expect(screen.getByText("res-2")).toBeInTheDocument();

    const pause = () => registry.latest("logs", "auditLog", "pause")!;
    expect(pause()).toMatchObject({ label: "Pause Updates", pressed: false });
    // Updating by itself, the log has no Refresh to offer.
    expect(registry.groupsFor("logs", ["auditLog"]).flatMap((g) => g.tools).some((t) => t.id === "refresh")).toBe(false);
    act(() => pause().onSelect!());
    expect(pause().pressed).toBe(true);
    await wait(20000);
    expect(api.getAudit).toHaveBeenCalledTimes(2);

    act(() => registry.latest("logs", "auditLog", "refresh")!.onSelect!());
    await flush();
    expect(api.getAudit).toHaveBeenCalledTimes(3);

    act(() => pause().onSelect!());
    await wait(5000);
    expect(api.getAudit).toHaveBeenCalledTimes(4);
  });

  it("says in an Access column, not a badge, whether a request was checked against permissions", async () => {
    vi.mocked(api.getAudit).mockResolvedValue({ entries: [{ ...entry(1), pass_through: true }, entry(2)], total: 2 });
    const { container } = render(inLogs(<AuditView accessKeys={[]} />, new ToolRegistry(), "auditLog"));
    await waitFor(() => expect(container.querySelectorAll("tbody tr[data-row]")).toHaveLength(2));
    const header = [...container.querySelectorAll("thead th")].map((th) => th.textContent);
    const at = header.findIndex((h) => h?.startsWith("Access") && !h.startsWith("Access key"));
    expect(at).toBeGreaterThan(-1);
    const access = [...container.querySelectorAll("tbody tr[data-row]")].map((r) => r.querySelectorAll("td")[at]?.textContent);
    // Newest first: entry 2 uses permissions, entry 1 is pass-through.
    expect(access).toEqual(["Permissions", "Pass-Through"]);
    expect(container.querySelector(".outcome-badge")).toBeNull();
    const detail = render(<AuditEntryDetail entry={{ ...entry(1), pass_through: true }} accessKeyName="key" />);
    const field = [...detail.container.querySelectorAll(".phx-log-field")].find((f) => f.querySelector("dt")?.textContent === "Access");
    expect(field?.querySelector("dd")?.textContent).toBe("Pass-Through");
  });

  it("does not update while its window is hidden", async () => {
    vi.useFakeTimers();
    vi.mocked(api.getAudit).mockResolvedValue({ entries: [entry(1)], total: 1 });
    render(inLogs(<AuditView accessKeys={[]} />, new ToolRegistry(), "auditLog", false));
    await flush();
    await wait(20000);
    expect(api.getAudit).toHaveBeenCalledTimes(1);
  });

  it("adds only unseen entries at the top when more pages are loaded", async () => {
    vi.useFakeTimers();
    const first = Array.from({ length: 100 }, (_, i) => entry(200 - i));
    const second = Array.from({ length: 50 }, (_, i) => entry(100 - i));
    vi.mocked(api.getAudit).mockResolvedValueOnce({ entries: first, total: 150 }).mockResolvedValueOnce({ entries: second, total: 150 });
    const { container } = render(<AuditView accessKeys={[]} />);
    await flush();
    act(() => screen.getByRole("button", { name: "Load More" }).click());
    await flush();
    const resource = () => [...container.querySelectorAll("thead th")].findIndex((th) => th.textContent?.startsWith("Resource"));
    const rows = () => [...container.querySelectorAll("tbody tr[data-row]")].map((r) => r.querySelectorAll("td")[resource()]?.textContent);
    expect(rows()).toHaveLength(150);

    vi.mocked(api.getAudit).mockResolvedValue({ entries: [entry(201), ...first.slice(0, 99)], total: 151 });
    await wait(5000);
    const shown = rows();
    expect(shown).toHaveLength(151);
    expect(shown[0]).toBe("res-201");
    expect(shown[150]).toBe("res-51");
    expect(new Set(shown).size).toBe(151);
  });
});

describe("Conversation Log updates", () => {
  it("stops refreshing while paused", async () => {
    vi.useFakeTimers();
    const registry = new ToolRegistry();
    render(inLogs(<ConversationsView />, registry, "conversationLog"));
    await flush();
    const before = vi.mocked(api.getConversations).mock.calls.length;
    await wait(5000);
    expect(vi.mocked(api.getConversations).mock.calls.length).toBe(before + 1);
    act(() => registry.latest("logs", "conversationLog", "pause")!.onSelect!());
    await wait(20000);
    expect(vi.mocked(api.getConversations).mock.calls.length).toBe(before + 1);
  });
});

describe("tools shared by both logs", () => {
  function both(registry: ToolRegistry, controller: WorkspaceController | null = null) {
    const logs = (
      <>
        <WindowPlaceContext.Provider value={{ workspace: "logs", window: "auditLog" }}><AuditView accessKeys={[]} /></WindowPlaceContext.Provider>
        <WindowPlaceContext.Provider value={{ workspace: "logs", window: "conversationLog" }}><ConversationsView /></WindowPlaceContext.Provider>
      </>
    );
    return render(
      <ToolRegistryContext.Provider value={registry}>
        <ControllerContext.Provider value={controller}>{logs}</ControllerContext.Provider>
      </ToolRegistryContext.Provider>,
    );
  }
  const order = ["auditLog", "conversationLog"];

  it("shows one Pause Updates for both logs and pauses both", async () => {
    vi.mocked(api.getAudit).mockResolvedValue({ entries: [], total: 0 });
    const registry = new ToolRegistry();
    both(registry);
    await waitFor(() => expect(registry.groupsFor("logs", order)[0]?.tools.map((t) => t.id)).toContain("pause"));
    expect(registry.groupsFor("logs", order).flatMap((g) => g.tools).filter((t) => t.id === "pause")).toHaveLength(1);
    act(() => registry.runShared("logs", order, "pause"));
    await waitFor(() => expect(registry.latest("logs", "auditLog", "pause")!.pressed).toBe(true));
    expect(registry.latest("logs", "conversationLog", "pause")!.pressed).toBe(true);
    // Log Settings needs a workspace holding the settings window; without one the logs do not offer it.
    expect(registry.groupsFor("logs", order).flatMap((g) => g.tools).some((t) => t.id === "logSettings")).toBe(false);
  });

  it("floats Logs and Notifications from Log Settings, and closes it again", async () => {
    vi.mocked(api.getAudit).mockResolvedValue({ entries: [], total: 0 });
    const registry = new ToolRegistry();
    let open = false;
    let version = 0;
    const listeners = new Set<() => void>();
    const controller = {
      windows: ["auditLog", "conversationLog", "logDetails", "settingsLogs"],
      subscribe: (l: () => void) => { listeners.add(l); return () => listeners.delete(l); },
      getVersion: () => version,
      hostOf: (id: string) => (id === "settingsLogs" && open ? "float" : null),
      toggleFloating: vi.fn(() => { open = !open; version++; listeners.forEach((l) => l()); }),
    } as unknown as WorkspaceController;
    both(registry, controller);
    const logSettings = () => registry.groupsFor("logs", order).flatMap((g) => g.tools).filter((t) => t.id === "logSettings");
    await waitFor(() => expect(logSettings()).toHaveLength(1));
    expect(logSettings()[0]).toMatchObject({ label: "Log Settings", pressed: false });
    act(() => registry.runShared("logs", order, "logSettings"));
    // Both logs offer the tool; one press opens the window once.
    expect(controller.toggleFloating).toHaveBeenCalledOnce();
    expect(controller.toggleFloating).toHaveBeenCalledWith("settingsLogs", expect.any(Number), expect.any(Number));
    await waitFor(() => expect(logSettings()[0].pressed).toBe(true));
    act(() => registry.runShared("logs", order, "logSettings"));
    await waitFor(() => expect(logSettings()[0].pressed).toBe(false));
  });
});
