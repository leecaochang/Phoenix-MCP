// Persistence for the agentCLI floating window so it survives navigating away
// from the panel and back, and closing/reopening. Two tiers:
//   - Durable (localStorage): open flag, geometry, and the token/provider/model/
//     options selections. Restored after a full page reload too.
//   - Conversation (localStorage plus a module cache): transcript, draft, and
//     usage. Survives panel remounts and full page reloads until Clear is used.

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
  size: { w: number; h: number };
  tokenId: string;
  instanceId: string;
  model: string;
  options: AgentCliOptions;
  // One-shot text seeded into the message box on the next window mount (used by
  // the onboarding wizard's test prompt); the window clears it after reading.
  prefill: string;
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
let ownerId: string | null = null;
let draftTimer: ReturnType<typeof setTimeout> | null = null;

/** Browser persistence belongs to one authenticated HA user. */
export function getAgentCliOwner(): string | null { return ownerId; }

export function setAgentCliOwner(userId: string | null): void {
  if (userId === ownerId) return;
  flushSessionDraft(ownerId);
  ownerId = userId;
  conversation = defaultConversation();
  loaded = false;
  try {
    // Unscoped state has no trustworthy owner and must not cross accounts.
    localStorage.removeItem(LS_KEY);
    localStorage.removeItem(CONV_KEY);
  } catch { /* storage unavailable */ }
}

function storageKey(base: string): string | null {
  return ownerId ? `${base}:${encodeURIComponent(ownerId)}` : null;
}

function ownsSession(owner: string | null): boolean { return owner !== null && owner === ownerId; }


export const AGENTCLI_TEXT_SIZE_DEFAULT_PT = 9.75;
export const AGENTCLI_TEXT_SIZE_MIN_PT = 8.25;
export const AGENTCLI_TEXT_SIZE_MAX_PT = 19.75;
export const AGENTCLI_TEXT_SIZE_STEP_PT = 0.5;

/** Keep persisted or user-adjusted text sizes on the supported half-point
 *  scale, anchored at 9.75pt so the 13px default sits on a step. */
export function normalizeAgentCliTextSize(value: unknown): number {
  const candidate = typeof value === "number" && Number.isFinite(value)
    ? value
    : AGENTCLI_TEXT_SIZE_DEFAULT_PT;
  const stepped = AGENTCLI_TEXT_SIZE_DEFAULT_PT
    + Math.round((candidate - AGENTCLI_TEXT_SIZE_DEFAULT_PT) / AGENTCLI_TEXT_SIZE_STEP_PT)
      * AGENTCLI_TEXT_SIZE_STEP_PT;
  return Math.min(AGENTCLI_TEXT_SIZE_MAX_PT, Math.max(AGENTCLI_TEXT_SIZE_MIN_PT, stepped));
}

export function defaultDurable(): AgentCliDurable {
  return {
    open: false,
    minimized: false,
    maximized: null,
    pos: { x: -1, y: -1 }, // -1 => "unset", the window computes a default
    pillPos: { x: -1, y: -1 }, // -1 => "unset", seeded from the window on minimize
    size: { w: 440, h: 560 },
    tokenId: "",
    instanceId: "",
    model: "",
    options: { thinking: true, effort: "high", temperature: "", verbose: false },
    prefill: "",
    showFooter: true,
    showTimestamps: false,
    textSizePt: AGENTCLI_TEXT_SIZE_DEFAULT_PT,
  };
}

export function getDurable(): AgentCliDurable {
  try {
    const key = storageKey(LS_KEY);
    const raw = key ? localStorage.getItem(key) : null;
    if (!raw) return defaultDurable();
    const p = JSON.parse(raw) as Partial<AgentCliDurable> & { showUsage?: unknown };
    // A legacy `showUsage` key (it hid only the token count) is honored as
    // showFooter when the new key is absent, and dropped from the next
    // persisted object.
    const { showUsage: legacyShowUsage, ...persisted } = p;
    const d = defaultDurable();
    const showFooter = typeof p.showFooter === "boolean"
      ? p.showFooter
      : typeof legacyShowUsage === "boolean" ? legacyShowUsage : d.showFooter;
    return {
      ...d, ...persisted,
      pos: { ...d.pos, ...(p.pos || {}) },
      pillPos: { ...d.pillPos, ...(p.pillPos || {}) },
      size: { ...d.size, ...(p.size || {}) },
      options: { ...d.options, ...(p.options || {}) },
      showFooter,
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
  tokenId?: string,
  viewport?: { w: number; h: number },
): Partial<AgentCliDurable> {
  const patch: Partial<AgentCliDurable> = {
    ...(tokenId ? { tokenId } : {}),
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
// and the token / provider / model selections must survive independently.
//
// Conversation content, including tool results, is therefore written to browser
// localStorage on an admin-only page. It is bounded by the scrollback-lines
// setting and by MAX_CONV_BYTES below.
// ---------------------------------------------------------------------------

const CONV_KEY = "phx-agentcli-conversation";

// localStorage is ~5MB per origin and shared with the window state and every
// other Phoenix MCP key. Cap the conversation well under that: on overflow the
// oldest turns are dropped until it fits, which mirrors what the scrollback
// limit already does to the visible transcript.
const MAX_CONV_BYTES = 2_000_000;

interface Conversation {
  logConversationId: string;
  turns: unknown[];
  draft: string;
  usage: SessionUsage;
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
  if (record.type === "image") {
    return { type: "text", text: "[camera image unavailable after reload]" };
  }
  if (record.type === "image_url") {
    return { type: "text", text: "[camera image unavailable after reload]" };
  }

  return Object.fromEntries(
    Object.entries(record).map(([key, child]) => [key, sanitizeForPersistence(child)]),
  );
}

function defaultConversation(): Conversation {
  return { logConversationId: "", turns: [], draft: "", usage: { input: 0, output: 0, context: 0, noData: false } };
}

let conversation: Conversation = defaultConversation();
let loaded = false;

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
      };
    }
  } catch {
    // Corrupt or unreadable: start clean rather than breaking the window.
    conversation = defaultConversation();
  }
  return conversation;
}

function save(): void {
  const key = storageKey(CONV_KEY);
  if (!key) return;
  try {
    // Drop oldest turns until the payload fits. A single turn can be large on
    // its own (a tool result with many entities), so give up rather than loop
    // forever if even one turn will not fit.
    let turns = conversation.turns;
    for (;;) {
      const payload = JSON.stringify({
        ...conversation,
        turns: sanitizeForPersistence(turns),
      });
      if (payload.length <= MAX_CONV_BYTES) {
        localStorage.setItem(key, payload);
        return;
      }
      if (turns.length === 0) {
        // Keep the draft and usage even when no transcript fits.
        localStorage.setItem(key, JSON.stringify({ ...conversation, turns: [] }));
        return;
      }
      turns = turns.slice(1);
    }
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
  load().turns = turns;
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

if (typeof window !== "undefined") window.addEventListener("pagehide", () => flushSessionDraft());


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
