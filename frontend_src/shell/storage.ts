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
import { readPhoneLayout, type PhoneLayout } from "./phoneLayout";

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
  mode: "icons" | "icon-text";
  /** Whether a floating toolbar stands upright; a docked one follows its edge. */
  vertical: boolean;
  /** A horizontal toolbar puts the tools on the main row, back on a second row while they do not fit. */
  singleRow: boolean;
}
/** Docked at the top, icons only, tools shown: the accepted starting point. */
export const DEFAULT_TOOLBAR: ToolbarState = { dock: "top", x: 16, y: 12, collapsed: false, mode: "icons", vertical: false, singleRow: false };

/** Whether the toolbar stands upright: docked at a side, or floating upright. */
export const toolbarVertical = (state: ToolbarState): boolean => (state.dock === "floating" ? state.vertical : state.dock === "left" || state.dock === "right");

/** The toolbar after a move to another dock; floating keeps the orientation it had docked. */
export function withToolbarDock(state: ToolbarState, dock: ToolbarDock): ToolbarState {
  if (dock === state.dock) return state;
  return { ...state, dock, ...(dock === "floating" ? { vertical: toolbarVertical(state) } : {}) };
}

/** Quick Help on or off, and the workspaces where it was reopened or closed against that. */
export interface HelpPrefs {
  show: boolean;
  /** true: Quick Help is open here although it is off in the Help menu; false: closed here although it is on. */
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
/**
 * The Guide: one window over every workspace, as Agent Chat is (user,
 * 2026-11-01), so opening or closing it anywhere does so everywhere. It
 * remembers where the user put it and whether it is folded.
 */
export interface GuideState { open: boolean; rect?: GuideRect; folded?: boolean; welcome?: WelcomeState }
/** The things Welcome to the Guide shows the reader, each reacting when tried. */
export const WELCOME_STEPS = ["point", "select", "search"] as const;
export type WelcomeStep = (typeof WELCOME_STEPS)[number];
/** Whether the reader has seen Welcome to the Guide, which opens first until then; Reset All Workspaces clears it. */
export interface WelcomeState { seen: boolean }
export const GUIDE_CLOSED: GuideState = { open: false };

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
  /** The Guide; it starts closed. */
  guide?: GuideState;
  workspaces: Partial<Record<WorkspaceId, WorkspaceLayout>>;
  /** Arrangements the user saved as My Default, one per workspace; automatic saving never changes them. */
  myDefaults?: Partial<Record<WorkspaceId, WorkspaceLayout>>;
  /** The windows added to My Workspace, in the order they were added; arranging it never changes them. */
  myWindows?: string[];
  /** Phones: each workspace's stack as the user arranged it (see PhoneLayout). */
  phone?: Partial<Record<WorkspaceId, PhoneLayout>>;
  /** Phones: the stacks the user saved as My Default. */
  phoneDefaults?: Partial<Record<WorkspaceId, PhoneLayout>>;
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
    mode: r.mode === "icon-text" || r.mode === "icons" ? r.mode : d.mode,
    singleRow: typeof r.singleRow === "boolean" ? r.singleRow : d.singleRow,
    vertical: typeof r.vertical === "boolean" ? r.vertical : d.vertical,
  };
}

const isWorkspace = (v: unknown): v is WorkspaceId => (WORKSPACE_IDS as readonly unknown[]).includes(v);

/** The saved Guide, with anything unreadable left out. */
export function readGuide(raw: unknown): GuideState {
  if (!raw || typeof raw !== "object") return GUIDE_CLOSED;
  const px = (v: unknown, max: number) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= max;
  const rectOf = (r: Partial<Record<keyof GuideRect, unknown>> | undefined): GuideRect | undefined =>
    r && px(r.x, 20000) && px(r.y, 20000) && px(r.w, 20000) && px(r.h, 20000) && (r.w as number) >= 200 && (r.h as number) >= 120
      ? { x: r.x as number, y: r.y as number, w: r.w as number, h: r.h as number } : undefined;
  type Saved = { open?: unknown; rect?: Partial<Record<keyof GuideRect, unknown>>; folded?: unknown; welcome?: unknown } | null;
  const v = raw as Saved;
  if (typeof v?.open === "boolean") {
    const rect = rectOf(v.rect);
    const welcome = readWelcome(v.welcome);
    return { open: v.open, ...(rect ? { rect } : {}), ...(v.folded === true ? { folded: true } : {}), ...(welcome ? { welcome } : {}) };
  }
  // Saved by an earlier build, one Guide per workspace: open if it was open anywhere, at the first place found.
  const old = Object.entries(raw).filter(([id]) => isWorkspace(id)).map(([, value]) => value as Saved);
  const rect = old.map((o) => rectOf(o?.rect ?? undefined)).find(Boolean);
  return { open: old.some((o) => o?.open === true), ...(rect ? { rect } : {}) };
}

/** Whether Welcome was seen; an earlier build's dismissed checklist counts as seen. None when nothing was saved. */
function readWelcome(raw: unknown): WelcomeState | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const r = raw as { seen?: unknown; dismissed?: unknown };
  return r.seen === true || r.dismissed === true ? { seen: true } : undefined;
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
const phoneLayouts = (raw: unknown): Partial<Record<WorkspaceId, PhoneLayout>> => {
  const out: Partial<Record<WorkspaceId, PhoneLayout>> = {};
  if (raw && typeof raw === "object") {
    for (const [id, layout] of Object.entries(raw)) if (isWorkspace(id)) out[id] = readPhoneLayout(layout);
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
  const phone = phoneLayouts(parsed.phone);
  const phoneDefaults = phoneLayouts(parsed.phoneDefaults);
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
    ...(Object.keys(phone).length ? { phone } : {}),
    ...(Object.keys(phoneDefaults).length ? { phoneDefaults } : {}),
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
