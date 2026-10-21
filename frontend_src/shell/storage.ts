/**
 * Per-user, per-environment workspace state in this browser.
 *
 * Desktop and mobile are separate records, so arranging one never rearranges
 * the other. Records are keyed by the Home Assistant user id and nothing is
 * stored without one, so state never crosses accounts. A record written by a
 * newer build is left untouched rather than overwritten.
 */

import type { WorkspaceLayout } from "./dock/controller";

export type Env = "desktop" | "mobile";
export const WORKSPACE_IDS = ["accessKeys", "approvals", "changes", "mesa", "logs", "settings"] as const;
export type WorkspaceId = (typeof WORKSPACE_IDS)[number];

export const SHELL_SCHEMA = 1;
/** Upper bound for one record; a larger arrangement is not saved. */
export const MAX_RECORD_BYTES = 256 * 1024;

export type ToolbarDock = "top" | "bottom" | "left" | "right" | "floating";
export interface ToolbarState {
  dock: ToolbarDock;
  /** Position while floating, from the shell's top-left corner. */
  x: number;
  y: number;
  collapsed: boolean;
  /** Whether the second row of workspace tools is shown. */
  toolsShown: boolean;
  mode: "icons" | "icon-text";
}
/** Docked at the top, icons only, tools shown: the accepted starting point. */
export const DEFAULT_TOOLBAR: ToolbarState = { dock: "top", x: 16, y: 12, collapsed: false, toolsShown: true, mode: "icons" };

export interface ShellRecord {
  schema: number;
  savedAt: number;
  lastWorkspace?: WorkspaceId;
  toolbar?: ToolbarState;
  workspaces: Partial<Record<WorkspaceId, WorkspaceLayout>>;
}

/** A saved toolbar state, with anything unreadable replaced by the default. */
export function readToolbar(raw: unknown): ToolbarState {
  const r = (raw && typeof raw === "object" ? raw : {}) as Partial<Record<keyof ToolbarState, unknown>>;
  const d = DEFAULT_TOOLBAR;
  const num = (v: unknown, fallback: number) => (typeof v === "number" && Number.isFinite(v) && v >= 0 && v < 10000 ? v : fallback);
  return {
    dock: ["top", "bottom", "left", "right", "floating"].includes(r.dock as string) ? r.dock as ToolbarDock : d.dock,
    x: num(r.x, d.x),
    y: num(r.y, d.y),
    collapsed: typeof r.collapsed === "boolean" ? r.collapsed : d.collapsed,
    toolsShown: typeof r.toolsShown === "boolean" ? r.toolsShown : d.toolsShown,
    mode: r.mode === "icon-text" || r.mode === "icons" ? r.mode : d.mode,
  };
}

const isWorkspace = (v: unknown): v is WorkspaceId => (WORKSPACE_IDS as readonly unknown[]).includes(v);

export function storageKey(userId: string, env: Env): string {
  return `phx-ui:${encodeURIComponent(userId)}:${env}`;
}

const emptyRecord = (): ShellRecord => ({ schema: SHELL_SCHEMA, savedAt: 0, workspaces: {} });

/** Before UI2 the panel remembered its tab; the logs tabs became one Logs workspace. */
function legacyWorkspace(): WorkspaceId | undefined {
  let tab: string | null = null;
  try { tab = localStorage.getItem("phx-tab"); } catch { /* storage blocked */ }
  if (tab === "audit" || tab === "conversations") return "logs";
  return isWorkspace(tab) ? tab : undefined;
}

/** The stored record, an empty one when absent or unreadable, or null when a newer build owns it. */
export function readRecord(userId: string | null, env: Env): ShellRecord | null {
  if (!userId) return emptyRecord();
  let raw: string | null = null;
  try { raw = localStorage.getItem(storageKey(userId, env)); } catch { /* storage blocked */ }
  if (!raw) return { ...emptyRecord(), lastWorkspace: legacyWorkspace() };
  try {
    const parsed = JSON.parse(raw) as Partial<ShellRecord>;
    if (typeof parsed.schema !== "number") return emptyRecord();
    if (parsed.schema > SHELL_SCHEMA) return null;
    const workspaces: ShellRecord["workspaces"] = {};
    if (parsed.workspaces && typeof parsed.workspaces === "object") {
      for (const [id, layout] of Object.entries(parsed.workspaces)) {
        if (isWorkspace(id) && layout && typeof layout === "object") workspaces[id] = layout as WorkspaceLayout;
      }
    }
    return {
      schema: SHELL_SCHEMA,
      savedAt: typeof parsed.savedAt === "number" ? parsed.savedAt : 0,
      lastWorkspace: isWorkspace(parsed.lastWorkspace) ? parsed.lastWorkspace : undefined,
      toolbar: parsed.toolbar === undefined ? undefined : readToolbar(parsed.toolbar),
      workspaces,
    };
  } catch {
    return emptyRecord();
  }
}

/** Store a record; false when there is no user, a newer build owns the slot, or it is too large. */
export function writeRecord(userId: string | null, env: Env, record: ShellRecord): boolean {
  if (!userId) return false;
  const key = storageKey(userId, env);
  try {
    const existing = localStorage.getItem(key);
    if (existing) {
      const schema = (JSON.parse(existing) as { schema?: unknown }).schema;
      if (typeof schema === "number" && schema > SHELL_SCHEMA) return false;
    }
  } catch { /* unreadable: overwrite it */ }
  const text = JSON.stringify({ ...record, schema: SHELL_SCHEMA, savedAt: Date.now() });
  if (text.length > MAX_RECORD_BYTES) return false;
  try {
    localStorage.setItem(key, text);
    return true;
  } catch {
    return false;
  }
}
