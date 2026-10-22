/**
 * The Conversation Log window: recorded conversations in a configurable
 * table, newest first, with server-side search and filters, refreshed every
 * five seconds while visible. Choosing a conversation shows its evidence in
 * Log Details. Clearing the log asks first and names what it removes.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { AccessKeyRecord, ConversationSummary } from "../types";
import { BusyButton } from "../components/BusyButton";
import { RefreshIcon } from "../components/common";
import { SignalIcon, label, outcomeIcon, outcomeSignal, rowStatus } from "../components/ConversationRecord";
import { localeDateTime, localeDateTimeShort, localeNumber, t } from "../i18n";
import { useWindowVisible } from "../shell/visibility";
import { useLogSelection } from "../shell/logDetails";
import { useToolbarTools } from "../shell/tools";
import { ActionButton } from "../ui/ActionButton";
import { ConfigTable, type Column } from "../ui/table/ConfigTable";
import { useTableSettings } from "../ui/table/settings";
import type { MenuEntry } from "../ui/Menu";

/** The server's own order, newest first; any other sort covers only the loaded conversations. */
const SERVER_SORT = { column: "latest", dir: "desc" } as const;

/** A conversation's prompt, or an honest description of what is missing. */
export function conversationTitle(entry: ConversationSummary): string {
  return entry.excerpt || (entry.prompt_attachments ? t("conversations.attachmentPrompt") : entry.first_tool)
    || t(entry.coverage === "tool_exchange" ? "conversations.toolExchange" : "conversations.promptUnavailable");
}

const NO_KEYS: AccessKeyRecord[] = [];

export function ConversationsView({ accessKeys = NO_KEYS }: { accessKeys?: AccessKeyRecord[] }) {
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
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const root = useRef<HTMLDivElement | null>(null);
  const generation = useRef(0);
  const clearingRef = useRef(false);
  const logSelection = useLogSelection();
  const selectionRef = useRef(logSelection);
  selectionRef.current = logSelection;
  useEffect(() => { const timer = window.setTimeout(() => setDebouncedSearch(search), 300); return () => clearTimeout(timer); }, [search]);
  // A renamed access key shows its current name in Log Details too.
  useEffect(() => {
    const shown = selectionRef.current?.get();
    if (shown?.kind === "conversation" && JSON.stringify(shown.accessKeyNames) !== JSON.stringify(accessKeyNames)) selectionRef.current!.set({ ...shown, accessKeyNames }, false);
  }, [accessKeyNames]);
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
      const unique = [...new Map(result.entries.map(entry => [entry.id, entry])).values()];
      setEntries(unique); setTotal(result.total); setEnabled(result.enabled); setAvailable(result.available); setError("");
      // The conversation shown in Log Details follows its newer copy.
      selectionRef.current?.follow(unique);
    } catch { if (own === generation.current) setError(t("conversations.loadFailed")); }
    finally { if (own === generation.current) setLoading(false); }
  }, [limit, debouncedSearch, source, outcome, days]);
  const windowVisible = useWindowVisible();
  const windowVisibleRef = useRef(windowVisible);
  windowVisibleRef.current = windowVisible;
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => { if (!document.hidden && windowVisibleRef.current) void load(true); }, 5000);
    const invalidate = () => { generation.current++; };
    return () => { clearInterval(timer); invalidate(); };
  }, [load]);
  const resetPage = () => setLimit(25);
  const activeFilters = [source, outcome, days].filter(Boolean).length;
  const clearFilters = () => { setSource(""); setOutcome(""); setDays(""); resetPage(); };
  // In the workspace shell, Refresh, Filters and Clear Conversation History move to the toolbar.
  const onToolbar = useToolbarTools([{
    id: "conversationLog",
    label: t("conversations.title"),
    tools: [
      { id: "refresh", label: t("workspace.refreshWindow", { window: t("conversations.title") }), short: t("common.refresh"), icon: "refresh",
        shared: { id: "refresh", label: t("common.refresh") }, onSelect: () => { void load(); } },
      { id: "filters", label: activeFilters ? t("logs.filtersCount", { count: localeNumber(activeFilters) }) : t("logs.filters"), short: t("logs.filters"), icon: "filter",
        pressed: filtersOpen, badge: activeFilters || undefined, shared: { id: "filters", label: t("logs.filters"), countLabel: "logs.filtersCount" },
        onSelect: () => setFiltersOpen((open) => !open), onSet: setFiltersOpen },
      { id: "clear", label: t("conversations.clear"), short: t("conversations.clearShort"), icon: "delete", tone: "danger", disabled: clearing || !entries.length, onSelect: () => setConfirmClear(true) },
    ],
  }]);
  const showFilters = !onToolbar || filtersOpen;
  const confirmBanner = confirmClear && <div className="banner banner-warning conversation-clear-confirm" role="alert">
    <p>{t("conversations.clearConfirm")}</p>
    <BusyButton className="btn btn-danger" busy={clearing} onClick={() => void clear()}>{t("conversations.clear")}</BusyButton>{" "}
    <BusyButton className="btn btn-ghost" busy={clearing} onClick={() => setConfirmClear(false)}>{t("common.cancel")}</BusyButton>
  </div>;
  async function clear() {
    clearingRef.current = true; ++generation.current; setClearing(true);
    try {
      await api.clearConversations(); setEntries([]); setTotal(0); setConfirmClear(false); setError("");
      // Cleared evidence must not stay open in Log Details.
      if (selectionRef.current?.get()?.kind === "conversation") selectionRef.current.set(null, false);
    } catch { setError(t("conversations.clearFailed")); }
    finally { clearingRef.current = false; setClearing(false); setLoading(false); }
  }

  // A column's menu opens the filter for that column.
  const openFilter = (name: string) => {
    setFiltersOpen(true);
    requestAnimationFrame(() => root.current?.querySelector<HTMLElement>(`[data-filter="${name}"]`)?.focus());
  };
  const filterItem = (name: string) => (): MenuEntry[] => [{ id: "filter", label: t("table.filterColumn"), onSelect: () => openFilter(name) }];
  const columns = useMemo((): Column<ConversationSummary>[] => [
    { id: "prompt", label: t("conversations.prompt"), width: 300, sortValue: conversationTitle, cell: (e) => conversationTitle(e), title: conversationTitle, className: "conversation-summary-title" },
    { id: "latest", label: t("logs.latest"), width: 150, sortValue: (e) => e.latest_at ?? e.started_at, cell: (e) => localeDateTimeShort(e.latest_at ?? e.started_at), title: (e) => localeDateTime(e.latest_at ?? e.started_at), menu: filterItem("days") },
    { id: "outcome", label: t("audit.rowOutcome"), width: 170, sortValue: (e) => label("outcome", rowStatus(e)), menu: filterItem("outcome"),
      cell: (e) => { const status = rowStatus(e); const signal = outcomeSignal(status); return <span className={`phx-outcome conversation-status conversation-signal-${signal}`}><SignalIcon name={outcomeIcon(status)} signal={signal} />{label("outcome", status)}</span>; } },
    { id: "source", label: t("conversations.sourceLabel"), width: 140, sortValue: (e) => label("source", e.source), cell: (e) => label("source", e.source), menu: filterItem("source") },
    { id: "turns", label: t("logs.turns"), width: 80, sortValue: (e) => e.turns?.length ?? 1, cell: (e) => localeNumber(e.turns?.length ?? 1) },
    { id: "tools", label: t("logs.toolCalls"), width: 100, hidden: true, sortValue: (e) => e.tool_count, cell: (e) => localeNumber(e.tool_count) },
    { id: "changes", label: t("shell.tabChanges"), width: 100, hidden: true, sortValue: (e) => e.change_count, cell: (e) => localeNumber(e.change_count) },
  ], []); // eslint-disable-line react-hooks/exhaustive-deps
  const [tableSettings] = useTableSettings("conversationLog");
  const partialSort = entries.length < total && !!tableSettings.sort && !(tableSettings.sort.column === SERVER_SORT.column && tableSettings.sort.dir === SERVER_SORT.dir);

  return <div className="view-root conversations-view phx-log-view" ref={root}>
    {!enabled && <p className="banner">{t("conversations.disabledHelp")}</p>}
    {!available && <p className="banner banner-warning" role="status">{t("conversations.storageUnavailable")}</p>}
    {error && <p className="banner banner-error" role="alert">{error}</p>}
    {confirmBanner}
    <div className="phx-filters" hidden={!showFilters}>
      <div className="filter-row">
        <select className="input input-auto" data-filter="source" aria-label={t("conversations.sourceFilter")} value={source} onChange={e => { setSource(e.target.value); resetPage(); }}>
          <option value="">{t("conversations.allSources")}</option>
          {["agentcli", "voice", "ai_task", "assist", "mcp", "home_companion"].map(value => <option key={value} value={value}>{label("source", value)}</option>)}
        </select>
        <select className="input input-auto" data-filter="outcome" aria-label={t("conversations.outcomeFilter")} value={outcome} onChange={e => { setOutcome(e.target.value); resetPage(); }}>
          <option value="">{t("conversations.allOutcomes")}</option>
          {["completed", "failed", "denied", "pending_approval", "cancelled", "interrupted", "unknown", "running"].map(value => <option key={value} value={value}>{label("outcome", value)}</option>)}
        </select>
        <select className="input input-auto" data-filter="days" aria-label={t("conversations.timeFilter")} value={days} onChange={e => { setDays(e.target.value); resetPage(); }}>
          <option value="">{t("conversations.allDates")}</option><option value="1">{t("conversations.day")}</option><option value="7">{t("conversations.week")}</option><option value="30">{t("conversations.month")}</option>
        </select>
        {activeFilters > 0 && <ActionButton icon="clearFilters" label={t("logs.clearFilters")} onClick={clearFilters} />}
      </div>
    </div>
    {!showFilters && activeFilters > 0 && (
      <p className="phx-filters-active">
        {t("logs.activeFilters", { count: localeNumber(activeFilters) })}{" "}
        <ActionButton icon="clearFilters" label={t("logs.clearFilters")} onClick={clearFilters} className="btn btn-text btn-sm" />
      </p>
    )}
    {/* The search sits below the filters, as the text filters do in the Audit Log. */}
    <div className="filter-row conversation-toolbar">
      <input className="input" type="search" aria-label={t("conversations.search")} placeholder={t("conversations.search")} value={search}
        onChange={e => { setSearch(e.target.value); resetPage(); }} />
      {!onToolbar && <BusyButton className="btn btn-ghost btn-sm btn-icon" busy={loading} onClick={() => void load()} aria-label={t("common.refresh")} title={t("common.refresh")}><RefreshIcon /></BusyButton>}
    </div>
    <div className="conversation-list" aria-busy={loading}>
      <ConfigTable
        id="conversationLog"
        label={t("conversations.title")}
        columns={columns}
        rows={entries}
        rowKey={(e) => e.id}
        defaultSort={SERVER_SORT}
        selectedKey={logSelection?.selectedKey("conversation")}
        onSelect={(entry, ordered) => logSelection?.set({ kind: "conversation", entry, ordered, accessKeyNames })}
        rowLabel={(e) => `${conversationTitle(e)}, ${label("outcome", rowStatus(e))}`}
        empty={!loading && <div className="conversation-empty"><h3>{t("conversations.empty")}</h3><p>{t("conversations.emptyHelp")}</p></div>}
      />
    </div>
    <div className="phx-table-footer">
      <span className="conversation-secondary">{t("conversations.showing", { shown: localeNumber(entries.length), total: localeNumber(total) })}</span>
      {partialSort && <span className="conversation-secondary">{t("table.sortedLoaded")}</span>}
      {entries.length < total && <ActionButton icon="loadMore" label={t("common.loadMore")} busy={loading} onClick={() => setLimit(n => n + 25)} />}
      {!onToolbar && <button className="btn btn-text" onClick={() => setConfirmClear(true)}>{t("conversations.clear")}</button>}
    </div>
  </div>;
}
