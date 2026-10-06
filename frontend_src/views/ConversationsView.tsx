import { mdiContentCopy, mdiCheck, mdiArrowBottomLeft, mdiArrowTopRight, mdiAlertCircleOutline, mdiAlertOutline, mdiCancel, mdiCheckCircleOutline, mdiClockOutline, mdiFileDocumentEditOutline, mdiInformationOutline, mdiPauseCircleOutline, mdiSourceBranch } from "@mdi/js";
import { useCallback, useEffect, useRef, useState, useMemo } from "react";
import { api } from "../api";
import { copyToClipboard } from "../utils";
import type { AccessKeyRecord, ConversationEntry, ConversationEvent, ConversationSummary } from "../types";
import { BusyButton } from "../components/BusyButton";
import { RefreshIcon } from "../components/common";
import { hasMessage, t, tn } from "../i18n";
import { localeDateTime, localeNumber } from "../i18n/format";

const LABEL_KEYS: Record<string, Record<string, string>> = {
  "source": {
    "agentcli": "conversations.source.agentcli",
    "voice": "conversations.source.voice",
    "ai_task": "conversations.source.ai_task",
    "assist": "conversations.source.assist",
    "mcp": "conversations.source.mcp",
    "home_companion": "conversations.source.home_companion"
  },
  "coverage": {
    "full_turn": "conversations.coverage.full_turn",
    "partial_conversation": "conversations.coverage.partial_conversation",
    "tool_exchange": "conversations.coverage.tool_exchange"
  },
  "event": {
    "assistant_text": "conversations.event.assistant_text",
    "tool_call": "conversations.event.tool_call",
    "tool_result": "conversations.event.tool_result",
    "change": "conversations.event.change",
    "approval_requested": "conversations.event.approval_requested",
    "approval_resolved": "conversations.event.approval_resolved",
    "routing": "conversations.event.routing",
    "turn_outcome": "conversations.event.turn_outcome",
    "error": "conversations.event.error",
    "notice": "conversations.event.notice",
    "continue_required": "conversations.event.continue_required"
  },
  "outcome": {
    "completed": "conversations.outcome.completed",
    "failed": "conversations.outcome.failed",
    "denied": "conversations.outcome.denied",
    "pending_approval": "conversations.outcome.pending_approval",
    "cancelled": "conversations.outcome.cancelled",
    "interrupted": "conversations.outcome.interrupted",
    "unknown": "conversations.outcome.unknown",
    "running": "conversations.outcome.running",
    "allowed": "conversations.outcome.allowed",
    "invalid_request": "conversations.outcome.invalid_request",
    "not_found": "conversations.outcome.not_found",
    "rate_limited": "conversations.outcome.rate_limited",
    "not_implemented": "conversations.outcome.not_implemented",
    "no_tools": "conversations.outcome.no_tools",
    "paused": "conversations.outcome.paused",
    "partial": "conversations.outcome.partial",
    "rejected": "conversations.outcome.rejected",
    "expired": "conversations.outcome.expired",
    "approved": "conversations.outcome.approved"
  },
  "responseStatus": {
    "produced": "conversations.responseStatus.produced",
    "unavailable": "conversations.responseStatus.unavailable",
    "client_unavailable": "conversations.responseStatus.client_unavailable",
    "failed": "conversations.responseStatus.failed",
    "cancelled": "conversations.responseStatus.cancelled",
    "interrupted": "conversations.responseStatus.interrupted",
    "recording_stopped": "conversations.responseStatus.recording_stopped",
    "size_limit": "conversations.responseStatus.size_limit"
  },
  "omission": {
    "privacy": "conversations.omission.privacy",
    "sanitization": "conversations.omission.sanitization",
    "size_limit": "conversations.omission.size_limit"
  }
};

function label(group: string, value: string): string {
  const key = LABEL_KEYS[group]?.[value] ?? "conversations.unknown";
  return hasMessage(key) ? t(key) : t("conversations.unknown");
}

function raw(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function TextEvidence({ value, code = false }: { value: unknown; code?: boolean }) {
  const text = raw(value);
  const [copyState, setCopyState] = useState<"" | "copied" | "failed">("");
  const feedback = copyState ? t(copyState === "copied" ? "conversations.copied" : "conversations.copyFailed") : "";
  const body = code ? <pre>{text}</pre> : <div className="conversation-prose">{text}</div>;
  return <div className="conversation-evidence">
    <div className="conversation-evidence-content">{text.length > 1200 ? <details><summary>{t("conversations.fullText", { count: localeNumber(text.length) })}</summary>{body}</details> : body}</div>
    <button type="button" className="btn btn-icon conversation-copy" aria-label={t("conversations.copy")} title={feedback || t("conversations.copy")} onClick={async () => {
      setCopyState("");
      try { await copyToClipboard(text); setCopyState("copied"); }
      catch { setCopyState("failed"); }
    }}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d={copyState === "copied" ? mdiCheck : mdiContentCopy} /></svg></button>
    <span role="status" className={copyState === "failed" ? "conversation-copy-feedback" : "sr-only"}>{feedback}</span>
  </div>;

}

const OPERATION_KEYS: Record<string, string> = {
  get_state: "conversations.operation.state",
  get_states: "conversations.operation.state",
  call_service: "conversations.operation.action",
  get_automation: "conversations.operation.read",
  get_script: "conversations.operation.read",
  get_dashboard: "conversations.operation.read",
  create_automation: "conversations.operation.write",
  update_automation: "conversations.operation.write",
  delete_automation: "conversations.operation.write",
  create_script: "conversations.operation.write",
  update_script: "conversations.operation.write",
  update_dashboard: "conversations.operation.write",
  search_entities: "conversations.operation.search",
  list_entities: "conversations.operation.search",
  delegate: "conversations.operation.delegate",
};

type Signal = "neutral" | "success" | "warning" | "error";
function outcomeSignal(outcome: string): Signal {
  if (["failed", "denied", "rejected", "invalid_request", "not_found", "not_implemented"].includes(outcome)) return "error";
  if (["pending_approval", "cancelled", "interrupted", "unknown", "paused", "partial", "expired", "rate_limited", "recording_stopped", "size_limit"].includes(outcome)) return "warning";
  if (["allowed", "approved", "completed", "produced", "ok"].includes(outcome)) return "success";
  return "neutral";
}
function outcomeIcon(outcome: string): string {
  if (["denied", "rejected"].includes(outcome)) return mdiCancel;
  if (outcomeSignal(outcome) === "error") return mdiAlertCircleOutline;
  if (["pending_approval", "running"].includes(outcome)) return mdiClockOutline;
  if (["paused", "cancelled"].includes(outcome)) return mdiPauseCircleOutline;
  if (outcomeSignal(outcome) === "warning") return mdiAlertOutline;
  return outcomeSignal(outcome) === "success" ? mdiCheckCircleOutline : mdiInformationOutline;
}
function SignalIcon({ path, signal = "neutral" }: { path: string; signal?: Signal }) {
  return <svg className={`conversation-icon conversation-signal-${signal}`} viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d={path} /></svg>;
}

function rowStatus(entry: ConversationSummary): string {
  if (["failed", "cancelled", "interrupted", "paused"].includes(entry.status)) return entry.status;
  if (["error", "warning"].includes(outcomeSignal(entry.execution_status))) return entry.execution_status;
  return entry.status;
}

function unavailable(entry: ConversationEntry, field: "prompt" | "response"): string {
  const reason = entry.field_omissions?.[field] ?? (entry.omissions.includes("privacy") ? "privacy" : undefined);
  if (reason) return label("omission", reason);
  if (field === "prompt" && entry.coverage === "tool_exchange") return t("conversations.clientPromptUnavailable");
  return t(field === "prompt" ? "conversations.promptUnavailable" : "conversations.responseUnavailable");
}

function PromptEvidence({ value }: { value: unknown }) {
  if (!Array.isArray(value)) return <TextEvidence value={value} />;
  const text = value.filter(block => block && ["text", "input_text"].includes(block.type) && typeof block.text === "string").map(block => block.text).join("\n\n");
  const attachmentOnly = !text && value.some(block => block?.attachment === true);
  return <>{text && <TextEvidence value={text} />}{attachmentOnly && <p className="conversation-secondary">{t("conversations.attachmentPrompt")}</p>}<details><summary>{t("conversations.details")}</summary><TextEvidence value={value} code /></details></>;
}

function routeHeading(payload: Record<string, unknown> | null): string {
  const route = String(payload?.route);
  const key = route === "provider" ? "conversations.routeProvider"
    : route === "assist" ? "conversations.routeAssist"
    : route === "decision_provider" ? "conversations.routeDecision"
    : route === "decision_result" ? "conversations.routeDecisionResult"
    : route === "decision" ? "conversations.routeDecisionLocal" : route === "local" ? "conversations.routeLocal" : "conversations.event.routing";
  return t(key) + (typeof payload?.provider === "string" ? `: ${payload.provider}` : "");
}

const ROUTING_PURPOSE: Record<string, string> = {
  classify: "conversations.routing.classify", target: "conversations.routing.target",
  batch: "conversations.routing.batch", context: "conversations.routing.context",
  reference: "conversations.routing.reference", tool: "conversations.routing.tool",
  models: "conversations.routing.models",
};
const ROUTING_REASON: Record<string, string> = {
  no_match: "conversations.routing.noMatch", context_unavailable: "conversations.routing.contextUnavailable",
  context_unverified: "conversations.routing.contextUnverified", invalid_context: "conversations.routing.invalidContext",
  busy: "conversations.routing.busy", target_declined: "conversations.routing.targetDeclined",
};
function RoutingEvidence({ payload }: { payload: Record<string, unknown> }) {
  const operation = typeof payload.operation === "string" ? ROUTING_PURPOSE[payload.operation] : undefined;
  const reason = String(payload.selection_reason || payload.reason || "");
  const input = payload.input && typeof payload.input === "object" ? payload.input as Record<string, unknown> : null;
  return <>
    {operation && <p>{t(operation)}</p>}
    {payload.route === "provider" && <>
      {typeof payload.model === "string" && <p className="conversation-secondary">{t("conversations.modelLabel")}: <code>{payload.model}</code></p>}
      {typeof payload.purpose === "string" && <p>{t(payload.purpose === "tool_results" ? "conversations.routing.toolResults" : "conversations.routing.conversation")}</p>}
      {typeof payload.text_characters === "number" && typeof payload.tool_call_count === "number"
        ? <p>{t("conversations.routing.output", { characters: localeNumber(payload.text_characters), tools: localeNumber(payload.tool_call_count) })}</p>
        : <p className="conversation-secondary">{t("conversations.routing.legacy")}</p>}
    </>}
    {payload.route === "assist" && payload.matched === true && <p>{t("conversations.routing.assistMatched")}</p>}
    {ROUTING_REASON[reason] && <p>{t(ROUTING_REASON[reason])}</p>}
    {typeof input?.text === "string" && <details><summary>{t("conversations.routing.input")}</summary>
      <TextEvidence value={input.text} />
      {payload.route === "decision_provider" && <p className="conversation-secondary">{t("conversations.routing.contextHelp")}</p>}
    </details>}
  </>;
}

function EventRow({ event, responseStatus }: { event: ConversationEvent; responseStatus?: ConversationEntry["response_status"] }) {
  const payload = event.payload && typeof event.payload === "object" && !Array.isArray(event.payload)
    ? event.payload as Record<string, unknown> : null;
  const name = typeof payload?.name === "string" ? payload.name : typeof payload?.tool_name === "string" ? payload.tool_name : "";
  const recordedOutcome = event.outcome || (typeof payload?.status === "string" ? payload.status : typeof payload?.outcome === "string" ? payload.outcome : typeof payload?.result === "string" ? payload.result : "");
  const candidate = recordedOutcome === "ok" ? "completed" : recordedOutcome === "pending" ? "pending_approval" : recordedOutcome;
  const outcome = LABEL_KEYS.outcome[candidate] ? candidate : "";
  const signal = event.kind === "error" ? (payload?.code === "cancelled" ? "warning" : "error")
    : event.kind === "approval_requested" || event.kind === "continue_required" ? "warning" : outcomeSignal(outcome);
  const path = signal === "error" ? outcomeIcon(outcome || "failed")
    : event.kind === "tool_call" || event.kind === "assistant_text" ? mdiArrowTopRight
    : event.kind === "tool_result" ? (signal === "warning" ? outcomeIcon(outcome) : mdiArrowBottomLeft)
    : event.kind === "change" ? mdiFileDocumentEditOutline
    : event.kind === "routing" ? mdiSourceBranch
    : event.kind === "approval_requested" ? mdiClockOutline
    : event.kind === "continue_required" || payload?.code === "cancelled" ? mdiPauseCircleOutline : outcomeIcon(outcome);
  const heading = event.kind === "assistant_text" ? t(responseStatus ? "conversations.response" : "conversations.event.assistant_text")
    : event.kind === "routing" ? routeHeading(payload)
    : event.kind === "notice" && payload?.reason === "execution_unknown" ? t("conversations.executionUnknown")
    : event.kind === "tool_call" && OPERATION_KEYS[name] ? t(OPERATION_KEYS[name]) : label("event", event.kind);
  return <li className={`conversation-event conversation-event-${signal}`}>
    <SignalIcon path={path} signal={signal} />
    <div className="conversation-event-body">
    <div className="conversation-event-heading">
      <span><strong>{heading}</strong>{name && <> <code>{name}</code></>}</span>
      <time dateTime={event.timestamp}>{localeDateTime(event.timestamp)}</time>
    </div>
    {event.kind === "routing" && payload?.observation_only === true && <p className="conversation-secondary">{t("conversations.observationOnly")}</p>}
    {event.kind === "routing" && payload?.executed === false && payload?.observation_only !== true && <p className="conversation-secondary">{t("conversations.routingOnly")}</p>}
    {event.kind === "routing" && payload && <RoutingEvidence payload={payload} />}
    {responseStatus && <p className="conversation-secondary">{label("responseStatus", responseStatus)}</p>}
    {event.omission && <p className="conversation-secondary">{label("omission", event.omission)}</p>}
    {outcome && <p className={`conversation-outcome conversation-signal-${signal}`}>{event.kind === "routing" && ["provider", "decision_provider"].includes(String(payload?.route)) && outcome === "completed" ? t("conversations.routing.requestCompleted") : label("outcome", outcome)}</p>}
    {event.kind === "error" && typeof payload?.message === "string" && <div className="conversation-prose conversation-error-message">{payload.message}</div>}
    {event.payload !== null && event.payload !== undefined
      ? event.kind === "assistant_text" ? <TextEvidence value={event.payload} /> : event.kind === "tool_call" && payload ? <TextEvidence value={`${name} ${raw(payload.arguments ?? {})}`} code /> : <details><summary>{t(event.kind === "change" ? "conversations.changeEvidence" : "conversations.details")}</summary>
          <TextEvidence value={event.payload} code />
        </details>
      : <p className="conversation-secondary">{t("conversations.payloadUnavailable")}</p>}
    </div>
  </li>;
}

interface TimelineItem { event: ConversationEvent; index: number; children: TimelineItem[]; result?: ConversationEvent; }

/** Pair only explicit operation IDs; timestamps never establish parentage. */
function timeline(events: ConversationEvent[]): TimelineItem[] {
  const roots: TimelineItem[] = [];
  const calls = new Map<string, TimelineItem>();
  events.forEach((event, index) => {
    const item: TimelineItem = { event, index, children: [] };
    if (event.kind === "tool_result" && event.tool_id && calls.has(event.tool_id)) {
      calls.get(event.tool_id)!.result = event;
      return;
    }
    const parent = event.kind === "tool_call" ? event.parent_tool_id : event.tool_id;
    const owner = parent ? calls.get(parent) : undefined;
    (owner ? owner.children : roots).push(item);
    if (event.kind === "tool_call" && event.tool_id) calls.set(event.tool_id, item);
  });
  return roots;
}

function TimelineRow({ item, finalIndex, running, responseStatus }: { item: TimelineItem; finalIndex: number; running: boolean; responseStatus: ConversationEntry["response_status"] }) {
  if (item.event.kind !== "tool_call") return <EventRow event={item.event} responseStatus={item.index === finalIndex ? responseStatus : undefined} />;
  const payload = item.event.payload as Record<string, unknown> | null;
  const name = typeof payload?.name === "string" ? payload.name : "";
  const outcome = item.result?.outcome ?? (running ? "running" : "unknown");
  const signal = outcomeSignal(outcome);
  const heading = OPERATION_KEYS[name] ? t(OPERATION_KEYS[name]) : label("event", "tool_call");
  return <li className={`conversation-event conversation-event-${signal}`}>
    <SignalIcon path={outcomeIcon(outcome)} signal={signal} />
    <div className="conversation-event-body">
      <div className="conversation-event-heading"><span><strong>{heading}</strong> <code>{name}</code></span><time dateTime={item.event.timestamp}>{localeDateTime(item.event.timestamp)}</time></div>
      <p className={`conversation-outcome conversation-signal-${signal}`}>{label("outcome", outcome)}</p>
      {item.event.omission && <p className="conversation-secondary">{label("omission", item.event.omission)}</p>}
      {payload && <div role="group" aria-label={t("conversations.parameters")}><TextEvidence value={`${name} ${raw(payload.arguments ?? {})}`} code /></div>}
      {item.children[0]?.event.kind === "routing" && <ul className="conversation-route-inline"><EventRow event={item.children[0].event} /></ul>}
      {item.children.slice(item.children[0]?.event.kind === "routing" ? 1 : 0).length > 0 && <ol className="conversation-timeline conversation-nested">{item.children.slice(item.children[0]?.event.kind === "routing" ? 1 : 0).map(child => <TimelineRow key={child.index} item={child} finalIndex={finalIndex} running={running} responseStatus={responseStatus} />)}</ol>}
      {item.result && <details><summary>{t("conversations.toolResult")}</summary><ol className="conversation-timeline"><EventRow event={item.result} /></ol></details>}
    </div>
  </li>;
}

function EntryDetail({ id, revision, accessKeyNames }: { id: string; revision: string; accessKeyNames: Record<string, string> }) {
  const [entry, setEntry] = useState<ConversationEntry | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    api.getConversation(id).then(value => { if (active) { setEntry(value); setError(""); } })
      .catch(() => { if (active) setError(t("conversations.detailFailed")); });
    return () => { active = false; };
  }, [id, revision, retry]);
  if (error) return <div role="alert">{error} <button className="btn btn-text" onClick={() => setRetry(n => n + 1)}>{t("conversations.retry")}</button></div>;
  if (!entry) return <p role="status">{t("common.loading")}</p>;
  const finalTextIndex = entry.events.reduce((last, event, index) => event.kind === "assistant_text" && event.payload === entry.response ? index : last, -1);
  const accessKeyName = accessKeyNames[String(entry.metadata.access_key_id ?? "")] ?? entry.metadata.access_key_name;
  const identityFields = [
    { key: "access_key_name", title: "conversations.accessKeyLabel", value: accessKeyName },
    { key: "provider", title: "conversations.providerLabel", value: entry.metadata.provider },
    { key: "model", title: "conversations.modelLabel", value: entry.metadata.model },
  ];
  return <div className="conversation-detail">
    <dl className="conversation-identity">
      <div><dt>{t("conversations.sourceLabel")}</dt><dd>{label("source", entry.source)}</dd></div>
      {identityFields.map(({ key, title, value }) => <div key={key}><dt>{t(title)}</dt><dd>{typeof value === "string" ? value : t("conversations.identityUnavailable")}</dd></div>)}
    </dl>
    {entry.coverage === "tool_exchange" && <p className="conversation-secondary">{t("conversations.groupingUnavailable")}</p>}
    {entry.omissions.map(reason => <p className="banner banner-warning" key={reason}>{label("omission", reason)}</p>)}
    <section aria-label={t("conversations.prompt")}>
      <h3><SignalIcon path={mdiArrowBottomLeft} />{t("conversations.prompt")}</h3>
      {entry.prompt !== null ? <PromptEvidence value={entry.prompt} /> : <p className="conversation-secondary">{unavailable(entry, "prompt")}</p>}
    </section>
    <section aria-label={t("conversations.activity")}>
      <h3>{t("conversations.activity")}</h3>
      {entry.events.length ? <ol className="conversation-timeline">{timeline(entry.events).map(item => <TimelineRow key={item.index} item={item} finalIndex={finalTextIndex} running={entry.status === "running"} responseStatus={entry.response_status} />)}</ol>
        : <p className="conversation-secondary">{t("conversations.noActivity")}</p>}
    </section>
    {!!entry.omitted_events && <p className="banner banner-warning">{t("conversations.omittedEvents", { count: localeNumber(entry.omitted_events) })}</p>}
    {finalTextIndex < 0 && <section aria-label={t("conversations.response")}>
      <h3><SignalIcon path={mdiArrowTopRight} signal={outcomeSignal(entry.response_status)} />{t("conversations.response")}</h3>
      <p className={`conversation-response-status conversation-signal-${outcomeSignal(entry.response_status)}`}><SignalIcon path={outcomeIcon(entry.response_status)} signal={outcomeSignal(entry.response_status)} />{label("responseStatus", entry.response_status)}</p>
      {entry.response !== null ? <TextEvidence value={entry.response} /> : <p className="conversation-secondary">{unavailable(entry, "response")}</p>}
    </section>}
    <details className="conversation-technical"><summary>{t("conversations.technical")}</summary><p className="conversation-secondary">{t("conversations.deliveryUnverified")}</p><TextEvidence value={{
      entry_id: entry.id, source: entry.source, ...entry.metadata, access_key_name: accessKeyName, started_at: entry.started_at, updated_at: entry.updated_at,
      coverage: entry.coverage, status: entry.status, execution_outcome: entry.execution_status, response_outcome: entry.response_status,
      tool_count: entry.tool_count, change_count: entry.change_count, omissions: entry.omissions,
    }} code /></details>
  </div>;
}

function ConversationDetail({ entry, accessKeyNames }: { entry: ConversationSummary; accessKeyNames: Record<string, string> }) {
  const [visible, setVisible] = useState(25);
  const turns = entry.turns ?? [entry];
  return <div className="conversation-turns">
    {entry.history_missing && <p className="banner banner-warning">{t("conversations.historyMissing")}</p>}
    {turns.slice(0, visible).map(turn => <section className="conversation-turn" key={turn.id} aria-label={turn.excerpt || t("conversations.prompt")}>
      <div className="conversation-turn-heading"><time dateTime={turn.started_at}>{localeDateTime(turn.started_at)}</time><span className={`conversation-signal-${outcomeSignal(rowStatus(turn))}`}>{label("outcome", rowStatus(turn))}</span></div>
      <EntryDetail id={turn.id} revision={turn.updated_at} accessKeyNames={accessKeyNames} />
    </section>)}
    {visible < turns.length && <button className="btn btn-ghost" onClick={() => setVisible(n => n + 25)}>{t("common.loadMore")}</button>}
  </div>;
}

export function ConversationsView({ accessKeys = [] }: { accessKeys?: AccessKeyRecord[] }) {
  const accessKeyNames = useMemo(() => Object.fromEntries(accessKeys.map(key => [key.id, key.name])), [accessKeys]);
  const [entries, setEntries] = useState<ConversationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [limit, setLimit] = useState(25);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [source, setSource] = useState("");
  const [outcome, setOutcome] = useState("");
  const [days, setDays] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [available, setAvailable] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);
  const generation = useRef(0);
  const clearingRef = useRef(false);
  useEffect(() => { const timer = window.setTimeout(() => setDebouncedSearch(search), 300); return () => clearTimeout(timer); }, [search]);
  const load = useCallback(async (quiet = false) => {
    if (clearingRef.current) return;
    const own = ++generation.current;
    if (!quiet) setLoading(true);
    try {
      const params = { grouped: true, search: debouncedSearch, source, outcome,
        since: days ? new Date(Date.now() - Number(days) * 86400000).toISOString() : undefined };
      const result = await api.getConversations({ ...params, limit: Math.min(100, limit) });
      for (let offset = 100; offset < Math.min(limit, result.total); offset += 100) {
        if (own !== generation.current) return;
        const page = await api.getConversations({ ...params, limit: Math.min(100, limit - offset), offset });
        result.entries.push(...page.entries);
      }
      if (own !== generation.current) return;
      setEntries([...new Map(result.entries.map(entry => [entry.id, entry])).values()]); setTotal(result.total); setEnabled(result.enabled); setAvailable(result.available); setError("");
    } catch { if (own === generation.current) setError(t("conversations.loadFailed")); }
    finally { if (own === generation.current) setLoading(false); }
  }, [limit, debouncedSearch, source, outcome, days]);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => { if (!document.hidden) void load(true); }, 5000);
    const invalidate = () => { generation.current++; };
    return () => { clearInterval(timer); invalidate(); };
  }, [load]);
  const resetPage = () => setLimit(25);
  async function clear() {
    clearingRef.current = true; ++generation.current; setClearing(true);
    try {
      await api.clearConversations(); setEntries([]); setTotal(0); setOpen(new Set()); setConfirmClear(false); setError("");
    } catch { setError(t("conversations.clearFailed")); }
    finally { clearingRef.current = false; setClearing(false); setLoading(false); }
  }
  return <div className="view-root conversations-view">
    {!enabled && <p className="banner">{t("conversations.disabledHelp")}</p>}
    {!available && <p className="banner banner-warning" role="status">{t("conversations.storageUnavailable")}</p>}
    {error && <p className="banner banner-error" role="alert">{error}</p>}
    <div className="card">
    <div className="filter-row conversation-toolbar">
      <input className="input" type="search" aria-label={t("conversations.search")} placeholder={t("conversations.search")} value={search}
        onChange={e => { setSearch(e.target.value); resetPage(); }} />
      <select className="input input-auto" aria-label={t("conversations.sourceFilter")} value={source} onChange={e => { setSource(e.target.value); resetPage(); }}>
        <option value="">{t("conversations.allSources")}</option>
        {["agentcli", "voice", "ai_task", "assist", "mcp", "home_companion"].map(value => <option key={value} value={value}>{label("source", value)}</option>)}
      </select>
      <select className="input input-auto" aria-label={t("conversations.outcomeFilter")} value={outcome} onChange={e => { setOutcome(e.target.value); resetPage(); }}>
        <option value="">{t("conversations.allOutcomes")}</option>
        {["completed", "failed", "denied", "pending_approval", "cancelled", "interrupted", "unknown", "running"].map(value => <option key={value} value={value}>{label("outcome", value)}</option>)}
      </select>
      <select className="input input-auto" aria-label={t("conversations.timeFilter")} value={days} onChange={e => { setDays(e.target.value); resetPage(); }}>
        <option value="">{t("conversations.allDates")}</option><option value="1">{t("conversations.day")}</option><option value="7">{t("conversations.week")}</option><option value="30">{t("conversations.month")}</option>
      </select>
      <BusyButton className="btn btn-ghost btn-sm btn-icon" busy={loading} onClick={() => void load()} aria-label={t("common.refresh")} title={t("common.refresh")}><RefreshIcon /></BusyButton>
    </div>
    <div className="conversation-list" aria-busy={loading}>
      {!entries.length && !loading && <div className="conversation-empty"><h3>{t("conversations.empty")}</h3><p>{t("conversations.emptyHelp")}</p></div>}
      {entries.map(entry => <details key={entry.id} className="conversation-entry" open={open.has(entry.id)} onToggle={e => {
        const expanded = e.currentTarget.open;
        setOpen(previous => { if (previous.has(entry.id) === expanded) return previous; const next = new Set(previous); if (expanded) next.add(entry.id); else next.delete(entry.id); return next; });
      }}>
        <summary>
          <span className="conversation-summary">
            <span className="conversation-summary-title">{entry.turns && <span className="conversation-secondary">{t("conversations.conversation")} · </span>}{entry.excerpt || (entry.prompt_attachments ? t("conversations.attachmentPrompt") : entry.first_tool) || t(entry.coverage === "tool_exchange" ? "conversations.toolExchange" : "conversations.promptUnavailable")}</span>
            <span className="conversation-secondary">{label("source", entry.source)} · {localeDateTime(entry.latest_at ?? entry.started_at)}{entry.turns ? ` · ${t("conversations.turns", { count: localeNumber(entry.turns.length) })}` : ""}</span>
            <span className="conversation-secondary">{[entry.tool_count ? tn("conversations.toolCount", entry.tool_count) : "", entry.change_count ? tn("conversations.changeCount", entry.change_count) : "", entry.tool_count ? t("conversations.execution", { outcome: label("outcome", entry.execution_status) }) : ""].filter(Boolean).join(" · ")}</span>
          </span>
          <span className={`conversation-status conversation-signal-${outcomeSignal(rowStatus(entry))}`}><SignalIcon path={outcomeIcon(rowStatus(entry))} signal={outcomeSignal(rowStatus(entry))} />{label("outcome", rowStatus(entry))}</span>
        </summary>
        {open.has(entry.id) && (entry.turns ? <ConversationDetail entry={entry} accessKeyNames={accessKeyNames} /> : <EntryDetail id={entry.id} revision={entry.updated_at} accessKeyNames={accessKeyNames} />)}
      </details>)}
    </div>
    <div className="conversation-footer">
      <span className="conversation-secondary">{t("conversations.showing", { shown: localeNumber(entries.length), total: localeNumber(total) })}</span>
      {entries.length < total && <BusyButton busy={loading} className="btn btn-ghost" onClick={() => setLimit(n => n + 25)}>{t("common.loadMore")}</BusyButton>}
      <button className="btn btn-text" onClick={() => setConfirmClear(true)}>{t("conversations.clear")}</button>
    </div>
    </div>
    {confirmClear && <div className="banner banner-warning">
      <p>{t("conversations.clearConfirm")}</p>
      <BusyButton className="btn btn-danger" busy={clearing} onClick={() => void clear()}>{t("conversations.clear")}</BusyButton>{" "}
      <BusyButton className="btn btn-ghost" busy={clearing} onClick={() => setConfirmClear(false)}>{t("common.cancel")}</BusyButton>
    </div>}
  </div>;
}
