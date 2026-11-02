import { describe, it, expect, beforeEach } from "vitest";
import { MAX_RECORD_BYTES, SHELL_SCHEMA, readRecord, storageKey, writeRecord, type ShellRecord } from "../shell/storage";

const record = (patch: Partial<ShellRecord> = {}): ShellRecord => ({ schema: SHELL_SCHEMA, savedAt: 0, workspaces: {}, ...patch });

describe("workspace state in the browser", () => {
  beforeEach(() => localStorage.clear());

  it("keeps desktop and mobile apart, and each user apart", () => {
    expect(writeRecord("u1", "desktop", record({ lastWorkspace: "logs" }))).toBe(true);
    expect(writeRecord("u1", "mobile", record({ lastWorkspace: "approvals" }))).toBe(true);
    expect(readRecord("u1", "desktop")?.lastWorkspace).toBe("logs");
    expect(readRecord("u1", "mobile")?.lastWorkspace).toBe("approvals");
    expect(readRecord("u2", "desktop")?.lastWorkspace).toBeUndefined();
    expect(Object.keys(localStorage)).toEqual(expect.arrayContaining([storageKey("u1", "desktop"), storageKey("u1", "mobile")]));
  });

  it("stores nothing without a user, so state never crosses accounts", () => {
    expect(writeRecord(null, "desktop", record({ lastWorkspace: "logs" }))).toBe(false);
    expect(localStorage.length).toBe(0);
    expect(readRecord(null, "desktop")).toEqual(record());
  });

  it("resumes the old tab once, mapping the two log tabs onto Logs", () => {
    localStorage.setItem("phx-tab", "audit");
    expect(readRecord("u1", "desktop")?.lastWorkspace).toBe("logs");
    localStorage.setItem("phx-tab", "mesa");
    expect(readRecord("u1", "desktop")?.lastWorkspace).toBe("mesa");
    localStorage.setItem("phx-tab", "nonsense");
    expect(readRecord("u1", "desktop")?.lastWorkspace).toBeUndefined();
    // Once a record exists, the old key no longer decides.
    writeRecord("u1", "desktop", record({ lastWorkspace: "settings" }));
    localStorage.setItem("phx-tab", "audit");
    expect(readRecord("u1", "desktop")?.lastWorkspace).toBe("settings");
  });

  it("leaves a record from a newer build untouched", () => {
    localStorage.setItem(storageKey("u1", "desktop"), JSON.stringify({ schema: SHELL_SCHEMA + 1, workspaces: {} }));
    expect(readRecord("u1", "desktop")).toBeNull();
    expect(writeRecord("u1", "desktop", record({ lastWorkspace: "logs" }))).toBe(false);
    expect(JSON.parse(localStorage.getItem(storageKey("u1", "desktop"))!).schema).toBe(SHELL_SCHEMA + 1);
  });

  it("reads an unreadable record as empty and drops unknown workspaces", () => {
    localStorage.setItem(storageKey("u1", "desktop"), "{not json");
    expect(readRecord("u1", "desktop")).toEqual(record());
    localStorage.setItem(storageKey("u1", "desktop"), JSON.stringify({ schema: SHELL_SCHEMA, lastWorkspace: "gone", workspaces: { logs: { dock: {} }, gone: {} } }));
    const r = readRecord("u1", "desktop")!;
    expect(r.lastWorkspace).toBeUndefined();
    expect(Object.keys(r.workspaces)).toEqual(["logs"]);
  });

  it("refuses a record over the size limit", () => {
    const huge = record({ workspaces: { logs: { dock: { pad: "x".repeat(MAX_RECORD_BYTES) } as never, folds: { folded: [], collapsed: {} }, closed: {} } } });
    expect(writeRecord("u1", "desktop", huge)).toBe(false);
    expect(localStorage.getItem(storageKey("u1", "desktop"))).toBeNull();
  });
});
