/**
 * Workspace records in Home Assistant's per-user storage: section-by-section
 * merging, the start-up adopt rule, debounced writes, and the cases that must
 * never write (a record from a newer build, a record over the size limit).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { RemoteRecord, SYNC_DELAY_MS, SYNC_MAX_DELAY_MS, mergeRecords, remoteKey, shouldAdopt, type UserDataConnection } from "../shell/sync";
import { SHELL_SCHEMA, parseRecord, type ShellRecord } from "../shell/storage";
import type { WorkspaceLayout } from "../shell/dock/controller";

const layout = (tag: string) => ({ dock: { grid: { tag } }, folds: { folded: [], collapsed: {} }, closed: {} }) as unknown as WorkspaceLayout;
const record = (patch: Partial<ShellRecord> = {}): ShellRecord => ({ schema: SHELL_SCHEMA, savedAt: 1, workspaces: {}, ...patch });

/** A fake websocket connection that keeps one stored value and tells subscribers about writes. */
function connection(initial: unknown, opts: { noSubscribe?: boolean; failWrites?: boolean } = {}) {
  let value = initial;
  const listeners: Array<(e: { value: unknown }) => void> = [];
  const writes: unknown[] = [];
  const conn: UserDataConnection = {
    sendMessagePromise: vi.fn(async (msg: Record<string, unknown>) => {
      if (msg.type === "frontend/get_user_data") return { value } as never;
      if (msg.type === "frontend/set_user_data") {
        if (opts.failWrites) throw new Error("offline");
        value = msg.value;
        writes.push(msg.value);
        for (const l of listeners) l({ value });
      }
      return undefined as never;
    }),
    subscribeMessage: vi.fn(async (cb: (e: never) => void) => {
      if (opts.noSubscribe) throw new Error("unknown command");
      listeners.push(cb as never);
      cb({ value } as never);
      return () => undefined;
    }),
  };
  return { conn, writes, stored: () => value };
}

describe("merging records", () => {
  it("takes this browser's changed sections over the stored copy and keeps the rest", () => {
    const stored = record({ rev: 4, toolbar: { dock: "left", x: 1, y: 1, collapsed: false, toolsShown: true, mode: "icons" }, workspaces: { logs: layout("other-logs"), mesa: layout("other-mesa") }, myDefaults: { logs: layout("other-default") } });
    const local = record({ rev: 3, toolbar: { dock: "top", x: 1, y: 1, collapsed: true, toolsShown: true, mode: "icons" }, workspaces: { logs: layout("mine-logs"), mesa: layout("mine-mesa") }, myDefaults: { mesa: layout("mine-default") } });
    const merged = mergeRecords(stored, local, new Set(["ws:logs", "my:mesa"]), "browser-b");
    expect(merged.rev).toBe(5);
    expect(merged.writer).toBe("browser-b");
    expect(merged.workspaces.logs).toEqual(layout("mine-logs"));
    expect(merged.workspaces.mesa).toEqual(layout("other-mesa"));
    expect(merged.toolbar?.dock).toBe("left");
    expect(merged.myDefaults).toEqual({ logs: layout("other-default"), mesa: layout("mine-default") });
    expect(merged.pending).toBeUndefined();
  });

  it("removes a section this browser removed, and writes the whole record when nothing is stored", () => {
    const stored = record({ rev: 2, workspaces: { logs: layout("x") } });
    expect(mergeRecords(stored, record(), new Set(["ws:logs"]), "w").workspaces).toEqual({});
    const first = mergeRecords(null, record({ workspaces: { mesa: layout("m") }, pending: true }), new Set(), "w");
    expect(first.workspaces).toEqual({ mesa: layout("m") });
    expect(first.rev).toBe(1);
    expect(first.pending).toBeUndefined();
  });
});

describe("adopting the stored copy at start-up", () => {
  it("adopts a newer stored copy unless this browser has unsent changes", () => {
    expect(shouldAdopt(record({ rev: 3 }), record({ rev: 2 }))).toBe(true);
    expect(shouldAdopt(record({ rev: 3 }), record({ rev: 2, pending: true }))).toBe(false);
    expect(shouldAdopt(record({ rev: 2 }), record({ rev: 2 }))).toBe(false);
    expect(shouldAdopt(null, record())).toBe(false);
    expect(shouldAdopt(record({ rev: 1 }), record())).toBe(true);
  });

  it("reads saved My Defaults and revisions, and refuses a record from a newer build", () => {
    const parsed = parseRecord({ schema: SHELL_SCHEMA, savedAt: 5, rev: 7, writer: "w", myDefaults: { logs: layout("d"), bogus: layout("x") } });
    expect(parsed).toMatchObject({ rev: 7, writer: "w", myDefaults: { logs: layout("d") } });
    expect(parseRecord({ schema: SHELL_SCHEMA + 1 })).toBe("newer");
    expect(parseRecord("nonsense")).toBeNull();
  });
});

describe("writing to Home Assistant", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const make = (stored: unknown, local: ShellRecord, opts = {}) => {
    const c = connection(stored, opts);
    const written: ShellRecord[] = [];
    const remote = new RemoteRecord(c.conn, "desktop", "browser-a", () => local, (r) => written.push(r));
    return { ...c, remote, written };
  };

  it("reads the stored copy once and keeps following it", async () => {
    const { remote, conn } = make(record({ rev: 2 }), record());
    const first = vi.fn();
    await remote.start(first);
    expect(first).toHaveBeenCalledWith(expect.objectContaining({ rev: 2 }));
    expect(conn.subscribeMessage).toHaveBeenCalledWith(expect.any(Function), { type: "frontend/subscribe_user_data", key: remoteKey("desktop") });
  });

  it("falls back to one read when the subscription is unavailable", async () => {
    const { remote, conn } = make(record({ rev: 2 }), record(), { noSubscribe: true });
    const first = vi.fn();
    await remote.start(first);
    expect(first).toHaveBeenCalledWith(expect.objectContaining({ rev: 2 }));
    expect(conn.sendMessagePromise).toHaveBeenCalledWith({ type: "frontend/get_user_data", key: "phoenix_mcp_ui:desktop" });
  });

  it("waits for a quiet spell before writing, but never longer than the maximum", async () => {
    const local = record({ workspaces: { logs: layout("mine") } });
    const { remote, writes } = make(null, local);
    await remote.start(() => {});
    remote.schedule(["ws:logs"]);
    await vi.advanceTimersByTimeAsync(SYNC_DELAY_MS - 100);
    expect(writes).toHaveLength(0);
    // Keep changing: the write still happens by the maximum delay.
    for (let t = 0; t < SYNC_MAX_DELAY_MS; t += 1000) {
      remote.schedule(["ws:logs"]);
      await vi.advanceTimersByTimeAsync(1000);
    }
    expect(writes.length).toBeGreaterThanOrEqual(1);
    expect((writes[0] as ShellRecord).workspaces.logs).toEqual(layout("mine"));
  });

  it("writes at once when asked, and reports the stored record", async () => {
    const local = record({ myDefaults: { mesa: layout("d") } });
    const { remote, written } = make(record({ rev: 1 }), local);
    await remote.start(() => {});
    remote.schedule(["my:mesa"], true);
    await vi.advanceTimersByTimeAsync(0);
    expect(written).toHaveLength(1);
    expect(written[0]).toMatchObject({ rev: 2, writer: "browser-a", myDefaults: { mesa: layout("d") } });
  });

  it("holds changes until the stored copy has been read", async () => {
    const local = record({ workspaces: { logs: layout("mine") } });
    const { remote, writes } = make(record({ rev: 3 }), local);
    remote.schedule(["ws:logs"], true);
    await vi.advanceTimersByTimeAsync(0);
    expect(writes).toHaveLength(0);
    await remote.start(() => {});
    await vi.advanceTimersByTimeAsync(SYNC_DELAY_MS);
    expect(writes).toHaveLength(1);
    expect((writes[0] as ShellRecord).rev).toBe(4);
  });

  it("never writes over a record from a newer build", async () => {
    const { remote, writes } = make({ schema: SHELL_SCHEMA + 1 }, record());
    const first = vi.fn();
    await remote.start(first);
    expect(first).toHaveBeenCalledWith(null);
    remote.schedule(["toolbar"], true);
    await vi.advanceTimersByTimeAsync(SYNC_DELAY_MS);
    expect(writes).toHaveLength(0);
  });

  it("keeps changes for the next try when a write fails", async () => {
    const { remote, written } = make(null, record({ workspaces: { logs: layout("mine") } }), { failWrites: true });
    await remote.start(() => {});
    remote.schedule(["ws:logs"], true);
    await vi.advanceTimersByTimeAsync(0);
    expect(written).toHaveLength(0);
    expect(remote.hasPending()).toBe(true);
  });

  it("skips a record above the size limit", async () => {
    const huge = record({ workspaces: { logs: { ...layout("x"), dock: { blob: "x".repeat(300 * 1024) } } as unknown as WorkspaceLayout } });
    const { remote, writes } = make(null, huge);
    await remote.start(() => {});
    remote.schedule(["ws:logs"], true);
    await vi.advanceTimersByTimeAsync(0);
    expect(writes).toHaveLength(0);
  });
});
