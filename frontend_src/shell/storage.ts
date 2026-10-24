/**
 * Per-user, per-environment workspace state.
 *
 * Desktop and mobile are separate records, so arranging one never rearranges
 * the other. Records are keyed by the Home Assistant user id and nothing is
 * stored without one, so state never crosses accounts. This browser keeps a
 * copy for an instant first paint; Home Assistant's per-user storage keeps the
 * shared one (see `sync.ts`). A record written by a newer build is left
 * untouched rather than overwritten.
 */

import type { WorkspaceLayout } from "./dock/controller";

export type Env = "desktop" | "mobile";
export const WORKSPACE_IDS = ["accessKeys", "approvals", "changes", "mesa", "logs", "settings", "my"] as const;
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

/** Show Help Bar, and the workspaces where Help was reopened or closed against it. */
export interface HelpPrefs {
  show: boolean;
  /** true: Help is open here although Show Help Bar is off; false: closed here although it is on. */
  workspaces: Partial<Record<WorkspaceId, boolean>>;
}
export const DEFAULT_HELP: HelpPrefs = { show: true, workspaces: {} };

/** Whether Help is open in a workspace. */
export const helpOpen = (help: HelpPrefs, workspace: WorkspaceId): boolean => help.workspaces[workspace] ?? help.show;

/** Help preferences after the user opened or closed Help in one workspace. */
export function withHelpIn(help: HelpPrefs, workspace: WorkspaceId, open: boolean): HelpPrefs {
  const workspaces = { ...help.workspaces };
  if (open === help.show) delete workspaces[workspace];
  else workspaces[workspace] = open;
  return { show: help.show, workspaces };
}

/** Where the Guide sits on screen, in CSS pixels from the viewport's top-left corner. */
export interface GuideRect { x: number; y: number; w: number; h: number }
/** The Guide in one workspace: whether it is open, and where the user put it. */
export interface GuideState { open: boolean; rect?: GuideRect }

export interface ShellRecord {
  schema: number;
  savedAt: number;
  /** The version of the shared copy this record matches; it grows with every shared write. */
  rev?: number;
  /** The browser that wrote the shared copy. */
  writer?: string;
  /** This browser has changes the shared copy does not have yet. */
  pending?: boolean;
  lastWorkspace?: WorkspaceId;
  toolbar?: ToolbarState;
  help?: HelpPrefs;
  /** The Guide per workspace; it starts closed. */
  guide?: Partial<Record<WorkspaceId, GuideState>>;
  workspaces: Partial<Record<WorkspaceId, WorkspaceLayout>>;
  /** Arrangements the user saved as My Default, one per workspace; automatic saving never changes them. */
  myDefaults?: Partial<Record<WorkspaceId, WorkspaceLayout>>;
  /** The windows added to My Workspace, in the order they were added; arranging it never changes them. */
  myWindows?: string[];
}

/** Most windows My Workspace keeps; more than every window there is, so only damage is cut off. */
const MAX_MY_WINDOWS = 64;

/** Saved My Workspace windows: unique window ids, with anything unreadable left out. */
export function readMyWindows(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const ids = raw.filter((id): id is string => typeof id === "string" && /^[A-Za-z][A-Za-z0-9]{0,40}$/.test(id));
  return [...new Set(ids)].slice(0, MAX_MY_WINDOWS);
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

/** Saved Guide states, with anything unreadable left out. */
export function readGuide(raw: unknown): Partial<Record<WorkspaceId, GuideState>> {
  const out: Partial<Record<WorkspaceId, GuideState>> = {};
  if (!raw || typeof raw !== "object") return out;
  const px = (v: unknown, max: number) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= max;
  for (const [id, value] of Object.entries(raw)) {
    const v = value as { open?: unknown; rect?: Partial<Record<keyof GuideRect, unknown>> } | null;
    if (!isWorkspace(id) || !v || typeof v.open !== "boolean") continue;
    const r = v.rect;
    const rect = r && px(r.x, 20000) && px(r.y, 20000) && px(r.w, 20000) && px(r.h, 20000) && (r.w as number) >= 200 && (r.h as number) >= 120
      ? { x: r.x as number, y: r.y as number, w: r.w as number, h: r.h as number } : undefined;
    out[id] = { open: v.open, ...(rect ? { rect } : {}) };
  }
  return out;
}

/** Saved Help preferences, with anything unreadable left out. */
export function readHelp(raw: unknown): HelpPrefs {
  const r = (raw && typeof raw === "object" ? raw : {}) as { show?: unknown; workspaces?: unknown };
  const workspaces: Partial<Record<WorkspaceId, boolean>> = {};
  if (r.workspaces && typeof r.workspaces === "object") {
    for (const [id, open] of Object.entries(r.workspaces)) if (isWorkspace(id) && typeof open === "boolean") workspaces[id] = open;
  }
  return { show: typeof r.show === "boolean" ? r.show : DEFAULT_HELP.show, workspaces };
}

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

const layouts = (raw: unknown): Partial<Record<WorkspaceId, WorkspaceLayout>> => {
  const out: Partial<Record<WorkspaceId, WorkspaceLayout>> = {};
  if (raw && typeof raw === "object") {
    for (const [id, layout] of Object.entries(raw)) {
      if (isWorkspace(id) && layout && typeof layout === "object") out[id] = layout as WorkspaceLayout;
    }
  }
  return out;
};
const count = (v: unknown): number | undefined => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : undefined);

/**
 * A record from storage: "newer" when a newer build wrote it, null when it is
 * not a record at all. Each workspace layout is checked again when it loads.
 */
export function parseRecord(raw: unknown): ShellRecord | "newer" | null {
  if (!raw || typeof raw !== "object") return null;
  const parsed = raw as Partial<ShellRecord>;
  if (typeof parsed.schema !== "number") return null;
  if (parsed.schema > SHELL_SCHEMA) return "newer";
  const myDefaults = layouts(parsed.myDefaults);
  return {
    schema: SHELL_SCHEMA,
    savedAt: typeof parsed.savedAt === "number" ? parsed.savedAt : 0,
    ...(count(parsed.rev) !== undefined ? { rev: count(parsed.rev) } : {}),
    ...(typeof parsed.writer === "string" ? { writer: parsed.writer.slice(0, 64) } : {}),
    ...(parsed.pending === true ? { pending: true } : {}),
    lastWorkspace: isWorkspace(parsed.lastWorkspace) ? parsed.lastWorkspace : undefined,
    toolbar: parsed.toolbar === undefined ? undefined : readToolbar(parsed.toolbar),
    ...(parsed.help !== undefined ? { help: readHelp(parsed.help) } : {}),
    ...(parsed.guide !== undefined ? { guide: readGuide(parsed.guide) } : {}),
    workspaces: layouts(parsed.workspaces),
    ...(Object.keys(myDefaults).length ? { myDefaults } : {}),
    ...(parsed.myWindows !== undefined ? { myWindows: readMyWindows(parsed.myWindows) } : {}),
  };
}

/** The stored record, an empty one when absent or unreadable, or null when a newer build owns it. */
export function readRecord(userId: string | null, env: Env): ShellRecord | null {
  if (!userId) return emptyRecord();
  let raw: string | null = null;
  try { raw = localStorage.getItem(storageKey(userId, env)); } catch { /* storage blocked */ }
  if (!raw) return { ...emptyRecord(), lastWorkspace: legacyWorkspace() };
  try {
    const parsed = parseRecord(JSON.parse(raw));
    if (parsed === "newer") return null;
    return parsed ?? emptyRecord();
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
