import React, { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { agentCliChat, api } from "../api";
import { ConnectionLostError, homeAssistantReachable, onConnectionChange, useHomeAssistantAway } from "../utils/connection";
import { renderMarkdown, flagsUnsafeContent } from "../utils/markdown";
import {
  ATTACHMENT_MAX_COUNT, AttachmentError, imageFiles, prepareAttachment, withoutSentImages,
  type ChatAttachment,
} from "../utils/chat_attachments";
import {
  getAgentCliOwner, flushSessionDraft, getDurable, patchDurable, getSessionTurns, setSessionTurns, getLogConversationId,
  getSessionDraft, setSessionDraft,
  getSessionUsage, setSessionUsage, onSavedConversationChange, announceRunningTurn, watchOtherTabTurn, type SessionUsage,
  AGENT_CHAT_TEXT_SIZE_MIN_PT, AGENT_CHAT_TEXT_SIZE_MAX_PT, AGENT_CHAT_TEXT_SIZE_STEP_PT,
  normalizeAgentCliTextSize,
} from "../utils/agentcli_state";
import { approvalStatusLabel, copyToClipboard, effortLevelLabel } from "../utils";
import { clearReasonDraft, getReasonDraft } from "../utils/approval_reason_draft";
import { localizedApprovalReason } from "../utils/approval_reason";
import { chatNoticeText, failureDetailText } from "../utils/chat_notice";
import { localizedExecutorError } from "../utils/approval_text";
import { subscribeApprovalEvents } from "../utils/approval_events";
import {
  AGENTCHAT_REVIEW_CLOSED_EVENT,
  AGENTCHAT_REVIEW_DECIDED_EVENT,
  notifyAgentChatReviewOpened,
  reviewDecisionApprovalId,
} from "../utils/agentchat_review";
import PHOENIX_ICON from "../../custom_components/phoenix_mcp/brand/icon.png";
import type {
  AgentCliProviderKind,
  AgentCliInstance,
  DeclaredModelCaps,
  AccessKeyRecord,
} from "../types";
import { displayedLanguage, hasMessage, localeClock, localeCompactNumber, localeNumber, resolveLanguage, t, tn } from "../i18n";
import { formatSignature } from "../i18n/format";
import { tRich } from "../i18n/rich";
import { registerAgentChatShortcut } from "../utils/agentchat_shortcut";
import { openPopupWindow } from "../ui/popup";
import { Icon } from "../ui/Icon";
import { MenuButton, MenuRootContext, type MenuEntry } from "../ui/Menu";
import { applyThemeClass, resolveThemeClass } from "../ui/theme";
import { isImeKey } from "../utils/ime";
import { StatusRegion } from "./common";

// One rendered item in the transcript. ts (epoch ms, stamped at creation) is
// optional: restored session entries may lack it, and the timestamp display
// skips those.
type ChatEntry =
  | { kind: "user"; text: string; ts?: number; images?: { name: string; dataUrl?: string }[] }
  | { kind: "assistant"; text: string; thinking: string; ts?: number }
  | { kind: "tool_call"; id: string; name: string; args: unknown }
  | { kind: "tool_result"; id: string; name: string; isError: boolean; summary: string }
  | { kind: "tool_image"; id: string; name: string; mimeType: string; dataUrl?: string; unavailable?: boolean }
  | { kind: "progress"; id: string; message: string; activity?: boolean }
  | { kind: "approval"; approvalId: string; toolName: string; status: string; reason?: string; executionReason?: boolean; checking?: boolean }
  | { kind: "notice"; code?: string; message: string; messageParams?: Record<string, string | number> }
  | { kind: "error"; code: string; message: string; messageParams?: Record<string, string | number>;
      rateLimit?: ProviderRateLimit; providerDetail?: string };

const COMPOSER_MAX_ROWS = 6;

// How often a streaming reply is drawn: each draw parses all of its text so far.
const LIVE_FRAME_MS = 50;

// How far above the end of the transcript still counts as being at its end.
const FOLLOW_SLOP_PX = 8;

/** Keep the composer at two visible rows, then grow with its content until six
 *  rows. Past that point it scrolls internally instead of consuming the chat. */
export function fitComposerTextarea(textarea: HTMLTextAreaElement): void {
  const view = textarea.ownerDocument.defaultView;
  const styles = view?.getComputedStyle(textarea);
  const fontSize = Number.parseFloat(styles?.fontSize || "") || 13;
  const rawLineHeight = styles?.lineHeight || "";
  const parsedLineHeight = Number.parseFloat(rawLineHeight);
  const lineHeight = Number.isFinite(parsedLineHeight)
    ? rawLineHeight.endsWith("px") ? parsedLineHeight : fontSize * parsedLineHeight
    : fontSize * 1.4;
  const verticalChrome = (Number.parseFloat(styles?.paddingTop || "") || 0)
    + (Number.parseFloat(styles?.paddingBottom || "") || 0)
    + (Number.parseFloat(styles?.borderTopWidth || "") || 0)
    + (Number.parseFloat(styles?.borderBottomWidth || "") || 0);
  const minHeight = lineHeight * 2 + verticalChrome;
  const maxHeight = lineHeight * COMPOSER_MAX_ROWS + verticalChrome;
  textarea.style.height = "auto";
  const contentHeight = Math.max(minHeight, Math.min(textarea.scrollHeight, maxHeight));
  textarea.style.height = `${Math.ceil(contentHeight)}px`;
  textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
}

interface ProviderRateLimit {
  tokens_minute_limit?: number;
  tokens_minute_remaining?: number;
  tokens_five_minute_limit?: number;
  tokens_five_minute_remaining?: number;
  tokens_month_limit?: number;
  tokens_month_remaining?: number;
  requests_minute_limit?: number;
  requests_minute_remaining?: number;
  requests_second_limit?: number;
  requests_second_remaining?: number;
  generic_limit?: number;
  generic_remaining?: number;
  query_cost?: number;
  retry_after_seconds?: number;
}

const RATE_LIMIT_FIELDS = [
  "tokens_minute_limit", "tokens_minute_remaining",
  "tokens_five_minute_limit", "tokens_five_minute_remaining",
  "tokens_month_limit", "tokens_month_remaining",
  "requests_minute_limit", "requests_minute_remaining",
  "requests_second_limit", "requests_second_remaining",
  "generic_limit", "generic_remaining", "query_cost", "retry_after_seconds",
] as const;

function providerRateLimit(value: unknown): ProviderRateLimit | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const source = value as Record<string, unknown>;
  const out: ProviderRateLimit = {};
  for (const field of RATE_LIMIT_FIELDS) {
    const candidate = source[field];
    if (typeof candidate === "number" && Number.isSafeInteger(candidate) && candidate >= 0) {
      out[field] = candidate;
    }
  }
  return Object.keys(out).length ? out : undefined;
}

// A completed turn: its entries plus the provider-format messages it produced.
export interface Turn {
  entries: ChatEntry[];
  messages: unknown[];
  lines: number;
}

interface GenOptions {
  thinking: boolean;
  effort: string;
  temperature: string;
  verbose: boolean;
}

// A single "Working: <tool>" line shown while a turn runs, so an operator with
// verbose off can tell working from hung. It is a live-only indicator: it moves
// to the bottom on each new tool call and is dropped when the turn is folded
// into the transcript, so the history keeps the reply rather than a trail of
// half-sentences about how it was produced.
const ACTIVITY_ID = "__activity__";

function isActivity(e: ChatEntry): boolean {
  return e.kind === "progress" && e.id === ACTIVITY_ID;
}

function stripActivity(entries: ChatEntry[]): ChatEntry[] {
  return entries.filter((e) => !isActivity(e));
}

// The string fields each saved entry kind needs to render.
const ENTRY_TEXT_FIELDS: Record<ChatEntry["kind"], string[]> = {
  user: ["text"],
  assistant: ["text", "thinking"],
  tool_call: ["id", "name"],
  tool_result: ["id", "name", "summary"],
  tool_image: ["id", "name", "mimeType"],
  progress: ["id", "message"],
  approval: ["approvalId", "toolName", "status"],
  notice: ["message"],
  error: ["code", "message"],
};

/** Keep only the saved turns and entries the window can render. Browser storage
 *  is outside Phoenix's control, and one malformed turn or entry would otherwise
 *  stop the window from opening on every reload. */
export function restorableTurns(saved: unknown[]): Turn[] {
  const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
  const renderable = (e: unknown): e is ChatEntry => {
    if (!isObject(e) || typeof e.kind !== "string" || !Object.prototype.hasOwnProperty.call(ENTRY_TEXT_FIELDS, e.kind)) return false;
    return ENTRY_TEXT_FIELDS[e.kind as ChatEntry["kind"]].every((field) => typeof e[field] === "string");
  };
  return saved.filter(isObject).flatMap((turn) => {
    if (!Array.isArray(turn.entries)) return [];
    const entries = turn.entries.filter(renderable);
    const lines = typeof turn.lines === "number" && Number.isFinite(turn.lines) ? turn.lines : entries.length;
    return [{ ...turn, entries, messages: Array.isArray(turn.messages) ? turn.messages : [], lines }];
  });
}

function countLines(entries: ChatEntry[]): number {
  let n = 0;
  for (const e of entries) {
    if (e.kind === "user" || e.kind === "assistant") {
      const txt = e.kind === "assistant" ? e.text : e.text;
      n += 1 + (txt.match(/\n/g)?.length ?? 0);
    } else {
      n += 1;
    }
  }
  return n;
}

export function trimTurns(turns: Turn[], scrollback: number): Turn[] {
  if (scrollback <= 0) return turns.slice(-1);
  const kept: Turn[] = [];
  let total = 0;
  for (let i = turns.length - 1; i >= 0; i--) {
    total += turns[i].lines;
    if (total > scrollback && kept.length > 0) break;
    kept.unshift(turns[i]);
  }
  return kept;
}

// The server refuses a request body over 1 MiB, images aside. Its count differs
// from requestBytes by the spaces Python's JSON puts after separators, which
// this margin covers.
const MEMORY_MAX_BYTES = 960_000;

/** What a value adds to the server's body count: JSON in ASCII, each non-ASCII
 *  UTF-16 unit taking a six-byte escape. Image bytes have a budget of their own. */
function requestBytes(value: unknown): number {
  const json = JSON.stringify(value, (_key, item: unknown) => {
    const type = item && typeof item === "object" ? (item as { type?: unknown }).type : undefined;
    return type === "image" || type === "image_url" ? null : item;
  }) ?? "";
  return json.length + 5 * (json.length - json.replace(/[\u0080-\uffff]/g, "").length);
}

/** The oldest turn the next request can carry within the budget. When older
 *  turns must go, memory starts at a turn the user opened: a turn resumed from
 *  the round-cap checkpoint carries on from the tool calls of the one before it. */
export function memoryStart(turns: Turn[], budget = MEMORY_MAX_BYTES): number {
  let total = 0;
  let start = turns.length;
  while (start > 0) {
    total += requestBytes(turns[start - 1].messages);
    if (total > budget) break;
    start -= 1;
  }
  if (start === 0) return 0;
  while (start < turns.length && (turns[start].entries[0]?.kind !== "user" || !turns[start].messages.length)) start += 1;
  return start;
}

/** Model memory for the next request. Only the newest attached images are
 *  resent, so follow-up questions can still look at them; earlier ones, and
 *  those too once a new message attaches images, become a short note. The
 *  oldest turns are left out once the rest would fill the request. */
export function memoryMessages(
  turns: Turn[], scrollback: number, replacingImages = false, budget = MEMORY_MAX_BYTES,
): unknown[] {
  if (scrollback <= 0) return [];
  const carriedImages = (turn: Turn) => turn.entries.some(
    (entry) => entry.kind === "user" && Array.isArray(entry.images) && entry.images.length > 0);
  let newest = -1;
  turns.forEach((turn, index) => { if (carriedImages(turn)) newest = index; });
  const start = memoryStart(turns, budget);
  return turns.flatMap((turn, index) => (index < start ? []
    : carriedImages(turn) && (index !== newest || replacingImages)
      ? withoutSentImages(turn.messages) : turn.messages));
}

// Wall-clock label for a transcript entry, in the viewer's locale convention.
function fmtClock(ts: number): string {
  return localeClock(ts);
}

// Compact token counts for the usage footer. CLDR owns both the scale and the
// suffix (Chinese groups by 10^4, so the right answer is 4.8万, not a
// translated "k"), so this defers to it entirely.
export function fmtTokens(n: number): string {
  return localeCompactNumber(n);
}

// One entry in a provider's Thinking dropdown.
export interface ThinkOption { value: string; label: string }

// The thinking/effort options a given provider+model actually exposes at the
// API level, so the gear shows the real levels for each backend rather than a
// generic scale. `style` says how a selected value maps to request options:
//   "effort"  -> the value is an effort level (or "off"), as for Claude and OpenAI
//   "boolean" -> the value is on/off only, as for Ollama's `think` flag
export interface ModelCaps {
  thinking: ThinkOption[];   // dropdown entries; [] => no thinking control
  style: "effort" | "boolean";
  defaultLevel: string;      // level used when the saved effort is not in the list
  temperature: boolean;      // temperature applies (given the current thinking state)
  note?: string;             // shown when thinking is intrinsic and not selectable
}

function thinkOpts(values: string[]): ThinkOption[] {
  return values.map((v) => ({ value: v, label: effortLevelLabel(v) }));
}

/** Narrow the shipped table by what the provider DECLARED about this model.
 *
 *  Declared capabilities REMOVE a control; only probed effort levels (below) may
 *  add one. A provider saying a model has no reasoning is authoritative; a
 *  provider saying nothing (most of them, and Ollama for temperature) leaves
 *  the table's answer standing. That asymmetry is the safety property: an
 *  absent field is never read as a limit, so a failed or unsupported lookup
 *  cannot strip a working model's controls.
 */
function applyDeclared(
  caps: ModelCaps, declared: DeclaredModelCaps | undefined,
  kind: AgentCliProviderKind,
): ModelCaps {
  if (!declared) return caps;
  const out = { ...caps };
  if (declared.thinking === false) out.thinking = [];
  if (declared.temperature === false) out.temperature = false;
  // Probed effort levels REPLACE the table's guess rather than narrowing it, and
  // may even CREATE a control the table does not offer (the aggregator case: one
  // key fronting many vendors, so no single built-in answer fits). That is sound
  // only because the probe proved the field is validated: an unvalidated field
  // records nothing, so a list here means the API accepted exactly these levels
  // and refused the rest. "off" is kept only if the table had it (it is Phoenix
  // not sending the field, not a level) and the levels hold no "none". A created
  // control gets no synthetic "off": omitting the field leaves the model at its
  // default reasoning, so an accepted "none" (shown as Off) is the only real off.
  // Ollama is the one deliberate conversion: its fallback is boolean for older
  // servers, but a successful per-model probe proves the endpoint validates
  // effort levels, so the backend knows how to send the resulting dropdown values.
  if (declared.effort_levels?.length
      && (out.style === "effort" || kind === "ollama" || kind === "ollama_cloud")) {
    out.style = "effort";
    const keepOff = out.thinking.some((o) => o.value === "off") && !declared.effort_levels.includes("none");
    out.thinking = [
      ...(keepOff ? thinkOpts(["off"]) : []),
      ...thinkOpts(declared.effort_levels),
    ];
    if (!declared.effort_levels.includes(out.defaultLevel)) {
      out.defaultLevel = declared.effort_levels[declared.effort_levels.length - 1];
    }
  }
  return out;
}

export function modelCaps(
  kind: AgentCliProviderKind, model: string, thinkingOn: boolean,
  declared?: DeclaredModelCaps,
): ModelCaps {
  return applyDeclared(shippedCaps(kind, model, thinkingOn), declared, kind);
}

/** Claude models that keep thinking when the request leaves it out (Claude 5 on). */
function claudeAlwaysThinks(m: string): boolean {
  return /^claude-(?:[a-z]+-)?(?:[5-9]|\d{2})(?:[-.]|$)/.test(m) || /^claude-fable/.test(m);
}

/** Google's per-model Gemini thinking levels, or [] for a model without them. */
function geminiLevels(m: string): string[] {
  if (/gemini-3-pro/.test(m)) return ["low", "high"];
  if (/gemini-3(\.\d+)?-flash(?!-lite)/.test(m) && !/gemini-3\.8/.test(m)) return ["minimal", "low", "medium", "high"];
  if (/gemini-(?:[4-9]|3\.)/.test(m)) return ["low", "medium", "high"];
  if (/gemini-2\.5-flash/.test(m)) return ["none", "low", "medium", "high"];
  if (/gemini-2\.5/.test(m)) return ["low", "medium", "high"];
  return [];
}

/** Whether a Grok model is at least major.minor (grok-4.20 is later than 4.6). */
function grokAtLeast(m: string, major: number, minor: number): boolean {
  const match = /^grok-(\d+)(?:[.-](\d+))?/.exec(m);
  if (!match) return false;
  const [have, sub] = [Number(match[1]), Number(match[2] ?? 0)];
  return have > major || (have === major && sub >= minor);
}

/** OpenAI's documented reasoning levels and default for one model id, or null.
 *
 *  From OpenAI's per-model pages. "none" turns reasoning off and is
 *  absent where a model cannot stop reasoning (GPT-6 Astra, GPT-6.1 Sol, GPT-5
 *  and the o-series); GPT-6 has no "minimal". The default is the model's own, so
 *  an untouched control keeps OpenAI's behaviour.
 */
function openaiReasoning(m: string): { levels: string[]; defaultLevel: string } | null {
  if (/^gpt-6-astra|^gpt-6\.1-sol/.test(m))
    return { levels: ["low", "medium", "high", "xhigh", "max"], defaultLevel: "medium" };
  if (/^gpt-6|^gpt-5\.[6-9]/.test(m))
    return { levels: ["none", "low", "medium", "high", "xhigh", "max"], defaultLevel: "medium" };
  if (/^gpt-5\.5/.test(m))
    return { levels: ["none", "low", "medium", "high", "xhigh"], defaultLevel: "medium" };
  if (/^gpt-5\.[2-4]/.test(m))
    return { levels: ["none", "low", "medium", "high", "xhigh"], defaultLevel: "none" };
  if (/^gpt-5\.1/.test(m))
    return { levels: ["none", "low", "medium", "high"], defaultLevel: "none" };
  if (/^gpt-5/.test(m))
    return { levels: ["minimal", "low", "medium", "high"], defaultLevel: "medium" };
  if (/^o\d/.test(m))
    return { levels: ["low", "medium", "high"], defaultLevel: "medium" };
  return null;
}

function shippedCaps(kind: AgentCliProviderKind, model: string, thinkingOn: boolean): ModelCaps {
  const m = (model || "").toLowerCase();
  switch (kind) {
    case "claude":
      // Anthropic output_config.effort. Opus does not take temperature. Leaving
      // thinking out turns it off only before the Claude 5 generation; from it
      // on, the model still thinks adaptively, so no Off is offered there.
      return { thinking: thinkOpts([...(claudeAlwaysThinks(m) ? [] : ["off"]), "low", "medium", "high", "xhigh", "max"]),
               style: "effort", defaultLevel: "high", temperature: false };
    case "deepseek":
      // A thinking on/off toggle plus reasoning_effort low/high/max, default high;
      // thinking mode ignores temperature. Nothing about the model NAME says which
      // effort set it takes, so this table is a starting point that the provider
      // card's capability refresh corrects rather than the last word.
      return { thinking: thinkOpts(["off", "low", "high", "max"]), style: "effort",
               defaultLevel: "high", temperature: !thinkingOn };
    case "chatgpt": {
      // OpenAI reasoning.effort (Responses API). Reasoning models reject a custom
      // temperature; plain gpt-* models have no thinking control and take it.
      const reasoning = openaiReasoning(m);
      if (reasoning)
        return { thinking: thinkOpts(reasoning.levels), style: "effort",
                 defaultLevel: reasoning.defaultLevel, temperature: false };
      return { thinking: [], style: "effort", defaultLevel: "high", temperature: true };
    }
    case "gemini": {
      // Gemini via Google's OpenAI-compatible endpoint: reasoning_effort maps to
      // the model's thinking level. Google strongly recommends NOT setting
      // temperature on its reasoning models, so none is offered. Levels follow
      // Google's per-model table; reasoning cannot be turned off on Gemini 3 or
      // later, and only 2.5 Flash takes "none".
      const levels = geminiLevels(m);
      // Gemini 3 and later default to high thinking; 2.5 to a medium budget.
      return { thinking: thinkOpts(levels), style: "effort",
               defaultLevel: /gemini-2\./.test(m) ? "medium" : "high", temperature: false };
    }
    case "grok": {
      // xAI reasoning_effort. Grok 4.5 and later always reason (default high):
      // low/medium/high, plus xhigh from 4.6. The multi-agent model reads the
      // field as an agent count, so it gets no control. Older reasoning models
      // take low/high and can run without it. Sending an effort marks a
      // reasoning turn, which drops temperature.
      if (m.includes("multi-agent")) return { thinking: [], style: "effort", defaultLevel: "high", temperature: false };
      if (grokAtLeast(m, 4, 5))
        return { thinking: thinkOpts(["low", "medium", "high", ...(grokAtLeast(m, 4, 6) ? ["xhigh"] : [])]),
                 style: "effort", defaultLevel: "high", temperature: false };
      return { thinking: thinkOpts(["off", "low", "high"]), style: "effort",
               defaultLevel: "low", temperature: !thinkingOn };
    }
    case "kimi": {
      // Kimi splits the control by model family. K3 takes reasoning_effort
      // (low/high/max) and always reasons, so there is no off. K2.x has no effort
      // levels but takes a thinking on/off toggle. Neither accepts temperature;
      // only the legacy moonshot-v1 models do, and they have no thinking control.
      if (m.startsWith("kimi-k3"))
        return { thinking: thinkOpts(["low", "high", "max"]), style: "effort",
                 defaultLevel: "high", temperature: false };
      if (m.startsWith("kimi"))
        return { thinking: thinkOpts(["off", "on"]), style: "boolean",
                 defaultLevel: "on", temperature: false };
      return { thinking: [], style: "effort", defaultLevel: "high", temperature: true };
    }
    case "meta":
      // Meta's Model API reasoning_effort. Muse models always reason (they reject
      // "none" with a 400), so no off is offered; Muse Wonder and Spark 1.3 also
      // take "max". Meta tunes the models for their default temperature, so
      // temperature is not offered either.
      return { thinking: thinkOpts(["minimal", "low", "medium", "high", "xhigh",
                                    ...(/^muse-wonder|^muse-spark-1\.3(?!.*contributor)/.test(m) ? ["max"] : [])]),
               style: "effort", defaultLevel: "medium", temperature: false };
    case "minimax":
      // MiniMax via its Anthropic-compatible API (no custom temperature). M3.1
      // Flash takes an effort (default max, no "none"); M3 has an on/off adaptive
      // toggle; M2 models always reason, so they get no control.
      if (/^minimax-m3\.1/.test(m))
        return { thinking: thinkOpts(["low", "medium", "high", "max"]), style: "effort",
                 defaultLevel: "max", temperature: false };
      if (/^minimax-m2/.test(m)) return { thinking: [], style: "effort", defaultLevel: "high", temperature: false };
      return { thinking: thinkOpts(["off", "on"]), style: "boolean", defaultLevel: "on", temperature: false };
    case "mistral":
      // Mistral's reasoning models: Small 4 and Medium 3.5 take none/high,
      // Large 4 takes low/medium/high and always reasons, and the hosted GLM 5.3
      // takes low/high/max. Other models keep their own default. Chat
      // Completions supports temperature in every case.
      if (/^mistral-(small-(latest|2603|4)|medium-(latest|3-5))/.test(m))
        return { thinking: thinkOpts(["none", "high"]), style: "effort",
                 defaultLevel: "high", temperature: true };
      if (/^mistral-large-(latest|4)/.test(m))
        return { thinking: thinkOpts(["low", "medium", "high"]), style: "effort",
                 defaultLevel: "high", temperature: true };
      if (/^zai-glm-5/.test(m))
        return { thinking: thinkOpts(["low", "high", "max"]), style: "effort",
                 defaultLevel: "max", temperature: true };
      return { thinking: [], style: "effort", defaultLevel: "high", temperature: true };
    case "ollama":
    case "ollama_cloud":
      // Boolean is the compatibility fallback until a per-model probe proves
      // which reasoning_effort levels this Ollama endpoint validates. Cloud is
      // the same wire format, just hosted.
      return { thinking: thinkOpts(["off", "on"]), style: "boolean", defaultLevel: "on", temperature: true };
    case "openrouter":
    case "infron":
    case "nvidia":
    case "opencode":
    case "tencent":
      // Aggregators: one key fronting many vendors' models, so no built-in
      // answer fits and none is guessed at. Checking a model's options
      // establishes its real levels per model, and those CREATE the control here
      // rather than only narrowing it. Until then the model still reasons at its
      // own default; only the ability to choose is missing.
      return { thinking: [], style: "effort", defaultLevel: "high", temperature: true };
    case "zai":
      // GLM 5.2 and later take reasoning_effort (default max); 5.3 and 4.7
      // cannot turn thinking off, and 5.2 turns it off with "none".
      if (/^glm-5\.[3-9]|^glm-[6-9]/.test(m))
        return { thinking: thinkOpts(["low", "high", "max"]), style: "effort",
                 defaultLevel: "max", temperature: false };
      if (/^glm-5\.2/.test(m))
        return { thinking: thinkOpts(["none", "low", "medium", "high", "max"]), style: "effort",
                 defaultLevel: "max", temperature: false };
      if (/^glm-4\.7/.test(m)) return { thinking: [], style: "effort", defaultLevel: "high", temperature: false };
      return { thinking: thinkOpts(["off", "on"]), style: "boolean",
               defaultLevel: "on", temperature: !thinkingOn };
    case "mimo":
    case "qwen":
      // These providers publish a boolean thinking control. Their APIs own the
      // reasoning depth, so Phoenix exposes only on and off and omits a custom
      // temperature while thinking is enabled.
      return { thinking: thinkOpts(["off", "on"]), style: "boolean",
               defaultLevel: "on", temperature: !thinkingOn };
    case "groq":
    case "together":
    case "cerebras":
    case "fireworks":
      // The protocol accepts ordinary sampling controls. Reasoning remains
      // unknown until the account probe proves model-specific effort levels.
      return { thinking: [], style: "effort", defaultLevel: "high", temperature: true };
    default:
      return { thinking: [], style: "effort", defaultLevel: "high", temperature: false };
  }
}

function hasOff(caps: ModelCaps): boolean {
  return caps.thinking.some((opt) => opt.value === "off");
}

// The dropdown value for the current options.
export function thinkValue(caps: ModelCaps, o: GenOptions): string {
  if (caps.style === "boolean") return o.thinking ? "on" : "off";
  if (hasOff(caps) && !o.thinking) return "off";
  const levels = caps.thinking.filter((opt) => opt.value !== "off").map((opt) => opt.value);
  return levels.includes(o.effort) ? o.effort : caps.defaultLevel;
}

// Fold a chosen dropdown value back into the thinking/effort options.
export function applyThink(
  caps: ModelCaps, v: string, set: (fn: (o: GenOptions) => GenOptions) => void,
): void {
  if (caps.style === "boolean") { set((o) => ({ ...o, thinking: v === "on" })); return; }
  if (v === "off") { set((o) => ({ ...o, thinking: false })); return; }
  set((o) => ({ ...o, thinking: true, effort: v }));
}

export function buildOptions(
  kind: AgentCliProviderKind, o: GenOptions, caps: ModelCaps,
): Record<string, unknown> {
  if (kind === "claude") return { thinking: o.thinking, effort: o.effort, show_thinking: o.verbose };
  const out: Record<string, unknown> = { show_thinking: o.verbose };
  if (caps.thinking.length) {
    if (caps.style === "boolean") {
      out.thinking = o.thinking;
    } else if (hasOff(caps)) {
      // DeepSeek: an on/off toggle plus a reasoning_effort level when on.
      out.thinking = o.thinking;
      if (o.thinking) out.effort = thinkValue(caps, o);
    } else {
      // OpenAI reasoning models: always on, effort only.
      out.effort = thinkValue(caps, o);
    }
  }
  if (caps.temperature && o.temperature.trim() !== "") {
    const temp = Number(o.temperature);
    if (!Number.isNaN(temp)) out.temperature = temp;
  }
  return out;
}

interface Props {
  accessKeys: AccessKeyRecord[];
  instances: AgentCliInstance[];
  scrollbackLines: number;
  initialAccessKeyId: string;
  onClose: () => void;
  hass?: unknown;
  getHass?: () => unknown;
  summonVersion?: number;
  /** Raised each time the user opens or summons the window: the message box then takes focus. */
  focusRequest?: number;
  /** My Workspace's Agent Chat slot while it is on screen: the window draws itself into it. */
  dockMount?: HTMLElement | null;
  /** Whether Agent Chat belongs in My Workspace, and the menu item that changes it; absent where it cannot dock. */
  dock?: { docked: boolean; toggle: () => void } | null;
}

interface AgentCliHass {
  enableShortcuts?: boolean;
  themes?: { darkMode?: boolean };
}

/** How much of a finished reply is read aloud; the rest is in the transcript. */
const ANNOUNCE_MAX_CHARS = 300;

/** Notices the backend sends as a turn's last word. Nothing follows them, so they are said. */
const TURN_END_NOTICES = new Set(["empty_turn", "max_tokens_stop"]);

/** The plain text a reply renders to, cut at a word when it is long. */
export function replyAnnouncement(markdown: string): string {
  const box = document.createElement("div");
  box.innerHTML = renderMarkdown(markdown);
  const text = (box.textContent ?? "").replace(/\s+/g, " ").trim();
  if (text.length <= ANNOUNCE_MAX_CHARS) return text;
  const cut = text.slice(0, ANNOUNCE_MAX_CHARS);
  const space = cut.lastIndexOf(" ");
  return t("agentchat.announceReplyStart", { text: space > ANNOUNCE_MAX_CHARS / 2 ? cut.slice(0, space) : cut });
}

const POPUP_WINDOW_NAME = "phoenix-mcp-agent-chat";
const POPUP_MOUNT_ID = "phx-agentchat-popout-root";
let activePopupWindow: Window | null = null;

/** Bring the live Agent Chat popup forward without remounting its React tree.
 * Browsers may decline focus-stealing requests, but a direct button gesture is
 * the context in which this request has the best chance of being honored. */
export function focusAgentCliPopup(): boolean {
  const popup = activePopupWindow;
  if (!popup) return false;
  try {
    if (popup.closed) {
      activePopupWindow = null;
      return false;
    }
    popup.focus();
  } catch {
    // Keep the live popup mounted if browser policy refuses the focus request.
  }
  return true;
}

function forgetAgentCliPopup(popup: Window | null): void {
  if (activePopupWindow === popup) activePopupWindow = null;
}

function applyPopupTheme(popup: Window, hass: unknown): void {
  const darkMode = (hass as AgentCliHass | null | undefined)?.themes?.darkMode;
  applyThemeClass(popup.document.documentElement, resolveThemeClass(darkMode));
}

const RESIZE_MIN_W = 320, RESIZE_MIN_H = 280;
const VIEWPORT_MARGIN = 8;
// The desktop minimized titlebar footprint, used to keep its remembered
// position within the viewport. Mobile uses the compact icon-only pill below.
const PILL_SIZE = { w: 220, h: 44 };
const MOBILE_PILL_SIZE = { w: 44, h: 44 };
// A finger drifts a little during a tap; only a longer move drags the phone circle.
const PILL_DRAG_THRESHOLD = 6;

function currentPillSize(): { w: number; h: number } {
  return isNarrowViewport() ? MOBILE_PILL_SIZE : PILL_SIZE;
}

// The window is normal (windowed), minimized to a pill, or maximized full screen.
type WinMode = "normal" | "min" | "max";

// Same breakpoint as the panel's mobile tab-bar split. Guarded because jsdom
// (the test environment) has no matchMedia.
function isNarrowViewport(): boolean {
  return typeof window.matchMedia === "function"
    && window.matchMedia("(max-width: 700px)").matches;
}

/** Convert Home Assistant's resolved safe-area value into the drag boundary.
 *  HA supplies the actual companion-app inset through --safe-area-inset-top;
 *  the ordinary viewport margin is the only fallback when it is unavailable. */
export function resolveAgentCliTopMargin(cssTop: string): number {
  const parsed = Number.parseFloat(cssTop);
  return Number.isFinite(parsed) && parsed >= 0
    ? Math.max(VIEWPORT_MARGIN, parsed)
    : VIEWPORT_MARGIN;
}

/** Read the same safe top used by maximized CSS. scroll-margin-top has no visual
 *  effect on this fixed window; it is only a computed-style bridge that lets JS
 *  include Home Assistant's resolved safe-area inset in drag/restore geometry. */
function agentCliTopMargin(el: HTMLElement | null): number {
  if (!isNarrowViewport()) return VIEWPORT_MARGIN;
  const ownerWindow = el?.ownerDocument.defaultView;
  const cssTop = el && ownerWindow
    ? ownerWindow.getComputedStyle(el).scrollMarginTop
    : "";
  return resolveAgentCliTopMargin(cssTop);
}

/** Compute a resized window rect, snapping the edge(s) being dragged to the
 *  viewport margin exactly as the drag handler snaps the whole window. The
 *  `<= SNAP` test is true both when an edge nears the boundary (snap) and when
 *  it has crossed it (clamp), so a resize can never push an edge off the page.
 *  Pure, so it is unit-tested directly. */
export function snapResizeRect(
  dir: string,
  o: { x: number; y: number; w: number; h: number },
  dx: number, dy: number, vw: number, vh: number,
  topMargin = VIEWPORT_MARGIN,
): { x: number; y: number; w: number; h: number } {
  const M = VIEWPORT_MARGIN, SNAP = 56;
  let w = o.w, h = o.h, x = o.x, y = o.y;
  const rightAnchor = o.x + o.w;   // fixed edge when dragging west
  const bottomAnchor = o.y + o.h;  // fixed edge when dragging north
  if (dir.includes("e")) {
    w = Math.max(RESIZE_MIN_W, o.w + dx);
    if (vw - (o.x + w) <= SNAP) w = Math.max(RESIZE_MIN_W, vw - M - o.x);
  }
  if (dir.includes("s")) {
    h = Math.max(RESIZE_MIN_H, o.h + dy);
    if (vh - (o.y + h) <= SNAP) h = Math.max(RESIZE_MIN_H, vh - M - o.y);
  }
  if (dir.includes("w")) {
    w = Math.max(RESIZE_MIN_W, o.w - dx);
    x = rightAnchor - w;
    if (x <= SNAP) { w = Math.max(RESIZE_MIN_W, rightAnchor - M); x = rightAnchor - w; }
  }
  if (dir.includes("n")) {
    h = Math.max(RESIZE_MIN_H, o.h - dy);
    y = bottomAnchor - h;
    if (y - topMargin <= SNAP) {
      y = topMargin;
      h = Math.max(RESIZE_MIN_H, bottomAnchor - y);
    }
  }
  return { x, y, w, h };
}

/** Clamp a window's top-left so a window of the given size sits fully within the
 *  viewport (margin M), pinning to the margin when it is larger than the view.
 *  Used on restore: the minimized pill may have been dragged somewhere a full
 *  size window would overflow, and it must not unfold off-screen. Pure, so it is
 *  unit-tested directly. */
export function clampPosToViewport(
  pos: { x: number; y: number },
  size: { w: number; h: number },
  vw: number, vh: number,
  topMargin = VIEWPORT_MARGIN,
): { x: number; y: number } {
  const M = VIEWPORT_MARGIN;
  return {
    x: Math.min(Math.max(M, pos.x), Math.max(M, vw - size.w - M)),
    y: Math.min(Math.max(topMargin, pos.y), Math.max(topMargin, vh - size.h - M)),
  };
}

/** The code of the notice that older messages were left out of what is sent. */
const MEMORY_TRIMMED = "memory_trimmed";

/** Statuses a card never asks about again. */
const TERMINAL_APPROVAL_STATUSES: ReadonlySet<string> = new Set(["approved", "rejected", "cancelled", "expired", "failed", "resolved"]);

export function AgentCliWindow({
  accessKeys, instances, scrollbackLines, initialAccessKeyId, onClose, hass = null, getHass: getLiveHass,
  summonVersion, focusRequest = 0, dockMount = null, dock = null,
}: Props) {
  // Restore persisted window state (survives navigation away/back and reopen).
  const persistenceOwner = useRef(getAgentCliOwner()).current;
  const saved = useRef(getDurable()).current;
  const firstInstance = (instances.some((i) => i.id === saved.instanceId) && saved.instanceId)
    || instances[0]?.id || "";

  const [accessKeyId, setAccessKeyId] = useState(saved.accessKeyId || initialAccessKeyId || accessKeys[0]?.id || "");
  const [instanceId, setInstanceId] = useState<string>(firstInstance);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState<string>(saved.model);
  const restoredApprovals = useRef(new Set<string>());
  // Turns taken from another tab's save: already in storage, so not written back.
  const adoptedTurns = useRef<Turn[] | null>(null);
  const [turns, setTurns] = useState<Turn[]>(() => (saved.freshConversation ? [] : restorableTurns(getSessionTurns())).map((turn) => ({
    ...turn,
    entries: turn.entries.map((entry) => {
      if (entry.kind !== "approval" || TERMINAL_APPROVAL_STATUSES.has(entry.status)) return entry;
      restoredApprovals.current.add(entry.approvalId);
      return { ...entry, checking: true };
    }),
  })));
  const [live, setLive] = useState<ChatEntry[]>([]);
  // Seeded from the session draft so a half-written prompt survives the panel
  // unmounting (navigating away in HA), the same way the transcript does. The
  // wizard's one-shot prefill still wins when present.
  const [input, setInput] = useState(saved.prefill || getSessionDraft());
  // Images waiting in the composer. Session-only: never written to browser storage.
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [attachError, setAttachError] = useState("");
  const [sending, setSending] = useState(false);
  const [otherTabTurn, setOtherTabTurn] = useState(false);
  // The server's account of the current model call, shown in place of "Working…".
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const turnGenerationRef = useRef(0);
  // Non-null when the last turn paused at the round-cap checkpoint (its value is
  // the round count reached); drives the Continue/Stop control.
  const [pendingContinue, setPendingContinue] = useState<number | null>(null);
  const [mode, setMode] = useState<WinMode>(() => {
    if (saved.minimized) return "min";
    // No stored maximize preference: mobile defaults to full screen.
    if (saved.maximized ?? isNarrowViewport()) return "max";
    return "normal";
  });
  const minimized = mode === "min";
  const maximized = mode === "max";
  // The connection fields (key, provider, model and its options) open under the line that names them.
  const [connectionOpen, setConnectionOpen] = useState(false);
  const connectionId = useId();
  const [options, setOptions] = useState<GenOptions>(saved.options);
  const [showFooter, setShowFooter] = useState(saved.showFooter);
  const [showTimestamps, setShowTimestamps] = useState(saved.showTimestamps);
  const [textSizePt, setTextSizePt] = useState(() => normalizeAgentCliTextSize(saved.textSizePt));
  // Provider-reported token usage for this conversation (session lifecycle,
  // like the transcript). The stream's usage events carry TURN-cumulative
  // totals, so each event is added to the session base captured at send time.
  const [usage, setUsage] = useState<SessionUsage>(() => (saved.freshConversation ? { input: 0, output: 0, context: 0, noData: false } : getSessionUsage()));
  // Screen-reader announcement of discrete chat events (completed replies,
  // approvals, errors). Deliberately NOT the streaming transcript itself:
  // a live region over token-by-token deltas would announce every chunk.
  const [announcement, setAnnouncement] = useState("");
  // Cleared first, so a repeated message is said again.
  const announcerRef = useRef<HTMLDivElement>(null);
  // The frame comes from the window showing the announcer: a hidden document runs none, and a
  // popped-out Chat stays in view while the main page behind it is hidden.
  const announce = useCallback((text: string) => {
    setAnnouncement("");
    const ownerWindow = announcerRef.current?.ownerDocument.defaultView ?? window;
    ownerWindow.requestAnimationFrame(() => setAnnouncement(text));
  }, []);
  const [popupMount, setPopupMount] = useState<HTMLElement | null>(null);
  const [popOutError, setPopOutError] = useState("");
  const poppedOut = popupMount !== null;
  // Docked in My Workspace: the workspace window gives the size and place, and its title bar moves and closes it.
  const docked = dockMount !== null && !poppedOut;
  const [narrowViewport, setNarrowViewport] = useState(isNarrowViewport);
  const agentChatTitle = t("agentchat.title");
  const presentation = `${displayedLanguage()} ${formatSignature()}`;
  // Home Assistant away: the chat floats over every page and pops out into a window of its
  // own, where the panel's notice cannot reach it, so it says so and takes no work.
  const away = useHomeAssistantAway(() => api.getSettings());
  const currentHass = useCallback(() => getLiveHass?.() ?? hass, [getLiveHass, hass]);
  const agentChatLanguage = resolveLanguage(currentHass());

  const [pos, setPos] = useState(() =>
    saved.pos.x >= 0 ? saved.pos : { x: Math.max(16, window.innerWidth - 480), y: 96 });
  const [pillPos, setPillPos] = useState(saved.pillPos);
  const circleDragged = useRef(false);
  const [size, setSize] = useState(saved.size);
  const [reviewingApprovalId, setReviewingApprovalId] = useState<string | null>(null);

  const rootRef = useRef<HTMLDivElement>(null);
  // Menus open inside the window, so they follow it into a separate browser window.
  const [rootEl, setRootEl] = useState<HTMLDivElement | null>(null);
  const attachRoot = useCallback((el: HTMLDivElement | null) => { rootRef.current = el; setRootEl(el); }, []);
  const bodyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const popupRef = useRef<Window | null>(null);
  const closingPopupRef = useRef(false);
  const cancelledRef = useRef(false);
  const lastQueryRef = useRef("");
  // Images sent with the running turn, restored to the composer if it is cancelled.
  const lastAttachmentsRef = useRef<ChatAttachment[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // The authoritative copy of the running turn. Every change goes through
  // updateLive, which writes the ref synchronously: a stream whose last event and
  // end arrive together finalizes the turn before React renders that event, and
  // a ref mirrored at render time would fold the turn without it.
  const liveRef = useRef<ChatEntry[]>([]);
  const updateLive = useCallback((next: ChatEntry[] | ((prev: ChatEntry[]) => ChatEntry[])) => {
    liveRef.current = typeof next === "function" ? next(liveRef.current) : next;
    setLive(liveRef.current);
  }, []);
  // Tokens can arrive far faster than they can be read. The ref stays current on
  // each one; the transcript is drawn at most once a frame, on a timer because a
  // hidden main page stops animation frames while its pop-out window is on screen.
  const liveFrame = useRef<ReturnType<typeof setTimeout> | null>(null);
  const streamLive = useCallback((next: (prev: ChatEntry[]) => ChatEntry[]) => {
    liveRef.current = next(liveRef.current);
    if (liveFrame.current !== null) return;
    setLive(liveRef.current);
    liveFrame.current = setTimeout(() => {
      liveFrame.current = null;
      setLive(liveRef.current);
    }, LIVE_FRAME_MS);
  }, []);
  const usageRef = useRef(usage);
  usageRef.current = usage;
  // Session totals at the moment the current turn started; the turn's
  // cumulative usage events are added on top of this base.
  const usageBaseRef = useRef({ input: 0, output: 0 });
  // Whether any usage event arrived during the current turn: a turn that
  // completes without one (and with nothing counted yet) marks the footer as
  // "this provider reports no usage" instead of a forever-zero counter.
  const turnSawUsageRef = useRef(false);
  // Skip the conversation-reset effect on the initial mount so a restored
  // transcript is not wiped when the window reopens.
  const firstRun = useRef(true);
  const lastSummonVersion = useRef(summonVersion);
  // Set only when Review itself minimized a narrow in-app window. This keeps a
  // later successful panel decision distinct from an ordinary/manual minimize.
  const reviewRestoreRef = useRef<{ approvalId: string; mode: Exclude<WinMode, "min"> } | null>(null);

  useEffect(() => {
    const restoreReviewedChat = (event: Event) => {
      const approvalId = reviewDecisionApprovalId(event);
      const pending = reviewRestoreRef.current;
      if (!approvalId) return;
      setReviewingApprovalId((current) => current === approvalId ? null : current);
      if (!pending || approvalId !== pending.approvalId) return;
      reviewRestoreRef.current = null;
      // Do not override the operator if they already expanded the window while
      // reviewing; otherwise return to the exact mode Review minimized.
      setMode((current) => current === "min" ? pending.mode : current);
    };
    const revealReviewedChatPill = (event: Event) => {
      const approvalId = reviewDecisionApprovalId(event);
      if (!approvalId) return;
      setReviewingApprovalId((current) => current === approvalId ? null : current);
    };
    window.addEventListener(AGENTCHAT_REVIEW_DECIDED_EVENT, restoreReviewedChat);
    window.addEventListener(AGENTCHAT_REVIEW_CLOSED_EVENT, revealReviewedChatPill);
    return () => {
      window.removeEventListener(AGENTCHAT_REVIEW_DECIDED_EVENT, restoreReviewedChat);
      window.removeEventListener(AGENTCHAT_REVIEW_CLOSED_EVENT, revealReviewedChatPill);
    };
  }, []);

  // A swipe that starts on the selectors, composer, or a transcript with no
  // scroll range has no scroll container of its own, so a mobile browser
  // otherwise hands it to the HA page beneath the transparent global overlay.
  // Redirect that gesture to the transcript. A populated, scrollable
  // transcript stays native-scrolling, with CSS containing its boundary
  // overscroll so it cannot chain into the page below.
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    let lastY: number | null = null;
    const ownsGesture = (target: EventTarget | null): boolean => {
      const element = target as { closest?: (selector: string) => Element | null } | null;
      if (typeof element?.closest !== "function") return false;
      if (element.closest(".agentcli-connection, .agentcli-input, .agentcli-continue, .agentcli-footer")) {
        return true;
      }
      const transcript = bodyRef.current;
      return transcript !== null
        && element.closest(".agentcli-body") === transcript
        && transcript.scrollHeight <= transcript.clientHeight;
    };
    const start = (event: TouchEvent) => {
      event.stopPropagation();
      lastY = ownsGesture(event.target) && event.touches.length === 1
        ? event.touches[0].clientY
        : null;
    };
    const move = (event: TouchEvent) => {
      event.stopPropagation();
      if (lastY === null || event.touches.length !== 1) return;
      const nextY = event.touches[0].clientY;
      if (bodyRef.current) bodyRef.current.scrollTop += lastY - nextY;
      lastY = nextY;
      if (event.cancelable) event.preventDefault();
    };
    const end = (event: TouchEvent) => {
      event.stopPropagation();
      lastY = null;
    };
    root.addEventListener("touchstart", start, { passive: true });
    root.addEventListener("touchmove", move, { passive: false });
    root.addEventListener("touchend", end, { passive: true });
    root.addEventListener("touchcancel", end, { passive: true });
    return () => {
      root.removeEventListener("touchstart", start);
      root.removeEventListener("touchmove", move);
      root.removeEventListener("touchend", end);
      root.removeEventListener("touchcancel", end);
    };
  }, [popupMount]);

  const instance = useMemo(() => instances.find((i) => i.id === instanceId), [instances, instanceId]);
  const kind: AgentCliProviderKind = instance?.kind ?? "claude";
  const instanceModel = instance?.model ?? "";
  // What this account's provider declared about the selected model, when it has
  // been refreshed at all. Absent for every provider that publishes nothing,
  // which leaves the shipped table's answer standing.
  const declaredCaps = useMemo(
    () => instances.find((i) => i.id === instanceId)?.capabilities?.[model],
    [instances, instanceId, model]);
  const caps = useMemo(() => modelCaps(kind, model, options.thinking, declaredCaps),
                       [kind, model, options.thinking, declaredCaps]);

  // Persist the selections, options, and geometry so they survive reopen/reload.
  useEffect(() => { patchDurable({ accessKeyId }, persistenceOwner); }, [persistenceOwner, accessKeyId]);
  useEffect(() => { patchDurable({ instanceId }, persistenceOwner); }, [instanceId, persistenceOwner]);
  useEffect(() => { patchDurable({ model }, persistenceOwner); }, [model, persistenceOwner]);
  useEffect(() => { patchDurable({ options }, persistenceOwner); }, [options, persistenceOwner]);
  useEffect(() => { patchDurable({ showFooter }, persistenceOwner); }, [persistenceOwner, showFooter]);
  useEffect(() => { patchDurable({ showTimestamps }, persistenceOwner); }, [persistenceOwner, showTimestamps]);
  useEffect(() => { patchDurable({ textSizePt }, persistenceOwner); }, [persistenceOwner, textSizePt]);
  useEffect(() => { patchDurable({ minimized, maximized }, persistenceOwner); }, [minimized, maximized, persistenceOwner]);
  // The prefill is one-shot: consumed into the input above, then cleared so it
  // does not reappear on a later open.
  useEffect(() => { if (saved.prefill) patchDurable({ prefill: "" }, persistenceOwner); }, [persistenceOwner, saved.prefill]);
  // A fresh conversation is one-shot too: the cleared transcript is saved by the
  // effect below, and the usage counters are saved here.
  useEffect(() => {
    if (!saved.freshConversation) return;
    patchDurable({ freshConversation: false }, persistenceOwner);
    setSessionUsage({ input: 0, output: 0, context: 0, noData: false }, persistenceOwner);
  }, [persistenceOwner, saved.freshConversation]);
  // The transcript is kept in this browser's storage for the Home Assistant user
  // (utils/agentcli_state), so it survives a remount and a reload.
  useEffect(() => {
    if (turns !== adoptedTurns.current) setSessionTurns(turns, persistenceOwner);
  }, [persistenceOwner, turns]);
  // Mirror the unsent message box into the same session tier. Done as an effect
  // rather than at each setInput call site so every path stays in sync: typing,
  // send and /clear (which set it to ""), and the cancel path that restores the
  // last query.
  useEffect(() => { setSessionDraft(input, persistenceOwner); }, [input, persistenceOwner]);

  useEffect(() => {
    if (inputRef.current) fitComposerTextarea(inputRef.current);
  }, [input, popupMount, textSizePt]);

  // Opening Agent Chat, or moving it into or out of its own window, puts focus in the message
  // box (or on the window when there is nothing to type into): otherwise a keyboard user has to
  // find it, last in the page after everything Home Assistant shows.
  const focusComposer = useCallback(() => {
    window.requestAnimationFrame(() => {
      const box = inputRef.current;
      if (box && !box.disabled) { box.focus({ preventScroll: true }); return; }
      const win = rootEl;
      if (!win) return;
      if (!win.hasAttribute("tabindex")) win.tabIndex = -1;
      win.focus({ preventScroll: true });
    });
  }, [rootEl]);
  const handledFocus = useRef(0);
  const folded = minimized && !poppedOut && !docked;
  useEffect(() => {
    if (!focusRequest || focusRequest === handledFocus.current || folded) return;
    handledFocus.current = focusRequest;
    focusComposer();
  }, [focusRequest, folded, focusComposer]);
  const lastMount = useRef(popupMount);
  useEffect(() => {
    if (lastMount.current === popupMount) return;
    lastMount.current = popupMount;
    focusComposer();
  }, [popupMount, focusComposer]);

  // The global Agent Chat button can be pressed while this live component is
  // already mounted as a minimized pill. Rehydrate the geometry that the button
  // just wrote to durable state, then unfold without remounting the component or
  // interrupting an active conversation. Mobile returns to its safe maximized
  // layout; desktop returns to the ordinary windowed layout.
  useEffect(() => {
    if (summonVersion === undefined || summonVersion === lastSummonVersion.current) return;
    lastSummonVersion.current = summonVersion;
    setReviewingApprovalId(null);
    if (mode !== "min") return;
    const latest = getDurable();
    setSize(latest.size);
    setPos(latest.pos);
    setMode(isNarrowViewport() ? "max" : "normal");
  }, [mode, summonVersion]);

  // Keep mobile-only actions in sync when a phone rotates or a desktop window
  // crosses the panel's narrow breakpoint.
  useEffect(() => {
    const syncNarrowViewport = () => setNarrowViewport(isNarrowViewport());
    window.addEventListener("resize", syncNarrowViewport);
    return () => window.removeEventListener("resize", syncNarrowViewport);
  }, []);

  // Load the model list whenever the account changes (including on mount, to
  // restore/refresh the list). Keeps the restored model if still valid.
  useEffect(() => {
    if (!instanceId) { setModels([]); setModel(""); return; }
    let cancelled = false;
    api.getAgentCliModels(instanceId)
      .then((r) => {
        if (cancelled) return;
        setModels(r.models);
        setModel((m) => (r.models.includes(m) ? m : (instanceModel || r.models[0] || "")));
      })
      .catch(() => { if (!cancelled) setModels([]); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instanceId]);

  const resetUsage = useCallback(() => {
    const zero = { input: 0, output: 0, context: 0, noData: false };
    setSessionUsage(zero, persistenceOwner);
    setUsage(zero);
  }, [persistenceOwner]);

  // Reset the conversation when the access key or account changes (tool scope /
  // message format differ), but NOT on the initial restore. A running turn is
  // retired with it: deleting the account in use selects another one, and the
  // old stream must not refill the new history or its next request.
  useEffect(() => {
    if (firstRun.current) { firstRun.current = false; return; }
    turnGenerationRef.current += 1;
    cancelledRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    setSending(false);
    // A paused turn's Continue belongs to the old conversation too.
    setPendingContinue(null);
    restoredApprovals.current.clear();
    setTurns([]);
    updateLive([]);
    resetUsage();
  }, [accessKeyId, instanceId, resetUsage, updateLive]);

  // If an account is added or removed while the window is open, keep the
  // selection valid without a page reload. When the last account is removed,
  // fall back to "" so the model list and selection clear too.
  useEffect(() => {
    if (!instances.some((i) => i.id === instanceId)) {
      setInstanceId(instances[0]?.id ?? "");
    }
  }, [instances, instanceId]);

  // Same as above for the access key: a revoke elsewhere (Access key detail fires
  // phx-access-keys-changed, which both window hosts turn into a fresh accessKeys prop)
  // must not leave a dead access key selected. Falls back to another available
  // access key, or "" if none remain, and takes the first key to arrive when none
  // was selected, the one its select then shows. The conversation-reset effect
  // above already clears history whenever accessKeyId changes, including this
  // reselect.
  useEffect(() => {
    if (!accessKeys.some((tok) => tok.id === accessKeyId)) {
      setAccessKeyId(accessKeys[0]?.id ?? "");
    }
  }, [accessKeys, accessKeyId]);

  // The transcript's size after the last update, to tell whether the view was at
  // its end before the next one, and whether the next update goes there regardless.
  const transcriptSize = useRef<{ content: number; view: number } | null>(null);
  const followNextUpdate = useRef(false);
  const noteTranscriptSize = useCallback((el: HTMLElement) => {
    transcriptSize.current = { content: el.scrollHeight, view: el.clientHeight };
  }, []);
  const scrollToLatest = useCallback(() => {
    const el = bodyRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    noteTranscriptSize(el);
  }, [noteTranscriptSize]);

  // New content moves the view to the newest message only if the view was at the
  // end before it arrived, so a reader who scrolled up stays put. The size of the
  // view can change between updates, as when the composer grows, and the larger
  // of its two sizes is the one that does not mistake that for scrolling up.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const before = transcriptSize.current;
    const reading = !followNextUpdate.current && before !== null
      && el.scrollTop < before.content - Math.max(before.view, el.clientHeight) - FOLLOW_SLOP_PX;
    followNextUpdate.current = false;
    if (reading) noteTranscriptSize(el); else scrollToLatest();
  }, [turns, live, noteTranscriptSize, scrollToLatest]);

  // Geometry changes always go to the newest message. The second write runs after
  // layout because browsers can reset a scroller while resizing it or moving it
  // between the panel and popup DOMs.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    scrollToLatest();
    const ownerWindow = el.ownerDocument.defaultView ?? window;
    const frame = ownerWindow.requestAnimationFrame(scrollToLatest);
    return () => ownerWindow.cancelAnimationFrame(frame);
  }, [mode, size, popupMount, scrollToLatest]);

  // Native viewport and popup resizes do not necessarily change React state,
  // so keep a listener on both browser windows as well.
  useEffect(() => {
    const popup = popupRef.current;
    let frameWindow: Window | null = null;
    let frame: number | null = null;
    const handleResize = () => {
      const ownerWindow = bodyRef.current?.ownerDocument.defaultView ?? window;
      if (frame !== null) frameWindow?.cancelAnimationFrame(frame);
      frameWindow = ownerWindow;
      frame = ownerWindow.requestAnimationFrame(() => {
        frame = null;
        frameWindow = null;
        scrollToLatest();
      });
    };
    window.addEventListener("resize", handleResize);
    popup?.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      popup?.removeEventListener("resize", handleResize);
      if (frame !== null) frameWindow?.cancelAnimationFrame(frame);
    };
  }, [popupMount, scrollToLatest]);

  const displayed = useMemo(() => [...turns.flatMap((turn) => turn.entries), ...live], [turns, live]);

  // Start a fresh conversation: drop the transcript and memory, cancel any turn
  // in flight. Shared by the gear's "Clear chat history" button and the /clear
  // slash command.
  const clearHistory = useCallback(() => {
    restoredApprovals.current.clear();
    turnGenerationRef.current += 1;
    cancelledRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    setSending(false);
    setPendingContinue(null);
    setTurns([]);
    updateLive([]);
    resetUsage();
    setAnnouncement("");
    // Images waiting to be sent belong to the conversation being cleared.
    setAttachments([]);
    setAttachError("");
  }, [resetUsage, updateLive]);

  // Cancel the running turn: abort the request, ignore any late response, and
  // put the query back in the box so it can be edited and resent. The partial
  // turn is folded into the transcript, not discarded (a long agentic exchange
  // is one turn, so dropping live entries would erase the whole visible chat). Any
  // still-pending approval card flips to cancelled, matching the server's
  // orphaned-approval cleanup. The folded turn carries no provider messages:
  // the aborted exchange is kept for display but deliberately not replayed as
  // model memory, since the restored query is usually edited and resent.
  const cancel = useCallback((chatEnded = false) => {
    turnGenerationRef.current += 1;
    cancelledRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    setSending(false);
    const entries = stripActivity(liveRef.current);
    if (entries.length) {
      const kept: ChatEntry[] = entries.map((e) =>
        e.kind === "approval" && e.status === "pending"
          ? { ...e, status: "cancelled", reason: chatEnded ? "agent_chat_ended" : e.reason } : e);
      if (!chatEnded || !kept.some((e) => e.kind === "approval" && e.reason === "agent_chat_ended")) {
        kept.push({ kind: "notice", message: t(chatEnded ? "agentchat.chatEnded" : "agentchat.cancelledNotice") });
      }
      const next = trimTurns([...turns, { entries: kept, messages: [], lines: countLines(kept) }], scrollbackLines);
      // pagehide cannot rely on React effects running before the page dies.
      setSessionTurns(next, persistenceOwner);
      setTurns(next);
    }
    updateLive([]);
    const draft = input || lastQueryRef.current;
    setSessionDraft(draft, persistenceOwner);
    flushSessionDraft(persistenceOwner);
    setInput(draft);
    const sentImages = lastAttachmentsRef.current;
    lastAttachmentsRef.current = [];
    if (sentImages.length) setAttachments((current) => (current.length ? current : sentImages));
  }, [input, persistenceOwner, scrollbackLines, turns, updateLive]);


  const endTurnRef = useRef(cancel);
  endTurnRef.current = cancel;

  // Closing the window must abort any in-flight turn, otherwise the server-side
  // agentic loop keeps running (and can still fire tool calls) after the operator
  // dismissed the chat. Same for unmount (e.g. navigating away).
  const handleClose = useCallback(() => {
    if (abortRef.current) endTurnRef.current(true);
    turnGenerationRef.current += 1;
    cancelledRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    onClose();
  }, [onClose]);

  useEffect(() => () => {
    if (abortRef.current) endTurnRef.current(true);
    if (liveFrame.current !== null) clearTimeout(liveFrame.current);
    flushSessionDraft(persistenceOwner);
    turnGenerationRef.current += 1;
    cancelledRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    closingPopupRef.current = true;
    const popup = popupRef.current;
    forgetAgentCliPopup(popup);
    popup?.close();
    popupRef.current = null;
  }, [persistenceOwner]);

  // The popup is still part of the same React tree, but it is a second browser
  // event target. Mirror the profile-gated Shift+A listener there, keep its
  // theme in sync with HA, and make the popup's native close equivalent to the
  // chat header's Close action.
  useEffect(() => {
    const popup = popupRef.current;
    if (!popup || !popupMount) return;

    const syncTheme = () => applyPopupTheme(popup, currentHass());
    const handlePopupClosed = () => {
      if (closingPopupRef.current) return;
      forgetAgentCliPopup(popup);
      popupRef.current = null;
      handleClose();
    };
    const unregisterShortcut = registerAgentChatShortcut(
      () => (currentHass() as AgentCliHass | null | undefined) ?? null,
      handleClose,
      popup,
    );
    popup.addEventListener("pagehide", handlePopupClosed);
    window.addEventListener("phx-theme-changed", syncTheme);
    const observer = new MutationObserver(syncTheme);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["style", "class"],
    });
    popup.document.title = agentChatTitle;
    popup.document.documentElement.lang = agentChatLanguage;
    syncTheme();

    return () => {
      unregisterShortcut();
      popup.removeEventListener("pagehide", handlePopupClosed);
      window.removeEventListener("phx-theme-changed", syncTheme);
      observer.disconnect();
    };
  }, [popupMount, currentHass, handleClose, agentChatLanguage, agentChatTitle]);

  // The popup's portal and request still belong to this page. Warn before a
  // reload destroys that owner, but never cancel work unless navigation occurs.
  useEffect(() => {
    if (!sending) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", warnBeforeUnload);
    };
  }, [sending]);

  useEffect(() => (sending ? announceRunningTurn(persistenceOwner) : undefined), [sending, persistenceOwner]);
  useEffect(() => watchOtherTabTurn(setOtherTabTurn), [persistenceOwner]);

  // One turn: either a new user message (mode "user") or a resume of a turn
  // that paused at the round-cap checkpoint (mode "continue", no new user
  // message; the model continues from the existing conversation).
  const runTurn = useCallback(async (
    mode: "user" | "continue",
    text: string,
    memoryOverride?: unknown[],
    images: ChatAttachment[] = [],
  ) => {
    if (sending) return;
    const generation = ++turnGenerationRef.current;
    followNextUpdate.current = true;
    setConnectionOpen(false);
    setPendingContinue(null);
    cancelledRef.current = false;
    lastQueryRef.current = mode === "continue" ? "" : text;
    lastAttachmentsRef.current = images;
    const stateless = accessKeys.find((accessKey) => accessKey.id === accessKeyId)?.persona === "home_companion";
    if (stateless) {
      setTurns([]);
      setSessionTurns([], persistenceOwner);
      setAnnouncement("");
      resetUsage();
    }
    usageBaseRef.current = stateless ? { input: 0, output: 0 }
      : { input: usageRef.current.input, output: usageRef.current.output };
    turnSawUsageRef.current = false;
    setLiveStatus(null);
    updateLive(mode === "continue" ? [] : [{
      kind: "user", text, ts: Date.now(),
      ...(images.length ? { images: images.map((image) => ({ name: image.name, dataUrl: image.dataUrl })) } : {}),
    }]);
    setSending(true);

    const memoryBudget = MEMORY_MAX_BYTES - requestBytes(text);
    const sentMessages = stateless ? [] : memoryOverride ?? memoryMessages(turns, scrollbackLines, images.length > 0, memoryBudget);
    const sentLen = sentMessages.length;
    // Said once: the cut moves forward with every later message while the transcript keeps them all.
    if (!stateless && scrollbackLines > 0 && memoryStart(turns, memoryBudget) > 0
        && !turns.some((turn) => turn.entries.some((e) => e.kind === "notice" && e.code === MEMORY_TRIMMED))) {
      const trimmed = t(`agentchat.notice.${MEMORY_TRIMMED}`);
      updateLive((prev) => [...prev, { kind: "notice", code: MEMORY_TRIMMED, message: trimmed }]);
      announce(trimmed);
    }
    let newMessages: unknown[] = [];
    // A turn that ended early was already said; its earlier text must not replace that.
    let endAnnounced = false;
    const announceEnd = (text: string) => {
      endAnnounced = true;
      announce(text);
    };
    const announceFailure = (message: string) => announceEnd(t("agentchat.announceError", { message }));
    const ac = new AbortController();
    abortRef.current = ac;

    const push = (e: ChatEntry) => updateLive((prev) => [...prev, e]);
    // Progress replaces the previous line for the same call rather than appending:
    // a five-minute build ticks dozens of times and would otherwise bury the chat.
    const showProgress = (id: string, message: string) => {
      updateLive((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.kind === "progress" && last.id === id) {
          return [...prev.slice(0, -1), { ...last, message }];
        }
        return [...prev, { kind: "progress", id, message } as ChatEntry];
      });
    };
    // Always moves to the bottom, so the line tracks the newest call instead of
    // sitting wherever the first one happened.
    const showActivity = (name: string) => {
      updateLive((prev) => [
        ...stripActivity(prev),
        { kind: "progress", id: ACTIVITY_ID, message: t("agentchat.workingOn", { name }), activity: true } as ChatEntry,
      ]);
    };
    const clearActivity = () => updateLive((prev) => stripActivity(prev));
    const appendAssistant = (delta: string, thinking: boolean) => {
      const latest = liveRef.current[liveRef.current.length - 1];
      if (latest?.kind === "assistant") {
        streamLive((prev) => {
          const upd = { ...prev[prev.length - 1] } as Extract<ChatEntry, { kind: "assistant" }>;
          if (thinking) upd.thinking += delta; else upd.text += delta;
          return [...prev.slice(0, -1), upd];
        });
        return;
      }
      // A new message shows at once; only the tokens that extend one wait for a frame.
      push({ kind: "assistant", text: thinking ? "" : delta, thinking: thinking ? delta : "", ts: Date.now() });
    };

    try {
      await agentCliChat(
        {
          access_key_id: accessKeyId,
          instance_id: instanceId,
          model: model || undefined,
          messages: sentMessages,
          log_conversation_id: getLogConversationId(sentMessages.length > 0, persistenceOwner),
          ...(mode === "continue" ? { continue: true } : { user: text }),
          ...(images.length ? { attachments: images.map((image) => ({ mime_type: image.mimeType, data: image.data })) } : {}),
          options: buildOptions(kind, options, caps),
        },
        (name, payload) => {
          if (cancelledRef.current || turnGenerationRef.current !== generation) return;
          const p = (payload ?? {}) as Record<string, unknown>;
          switch (name) {
            case "status":
              setLiveStatus(modelStatusText(p));
              break;
            case "assistant_delta":
              setLiveStatus(null);
              appendAssistant(String(p.text ?? ""), false);
              break;
            case "thinking_delta":
              appendAssistant(String(p.text ?? ""), true);
              break;
            case "tool_call":
              setLiveStatus(null);
              push({ kind: "tool_call", id: String(p.id ?? ""), name: String(p.name ?? ""), args: p.arguments });
              showActivity(String(p.name ?? ""));
              break;
            case "tool_progress":
              // A tool reporting its own progress says strictly more than the
              // generic line, so it takes over rather than stacking under it.
              clearActivity();
              {
                const messageKey = typeof p.message_key === "string" ? p.message_key : "";
                const messageParams = p.message_params && typeof p.message_params === "object"
                  ? p.message_params as Record<string, string | number>
                  : undefined;
                showProgress(
                  String(p.id ?? ""),
                  messageKey && hasMessage(messageKey)
                    ? t(messageKey, messageParams)
                    : String(p.message ?? ""),
                );
              }
              break;
            case "tool_result":
              push({ kind: "tool_result", id: String(p.id ?? ""), name: String(p.name ?? ""),
                     isError: Boolean(p.is_error), summary: String(p.summary ?? "") });
              break;
            case "tool_image": {
              const mimeType = String(p.mime_type ?? "");
              const data = String(p.data ?? "");
              const allowed = new Set(["image/jpeg", "image/png", "image/gif"]);
              if (allowed.has(mimeType) && data) {
                push({
                  kind: "tool_image",
                  id: String(p.id ?? ""),
                  name: String(p.name ?? ""),
                  mimeType,
                  dataUrl: `data:${mimeType};base64,${data}`,
                });
              }
              break;
            }
            case "approval_required":
              push({ kind: "approval", approvalId: String(p.approval_id ?? ""),
                     toolName: String(p.tool_name ?? ""),
                     status: "pending" });
              setAnnouncement(t("agentchat.announceApproval", { name: String(p.tool_name ?? "") }));
              break;
            case "approval_resolved":
              updateLive((prev) => prev.map((e) =>
                e.kind === "approval" && e.approvalId === p.approval_id
                  ? { ...e, status: String(p.status ?? "resolved"),
                      reason: typeof p.reason === "string" && p.reason ? p.reason : undefined,
                      executionReason: p.reason_kind === "execution" } : e));
              setAnnouncement(t("agentchat.announceResolved", { status: approvalStatusLabel(String(p.status ?? "resolved")) }));
              break;
            case "usage": {
              // Turn-cumulative totals from the server, added onto the session
              // base captured at send time. Context is the newest call's input.
              turnSawUsageRef.current = true;
              const base = usageBaseRef.current;
              const next: SessionUsage = {
                input: base.input + Number(p.input_tokens ?? 0),
                output: base.output + Number(p.output_tokens ?? 0),
                context: Number(p.context_tokens ?? 0) || usageRef.current.context,
                noData: false,
              };
              setSessionUsage(next, persistenceOwner);
              setUsage(next);
              break;
            }
            case "notice":
              push({ kind: "notice", code: p.code ? String(p.code) : undefined,
                     message: String(p.message ?? ""),
                     messageParams: p.message_params && typeof p.message_params === "object"
                       ? p.message_params as Record<string, string | number> : undefined });
              break;
            case "focus_declined":
              // The refusal text is the whole response; nothing more to show.
              break;
            case "continue_required":
              // The turn paused at the round-cap checkpoint. Show the
              // Continue/Stop control; the conversation is left resumable.
              setPendingContinue(Number(p.iterations ?? 0) || 0);
              break;
            case "error":
              setLiveStatus(null);
              {
                const code = String(p.code ?? "error");
                const message = String(p.message ?? "");
                const messageParams = p.message_params && typeof p.message_params === "object"
                  ? p.message_params as Record<string, string | number> : undefined;
                const rateLimit = providerRateLimit(p.rate_limit);
                if (p.refused === "images") {
                  // The server has just recorded that this model refuses images.
                  window.dispatchEvent(new CustomEvent("phx-agentcli-providers-changed"));
                }
                // The provider's own error sentence, clipped and scrubbed server-side.
                const providerDetail = typeof p.provider_detail === "string" && p.provider_detail
                  ? p.provider_detail : undefined;
                push({ kind: "error", code, message, messageParams, rateLimit, providerDetail });
                announceFailure(serverText(code, message, messageParams, rateLimit, providerDetail));
              }
              break;
            case "messages":
              newMessages = (Array.isArray(p.messages) ? p.messages : []).slice(sentLen);
              break;
            default:
              break;
          }
        },
        ac.signal,
      );
    } catch (err) {
      // An intentional cancel aborts the fetch; don't surface that as an error.
      if (!cancelledRef.current && turnGenerationRef.current === generation) {
        const restorePrompt = () => {
          if (lastQueryRef.current) setInput((current) => current || lastQueryRef.current);
          const sentImages = lastAttachmentsRef.current;
          lastAttachmentsRef.current = [];
          if (sentImages.length) setAttachments((current) => (current.length ? current : sentImages));
        };
        // Home Assistant going away is not the model provider failing, and the window
        // already says so above the composer: no red line is added, and what was typed
        // comes back so it can be sent again.
        // A server answer carries its HTTP status; a network failure does not.
        if (err instanceof ConnectionLostError) {
          // Once anything came back the model may have acted on the message, so sending
          // it again is the reader's call and it stays out of the composer.
          const answered = liveRef.current.some((e) => e.kind !== "user" && e.kind !== "notice" && !isActivity(e));
          const lost = t("agentchat.connectionLost");
          push({ kind: "notice", message: lost });
          announceEnd(lost);
          if (!answered) restorePrompt();
        } else if (err instanceof Error && typeof (err as { status?: unknown }).status === "number") {
          // Phoenix answered and refused before any reply streamed (the kill switch, a key or
          // account no longer usable, an attachment over its limit): its own message says why,
          // so it is shown as is, never replaced by the provider-unavailable text. Nothing
          // reached the model, so what was typed comes back.
          const code = (err as { status?: unknown }).status === 413 ? "request_too_large" : "";
          push({ kind: "error", code, message: err.message });
          announceFailure(serverText(code, err.message));
          restorePrompt();
        } else if (homeAssistantReachable()) {
          const message = err instanceof Error ? err.message : t("agentchat.requestFailed");
          push({ kind: "error", code: "network", message });
          announceFailure(serverText("network", message));
        } else {
          announceEnd(t("shell.offlineBody"));
          restorePrompt();
        }
      }
    } finally {
      if (abortRef.current === ac) abortRef.current = null;
      if (!cancelledRef.current && turnGenerationRef.current === generation) {
        setSending(false);
        // A completed turn with no usage report (and nothing counted so far)
        // means this provider does not report usage; the footer says so
        // instead of showing a forever-zero counter. Cleared if usage ever
        // arrives (e.g. after switching models on the same account).
        if (!turnSawUsageRef.current
            && usageBaseRef.current.input + usageBaseRef.current.output === 0
            && !usageRef.current.noData) {
          const marked: SessionUsage = { ...usageRef.current, noData: true };
          setSessionUsage(marked, persistenceOwner);
          setUsage(marked);
        }
        // Finalize the turn: fold live entries into the turn history, then trim.
        const entries = stripActivity(liveRef.current);
        const turn: Turn = { entries, messages: newMessages, lines: countLines(entries) };
        setTurns((prev) => trimTurns([...prev, turn], scrollbackLines));
        updateLive([]);
        // Announce the completed reply now that streaming is over.
        const finals = entries.filter((e) => e.kind === "assistant" && e.text.trim());
        const finalText = finals.length ? (finals[finals.length - 1] as Extract<ChatEntry, { kind: "assistant" }>).text : "";
        const endNotice = entries.find((e): e is Extract<ChatEntry, { kind: "notice" }> => e.kind === "notice" && TURN_END_NOTICES.has(e.code ?? ""));
        // A reply is said as the text it renders to, not its Markdown, and only its start: a long
        // answer read in full would hold the screen reader. The notice comes after it, whole.
        const spoken = [
          finalText ? replyAnnouncement(finalText) : "",
          endNotice ? chatNoticeText(endNotice.code, endNotice.message, endNotice.messageParams) : "",
        ].filter(Boolean).join(" ");
        if (spoken && !endAnnounced) announce(spoken);
      }
    }
  }, [sending, accessKeys, turns, scrollbackLines, accessKeyId, persistenceOwner, resetUsage, instanceId, model, kind, options, caps, updateLive, streamLive, announce]);

  // Why images cannot go with the next message, when they cannot. Unknown model
  // support is allowed: the composer says so, and a provider refusal is learned.
  const companionKey = accessKeys.find((accessKey) => accessKey.id === accessKeyId)?.persona === "home_companion";
  const attachBlocked = instances.length === 0 ? t("agentchat.configureFirst")
    : companionKey ? t("agentchat.attachCompanion")
    : declaredCaps?.vision === false ? t("agentchat.attachNoVision") : "";

  const send = useCallback(async () => {
    const text = input.trim();
    if ((!text && !attachments.length) || sending) return;
    // Slash commands are handled locally and never sent to the model. /clear
    // starts a new conversation, identical to the gear's Clear chat history.
    if (text.toLowerCase() === "/clear") {
      setInput("");
      clearHistory();
      return;
    }
    if (attachments.length && attachBlocked) {
      setAttachError(attachBlocked);
      return;
    }
    // The onboarding wizard listens for this to auto-close once the user has
    // sent their first prompt through the window it opened.
    window.dispatchEvent(new CustomEvent("phx-agentcli-sent"));
    setInput("");
    const images = attachments;
    setAttachments([]);
    setAttachError("");
    void runTurn("user", text, undefined, images);
  }, [input, sending, runTurn, clearHistory, attachments, attachBlocked]);

  const addFiles = useCallback(async (files: File[]) => {
    if (!files.length) return;
    setAttachError("");
    if (attachBlocked) {
      setAttachError(attachBlocked);
      return;
    }
    const room = ATTACHMENT_MAX_COUNT - attachments.length;
    if (files.length > room) setAttachError(tn("agentchat.attachTooMany", ATTACHMENT_MAX_COUNT));
    const prepared: ChatAttachment[] = [];
    for (const file of files.slice(0, Math.max(0, room))) {
      try {
        prepared.push(await prepareAttachment(file));
      } catch (err) {
        const problem = err instanceof AttachmentError ? err.problem : "unreadable";
        setAttachError(t(problem === "type" ? "agentchat.attachNotImage"
          : problem === "size" ? "agentchat.attachTooLarge" : "agentchat.attachUnreadable", { name: file.name }));
      }
    }
    // Two quick pastes can both see room; the slice keeps the limit.
    if (prepared.length) setAttachments((current) => [...current, ...prepared].slice(0, ATTACHMENT_MAX_COUNT));
  }, [attachBlocked, attachments.length]);

  // Grant the agent another batch of rounds from where it paused: resends the
  // held conversation with continue:true, no new user message.
  const continueTurn = useCallback(() => {
    void runTurn("continue", "");
  }, [runTurn]);


  useEffect(() => {
    const handlePageHide = () => {
      if (abortRef.current) cancel(true);
      flushSessionDraft(persistenceOwner);
      // Browser navigation does not unmount React. Close the portal document
      // explicitly, without its pagehide handler clearing the restore flag.
      closingPopupRef.current = true;
      const popup = popupRef.current;
      forgetAgentCliPopup(popup);
      popupRef.current = null;
      setPopupMount(null);
      popup?.close();
    };
    window.addEventListener("pagehide", handlePageHide);
    return () => window.removeEventListener("pagehide", handlePageHide);
  }, [cancel, persistenceOwner]);

  const resolvingApprovalsRef = useRef<Set<string>>(new Set());
  const approvalEventVersions = useRef(new Map<string, number>());

  const resolveApproval = useCallback(async (approvalId: string, approve: boolean) => {
    // Guard against a second click landing before the optimistic re-render
    // below has taken the buttons away.
    if (resolvingApprovalsRef.current.has(approvalId)) return;
    resolvingApprovalsRef.current.add(approvalId);
    // Take the buttons away immediately: the card otherwise stays clickable
    // for the whole round trip plus tool execution, and repeated Approve
    // clicks each fire another request whose conflict error lands in the
    // chat. Only a still-pending card is swapped, so a fast SSE resolution
    // is never overwritten.
    const marker = approve ? t("agentchat.approving") : t("agentchat.rejecting");
    updateLive((prev) => prev.map((e) =>
      e.kind === "approval" && e.approvalId === approvalId && e.status === "pending"
        ? { ...e, status: marker } : e));
    try {
      if (approve) {
        await api.approveApproval(approvalId);
      } else {
        // A reason typed into the panel's approval detail modal but never
        // submitted there (the operator reviewed in the panel, then rejected
        // from the chat) still steers the next proposal. Empty when nothing
        // was typed, which is the plain do-not-retry rejection.
        const reason = getReasonDraft(approvalId);
        await api.rejectApproval(approvalId, reason ? { reason } : {});
      }
      // The draft has served its purpose either way; the resolved bubble shows
      // the reason the server recorded.
      clearReasonDraft(approvalId);
      // The stream's approval_resolved event updates the card's own status on
      // success; nothing else to do here.
    } catch (err: unknown) {
      // A failure (the capability having been denied since, a transient
      // network error, ...) must be visible: otherwise the card keeps showing
      // Approve/Reject with no sign that the click did nothing. Surface it and
      // restore the buttons so the admin can retry. Restore only from this
      // handler's own marker: if the SSE event resolved the card meanwhile
      // (say, an admin approved it in the panel while this reject failed),
      // that final status wins.
      updateLive((prev) => prev.map((e) =>
        e.kind === "approval" && e.approvalId === approvalId && e.status === marker
          ? { ...e, status: "pending" } : e));
      updateLive((prev) => [...prev, {
        kind: "error", code: "approval_action_failed",
        message: err instanceof Error ? err.message : t("agentchat.approvalActionFailed"),
      }]);
    } finally {
      resolvingApprovalsRef.current.delete(approvalId);
    }
  }, [updateLive]);

  // Update one approval card wherever it currently lives.
  //
  // While the agent is blocked waiting for a human the stream is still open and
  // the card is in `live`, which is the usual case. But a turn that ended or was
  // cancelled with an approval still pending folds its entries into `turns` and
  // clears `live`, and that card stays on screen and stays actionable. A handler
  // that only mapped `live` would silently do nothing for exactly those cards.
  // `lines` is left alone deliberately: it bounds scrollback trimming, and a
  // status word is not a line.
  const updateApprovalEntry = useCallback((
    approvalId: string, update: (e: Extract<ChatEntry, { kind: "approval" }>) => ChatEntry,
  ) => {
    if (restoredApprovals.current.has(approvalId)) {
      approvalEventVersions.current.set(approvalId, (approvalEventVersions.current.get(approvalId) || 0) + 1);
    }
    const mapEntry = (e: ChatEntry) =>
      e.kind === "approval" && e.approvalId === approvalId ? update(e) : e;
    updateLive((prev) => prev.map(mapEntry));
    setTurns((prev) => prev.map((turn) =>
      turn.entries.some((e) => e.kind === "approval" && e.approvalId === approvalId)
        ? { ...turn, entries: turn.entries.map(mapEntry) } : turn));
  }, [updateLive]);

  // Home Assistant restarting cancels the approvals it was still holding, so a
  // card left offering Approve and Reject is stale. When it answers again, every
  // unresolved card is verified the same way a restored one is.
  const turnsRef = useRef(turns);
  turnsRef.current = turns;
  const [verifyNonce, setVerifyNonce] = useState(0);
  useEffect(() => onConnectionChange((up) => {
    if (!up) return;
    let found = false;
    for (const entry of [...liveRef.current, ...turnsRef.current.flatMap((turn) => turn.entries)]) {
      if (entry.kind !== "approval" || TERMINAL_APPROVAL_STATUSES.has(entry.status)) continue;
      restoredApprovals.current.add(entry.approvalId);
      found = true;
    }
    if (found) setVerifyNonce((n) => n + 1);
  }), []);

  // Agent Chat open in two tabs shows one conversation: a save in the other tab
  // replaces this one's, so neither brings an older copy back. A running turn keeps
  // its own, and its save at the end is the one that stands.
  useEffect(() => onSavedConversationChange(() => {
    if (abortRef.current || getAgentCliOwner() !== persistenceOwner) return;
    const adopted = restorableTurns(getSessionTurns());
    adoptedTurns.current = adopted;
    setTurns(adopted);
    setUsage(getSessionUsage());
    setPendingContinue(null);
    // The other tab may close before it finishes checking a card it restored.
    const unchecked = adopted.flatMap((turn) => turn.entries).filter((e) => e.kind === "approval" && e.checking);
    for (const entry of unchecked) if (entry.kind === "approval") restoredApprovals.current.add(entry.approvalId);
    if (unchecked.length) setVerifyNonce((n) => n + 1);
  }), [persistenceOwner]);

  // Resolution events sent while this document was absent cannot reach its
  // subscription. Verify restored cards before offering actions, and poll until
  // terminal so a disconnect cleanup that lands just after reload is not missed.
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async () => {
      await Promise.all([...restoredApprovals.current].map(async (id) => {
        try {
          const version = approvalEventVersions.current.get(id);
          const record = await api.getApproval(id);
          if (disposed || !restoredApprovals.current.has(id)) return;
          if (approvalEventVersions.current.get(id) !== version) return;
          const pending = record.status === "pending";
          updateApprovalEntry(id, (entry) => {
            return { ...entry, checking: false,
              status: record.in_progress ? t("agentchat.approving") : record.status,
              reason: record.closure_reason || undefined };
          });
          if (!pending) restoredApprovals.current.delete(id);
        } catch {
          // Keep unverified cards inert and retry transient failures.
        }
      }));
      if (!disposed && restoredApprovals.current.size) timer = setTimeout(() => { void refresh(); }, 2000);
    };
    void refresh();
    return () => { disposed = true; clearTimeout(timer); };
  }, [updateApprovalEntry, verifyNonce]);

  // Learn about approvals resolved or claimed ELSEWHERE, straight off the HA bus.
  //
  // The SSE stream also carries approval_resolved, but that frame rides the agent
  // loop resuming and can be several seconds behind a click made in the panel or
  // a notification, leaving this bubble offering Approve and Reject on an
  // approval already acted on. A claim (an admin's Approve executing its saved
  // action, which is inline and slow) has no SSE frame at all. Both land here
  // immediately; whichever of the bus and SSE paths arrives first wins, and both
  // write the same status, so the duplicate is harmless.
  useEffect(() => subscribeApprovalEvents(null, {
    onResolved: (approvalId, status) => {
      // Unconditional for a matching id, exactly like the SSE handler: an
      // approval resolves once, so the server's status is authoritative.
      updateApprovalEntry(approvalId, (e) => ({ ...e, status: status || "resolved" }));
    },
    onClaimChanged: (approvalId, claimed) => {
      // Claimed: show it as being approved and take the buttons away. Released:
      // the execution failed and it is pending again, so the buttons come back.
      // A card this window itself is mid-request on is left alone, since
      // resolveApproval owns its own optimistic marker and its own rollback.
      if (resolvingApprovalsRef.current.has(approvalId)) return;
      updateApprovalEntry(approvalId, (e) => {
        if (claimed) {
          return e.status === "pending" ? { ...e, status: t("agentchat.approving") } : e;
        }
        return e.status === t("agentchat.approving") ? { ...e, status: "pending" } : e;
      });
    },
  }), [updateApprovalEntry]);

  // --- drag ---
  const startDrag = useCallback((e: React.PointerEvent, phoneCircle = false) => {
    if (maximized || poppedOut || docked) return;  // full screen, popup or docked: nothing to drag
    if (!phoneCircle && (e.target as HTMLElement).closest("button, select")) return;
    e.preventDefault();
    circleDragged.current = false;
    const startX = e.clientX, startY = e.clientY;
    // The pill remembers its own position independently of the restored window,
    // so a drag moves (and persists) whichever one is showing.
    const setActive = minimized ? setPillPos : setPos;
    const persistKey = minimized ? "pillPos" : "pos";
    const origin = minimized
      ? (pillPos.x >= 0 ? { ...pillPos } : { ...pos })
      : { ...pos };
    let latest = { ...origin };
    const move = (ev: PointerEvent) => {
      if (phoneCircle && !circleDragged.current) {
        if (Math.hypot(ev.clientX - startX, ev.clientY - startY) < PILL_DRAG_THRESHOLD) return;
        circleDragged.current = true;
      }
      // Keep the whole window on screen on every side. Uses the live element size
      // (so it is correct whether minimized or full height); if the window is
      // larger than the usable viewport it pins to the safe top edge.
      const w = rootRef.current?.offsetWidth ?? size.w;
      const h = rootRef.current?.offsetHeight ?? size.h;
      const topMargin = agentCliTopMargin(rootRef.current);
      const maxX = Math.max(0, window.innerWidth - w);
      const maxY = Math.max(topMargin, window.innerHeight - h - VIEWPORT_MARGIN);
      latest = {
        x: Math.max(0, Math.min(maxX, origin.x + ev.clientX - startX)),
        y: Math.max(topMargin, Math.min(maxY, origin.y + ev.clientY - startY)),
      };
      setActive(latest);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      // Snap to a nearby edge or corner on release: each axis independently snaps
      // to its margin when the window's edge is within SNAP of the viewport edge,
      // so dragging into a corner clicks neatly into it. Skipped while minimized
      // (the bar has no fixed height to snap the bottom edge against).
      let final = latest;
      if (!minimized) {
        const M = VIEWPORT_MARGIN, SNAP = 56;
        const topMargin = agentCliTopMargin(rootRef.current);
        const { w, h } = size;
        let { x, y } = latest;
        if (x <= SNAP) x = M;
        else if (window.innerWidth - (x + w) <= SNAP) x = Math.max(M, window.innerWidth - w - M);
        if (y - topMargin <= SNAP) y = topMargin;
        else if (window.innerHeight - (y + h) <= SNAP) y = Math.max(M, window.innerHeight - h - M);
        final = { x, y };
        setActive(final);
      }
      // Persist only when the drag settles; a tap leaves the circle's place alone.
      if (phoneCircle) {
        if (circleDragged.current) patchDurable({ pillPos: final, mobilePillPos: final }, persistenceOwner);
      } else {
        patchDurable({ [persistKey]: final }, persistenceOwner);
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }, [maximized, poppedOut, docked, minimized, pillPos, pos, size, persistenceOwner]);

  // --- resize ---
  const startResize = useCallback((dir: string) => (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX, startY = e.clientY;
    const o = { ...size, ...pos };
    let latestSize = { w: o.w, h: o.h };
    let latestPos = { x: o.x, y: o.y };
    const move = (ev: PointerEvent) => {
      const r = snapResizeRect(
        dir, o, ev.clientX - startX, ev.clientY - startY,
        window.innerWidth, window.innerHeight, agentCliTopMargin(rootRef.current),
      );
      latestSize = { w: r.w, h: r.h };
      latestPos = { x: r.x, y: r.y };
      setSize(latestSize);
      setPos(latestPos);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      patchDurable({ size: latestSize, pos: latestPos }, persistenceOwner);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, [size, pos, persistenceOwner]);

  // Mode transitions. The pill and the restored window each remember their own
  // position, so each transition seeds or clamps only the geometry it enters.
  const minimize = useCallback(() => {
    // A remembered desktop-width pill position has no useful meaning on a phone
    // and can strand the compact circle over the Phoenix title. The circle keeps
    // its own dragged position and otherwise starts at the top-right. Desktop
    // retains the independent remembered pill position.
    const pillSize = currentPillSize();
    const dragged = getDurable().mobilePillPos;
    const base = isNarrowViewport()
      ? (dragged.x >= 0 ? dragged : {
          x: window.innerWidth - pillSize.w - VIEWPORT_MARGIN,
          y: VIEWPORT_MARGIN,
        })
      : pillPos.x >= 0 ? pillPos : pos;
    const np = clampPosToViewport(
      base, pillSize, window.innerWidth, window.innerHeight,
      agentCliTopMargin(rootRef.current),
    );
    setPillPos(np);
    patchDurable({ pillPos: np }, persistenceOwner);
    setMode("min");
  }, [persistenceOwner, pillPos, pos]);

  // Open the full approval in the Phoenix MCP panel's Approvals tab. A narrow
  // in-app chat fills the viewport, so minimize it before the review and restore
  // it after a decision. Desktop keeps the floating window open and in place;
  // a popped-out chat is already in a separate browser window. Navigation stays
  // on HA's soft SPA path so the turn survives.
  const openReview = useCallback((approvalId: string) => {
    if (!poppedOut && !docked && isNarrowViewport()) {
      reviewRestoreRef.current = mode !== "min"
        ? { approvalId, mode }
        : null;
      minimize();
      setReviewingApprovalId(approvalId);
      // The global window lives in a separate fixed shadow layer above the HA
      // panel. Removing that entire layer during review avoids stale iOS WebKit
      // compositor tiles from surviving over the panel modal after navigation.
      notifyAgentChatReviewOpened(approvalId);
    } else {
      reviewRestoreRef.current = null;
    }
    const url = `/phoenix-mcp/approvals/${encodeURIComponent(approvalId)}`;
    try {
      window.history.pushState(null, "", url);
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    } catch {
      window.location.href = url;
    }
  }, [minimize, mode, poppedOut, docked]);

  // Stable for the transcript's memoized items: what it closes over changes on every drag.
  const openReviewRef = useRef(openReview);
  openReviewRef.current = openReview;
  const reviewFromItem = useCallback((approvalId: string) => openReviewRef.current(approvalId), []);

  const maximize = useCallback(() => setMode("max"), []);

  // Rotating a phone (any viewport resize) can strand the remembered window or
  // pill geometry entirely off the new viewport, with nothing left to tap;
  // refit whichever is showing as soon as the viewport changes. Also run once
  // on mount for a geometry that went stale while the window was unmounted.
  // The change-guards make in-bounds calls no-ops, so drags and resizes (which
  // already clamp to the viewport) are never fought. It runs before the window is
  // painted, so a window saved on a larger screen never shows past the edge of a
  // smaller one, such as an iPad.
  useLayoutEffect(() => {
    if (poppedOut) return;
    const refit = () => {
      const vw = window.innerWidth, vh = window.innerHeight, M = VIEWPORT_MARGIN;
      const topMargin = agentCliTopMargin(rootRef.current);
      if (mode === "min") {
        const pillSize = currentPillSize();
        const base = pillPos.x >= 0 ? pillPos : pos;  // same fallback the pill renders at
        const np = clampPosToViewport(base, pillSize, vw, vh, topMargin);
        if (np.x !== pillPos.x || np.y !== pillPos.y) {
          setPillPos(np);
          patchDurable({ pillPos: np }, persistenceOwner);
        }
      } else if (mode === "normal") {
        const ns = {
          w: Math.min(size.w, Math.max(RESIZE_MIN_W, vw - 2 * M)),
          h: Math.min(size.h, Math.max(RESIZE_MIN_H, vh - topMargin - M)),
        };
        const np = clampPosToViewport(pos, ns, vw, vh, topMargin);
        if (ns.w !== size.w || ns.h !== size.h || np.x !== pos.x || np.y !== pos.y) {
          setSize(ns);
          setPos(np);
          patchDurable({ size: ns, pos: np }, persistenceOwner);
        }
      }
    };
    refit();
    window.addEventListener("resize", refit);
    return () => window.removeEventListener("resize", refit);
  }, [mode, pos, size, pillPos, poppedOut, persistenceOwner]);

  // Back to the windowed geometry, refitted to the CURRENT viewport: both were
  // remembered under a possibly different one (a phone rotated while minimized
  // or maximized), so clamping the position alone is not enough; a size wider
  // or taller than the viewport must shrink too or it still hangs off-screen.
  const restore = useCallback(() => {
    const vw = window.innerWidth, vh = window.innerHeight, M = VIEWPORT_MARGIN;
    const topMargin = agentCliTopMargin(rootRef.current);
    const ns = {
      w: Math.min(size.w, Math.max(RESIZE_MIN_W, vw - 2 * M)),
      h: Math.min(size.h, Math.max(RESIZE_MIN_H, vh - topMargin - M)),
    };
    const np = clampPosToViewport(pos, ns, vw, vh, topMargin);
    setSize(ns);
    setPos(np);
    patchDurable({ size: ns, pos: np }, persistenceOwner);
    setMode("normal");
  }, [persistenceOwner, pos, size.h, size.w]);

  const popIn = useCallback(() => {
    const popup = popupRef.current;
    closingPopupRef.current = true;
    forgetAgentCliPopup(popup);
    popupRef.current = null;
    setPopupMount(null);
    setPopOutError("");
    // Let React detach the portal before its document goes away.
    window.setTimeout(() => popup?.close(), 0);
  }, []);

  const popOut = useCallback(() => {
    // Separate browser windows are unreliable on mobile and can strand the
    // conversation behind browser chrome. The control is hidden there too,
    // but keep the action itself guarded against stale or scripted calls.
    if (isNarrowViewport()) return;
    const opened = openPopupWindow({
      name: POPUP_WINDOW_NAME, mountId: POPUP_MOUNT_ID, width: size.w, height: size.h,
      title: agentChatTitle, language: agentChatLanguage, background: "var(--phx-agentcli-surface)",
    });
    if (!opened) {
      const message = t("agentchat.popOutBlocked");
      setPopOutError(message);
      setAnnouncement(message);
      return;
    }
    const { window: popup, mount } = opened;

    closingPopupRef.current = false;
    activePopupWindow = popup;
    popupRef.current = popup;
    applyPopupTheme(popup, currentHass());
    setMode("normal");
    setPopOutError("");
    setPopupMount(mount);
  }, [agentChatLanguage, agentChatTitle, currentHass, size]);

  const noInstances = instances.length === 0;

  const pillXY = pillPos.x >= 0 ? pillPos : pos;  // fall back before first seed
  const collapsed = minimized && !poppedOut && !docked;
  // Maximized geometry comes entirely from CSS (inset: 0); no inline style.
  const geometryStyle: React.CSSProperties = poppedOut || docked || maximized
    ? {}
    : collapsed
      ? { left: pillXY.x, top: pillXY.y }
      : { left: pos.x, top: pos.y, width: size.w, height: size.h };
  const style = {
    ...geometryStyle,
    "--phx-agentcli-text-size": `${textSizePt}pt`,
  } as React.CSSProperties;

  const adjustTextSize = (delta: number) => {
    setTextSizePt((current) => normalizeAgentCliTextSize(current + delta));
  };

  // The header menu: the connection, what the conversation shows, text size, and clearing it.
  const chatMenu = (): MenuEntry[] => [
    { id: "connection", label: t("agentchat.connection"), toggle: true, checked: connectionOpen, onSelect: () => setConnectionOpen((open) => !open) },
    { id: "verbose", label: t("agentchat.verbose"), hint: t("agentchat.verboseHint"), toggle: true, checked: options.verbose,
      onSelect: () => setOptions((o) => ({ ...o, verbose: !o.verbose })) },
    { id: "timestamps", label: t("agentchat.showTimestamps"), hint: t("agentchat.showTimestampsHint"), toggle: true, checked: showTimestamps,
      onSelect: () => setShowTimestamps((on) => !on) },
    { id: "footer", label: t("agentchat.showFooter"), hint: t("agentchat.showFooterHint"), toggle: true, checked: showFooter,
      onSelect: () => setShowFooter((on) => !on) },
    "separator",
    { id: "smaller", label: t("agentchat.decreaseTextSize"), disabled: textSizePt <= AGENT_CHAT_TEXT_SIZE_MIN_PT, onSelect: () => adjustTextSize(-AGENT_CHAT_TEXT_SIZE_STEP_PT) },
    { id: "larger", label: t("agentchat.increaseTextSize"), disabled: textSizePt >= AGENT_CHAT_TEXT_SIZE_MAX_PT, onSelect: () => adjustTextSize(AGENT_CHAT_TEXT_SIZE_STEP_PT) },
    "separator",
    { id: "clear", label: t("agentchat.clearHistory"), onSelect: clearHistory },
    ...(dock ? ["separator" as const, dock.docked
      ? { id: "float", label: t("agentchat.floatChat"), onSelect: dock.toggle }
      : { id: "dock", label: t("workspace.addToMy"), onSelect: dock.toggle }] : []),
  ];

  const content = (
    <MenuRootContext.Provider value={rootEl}>
    <div ref={attachRoot} className={`agentcli-window${collapsed ? " agentcli-minimized" : ""}${reviewingApprovalId ? " agentcli-reviewing" : ""}${maximized && !poppedOut && !docked ? " agentcli-maximized" : ""}${poppedOut ? " agentcli-popped-out" : ""}${docked ? " agentcli-docked" : ""}`} style={style} role={docked ? "region" : "dialog"} aria-label={agentChatTitle}
         // Files dropped anywhere on the window attach; unhandled, the browser
         // would open the image in place of Home Assistant.
         onDragOver={(e) => { if (e.dataTransfer?.types.includes("Files")) e.preventDefault(); }}
         onDrop={(e) => {
           if (!e.dataTransfer?.types.includes("Files")) return;
           e.preventDefault();
           // A drop is deliberate: report non-images rather than ignore them.
           void addFiles(Array.from(e.dataTransfer.files));
         }}>
      {/* Persistent polite announcer; mounted unconditionally so assistive
          tech reliably picks up updates (live regions must pre-exist). */}
      <div ref={announcerRef} className="sr-only" role="status">{announcement}</div>
      {collapsed && narrowViewport ? (
        // On a phone Chat comes back full screen, as when the toolbar opens it.
        <button type="button" className="agentcli-mobile-pill"
                onPointerDown={(e) => startDrag(e, true)}
                onClick={() => {
                  // The click that ends a drag leaves the circle where it was put.
                  if (circleDragged.current) { circleDragged.current = false; return; }
                  if (isNarrowViewport()) setMode("max"); else restore();
                }}
                title={t("agentchat.restore")} aria-label={t("agentchat.restore")}>
          <img src={PHOENIX_ICON} className="agentcli-mobile-pill-logo" alt="" draggable={false} />
        </button>
      ) : <div className="agentcli-titlebar" onPointerDown={startDrag}>
        <span className="agentcli-title">{agentChatTitle}</span>
        <div className="agentcli-titlebar-actions">
          {!collapsed && (
            <MenuButton label={t("agentchat.options")} icon={<Icon name="chatMenu" />} items={chatMenu}
              className="agentcli-icon-btn agentcli-menu-btn" />
          )}
          {(!narrowViewport || poppedOut) && (
            <button
              className="agentcli-icon-btn agentcli-pop-btn"
              title={poppedOut ? t("agentchat.popIn") : t("agentchat.popOut")}
              aria-label={poppedOut ? t("agentchat.popIn") : t("agentchat.popOut")}
              onClick={poppedOut ? popIn : popOut}
            >
              {poppedOut ? (
                <Icon name="popIn" />
              ) : (
                <Icon name="popOut" />
              )}
            </button>
          )}
          {!poppedOut && !docked && mode !== "min" && (
            <button className="agentcli-icon-btn" title={t("agentchat.minimize")} aria-label={t("agentchat.minimize")}
                    onClick={minimize}><Icon name="minimize" /></button>
          )}
          {!poppedOut && !docked && mode !== "max" && (
            <button className="agentcli-icon-btn" title={t("agentchat.maximize")} aria-label={t("agentchat.maximize")}
                    onClick={maximize}><Icon name="maximize" /></button>
          )}
          {!poppedOut && !docked && mode !== "normal" && (
            <button className="agentcli-icon-btn" title={t("agentchat.restore")} aria-label={t("agentchat.restore")}
                    onClick={restore}><Icon name="restore" /></button>
          )}
          {!docked && <button className="agentcli-icon-btn" title={t("common.close")} aria-label={t("common.close")} onClick={handleClose}><Icon name="close" /></button>}
        </div>
      </div>}

      {popOutError && (
        <div className="agentcli-popout-error" role="alert">{popOutError}</div>
      )}

      {!collapsed && (
        <>
          {/* One line naming the key, provider and model; the triangle beside it opens the fields that change them. */}
          <div className="agentcli-connection" data-guide="window.agentChat#connection">
            <div className="agentcli-connection-head">
              {/* Only the triangle opens the fields: the line itself is text, not a control. */}
              <button type="button" className="agentcli-connection-toggle" aria-expanded={connectionOpen}
                      aria-controls={connectionId} aria-label={t("agentchat.changeConnection")}
                      title={t("agentchat.changeConnection")} onClick={() => setConnectionOpen((open) => !open)}>
                <Icon name={connectionOpen ? "expand" : "folded"} size={14} />
              </button>
              {connectionOpen
                ? <span className="agentcli-connection-label">{t("agentchat.connection")}</span>
                : (
                  <span className="agentcli-connection-summary">
                    <span className="agentcli-connection-key">{accessKeys.find((k) => k.id === accessKeyId)?.name ?? t("common.none")}</span>
                    <span className="agentcli-connection-rest">{noInstances ? ` · ${t("common.none")}` : ` · ${instance?.name ?? ""} · ${model || t("agentchat.noModels")}`}</span>
                  </span>
                )}
            </div>
            {/* Kept mounted while closed, so the selections stay in place for the rest of the window. */}
            <div className="agentcli-controls" id={connectionId} hidden={!connectionOpen}
                 onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setConnectionOpen(false); } }}>
              {/* Two rows of their own: too narrow for one line, the second drops below the first and neither wraps. */}
              <div className="agentcli-control-row">
                <label className="agentcli-control" title={t("agentchat.accessKeyHint")}>
                  <span>{t("agentchat.accessKey")}</span>
                  <select className="input agentcli-select" value={accessKeyId} onChange={(e) => setAccessKeyId(e.target.value)} aria-label={t("agentchat.accessKey")} disabled={sending}>
                    {accessKeys.map((tok) => <option key={tok.id} value={tok.id}>{tok.name}</option>)}
                  </select>
                </label>
                <label className="agentcli-control" title={t("agentchat.providerHint")}>
                  <span>{t("agentchat.provider")}</span>
                  <select className="input agentcli-select" value={instanceId} onChange={(e) => setInstanceId(e.target.value)} aria-label={t("agentchat.provider")} disabled={sending || noInstances}>
                    {noInstances
                      ? <option value="">{t("common.none")}</option>
                      : instances.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
                  </select>
                </label>
              </div>
              <div className="agentcli-control-row">
                <label className="agentcli-control" title={t("agentchat.modelHint")}>
                  <span>{t("agentchat.model")}</span>
                  <select className="input agentcli-select" value={model} onChange={(e) => setModel(e.target.value)} aria-label={t("agentchat.model")} disabled={sending || !models.length}>
                    {noInstances
                      ? <option value="">{t("common.none")}</option>
                      : models.length
                        ? models.map((m) => <option key={m} value={m}>{m}</option>)
                        : <option value="">{t("agentchat.noModels")}</option>}
                  </select>
                </label>
                {caps.thinking.length > 0 && (
                  // One dropdown carries this provider's real API thinking levels.
                  <label className="agentcli-control">
                    <span>{kind === "ollama" ? t("agentchat.thinkingOllama") : t("agentchat.thinking")}</span>
                    <select className="input agentcli-select" value={thinkValue(caps, options)}
                            onChange={(e) => applyThink(caps, e.target.value, setOptions)}>
                      {caps.thinking.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                    </select>
                  </label>
                )}
                {caps.temperature && (
                  <label className="agentcli-control">
                    <span>{t("agentchat.temperature")}</span>
                    <input className="input agentcli-select agentcli-number" type="number" step="0.1" min="0" max="2" value={options.temperature} placeholder={t("agentchat.temperatureDefault")}
                           onChange={(e) => setOptions((o) => ({ ...o, temperature: e.target.value }))} />
                  </label>
                )}
              </div>
              {caps.note && <div className="agentcli-connection-note">{caps.note}</div>}
            </div>
          </div>

          {/* The transcript, read in order; replies are announced by the status line above, so it stays quiet. */}
          <div className="agentcli-body" ref={bodyRef} role="log" aria-live="off" aria-label={t("conversations.conversation")}>
            {noInstances && (
              <div className="agentcli-empty">{t("agentchat.noProvider")}</div>
            )}
            {displayed.map((e, i) => (
              <ChatItem key={i} entry={e} verbose={options.verbose} showTs={showTimestamps} away={away} presentation={presentation}
                        onResolve={resolveApproval} onReview={reviewFromItem}
                      />
            ))}
            {sending && (
              <div className="agentcli-working" role="status" aria-live="polite">
                <span className="agentcli-spin" aria-hidden="true" />
                {liveStatus ?? t("agentchat.workingSpinner")}
              </div>
            )}
          </div>

          <StatusRegion>{otherTabTurn && <div className="agentcli-notice">{t("agentchat.otherTabTurn")}</div>}</StatusRegion>

          <StatusRegion>{pendingContinue !== null && !sending && (
            <div className="agentcli-continue" data-guide="window.agentChat#check-in">
              <span className="agentcli-continue-text">
                {tn("agentchat.pausedAfter", pendingContinue)}
              </span>
              <button className="btn btn-primary btn-sm" onClick={continueTurn}>{t("agentchat.continue")}</button>
              <button className="btn btn-sm" onClick={() => setPendingContinue(null)}>{t("agentchat.stop")}</button>
            </div>
          )}</StatusRegion>

          {(attachments.length > 0 || attachError) && (
            <div className="agentcli-attachments" data-guide="window.agentChat#images">
              {attachments.length > 0 && (
                <ul className="agentcli-attachment-list">
                  {attachments.map((attachment) => (
                    <li key={attachment.id} className="agentcli-attachment">
                      <img src={attachment.dataUrl} alt={t("agentchat.attachmentAlt", { name: attachment.name })} />
                      <button type="button" className="agentcli-attachment-remove"
                              aria-label={t("agentchat.removeAttachment", { name: attachment.name })}
                              title={t("agentchat.removeAttachment", { name: attachment.name })}
                              onClick={(e) => {
                                setAttachError("");
                                // The next picture's remove button takes focus, else the attach button.
                                const list = e.currentTarget.closest(".agentcli-attachment-list");
                                const at = attachments.findIndex((item) => item.id === attachment.id);
                                const panel = e.currentTarget.closest(".agentcli-window");
                                setAttachments((current) => current.filter((item) => item.id !== attachment.id));
                                // The button's own window: a popped-out Chat runs frames while the page behind it is hidden.
                                (e.currentTarget.ownerDocument.defaultView ?? window).requestAnimationFrame(() => {
                                  const left = [...(list?.querySelectorAll<HTMLElement>(".agentcli-attachment-remove") ?? [])];
                                  (left[Math.min(at, left.length - 1)] ?? panel?.querySelector<HTMLElement>(".agentcli-attach-btn"))?.focus();
                                });
                              }}>
                        <Icon name="close" size={14} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {attachments.length > 0 && (attachBlocked || declaredCaps?.vision !== true) && (
                <div className="agentcli-attachment-note">{attachBlocked || t("agentchat.attachVisionUnknown")}</div>
              )}
              {attachError && <div className="agentcli-attachment-error" role="alert">{attachError}</div>}
            </div>
          )}

          {away && (
            <div className="agentcli-offline" role="status">
              <span className="agentcli-spin" aria-hidden="true" />
              <span>{t("shell.offlineBody")}</span>
            </div>
          )}

          <div className="agentcli-input" data-guide="window.agentChat#conversation">
            <input
              ref={fileInputRef} type="file" accept="image/*" multiple hidden
              onChange={(e) => { const files = Array.from(e.target.files ?? []); e.target.value = ""; void addFiles(files); }}
            />
            <button
              type="button" className="btn btn-sm agentcli-attach-btn"
              aria-label={t("agentchat.attachImage")} title={attachBlocked || t("agentchat.attachImage")} data-guide="window.agentChat#images"
              aria-disabled={Boolean(attachBlocked) || attachments.length >= ATTACHMENT_MAX_COUNT}
              onClick={() => {
                if (attachBlocked) setAttachError(attachBlocked);
                else if (attachments.length >= ATTACHMENT_MAX_COUNT) setAttachError(tn("agentchat.attachTooMany", ATTACHMENT_MAX_COUNT));
                else fileInputRef.current?.click();
              }}
            >
              <Icon name="attach" size={18} />
            </button>
            {/* Stays editable while a turn runs so the next message can be drafted;
                send() and runTurn() refuse a second turn until this one ends. */}
            <textarea
              ref={inputRef}
              value={input}
              aria-label={t("agentchat.messageAria")}
              placeholder={noInstances ? t("agentchat.configureFirst") : away ? t("shell.offlineTitle") : t("agentchat.messagePlaceholder")}
              disabled={noInstances || away}
              rows={2}
              onChange={(e) => setInput(e.target.value)}
              // Enter that ends an input method's composition (Chinese, Japanese, Korean) picks the
              // characters; it does not send. Safari reports that as key code 229.
              onKeyDown={(e) => {
                if (e.key !== "Enter" || e.shiftKey || isImeKey(e)) return;
                e.preventDefault();
                void send();
              }}
              onPaste={(e) => {
                const files = imageFiles(e.clipboardData);
                if (files.length) { e.preventDefault(); void addFiles(files); }
              }}
            />
            {sending ? (
              <button type="button" className="btn btn-sm agentcli-cancel-btn" onClick={() => cancel()}
                      aria-label={t("agentchat.stop")} title={t("agentchat.stopRequest")}>
                <Icon name="stop" size={16} filled />
              </button>
            ) : (
              <button type="button" className="btn btn-primary btn-sm agentcli-send-btn"
                      aria-label={t("agentchat.send")} title={t("agentchat.send")}
                      aria-disabled={noInstances || away || (!input.trim() && !attachments.length) || Boolean(attachments.length && attachBlocked)}
                      onClick={() => { if (!noInstances && !away) void send(); }}>
                <Icon name="send" size={18} />
              </button>
            )}
          </div>

          {showFooter && (
            <div className="agentcli-footer" data-guide="window.agentChat#conversation">
              <div className="agentcli-text-size-controls" role="group" aria-label={t("agentchat.textSize")}>
                <button type="button" className="agentcli-text-size-btn"
                        aria-label={t("agentchat.decreaseTextSize")} title={t("agentchat.decreaseTextSize")}
                        disabled={textSizePt <= AGENT_CHAT_TEXT_SIZE_MIN_PT}
                        onClick={() => adjustTextSize(-AGENT_CHAT_TEXT_SIZE_STEP_PT)}>
                  <span aria-hidden="true">−</span>
                </button>
                <button type="button" className="agentcli-text-size-btn"
                        aria-label={t("agentchat.increaseTextSize")} title={t("agentchat.increaseTextSize")}
                        disabled={textSizePt >= AGENT_CHAT_TEXT_SIZE_MAX_PT}
                        onClick={() => adjustTextSize(AGENT_CHAT_TEXT_SIZE_STEP_PT)}>
                  <span aria-hidden="true">+</span>
                </button>
              </div>
              <div className="agentcli-usage"
                 title={usage.input + usage.output > 0
                   ? t("agentchat.usageTitle", { input: localeNumber(usage.input), output: localeNumber(usage.output) })
                   : t("agentchat.usageTitleEmpty")}>
                {usage.input + usage.output > 0 ? (
                  <>
                    {t("agentchat.usageSession", { tokens: fmtTokens(usage.input + usage.output) })}
                    {usage.context > 0 ? t("agentchat.usageContext", { tokens: fmtTokens(usage.context) }) : ""}
                  </>
                ) : usage.noData ? (
                  t("agentchat.usageNoData")
                ) : (
                  t("agentchat.usageZero")
                )}
              </div>
            </div>
          )}

          {/* resize handles: windowed mode only (full screen has no edges to drag) */}
          {mode === "normal" && !poppedOut && !docked && (["n", "s", "e", "w", "ne", "nw", "se", "sw"] as const).map((d) => (
            <div key={d} className={`agentcli-resize agentcli-resize-${d}`} onPointerDown={startResize(d)} />
          ))}
        </>
      )}
    </div>
    </MenuRootContext.Provider>
  );
  // A popped-out window stays in its browser window; otherwise a docked one draws into My Workspace.
  return popupMount ? createPortal(content, popupMount) : dockMount ? createPortal(content, dockMount) : content;
}

/** A backend notice or error in the operator's language.
 *
 *  The event carries a stable `code` and the English `message`. Codes Phoenix
 *  itself raises have a catalog entry; a provider-relayed message (a model API's
 *  own error text) has none and shows verbatim, as is right for text Phoenix
 *  did not write. */
// The status line for one model call, from the facts the server sent: the
// provider, the turn's step and tools run, how long nothing has shown, and a
// retry with the provider's own sentence. Never the model's words.
export function modelStatusText(p: Record<string, unknown>): string {
  const provider = String(p.provider ?? "");
  const seconds = localeNumber(Number(p.seconds ?? 0));
  const detail = failureDetailText(String(p.detail ?? ""), p.detail_code);
  const attempt = localeNumber(Number(p.attempt ?? 0));
  let status: string;
  if (p.phase === "retrying") {
    status = t("agentchat.status.retrying", { provider, detail, attempt });
  } else if (p.phase === "thinking") {
    status = t("agentchat.status.thinking", { provider, seconds });
  } else if (detail && Number(p.attempt ?? 0) > 0) {
    status = t("agentchat.status.retryWait", { provider, detail, attempt, seconds });
  } else if (Number(p.seconds ?? 0) > 0) {
    status = t("agentchat.status.noReply", { provider, seconds });
  } else {
    status = t("agentchat.status.waiting", { provider });
  }
  const step = Number(p.step ?? 1);
  const tools = Number(p.tools ?? 0);
  const turn = step > 1 || tools > 0
    ? [t("agentchat.status.step", { step: localeNumber(step) }), tn("agentchat.status.tools", tools, { count: localeNumber(tools) })]
    : [];
  return [...turn, status].join(" · ");
}

function serverText(
  code: string | undefined,
  message: string,
  params?: Record<string, string | number>,
  rateLimit?: ProviderRateLimit,
  providerDetail?: string,
): string {
  const lines = [chatNoticeText(code, message, params), rateLimitDiagnostics(rateLimit)];
  if (providerDetail) lines.push(t("agentchat.providerDetail", { detail: providerDetail }));
  return lines.filter(Boolean).join("\n");
}

function rateLimitDiagnostics(info?: ProviderRateLimit): string {
  if (!info) return "";
  const lines: string[] = [];
  const pair = (
    remaining: number | undefined, limit: number | undefined, key: string,
  ) => {
    if (remaining !== undefined && limit !== undefined) {
      lines.push(t(key, { remaining: localeNumber(remaining), limit: localeNumber(limit) }));
    }
  };
  pair(info.tokens_minute_remaining, info.tokens_minute_limit,
       "agentchat.rateLimitTokensMinute");
  pair(info.tokens_five_minute_remaining, info.tokens_five_minute_limit,
       "agentchat.rateLimitTokensFiveMinute");
  pair(info.tokens_month_remaining, info.tokens_month_limit,
       "agentchat.rateLimitTokensMonth");
  pair(info.requests_minute_remaining, info.requests_minute_limit,
       "agentchat.rateLimitRequestsMinute");
  pair(info.requests_second_remaining, info.requests_second_limit,
       "agentchat.rateLimitRequestsSecond");
  if (info.generic_remaining !== undefined && info.generic_limit !== undefined) {
    lines.push(t("agentchat.rateLimitGeneric", {
      remaining: localeNumber(info.generic_remaining),
      limit: localeNumber(info.generic_limit),
    }));
  } else if (info.generic_remaining !== undefined) {
    lines.push(t("agentchat.rateLimitGenericRemaining", {
      remaining: localeNumber(info.generic_remaining),
    }));
  }
  if (info.query_cost !== undefined) {
    lines.push(t("agentchat.rateLimitQueryCost", { cost: localeNumber(info.query_cost) }));
  }
  if (info.retry_after_seconds !== undefined) {
    lines.push(t("agentchat.rateLimitRetryAfter", {
      seconds: localeNumber(info.retry_after_seconds),
    }));
  }
  return lines.join("\n");
}

function CopyResponseButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = async () => {
    window.clearTimeout(timer.current);
    try {
      await copyToClipboard(text);
      setCopied(true);
      timer.current = window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // A browser may deny both clipboard mechanisms, especially on HTTP. Keep
      // the action available and do not claim the response was copied.
      setCopied(false);
    }
  };

  const label = copied ? t("common.copied") : t("common.copy");
  return (
    <button
      type="button"
      className={`agentcli-copy-btn${copied ? " is-copied" : ""}`}
      aria-label={label}
      title={label}
      onClick={(event) => { event.stopPropagation(); void copy(); }}
    >
      <Icon name={copied ? "check" : "copy"} size={14} />
    </button>
  );
}

/** Parsed once per text: a transcript item redraws for reasons that leave its text alone. */
const MarkdownText = React.memo(function MarkdownText({ text, className }: { text: string; className: string }) {
  return <div className={className} dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }} />;
});

// Memoized, with every prop stable for an entry that has not changed: the window
// redraws on each keystroke and each drawn token, and a long transcript must not.
const ChatItem = React.memo(function ChatItem({
  entry, verbose, showTs, away = false, onResolve, onReview,
}: {
  /** The language and formats on display. Unread here: text comes from module-level helpers, so a change must arrive as a prop. */
  presentation: string;
  entry: ChatEntry;
  verbose: boolean;
  showTs: boolean;
  /** Home Assistant is not answering: a decision cannot be sent, so it is not offered. */
  away?: boolean;
  onResolve: (id: string, approve: boolean) => void;
  onReview: (approvalId: string) => void;
}) {
  // Under a message: Copy, always available, and the wall-clock time when
  // timestamps are on. Entries without a ts (restored from a session
  // transcript) show no time.
  const copyText = (entry.kind === "user" || entry.kind === "assistant") && entry.text.trim()
    ? entry.text
    : null;
  const time = showTs && "ts" in entry && entry.ts ? fmtClock(entry.ts) : null;
  const ts = copyText !== null || time !== null
    ? (
      <div className="agentcli-msg-meta">
        {copyText !== null && <CopyResponseButton text={copyText} />}
        {time !== null && <span className="agentcli-ts">{time}</span>}
      </div>
    )
    : null;
  switch (entry.kind) {
    case "user": {
      // Browser storage keeps only each image's name, so a restored entry says
      // the image is gone instead of showing it.
      const images = Array.isArray(entry.images)
        ? entry.images.filter((image) => image && typeof image.name === "string") : [];
      return (
        <div className="agentcli-msg agentcli-msg-user">
          {/* Who wrote it shows by alignment and colour; this says it. */}
          <span className="sr-only">{t("agentchat.authorUser")}: </span>
          {images.length > 0 && (
            <div className="agentcli-msg-images">
              {images.map((image, index) => (typeof image.dataUrl === "string" && image.dataUrl.startsWith("data:image/")
                ? <img key={index} src={image.dataUrl} alt={t("agentchat.attachmentAlt", { name: image.name })} />
                : <span key={index} className="agentcli-msg-image-gone">{t("agentchat.attachmentGone", { name: image.name })}</span>))}
            </div>
          )}
          {entry.text}{ts}
        </div>
      );
    }
    case "assistant": {
      const hasReply = entry.text.trim().length > 0;
      const hasVisibleThinking = verbose && entry.thinking.trim().length > 0;
      // A tool-use round can carry reasoning but no reply text. When reasoning
      // is hidden, suppress the whole bubble so its timestamp cannot survive as
      // an orphaned row. Turning verbose on reveals both together.
      if (!hasReply && !hasVisibleThinking) return null;
      return (
        <div className="agentcli-msg agentcli-msg-assistant">
          <span className="sr-only">{t("agentchat.authorAgent")}: </span>
          {hasVisibleThinking && (
            <details className="agentcli-thinking">
              <summary>{t("agentchat.reasoning")}</summary>
              {/* Same sanitizing renderer as the reply body: models emit
                  markdown in their reasoning too, which a raw <pre> would
                  show as literal ** and # markup. */}
              <MarkdownText className="agentcli-md agentcli-thinking-body" text={entry.thinking} />
            </details>
          )}
          {hasReply && flagsUnsafeContent(entry.text) && (
            <div className="agentcli-unsafe">{t("agentchat.unsafeContent")}</div>
          )}
          {hasReply && <MarkdownText className="agentcli-md" text={entry.text} />}
          {ts}
        </div>
      );
    }
    case "tool_call":
      // Tool activity is "verbose" detail; hidden unless the operator opts in.
      return verbose ? <div className="agentcli-tool">{tRich("agentchat.callingTool", { code: (c) => <code>{c}</code> }, { name: entry.name })}</div> : null;
    case "progress":
      // Shown even with verbose off: this is not debug detail, it is the only
      // sign of life during a multi-minute build, which is exactly when a
      // non-verbose operator would otherwise be staring at an empty window.
      // The generic activity line is the mirror image: with verbose ON the
      // "calling <tool>" entries already say it, so rendering both is noise.
      if (entry.activity && verbose) return null;
      return (
        // Not a live region: a long build rewrites this line many times, and every finished turn
        // keeps its lines; the working line below the transcript speaks for the turn in progress.
        <div className="agentcli-progress">
          <span className="agentcli-progress-dot" aria-hidden="true" />
          {entry.message}
        </div>
      );
    case "tool_result":
      return verbose ? (
        <div className={`agentcli-tool-result${entry.isError ? " is-error" : ""}`}>
          {/* A failure is marked by a sign as well as its colour. */}
          {entry.isError && <><Icon name="warning" size={13} /><span className="sr-only">{t("agentchat.errorLabel")}: </span></>}
          {tRich("agentchat.toolResult", { code: (c) => <code>{c}</code> },
                 { name: entry.name, summary: entry.summary || (entry.isError ? t("agentchat.resultError") : t("agentchat.resultOk")) })}
        </div>
      ) : null;
    case "tool_image":
      return (
        <figure className="agentcli-tool-image">
          {entry.unavailable || !entry.dataUrl ? (
            <div className="agentcli-tool-image-unavailable" role="status">
              {t("agentchat.imageUnavailable")}
            </div>
          ) : (
            <img src={entry.dataUrl} alt={t("agentchat.toolImageAlt", { name: entry.name })} />
          )}
          <figcaption>{entry.unavailable ? "" : entry.name}</figcaption>
        </figure>
      );
    case "approval":
      if (entry.status === "cancelled" && entry.reason === "agent_chat_ended") {
        return <div className="agentcli-notice">{t("agentchat.chatEnded")}</div>;
      }
      return (
        <div className="agentcli-approval" data-guide="window.agentChat#approvals">
          <div>{tRich("agentchat.approvalNeeded", { code: (c) => <code>{c}</code> }, { name: entry.toolName })}</div>
          {entry.status === "pending" && !entry.checking ? (
            <div className="agentcli-approval-actions">
              <button className="btn btn-outline btn-sm" disabled={away} onClick={() => onReview(entry.approvalId)}>{t("agentchat.review")}</button>
              <button className="btn btn-sm agentcli-approve" disabled={away} onClick={() => onResolve(entry.approvalId, true)}>
                <Icon name="check" size={14} />{t("agentchat.approve")}
              </button>
              <button className="btn btn-sm agentcli-reject" disabled={away} onClick={() => onResolve(entry.approvalId, false)}>
                <Icon name="close" size={14} />{t("agentchat.reject")}
              </button>
            </div>
          ) : (
            <div className="agentcli-approval-status">
              {entry.checking ? t("common.loading") : approvalStatusLabel(entry.status)}
              {entry.reason ? <span className="agentcli-approval-reason">{t("agentchat.approvalReason", { reason: entry.executionReason ? localizedExecutorError(entry.reason) : localizedApprovalReason(entry.reason) })}</span> : null}
            </div>
          )}
        </div>
      );
    case "notice":
      return <div className="agentcli-notice">{serverText(entry.code, entry.message, entry.messageParams)}</div>;
    case "error":
      return <div className="agentcli-error"><Icon name="warning" size={14} /><span className="sr-only">{t("agentchat.errorLabel")}: </span>{
        serverText(entry.code, entry.message, entry.messageParams, entry.rateLimit, entry.providerDetail)
      }{(entry.code === "outcome_unknown" || entry.code === "provider_after_dispatch") && <p>
        <a href="/phoenix-mcp#attention" target="_blank" rel="noopener noreferrer">{t("attention.title")}</a>
      </p>}</div>;
    default:
      return null;
  }
});
