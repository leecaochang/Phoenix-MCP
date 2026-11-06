// Persistence for the agentCLI floating window so it survives navigating away
// from the panel and back, and closing/reopening. Two tiers:
//   - Durable (localStorage): open flag, geometry, and the access key/provider/model/
//     options selections. Restored after a full page reload too.
//   - Conversation (localStorage plus a module cache): transcript, draft, and
//     usage. Survives panel remounts and full page reloads until Clear is used,
//     it goes unused for MAX_CONV_AGE_MS, or purgeAgentCliStorage removes it.
//     Each browser holds one conversation per user, shared by its tabs.

import { clearAllReasonDrafts } from "./approval_reason_draft";

export interface AgentCliOptions {
  thinking: boolean;
  effort: string;
  temperature: string;
  verbose: boolean;
}

export interface AgentCliDurable {
  open: boolean;
  minimized: boolean;
  // null = the user never chose: the window resolves a default at mount
  // (full screen on a narrow/mobile viewport, windowed on desktop).
  maximized: boolean | null;
  pos: { x: number; y: number };
  pillPos: { x: number; y: number };
  /** Where the phone circle was last dragged; a desktop position means nothing at that size. */
  mobilePillPos: { x: number; y: number };
  size: { w: number; h: number };
  accessKeyId: string;
  instanceId: string;
  model: string;
  options: AgentCliOptions;
  // One-shot text seeded into the message box on the next window mount (used by
  // the onboarding wizard's test prompt); the window clears it after reading.
  prefill: string;
  // One-shot: the next window mount starts a new conversation instead of
  // restoring the saved one (the wizard's test run); the window clears it.
  freshConversation: boolean;
  // Footer visibility (gear toggle). The footer owns both text-size controls
  // and provider-reported token usage. Default on.
  showFooter: boolean;
  // Per-message timestamps in the transcript (gear toggle). Default off.
  showTimestamps: boolean;
  // Composer and transcript-bubble text size, in points. The rest of the Agent
  // Chat interface deliberately remains at its fixed design-system sizes.
  textSizePt: number;
}

const LS_KEY = "phx-agentcli";
const TURN_KEY = "phx-agentcli-turn";
let ownerId: string | null = null;
let draftTimer: ReturnType<typeof setTimeout> | null = null;
/** Set once the stored data was purged: nothing may write it back in this page. */
let storageClosed = false;

/** Browser persistence belongs to one authenticated HA user. */
export function getAgentCliOwner(): string | null { return ownerId; }

export function setAgentCliOwner(userId: string | null): void {
  if (userId === ownerId) return;
  flushSessionDraft(ownerId);
  ownerId = userId;
  storageClosed = false;
  conversation = defaultConversation();
  loaded = false;
  try {
    // Unscoped state has no trustworthy owner and must not cross accounts.
    localStorage.removeItem(LS_KEY);
    localStorage.removeItem(CONV_KEY);
  } catch { /* storage unavailable */ }
  if (userId) expireConversations();
}

function storageKey(base: string): string | null {
  return ownerId && !storageClosed ? `${base}:${encodeURIComponent(ownerId)}` : null;
}

function ownsSession(owner: string | null): boolean {
  return owner !== null && owner === ownerId && !storageClosed;
}


export const AGENT_CHAT_TEXT_SIZE_DEFAULT_PT = 9.75;
export const AGENT_CHAT_TEXT_SIZE_MIN_PT = 8.25;
export const AGENT_CHAT_TEXT_SIZE_MAX_PT = 19.75;
export const AGENT_CHAT_TEXT_SIZE_STEP_PT = 0.5;

/** Keep persisted or user-adjusted text sizes on the supported half-point
 *  scale, anchored at 9.75pt so the 13px default sits on a step. */
export function normalizeAgentCliTextSize(value: unknown): number {
  const candidate = typeof value === "number" && Number.isFinite(value)
    ? value
    : AGENT_CHAT_TEXT_SIZE_DEFAULT_PT;
  const stepped = AGENT_CHAT_TEXT_SIZE_DEFAULT_PT
    + Math.round((candidate - AGENT_CHAT_TEXT_SIZE_DEFAULT_PT) / AGENT_CHAT_TEXT_SIZE_STEP_PT)
      * AGENT_CHAT_TEXT_SIZE_STEP_PT;
  return Math.min(AGENT_CHAT_TEXT_SIZE_MAX_PT, Math.max(AGENT_CHAT_TEXT_SIZE_MIN_PT, stepped));
}

export function defaultDurable(): AgentCliDurable {
  return {
    open: false,
    minimized: false,
    maximized: null,
    pos: { x: -1, y: -1 }, // -1 => "unset", the window computes a default
    pillPos: { x: -1, y: -1 }, // -1 => "unset", seeded from the window on minimize
    mobilePillPos: { x: -1, y: -1 }, // -1 => "unset", the circle starts at the top right
    size: { w: 440, h: 560 },
    accessKeyId: "",
    instanceId: "",
    model: "",
    options: { thinking: true, effort: "high", temperature: "", verbose: false },
    prefill: "",
    freshConversation: false,
    showFooter: true,
    showTimestamps: false,
    textSizePt: AGENT_CHAT_TEXT_SIZE_DEFAULT_PT,
  };
}

/** A stored value of the expected type, else the fallback: stored data is not trusted to have the shape it was saved in. */
function ofType<T extends string | boolean>(type: "string" | "boolean", value: unknown, fallback: T): T {
  return typeof value === type ? value as T : fallback;
}

function finiteOr(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function durablePoint(value: unknown, fallback: { x: number; y: number }): { x: number; y: number } {
  const point = (value && typeof value === "object" ? value : {}) as { x?: unknown; y?: unknown };
  return { x: finiteOr(point.x, fallback.x), y: finiteOr(point.y, fallback.y) };
}

function durableSize(value: unknown, fallback: { w: number; h: number }): { w: number; h: number } {
  const size = (value && typeof value === "object" ? value : {}) as { w?: unknown; h?: unknown };
  return { w: finiteOr(size.w, fallback.w), h: finiteOr(size.h, fallback.h) };
}

export function getDurable(): AgentCliDurable {
  try {
    const key = storageKey(LS_KEY);
    const raw = key ? localStorage.getItem(key) : null;
    if (!raw) return defaultDurable();
    const p = JSON.parse(raw) as Partial<AgentCliDurable> & { showUsage?: unknown; tokenId?: unknown };
    // A legacy `showUsage` key (it hid only the token count) is honored as
    // showFooter when the new key is absent, and dropped from the next
    // persisted object.
    const { showUsage: legacyShowUsage, tokenId: legacyAccessKeyId } = p;
    const d = defaultDurable();
    const options = (p.options && typeof p.options === "object" ? p.options : {}) as Partial<AgentCliOptions>;
    return {
      open: ofType("boolean", p.open, d.open),
      minimized: ofType("boolean", p.minimized, d.minimized),
      maximized: typeof p.maximized === "boolean" ? p.maximized : d.maximized,
      pos: durablePoint(p.pos, d.pos),
      pillPos: durablePoint(p.pillPos, d.pillPos),
      mobilePillPos: durablePoint(p.mobilePillPos, d.mobilePillPos),
      size: durableSize(p.size, d.size),
      accessKeyId: ofType("string", p.accessKeyId, ofType("string", legacyAccessKeyId, d.accessKeyId)),
      instanceId: ofType("string", p.instanceId, d.instanceId),
      model: ofType("string", p.model, d.model),
      options: {
        thinking: ofType("boolean", options.thinking, d.options.thinking),
        effort: ofType("string", options.effort, d.options.effort),
        temperature: ofType("string", options.temperature, d.options.temperature),
        verbose: ofType("boolean", options.verbose, d.options.verbose),
      },
      prefill: ofType("string", p.prefill, d.prefill),
      freshConversation: ofType("boolean", p.freshConversation, d.freshConversation),
      showFooter: ofType("boolean", p.showFooter, ofType("boolean", legacyShowUsage, d.showFooter)),
      showTimestamps: ofType("boolean", p.showTimestamps, d.showTimestamps),
      textSizePt: normalizeAgentCliTextSize(p.textSizePt),
    };
  } catch {
    return defaultDurable();
  }
}

/**
 * Build the durable-state change for opening Agent Chat.
 *
 * Button-driven opens pass a viewport so the window is summoned in the center.
 * Shortcut-driven reopens omit it, leaving the user's last dragged position
 * untouched while still restoring a minimized window to its full form.
 */
export function agentCliOpenPatch(
  durable: AgentCliDurable,
  accessKeyId?: string,
  viewport?: { w: number; h: number },
): Partial<AgentCliDurable> {
  const patch: Partial<AgentCliDurable> = {
    ...(accessKeyId ? { accessKeyId } : {}),
    open: true,
    minimized: false,
  };
  if (viewport) {
    patch.pos = {
      x: Math.max(8, Math.round((viewport.w - durable.size.w) / 2)),
      y: Math.max(8, Math.round((viewport.h - durable.size.h) / 2)),
    };
  }
  return patch;
}

export function patchDurable(patch: Partial<AgentCliDurable>, owner = ownerId): void {
  if (!ownsSession(owner)) return;
  try {
    localStorage.setItem(storageKey(LS_KEY)!, JSON.stringify({ ...getDurable(), ...patch }));
  } catch {
    /* storage blocked: window state simply won't persist */
  }
}

// ---------------------------------------------------------------------------
// Conversation tier: the transcript, the unsent message box, and the usage
// counters. Persisted so the conversation resumes after a full page reload,
// not just after the panel unmounts (which happens whenever the user navigates
// away from the Phoenix MCP panel in Home Assistant).
//
// Kept under its OWN storage key: it is the only part that can grow without
// bound, and if it has to be dropped (quota, corruption) the window geometry
// and the access key / provider / model selections must survive independently.
//
// Conversation content, including tool results, is therefore written to browser
// localStorage on an admin-only page. It is bounded by the scrollback-lines
// setting, by MAX_CONV_BYTES and by MAX_CONV_AGE_MS below. Home Assistant has no
// logout hook, so what an admin saved stays until the next identity shows up: a
// non-admin session and a wipe purge it, and another admin, who can read the same
// data through the Conversation Log, leaves it in place.
// ---------------------------------------------------------------------------

const CONV_KEY = "phx-agentcli-conversation";
const PURGE_EVENT = "phx-agentcli-purged";
// PURGE_EVENT stays inside its page. This key reaches the other tabs: a storage
// event fires there only when the value changes, so each purge writes a new one.
// It holds nothing but that value.
const PURGE_MARKER_KEY = "phx-purged-agentcli";

// localStorage is ~5MB per origin and shared with the window state and every
// other Phoenix MCP key. Cap the conversation well under that: on overflow the
// oldest turns are dropped until it fits, which mirrors what the scrollback
// limit already does to the visible transcript.
const MAX_CONV_BYTES = 2_000_000;

// A conversation left unused this long is deleted the next time a user signs in here.
const MAX_CONV_AGE_MS = 30 * 24 * 60 * 60 * 1000;

interface Conversation {
  logConversationId: string;
  turns: unknown[];
  draft: string;
  usage: SessionUsage;
  /** Changes with every write of the turns, so a tab can tell them from a draft or usage write. */
  turnsRevision: number;
}

function sanitizeForPersistence(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sanitizeForPersistence);
  if (!value || typeof value !== "object") return value;
  const record = value as Record<string, unknown>;

  // Camera image bytes are intentionally session-only. Keep a placeholder
  // entry for the transcript, but never put the data URL or provider base64
  // into localStorage.
  if (record.kind === "tool_image") {
    return {
      kind: "tool_image",
      id: record.id,
      name: record.name,
      mimeType: record.mimeType,
      unavailable: true,
    };
  }
  // Attached images likewise: the transcript keeps each name, never the pixels.
  if (record.kind === "user" && Array.isArray(record.images)) {
    const { images, ...rest } = record;
    return {
      ...(sanitizeForPersistence(rest) as Record<string, unknown>),
      images: (images as unknown[]).map((image) => {
        const name = image && typeof image === "object" ? (image as Record<string, unknown>).name : undefined;
        return { name: typeof name === "string" ? name : "" };
      }),
    };
  }
  if (record.type === "image") {
    return { type: "text", text: "[image unavailable after reload]" };
  }
  if (record.type === "image_url") {
    return { type: "text", text: "[image unavailable after reload]" };
  }

  return Object.fromEntries(
    Object.entries(record).map(([key, child]) => [key, sanitizeForPersistence(child)]),
  );
}

function defaultConversation(): Conversation {
  return { logConversationId: "", turns: [], draft: "", usage: { input: 0, output: 0, context: 0, noData: false }, turnsRevision: 0 };
}

let conversation: Conversation = defaultConversation();
let loaded = false;
/** The revision of the turns this tab last read or wrote. */
let seenRevision = 0;
const conversationListeners = new Set<() => void>();

/** Called when another tab replaced the saved turns of this conversation, or purged them. */
export function onSavedConversationChange(listener: () => void): () => void {
  conversationListeners.add(listener);
  return () => { conversationListeners.delete(listener); };
}

function notifyConversationChange(): void {
  for (const listener of [...conversationListeners]) listener();
}

// The module cache is per tab and a save writes the whole conversation, so a
// tab must drop its cache when another one saves, or its next save would bring
// the older conversation back.
function adoptOtherTab(event: StorageEvent): void {
  if (event.key === PURGE_MARKER_KEY && event.newValue !== null) {
    closeStorage();
    notifyConversationChange();
    return;
  }
  const key = storageKey(CONV_KEY);
  if (!key || (event.key !== null && event.key !== key)) return;
  const seen = seenRevision;
  conversation = defaultConversation();
  loaded = false;
  if (!conversationListeners.size || load().turnsRevision === seen) return;
  notifyConversationChange();
}

// A conversation is saved whole, so of two turns running at once only the one
// that finishes last is kept. A tab announces its running turn so the others can
// say so. The record renews on a heartbeat, so a tab that dies stops counting.
const TURN_HEARTBEAT_MS = 10_000;
const TURN_STALE_MS = 30_000;
const tabId = Math.random().toString(36).slice(2);

/** When the record of a turn running in another tab goes stale, or 0 when there is none. */
function otherTabTurnUntil(): number {
  try {
    const key = storageKey(TURN_KEY);
    const record = key ? JSON.parse(localStorage.getItem(key) ?? "null") as { tab?: unknown; at?: unknown } | null : null;
    if (!record || record.tab === tabId || typeof record.at !== "number") return 0;
    return record.at + TURN_STALE_MS;
  } catch {
    return 0;
  }
}

/** Tell other tabs this one is running a turn; the returned function ends that. */
export function announceRunningTurn(owner = ownerId): () => void {
  const key = ownsSession(owner) ? storageKey(TURN_KEY) : null;
  if (!key) return () => {};
  const beat = () => {
    if (!ownsSession(owner)) return;
    try { localStorage.setItem(key, JSON.stringify({ tab: tabId, at: Date.now() })); } catch { /* storage unavailable */ }
  };
  beat();
  const heartbeat = setInterval(beat, TURN_HEARTBEAT_MS);
  const stop = () => {
    clearInterval(heartbeat);
    window.removeEventListener("pagehide", stop);
    try {
      if ((JSON.parse(localStorage.getItem(key) ?? "null") as { tab?: unknown } | null)?.tab === tabId) localStorage.removeItem(key);
    } catch { /* storage unavailable */ }
  };
  window.addEventListener("pagehide", stop);
  return stop;
}

/** Report whether another tab is running a turn, now and on every change, until the returned function runs. */
export function watchOtherTabTurn(listener: (running: boolean) => void): () => void {
  let stale: ReturnType<typeof setTimeout> | null = null;
  const check = () => {
    if (stale !== null) clearTimeout(stale);
    stale = null;
    const left = otherTabTurnUntil() - Date.now();
    listener(left > 0);
    if (left > 0) stale = setTimeout(check, left);
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || event.key === storageKey(TURN_KEY)) check();
  };
  window.addEventListener("storage", onStorage);
  check();
  return () => {
    window.removeEventListener("storage", onStorage);
    if (stale !== null) clearTimeout(stale);
  };
}

/** Delete conversations past their age, and date any saved before they carried one. */
function expireConversations(): void {
  try {
    const now = Date.now();
    for (const key of Object.keys(localStorage)) {
      if (!key.startsWith(`${CONV_KEY}:`)) continue;
      let saved: Record<string, unknown> | null = null;
      try { saved = JSON.parse(localStorage.getItem(key) ?? "null") as Record<string, unknown> | null; } catch { /* load() starts clean */ }
      if (typeof saved?.savedAt !== "number") {
        if (saved) localStorage.setItem(key, JSON.stringify({ ...saved, savedAt: now }));
      } else if (now - saved.savedAt > MAX_CONV_AGE_MS) {
        localStorage.removeItem(key);
      }
    }
  } catch { /* storage unavailable */ }
}

function closeStorage(): void {
  storageClosed = true;
  if (draftTimer !== null) clearTimeout(draftTimer);
  draftTimer = null;
  conversation = defaultConversation();
  loaded = false;
}

/**
 * Remove every Agent Chat conversation and window state in this browser, for
 * every user, along with unsent rejection reasons, and keep this page and the
 * other tabs from writing them back. For a wipe and for a session that may not
 * read them. The page's other bundles have their own module state, so they are
 * told too.
 */
export function purgeAgentCliStorage(): void {
  closeStorage();
  try {
    for (const key of Object.keys(localStorage)) {
      if (key === LS_KEY || key === CONV_KEY || [LS_KEY, CONV_KEY, TURN_KEY].some((base) => key.startsWith(`${base}:`))) {
        localStorage.removeItem(key);
      }
    }
    localStorage.setItem(PURGE_MARKER_KEY, `${Date.now()}.${tabId}`);
  } catch { /* storage unavailable */ }
  clearAllReasonDrafts();
  window.dispatchEvent(new Event(PURGE_EVENT));
}

function load(): Conversation {
  if (loaded) return conversation;
  loaded = true;
  try {
    const key = storageKey(CONV_KEY);
    const raw = key ? localStorage.getItem(key) : null;
    if (raw) {
      const p = JSON.parse(raw) as Partial<Conversation>;
      const d = defaultConversation();
      conversation = {
        logConversationId: typeof p.logConversationId === "string" ? p.logConversationId : "",
        turns: Array.isArray(p.turns) ? p.turns : d.turns,
        draft: typeof p.draft === "string" ? p.draft : d.draft,
        usage: { ...d.usage, ...(p.usage || {}) },
        turnsRevision: typeof p.turnsRevision === "number" ? p.turnsRevision : d.turnsRevision,
      };
    }
  } catch {
    // Corrupt or unreadable: start clean rather than breaking the window.
    conversation = defaultConversation();
  }
  seenRevision = conversation.turnsRevision;
  return conversation;
}

function save(): void {
  const key = storageKey(CONV_KEY);
  if (!key) return;
  try {
    // Keep the newest turns that fit, walking back from the latest, so a save
    // costs at most MAX_CONV_BYTES of serializing however long the conversation
    // grew. A single turn can be large on its own (a tool result with many
    // entities). When not even the newest fits, the draft and usage are still kept.
    const { turns, ...rest } = conversation;
    const head = JSON.stringify({ ...rest, savedAt: Date.now() });
    const kept: string[] = [];
    let length = head.length + ',"turns":[]'.length;
    for (let i = turns.length - 1; i >= 0; i -= 1) {
      const turn = JSON.stringify(sanitizeForPersistence(turns[i])) ?? "null";
      length += turn.length + 1;
      if (length > MAX_CONV_BYTES + 1) break;
      kept.push(turn);
    }
    localStorage.setItem(key, `${head.slice(0, -1)},"turns":[${kept.reverse().join(",")}]}`);
  } catch {
    /* storage blocked or quota exceeded: the conversation stays in memory only */
  }
}

/** Explicit identity follows persisted history; an empty request starts a new chat. */
export function getLogConversationId(hasHistory: boolean, owner = ownerId): string {
  if (!ownsSession(owner)) return "";
  const state = load();
  if (!hasHistory || !state.logConversationId) {
    state.logConversationId = "";
    try {
      // getRandomValues also works on HA installations served over local HTTP.
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 15) | 64;
      bytes[8] = (bytes[8] & 63) | 128;
      const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
      state.logConversationId = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
      save();
    } catch { /* Optional correlation must never prevent a chat request. */ }
  }
  return state.logConversationId;
}

export function getSessionTurns(): unknown[] {
  return load().turns;
}
export function setSessionTurns(turns: unknown[], owner = ownerId): void {
  if (!ownsSession(owner)) return;
  const state = load();
  state.turns = turns;
  state.turnsRevision = seenRevision = Math.random();
  save();
}

// The unsent message box. Cleared on send and on /clear, because the window
// sets the box to "" on both paths.
export function getSessionDraft(): string {
  return load().draft;
}
export function setSessionDraft(draft: string, owner = ownerId): void {
  if (!ownsSession(owner)) return;
  load().draft = draft;
  if (draftTimer !== null) clearTimeout(draftTimer);
  draftTimer = setTimeout(() => flushSessionDraft(owner), 300);
}

export function flushSessionDraft(owner = ownerId): void {
  if (owner !== ownerId) return;
  const pending = draftTimer !== null;
  if (draftTimer !== null) clearTimeout(draftTimer);
  draftTimer = null;
  if (pending && ownsSession(owner) && loaded) save();
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => flushSessionDraft());
  window.addEventListener("storage", adoptOtherTab);
  window.addEventListener(PURGE_EVENT, closeStorage);
}


// Token usage, provider-reported. Same lifecycle as the transcript it belongs
// to, so the footer totals stay coherent with the restored conversation.
export interface SessionUsage {
  input: number;   // prompt tokens across all model calls this conversation
  output: number;  // completion tokens across all model calls
  context: number; // input tokens of the newest model call (current context size)
  // True once a turn completed without the provider reporting any usage, so
  // the footer can say so instead of showing a forever-zero counter. Cleared
  // the moment usage arrives (e.g. after switching models).
  noData: boolean;
}
export function getSessionUsage(): SessionUsage {
  return load().usage;
}
export function setSessionUsage(usage: SessionUsage, owner = ownerId): void {
  if (!ownsSession(owner)) return;
  load().usage = usage;
  save();
}

// Test helper: simulate a full page reload. Drops the in-memory cache but
// leaves localStorage alone, so the next read comes back off disk.
export function __reloadFromStorage(): void {
  flushSessionDraft();
  conversation = defaultConversation();
  loaded = false;
}

// Test helper: reset both tiers between tests.
export function __resetAgentCliState(): void {
  if (draftTimer !== null) clearTimeout(draftTimer);
  draftTimer = null;
  storageClosed = false;
  seenRevision = 0;
  conversation = defaultConversation();
  loaded = true;
  try {
    const convKey = storageKey(CONV_KEY);
    const durableKey = storageKey(LS_KEY);
    if (convKey) localStorage.removeItem(convKey);
    if (durableKey) localStorage.removeItem(durableKey);
  } catch {
    /* ignore */
  }
}
