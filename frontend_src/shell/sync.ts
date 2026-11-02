/**
 * Keeps each user's workspace record in Home Assistant's per-user frontend
 * storage, so the desktop arrangement follows the user to other computers
 * signed into the same account, while mobile keeps a record of its own.
 *
 * The browser's copy paints first. When Home Assistant's copy is newer (a
 * higher `rev`) and this browser has nothing unsent, the shell adopts it.
 * Writes are debounced, because Home Assistant saves the whole store on every
 * write, and merge section by section: the sections this browser changed
 * (the toolbar, Help, the Guide, the last workspace, each workspace's arrangement, each
 * My Default, and on phones each stack and its My Default) replace the stored ones, and everything else keeps the stored copy.
 * Nothing is written over a record from a newer build or above the size limit.
 */

import { MAX_RECORD_BYTES, SHELL_SCHEMA, parseRecord, type Env, type ShellRecord, type WorkspaceId } from "./storage";

/** A part of the record that changes independently. */
export type Section = "toolbar" | "help" | "guide" | "lastWorkspace" | "myWindows" | `ws:${WorkspaceId}` | `my:${WorkspaceId}`
  | `phone:${WorkspaceId}` | `phoneDefault:${WorkspaceId}`;

/** Wait this long after the last change before writing to Home Assistant, but never longer than the maximum. */
export const SYNC_DELAY_MS = 2000;
export const SYNC_MAX_DELAY_MS = 10000;

export const remoteKey = (env: Env): string => `phoenix_mcp_ui:${env}`;

/** The part of Home Assistant's websocket connection this needs. */
export interface UserDataConnection {
  sendMessagePromise<T>(message: Record<string, unknown>): Promise<T>;
  subscribeMessage<T>(callback: (event: T) => void, message: Record<string, unknown>): Promise<() => unknown>;
}

/** This browser's name in the shared record, kept in the browser. */
export function writerId(): string {
  const key = "phx-ui-writer";
  try {
    const known = localStorage.getItem(key);
    if (known) return known;
    const id = Math.random().toString(36).slice(2, 12);
    localStorage.setItem(key, id);
    return id;
  } catch {
    return "browser";
  }
}

/**
 * The record to store: the stored copy with this browser's changed sections
 * over it, one revision higher. Without a stored copy, this browser's record.
 */
export function mergeRecords(stored: ShellRecord | null, local: ShellRecord, changed: ReadonlySet<Section>, writer: string): ShellRecord {
  const rev = Math.max(stored?.rev ?? 0, local.rev ?? 0) + 1;
  const strip = (r: ShellRecord): ShellRecord => {
    const { pending: _pending, ...rest } = r;
    return rest;
  };
  if (!stored) return { ...strip(local), schema: SHELL_SCHEMA, rev, writer, savedAt: Date.now() };
  const out: ShellRecord = {
    ...strip(stored),
    schema: SHELL_SCHEMA,
    rev,
    writer,
    savedAt: Date.now(),
    workspaces: { ...stored.workspaces },
    myDefaults: { ...stored.myDefaults },
    phone: { ...stored.phone },
    phoneDefaults: { ...stored.phoneDefaults },
  };
  for (const section of changed) {
    if (section === "toolbar") out.toolbar = local.toolbar;
    else if (section === "help") out.help = local.help;
    else if (section === "guide") out.guide = local.guide;
    else if (section === "lastWorkspace") out.lastWorkspace = local.lastWorkspace;
    else if (section === "myWindows") out.myWindows = local.myWindows;
    else if (section.startsWith("phone")) {
      const [kind, id] = section.split(":") as ["phone" | "phoneDefault", WorkspaceId];
      const target = kind === "phone" ? out.phone! : out.phoneDefaults!;
      const value = kind === "phone" ? local.phone?.[id] : local.phoneDefaults?.[id];
      if (value) target[id] = value;
      else delete target[id];
    } else {
      const [kind, id] = section.split(":") as ["ws" | "my", WorkspaceId];
      const target = kind === "ws" ? out.workspaces : out.myDefaults!;
      const value = kind === "ws" ? local.workspaces[id] : local.myDefaults?.[id];
      if (value) target[id] = value;
      else delete target[id];
    }
  }
  if (!Object.keys(out.myDefaults!).length) delete out.myDefaults;
  if (!Object.keys(out.phone!).length) delete out.phone;
  if (!Object.keys(out.phoneDefaults!).length) delete out.phoneDefaults;
  return out;
}

/** Whether a stored record should replace this browser's copy at start-up. */
export function shouldAdopt(stored: ShellRecord | null, local: ShellRecord | null): boolean {
  if (!stored || !local || local.pending) return false;
  return (stored.rev ?? 0) > (local.rev ?? 0);
}

/** One environment's record in Home Assistant, kept current by a subscription. */
export class RemoteRecord {
  /** The newest stored record seen, or null when there is none yet. */
  latest: ShellRecord | null = null;
  /** True once the stored copy is known (or known to be unavailable). */
  ready = false;
  /** False when the stored copy belongs to a newer build or cannot be read; nothing is written then. */
  writable = true;
  private changed = new Set<Section>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private firstChange = 0;
  private writing: Promise<void> | null = null;
  private unsubscribe: (() => unknown) | null = null;

  constructor(
    private readonly connection: UserDataConnection,
    private readonly env: Env,
    private readonly writer: string,
    /** The current local record, read when a write happens. */
    private readonly local: () => ShellRecord | null,
    /** Called after a write with the record now stored, so the browser's copy can match it. */
    private readonly onWritten: (stored: ShellRecord) => void,
  ) {}

  /** Read the stored copy and follow later changes. `first` gets the stored copy once. */
  async start(first: (stored: ShellRecord | null) => void): Promise<void> {
    let delivered = false;
    const take = (value: unknown) => {
      const parsed = value == null ? null : parseRecord(value);
      if (parsed === "newer") {
        this.writable = false;
        this.latest = null;
      } else {
        this.latest = parsed;
      }
      if (!delivered) {
        delivered = true;
        this.ready = true;
        first(parsed === "newer" ? null : parsed);
        // Changes noted before the stored copy arrived are written now.
        if (this.changed.size) this.schedule([]);
      }
    };
    try {
      this.unsubscribe = await this.connection.subscribeMessage<{ value?: unknown }>(
        (event) => take(event?.value), { type: "frontend/subscribe_user_data", key: remoteKey(this.env) },
      );
    } catch {
      try {
        const result = await this.connection.sendMessagePromise<{ value?: unknown }>({ type: "frontend/get_user_data", key: remoteKey(this.env) });
        take(result?.value);
      } catch {
        this.ready = true;
        this.writable = false;
        if (!delivered) first(null);
      }
    }
  }

  /** Whether changes are waiting to be written. */
  hasPending(): boolean {
    return this.changed.size > 0 || this.writing !== null;
  }

  stop(): void {
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = undefined;
    void this.unsubscribe?.();
    this.unsubscribe = null;
  }

  /** Note changed sections and write soon: after a quiet spell, and never later than the maximum delay. */
  schedule(sections: Iterable<Section>, now = false): void {
    for (const s of sections) this.changed.add(s);
    if (!this.changed.size) return;
    const time = Date.now();
    if (!this.firstChange) this.firstChange = time;
    if (this.timer !== undefined) clearTimeout(this.timer);
    const wait = now ? 0 : Math.max(0, Math.min(SYNC_DELAY_MS, this.firstChange + SYNC_MAX_DELAY_MS - time));
    this.timer = setTimeout(() => { this.timer = undefined; void this.flush(); }, wait);
  }

  /** Write the changed sections now. */
  async flush(): Promise<void> {
    if (this.writing) await this.writing;
    if (!this.ready || !this.writable || !this.changed.size) return;
    const local = this.local();
    if (!local) return;
    const sections = new Set(this.changed);
    this.changed.clear();
    this.firstChange = 0;
    const merged = mergeRecords(this.latest, local, sections, this.writer);
    if (JSON.stringify(merged).length > MAX_RECORD_BYTES) return;
    this.writing = (async () => {
      try {
        await this.connection.sendMessagePromise({ type: "frontend/set_user_data", key: remoteKey(this.env), value: merged });
        this.latest = merged;
        this.onWritten(merged);
      } catch {
        // Keep the sections for the next try; the browser's copy still has them.
        for (const s of sections) this.changed.add(s);
      }
    })();
    try { await this.writing; } finally { this.writing = null; }
  }
}
