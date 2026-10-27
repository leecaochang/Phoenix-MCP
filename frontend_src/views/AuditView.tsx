/**
 * The Audit Log window: requests to Phoenix in a configurable table, newest
 * first, loaded a page at a time with server-side filters, and updated every
 * five seconds while visible unless updates are paused. Choosing an entry
 * shows it in Log Details. In the workspace shell, Refresh, Filters, Pause
 * Updates and Log Settings sit on the toolbar; the filters open in a
 * panel above the table, and a column's menu can open its filter.
 */

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import type { AuditEntry, AccessKeyRecord, Outcome } from "../types";
import { api } from "../api";
import { AuditOutcome, accessLabel, auditEntryKey, mesaLabel, formatAccessKeyName, formatAccessKeyNameShort, outcomeLabel } from "../components/AuditEntry";
import { RefreshIcon } from "../components/common";
import { localeDateTime, localeDateTimeShort, localeNumber, t } from "../i18n";
import { friendlyResource } from "../utils";
import { auditSourceFilterValue, auditSourceLabel, isNamedAuditSource } from "../utils/audit_source";
import { useLatestRequest } from "../utils/latest_request";
import { useLogSelection } from "../shell/logDetails";
import { useOnToolbar, useToolbarTools } from "../shell/tools";
import { pauseUpdatesTool, useLogSettingsTool } from "../shell/settings";
import { useWindowVisible } from "../shell/visibility";
import { ActionButton } from "../ui/ActionButton";
import { ConfigTable, type Column } from "../ui/table/ConfigTable";
import { useTableSettings } from "../ui/table/settings";

interface Props {
  accessKeys: AccessKeyRecord[];
}

type TimeWindow = "" | "5m" | "1h" | "24h" | "1w";

const TIME_WINDOW_MS: Record<Exclude<TimeWindow, "">, number> = {
  "5m": 5 * 60 * 1000,
  "1h": 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "1w": 7 * 24 * 60 * 60 * 1000,
};

const PAGE_SIZE = 100;
/** How often a visible, unpaused Audit Log checks for new entries. */
const UPDATE_MS = 5000;
/** The server's own order, newest first; any other sort covers only the loaded entries. */
const SERVER_SORT = { column: "time", dir: "desc" } as const;

export function AuditView({ accessKeys }: Props) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  // A failed read is reported as such: an empty table would claim there was no
  // activity when the evidence was only unavailable.
  const [error, setError] = useState<string | null>(null);
  const [outcomeFilter, setOutcomeFilter] = useState<Outcome | "">("");
  const [accessKeyFilter, setAccessKeyFilter] = useState("");
  const [timeWindow, setTimeWindow] = useState<TimeWindow>("");
  const [methodFilter, setMethodFilter] = useState("");
  const [resourceFilter, setResourceFilter] = useState("");
  const [ipFilter, setIpFilter] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  // Hidden filters stop applying, and come back as they were when shown again (user, 2026-10-27).
  const filtersApply = !useOnToolbar() || filtersOpen;

  // Method/resource/ip are server-side filters; debounce them so typing does
  // not fire a request per keystroke.
  const [debouncedMethod, setDebouncedMethod] = useState("");
  const [debouncedResource, setDebouncedResource] = useState("");
  const [debouncedIp, setDebouncedIp] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedMethod(methodFilter);
      setDebouncedResource(resourceFilter);
      setDebouncedIp(ipFilter);
    }, 300);
    return () => clearTimeout(timer);
  }, [methodFilter, resourceFilter, ipFilter]);

  // Current id to name map so renamed access keys show their current name on existing
  // audit rows (falls back to the row's stored name for archived/admin entries).
  const accessKeyNames = useMemo(
    () => Object.fromEntries(accessKeys.map((t) => [t.id, t.name])),
    [accessKeys],
  );
  const displayName = useCallback((e: AuditEntry): string => accessKeyNames[e.access_key_id] ?? e.access_key_name, [accessKeyNames]);

  const filterParams = useMemo(() => (filtersApply ? {
    outcome: outcomeFilter || undefined,
    access_key_id: accessKeyFilter || undefined,
    since: timeWindow ? new Date(Date.now() - TIME_WINDOW_MS[timeWindow]).toISOString() : undefined,
    method: debouncedMethod || undefined,
    resource: debouncedResource || undefined,
    ip: debouncedIp ? auditSourceFilterValue(debouncedIp) : undefined,
  } : {}), [filtersApply, outcomeFilter, accessKeyFilter, timeWindow, debouncedMethod, debouncedResource, debouncedIp]);

  const beginLoad = useLatestRequest();
  // Each load starts a generation; a background update applies only within the one it began in.
  const loadGeneration = useRef(0);
  const loadsRunning = useRef(0);

  const load = useCallback(async (offset: number) => {
    const isLatest = beginLoad();
    loadGeneration.current++;
    loadsRunning.current++;
    if (offset === 0) setLoading(true); else setLoadingMore(true);
    try {
      const resp = await api.getAudit({ ...filterParams, limit: PAGE_SIZE, offset });
      if (!isLatest()) return;
      setEntries((prev) => (offset === 0 ? resp.entries : [...prev, ...resp.entries]));
      setTotal(resp.total);
      setHasMore(offset + resp.entries.length < resp.total);
      setError(null);
    } catch {
      if (!isLatest()) return;
      // A failed first page leaves no rows for the current filters; a failed
      // later page keeps the rows already shown and Load more stays to retry.
      if (offset === 0) {
        setEntries([]);
        setTotal(0);
        setHasMore(false);
      }
      setError(t("audit.loadFailed"));
    } finally {
      loadsRunning.current--;
      if (isLatest()) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [beginLoad, filterParams]);

  // (Re)load from the top whenever a filter changes.
  useEffect(() => { load(0); }, [load]);

  // New entries arrive while the log is visible, unless updates are paused. The newest
  // page is read again; with more pages loaded, only entries not shown yet are added
  // at the top, so the pages below stay where they are.
  const [paused, setPaused] = useState(false);
  const windowVisible = useWindowVisible();
  const updateQuietly = useCallback(async () => {
    if (loadsRunning.current > 0) return;
    const generation = loadGeneration.current;
    try {
      const resp = await api.getAudit({ ...filterParams, limit: PAGE_SIZE, offset: 0 });
      if (generation !== loadGeneration.current || loadsRunning.current > 0) return;
      setEntries((prev) => {
        if (prev.length <= PAGE_SIZE) return resp.entries;
        const shown = new Set(prev.map(auditEntryKey));
        return [...resp.entries.filter((e) => !shown.has(auditEntryKey(e))), ...prev];
      });
      setTotal(resp.total);
      setHasMore((more) => more || resp.entries.length < resp.total);
      setError(null);
    } catch {
      // A failed background update keeps what is shown; the next one or Refresh retries.
    }
  }, [filterParams]);
  useEffect(() => {
    if (paused || !windowVisible) return;
    const timer = window.setInterval(() => { if (!document.hidden) void updateQuietly(); }, UPDATE_MS);
    return () => clearInterval(timer);
  }, [paused, windowVisible, updateQuietly]);
  const logSettings = useLogSettingsTool();

  const activeFilters = filtersApply ? [outcomeFilter, accessKeyFilter, timeWindow, methodFilter, resourceFilter, ipFilter].filter(Boolean).length : 0;
  const clearFilters = () => {
    setOutcomeFilter(""); setAccessKeyFilter(""); setTimeWindow("");
    setMethodFilter(""); setResourceFilter(""); setIpFilter("");
  };

  // In the workspace shell, a chosen entry opens in Log Details; Refresh and Filters move to the toolbar.
  const logSelection = useLogSelection();
  // A renamed access key shows its current name in Log Details too.
  useEffect(() => {
    const shown = logSelection?.get();
    if (shown?.kind === "audit" && shown.nameOf !== displayName) logSelection!.set({ ...shown, nameOf: displayName }, false);
  }, [displayName]); // eslint-disable-line react-hooks/exhaustive-deps
  const onToolbar = useToolbarTools([{
    id: "auditLog",
    label: t("shell.tabAudit"),
    tools: [
      // The log updates itself; Refresh is offered only while updates are paused.
      { id: "filters", label: activeFilters ? t("logs.filtersCount", { count: localeNumber(activeFilters) }) : t("logs.filters"), short: t("logs.filters"), icon: "filter",
        pressed: filtersOpen, badge: activeFilters || undefined, shared: { id: "filters", label: t("logs.filters"), countLabel: "logs.filtersCount" },
        onSelect: () => setFiltersOpen((open) => !open), onSet: setFiltersOpen },
      pauseUpdatesTool(paused, setPaused),
      // Refresh sits to the right of Pause Updates, and only while paused.
      ...(paused ? [{ id: "refresh", label: t("workspace.refreshWindow", { window: t("shell.tabAudit") }), short: t("common.refresh"), icon: "refresh" as const,
        shared: { id: "refresh", label: t("common.refresh") }, onSelect: () => { void load(0); } }] : []),
      ...logSettings,
    ],
  }]);
  const showFilters = !onToolbar || filtersOpen;


  const columns = useMemo((): Column<AuditEntry>[] => [
    { id: "time", label: t("audit.rowTime"), width: 170, sortValue: (e) => e.timestamp, cell: (e) => localeDateTimeShort(e.timestamp), title: (e) => localeDateTime(e.timestamp) },
    { id: "outcome", label: t("audit.rowOutcome"), width: 140, sortValue: (e) => outcomeLabel(e), cell: (e) => <AuditOutcome entry={e} /> },
    { id: "access", label: t("audit.rowAccess"), width: 120, sortValue: accessLabel, cell: accessLabel },
    { id: "resource", label: t("audit.rowResource"), width: 260, maxWidth: 360, sortValue: (e) => friendlyResource(e.resource), cell: (e) => friendlyResource(e.resource), title: (e) => friendlyResource(e.resource), className: "phx-cell-mono" },
    { id: "mesa", label: t("audit.rowMesa"), width: 100, sortValue: mesaLabel, cell: mesaLabel, title: (e) => (e.mesa_advisory ? t("audit.mesaAdvisory") : undefined) },
    { id: "accessKey", label: t("audit.rowAccessKey"), width: 170, maxWidth: 240, sortValue: displayName, cell: (e) => formatAccessKeyNameShort(displayName(e)), title: (e) => formatAccessKeyName(displayName(e)) },
    { id: "method", label: t("audit.rowMethod"), width: 130, maxWidth: 200, sortValue: (e) => e.method, cell: (e) => e.method, className: "phx-cell-mono" },
    { id: "source", label: t("audit.rowIp"), width: 150, maxWidth: 220, sortValue: (e) => auditSourceLabel(e.client_ip), cell: (e) => <span className={isNamedAuditSource(e.client_ip) ? "audit-source-name" : undefined}>{auditSourceLabel(e.client_ip)}</span>, title: (e) => auditSourceLabel(e.client_ip), className: "audit-cell-source" },
  ], [displayName]); // eslint-disable-line react-hooks/exhaustive-deps
  // Sorting by anything but the server's order covers only the entries loaded so far.
  const [tableSettings] = useTableSettings("auditLog");
  const partialSort = hasMore && !!tableSettings.sort && !(tableSettings.sort.column === SERVER_SORT.column && tableSettings.sort.dir === SERVER_SORT.dir);

  return (
    <div className="view-root phx-log-view">
      <div className="phx-filters" hidden={!showFilters}>
        <div className="filter-row">
          <select
            aria-label={t("audit.filterOutcomeAria")}
            data-filter="outcome"
            className="input input-auto"
            value={outcomeFilter}
            onChange={(e) => setOutcomeFilter(e.target.value as Outcome | "")}
          >
            <option value="">{t("audit.allOutcomes")}</option>
            <option value="allowed">{t("audit.outcomeAllowed")}</option>
            <option value="denied">{t("audit.outcomeDenied")}</option>
            <option value="not_found">{t("audit.outcomeNotFound")}</option>
            <option value="rate_limited">{t("audit.outcomeRateLimited")}</option>
            <option value="not_implemented">{t("audit.outcomeNotImplemented")}</option>
            <option value="invalid_request">{t("audit.outcomeInvalidRequest")}</option>
            <option value="pending_approval">{t("audit.outcomePendingApproval")}</option>
          </select>
          <select
            aria-label={t("audit.filterAccessKeyAria")}
            data-filter="accessKey"
            className="input input-auto"
            value={accessKeyFilter}
            onChange={(e) => setAccessKeyFilter(e.target.value)}
          >
            <option value="">{t("audit.allAccessKeys")}</option>
            {accessKeys.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
          <select
            aria-label={t("audit.filterTimeAria")}
            data-filter="time"
            className="input input-auto"
            value={timeWindow}
            onChange={(e) => setTimeWindow(e.target.value as TimeWindow)}
          >
            <option value="">{t("audit.allTime")}</option>
            <option value="1w">{t("audit.pastWeek")}</option>
            <option value="24h">{t("audit.past24h")}</option>
            <option value="1h">{t("audit.pastHour")}</option>
            <option value="5m">{t("audit.past5m")}</option>
          </select>
          <input
            className="input"
            aria-label={t("audit.filterMethodAria")}
            data-filter="method"
            placeholder={t("audit.methodPlaceholder")}
            value={methodFilter}
            onChange={(e) => setMethodFilter(e.target.value)}
          />
          <input
            className="input"
            aria-label={t("audit.filterResourceAria")}
            data-filter="resource"
            placeholder={t("audit.resourcePlaceholder")}
            value={resourceFilter}
            onChange={(e) => setResourceFilter(e.target.value)}
          />
          <input
            className="input"
            aria-label={t("audit.filterIpAria")}
            data-filter="source"
            placeholder={t("audit.ipPlaceholder")}
            value={ipFilter}
            onChange={(e) => setIpFilter(e.target.value)}
          />
          {!onToolbar && (
            <button
              className="btn btn-ghost btn-sm btn-icon"
              onClick={() => load(0)}
              title={t("common.refresh")}
              aria-label={t("audit.refreshAria")}
            >
              <RefreshIcon />
            </button>
          )}
          {activeFilters > 0 && (
            <ActionButton icon="clearFilters" label={t("logs.clearFilters")} onClick={clearFilters} />
          )}
        </div>
      </div>

      {error && <div className="banner banner-error mb-8" role="alert">{error}</div>}
      {loading ? (
        <div className="loading-wrap"><div className="spinner" /><span>{t("common.loading")}</span></div>
      ) : !(error && entries.length === 0) && (
        <ConfigTable
          id="auditLog"
          label={t("shell.tabAudit")}
          columns={columns}
          rows={entries}
          rowKey={auditEntryKey}
          defaultSort={SERVER_SORT}
          selectedKey={logSelection?.selectedKey("audit")}
          onSelect={(entry, ordered) => logSelection?.set({ kind: "audit", entry, ordered, nameOf: displayName })}
          rowLabel={(e) => t("audit.openEntryAria", { outcome: outcomeLabel(e), name: displayName(e) })}
          empty={<p className="audit-empty">{t("audit.empty")}</p>}
        />
      )}
      {!loading && entries.length > 0 && (
        <div className="phx-table-footer">
          <span className="conversation-secondary">{t("conversations.showing", { shown: localeNumber(entries.length), total: localeNumber(total) })}</span>
          {partialSort && <span className="conversation-secondary">{t("table.sortedLoaded")}</span>}
          {/* Not while a first page is loading: a later page fetched for the new
              filters would append to rows from the old ones. */}
          {hasMore && (
            <ActionButton icon="loadMore" label={loadingMore ? t("common.loading") : t("common.loadMore")} busy={loadingMore}
              onClick={() => { void load(entries.length); }} />
          )}
        </div>
      )}
    </div>
  );
}
