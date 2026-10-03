import { mdiArrowBottomLeft, mdiArrowTopRight, mdiAlertCircleOutline, mdiAlertOutline, mdiCancel, mdiCheckCircleOutline, mdiClockOutline, mdiFileDocumentEditOutline, mdiInformationOutline, mdiPauseCircleOutline, mdiSourceBranch } from "@mdi/js";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ConversationEntry, ConversationEvent, ConversationSummary } from "../types";
import { BusyButton } from "../components/BusyButton";
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
  const [copyState, setCopyState] = useState("");
  const body = code ? <pre>{text}</pre> : <div className="conversation-prose">{text}</div>;
  return <div className="conversation-evidence">
    {text.length > 1200 ? <details><summary>{t("conversations.fullText", { count: localeNumber(text.length) })}</summary>{body}</details> : body}
    <button className="btn btn-text btn-sm" onClick={async () => {
      try { await navigator.clipboard.writeText(text); setCopyState(t("conversations.copied")); }
      catch { setCopyState(t("conversations.copyFailed")); }
    }}>{t("conversations.copy")}</button>
    <span role="status">{copyState}</span>
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
  return <>{text && <TextEvidence value={text} />}<details><summary>{t("conversations.details")}</summary><TextEvidence value={value} code /></details></>;
}

function EventRow({ event, responseStatus }: { event: ConversationEvent; responseStatus?: ConversationEntry["response_status"] }) {
  const payload = event.payload && typeof event.payload === "object" && !Array.isArray(event.payload)
    ? event.payload as Record<string, unknown> : null;
  const name = typeof payload?.name === "string" ? payload.name : typeof payload?.tool_name === "string" ? payload.tool_name : "";
  const recordedOutcome = event.outcome || (typeof payload?.status === "string" ? payload.status : typeof payload?.outcome === "string" ? payload.outcome : "");
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
    : event.kind === "routing" && ["provider", "local", "decision"].includes(String(payload?.route)) ? t(payload.route === "provider" ? "conversations.routeProvider" : "conversations.routeLocal")
    : event.kind === "notice" && payload?.reason === "execution_unknown" ? t("conversations.executionUnknown")
    : event.kind === "tool_call" && OPERATION_KEYS[name] ? t(OPERATION_KEYS[name]) : label("event", event.kind);
  return <li className={`conversation-event conversation-event-${signal}`}>
    <SignalIcon path={path} signal={signal} />
    <div className="conversation-event-body">
    <div className="conversation-event-heading">
      <span><strong>{heading}</strong>{name && <> <code>{name}</code></>}</span>
      <time dateTime={event.timestamp}>{localeDateTime(event.timestamp)}</time>
    </div>
    {responseStatus && <p className="conversation-secondary">{label("responseStatus", responseStatus)}</p>}
    {event.omission && <p className="conversation-secondary">{label("omission", event.omission)}</p>}
    {outcome && <p className={`conversation-outcome conversation-signal-${signal}`}>{label("outcome", outcome)}</p>}
    {event.kind === "error" && typeof payload?.message === "string" && <div className="conversation-prose conversation-error-message">{payload.message}</div>}
    {event.payload !== null && event.payload !== undefined
      ? event.kind === "assistant_text" ? <TextEvidence value={event.payload} /> : <details><summary>{t(event.kind === "tool_call" ? "conversations.parameters" : event.kind === "change" ? "conversations.changeEvidence" : "conversations.details")}</summary>
          <TextEvidence value={event.kind === "tool_call" && payload ? payload.arguments : event.payload} code />
        </details>
      : <p className="conversation-secondary">{t("conversations.payloadUnavailable")}</p>}
    </div>
  </li>;
}

function EntryDetail({ id, revision }: { id: string; revision: string }) {
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
  return <div className="conversation-detail">
    <dl className="conversation-identity">
      <div><dt>{t("conversations.sourceLabel")}</dt><dd>{label("source", entry.source)}</dd></div>
      {([ ["token_name", "conversations.tokenLabel"], ["provider", "conversations.providerLabel"], ["model", "conversations.modelLabel"] ] as const).map(([key, title]) => <div key={key}><dt>{t(title)}</dt><dd>{typeof entry.metadata[key] === "string" ? String(entry.metadata[key]) : t("conversations.identityUnavailable")}</dd></div>)}
    </dl>
    {entry.coverage === "tool_exchange" && <p className="conversation-secondary">{t("conversations.groupingUnavailable")}</p>}
    {entry.omissions.map(reason => <p className="banner banner-warning" key={reason}>{label("omission", reason)}</p>)}
    <section aria-label={t("conversations.prompt")}>
      <h3><SignalIcon path={mdiArrowBottomLeft} />{t("conversations.prompt")}</h3>
      {entry.prompt !== null ? <PromptEvidence value={entry.prompt} /> : <p className="conversation-secondary">{unavailable(entry, "prompt")}</p>}
    </section>
    <section aria-label={t("conversations.activity")}>
      <h3>{t("conversations.activity")}</h3>
      {entry.events.length ? <ol className="conversation-timeline">{entry.events.map((event, index) => <EventRow key={`${event.timestamp}-${index}`} event={event} responseStatus={index === finalTextIndex ? entry.response_status : undefined} />)}</ol>
        : <p className="conversation-secondary">{t("conversations.noActivity")}</p>}
    </section>
    {!!entry.omitted_events && <p className="banner banner-warning">{t("conversations.omittedEvents", { count: localeNumber(entry.omitted_events) })}</p>}
    {finalTextIndex < 0 && <section aria-label={t("conversations.response")}>
      <h3><SignalIcon path={mdiArrowTopRight} signal={outcomeSignal(entry.response_status)} />{t("conversations.response")}</h3>
      <p className={`conversation-response-status conversation-signal-${outcomeSignal(entry.response_status)}`}><SignalIcon path={outcomeIcon(entry.response_status)} signal={outcomeSignal(entry.response_status)} />{label("responseStatus", entry.response_status)}</p>
      {entry.response !== null ? <TextEvidence value={entry.response} /> : <p className="conversation-secondary">{unavailable(entry, "response")}</p>}
    </section>}
    <details className="conversation-technical"><summary>{t("conversations.technical")}</summary><p className="conversation-secondary">{t("conversations.deliveryUnverified")}</p><TextEvidence value={{
      entry_id: entry.id, source: entry.source, ...entry.metadata, started_at: entry.started_at, updated_at: entry.updated_at,
      coverage: entry.coverage, status: entry.status, execution_outcome: entry.execution_status, response_outcome: entry.response_status,
      tool_count: entry.tool_count, change_count: entry.change_count, omissions: entry.omissions,
    }} code /></details>
  </div>;
}

export function ConversationsView() {
  const [entries, setEntries] = useState<ConversationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [storage, setStorage] = useState({ bytes: 0, max: 0 });
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
      const params = { search: debouncedSearch, source, outcome,
        since: days ? new Date(Date.now() - Number(days) * 86400000).toISOString() : undefined };
      const result = await api.getConversations({ ...params, limit: Math.min(100, limit) });
      for (let offset = 100; offset < Math.min(limit, result.total); offset += 100) {
        if (own !== generation.current) return;
        const page = await api.getConversations({ ...params, limit: Math.min(100, limit - offset), offset });
        result.entries.push(...page.entries);
      }
      if (own !== generation.current) return;
      setEntries([...new Map(result.entries.map(entry => [entry.id, entry])).values()]); setTotal(result.total); setEnabled(result.enabled); setAvailable(result.available); setStorage({ bytes: result.bytes, max: result.max_bytes ?? 0 }); setError("");
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
      await api.clearConversations(); setEntries([]); setTotal(0); setStorage(previous => ({ ...previous, bytes: 0 })); setOpen(new Set()); setConfirmClear(false); setError("");
    } catch { setError(t("conversations.clearFailed")); }
    finally { clearingRef.current = false; setClearing(false); setLoading(false); }
  }
  return <div className="view-root conversations-view">
    <div className="conversation-intro">
      <div><h2>{t("conversations.title")}</h2><p>{t("conversations.description")}</p></div>
      <span className="conversation-secondary">{t(enabled ? "conversations.recording" : "conversations.recordingOff")}</span>
    </div>
    {storage.max > 0 && <p className="conversation-secondary">{t("conversations.storage", { used: localeNumber(Math.round(storage.bytes / 10000) / 100), limit: localeNumber(storage.max / 1000000) })}</p>}
    {!enabled && <p className="banner">{t("conversations.disabledHelp")}</p>}
    {!available && <p className="banner banner-warning" role="status">{t("conversations.storageUnavailable")}</p>}
    {error && <p className="banner banner-error" role="alert">{error}</p>}
    <div className="conversation-toolbar">
      <input className="input" type="search" aria-label={t("conversations.search")} placeholder={t("conversations.search")} value={search}
        onChange={e => { setSearch(e.target.value); resetPage(); }} />
      <select className="input" aria-label={t("conversations.sourceFilter")} value={source} onChange={e => { setSource(e.target.value); resetPage(); }}>
        <option value="">{t("conversations.allSources")}</option>
        {["agentcli", "voice", "ai_task", "assist", "mcp", "home_companion"].map(value => <option key={value} value={value}>{label("source", value)}</option>)}
      </select>
      <select className="input" aria-label={t("conversations.outcomeFilter")} value={outcome} onChange={e => { setOutcome(e.target.value); resetPage(); }}>
        <option value="">{t("conversations.allOutcomes")}</option>
        {["completed", "failed", "denied", "pending_approval", "cancelled", "interrupted", "unknown", "running"].map(value => <option key={value} value={value}>{label("outcome", value)}</option>)}
      </select>
      <select className="input" aria-label={t("conversations.timeFilter")} value={days} onChange={e => { setDays(e.target.value); resetPage(); }}>
        <option value="">{t("conversations.allDates")}</option><option value="1">{t("conversations.day")}</option><option value="7">{t("conversations.week")}</option><option value="30">{t("conversations.month")}</option>
      </select>
      <BusyButton className="btn btn-ghost" busy={loading} onClick={() => void load()}>{t("common.refresh")}</BusyButton>
    </div>
    <div className="conversation-list" aria-busy={loading}>
      {!entries.length && !loading && <div className="conversation-empty"><h3>{t("conversations.empty")}</h3><p>{t("conversations.emptyHelp")}</p></div>}
      {entries.map(entry => <details key={entry.id} className="conversation-entry" open={open.has(entry.id)} onToggle={e => {
        const expanded = e.currentTarget.open;
        setOpen(previous => { if (previous.has(entry.id) === expanded) return previous; const next = new Set(previous); if (expanded) next.add(entry.id); else next.delete(entry.id); return next; });
      }}>
        <summary>
          <span className="conversation-summary">
            <span className="conversation-summary-title">{entry.excerpt || entry.first_tool || t(entry.coverage === "tool_exchange" ? "conversations.toolExchange" : "conversations.promptUnavailable")}</span>
            <span className="conversation-secondary">{label("source", entry.source)} · {localeDateTime(entry.started_at)} · {label("coverage", entry.coverage)}</span>
            <span className="conversation-secondary">{[entry.tool_count ? tn("conversations.toolCount", entry.tool_count) : "", entry.change_count ? tn("conversations.changeCount", entry.change_count) : "", entry.tool_count ? t("conversations.execution", { outcome: label("outcome", entry.execution_status) }) : ""].filter(Boolean).join(" · ")}</span>
          </span>
          <span className={`conversation-status conversation-signal-${outcomeSignal(rowStatus(entry))}`}><SignalIcon path={outcomeIcon(rowStatus(entry))} signal={outcomeSignal(rowStatus(entry))} />{label("outcome", rowStatus(entry))}</span>
        </summary>
        {open.has(entry.id) && <EntryDetail id={entry.id} revision={entry.updated_at} />}
      </details>)}
    </div>
    <div className="conversation-footer">
      <span className="conversation-secondary">{t("conversations.showing", { shown: localeNumber(entries.length), total: localeNumber(total) })}</span>
      {entries.length < total && <BusyButton busy={loading} className="btn btn-ghost" onClick={() => setLimit(n => n + 25)}>{t("common.loadMore")}</BusyButton>}
      <button className="btn btn-text" onClick={() => setConfirmClear(true)}>{t("conversations.clear")}</button>
    </div>
    {confirmClear && <div className="banner banner-warning">
      <p>{t("conversations.clearConfirm")}</p>
      <BusyButton className="btn btn-danger" busy={clearing} onClick={() => void clear()}>{t("conversations.clear")}</BusyButton>{" "}
      <BusyButton className="btn btn-ghost" busy={clearing} onClick={() => setConfirmClear(false)}>{t("common.cancel")}</BusyButton>
    </div>}
  </div>;
}
