/**
 * The Audit Log window: requests to Phoenix in a configurable table, newest
 * first, loaded a page at a time with server-side filters. Choosing an entry
 * shows it in Log Details. In the workspace shell, Refresh and Filters sit on
 * the toolbar; the filters open in a panel above the table, and a column's
 * menu can open its filter.
 */

import { BusyButton } from "../components/BusyButton";
import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import type { AuditEntry, AccessKeyRecord, Outcome } from "../types";
import { api } from "../api";
import { AuditOutcome, auditEntryKey, formatAccessKeyName, formatAccessKeyNameShort, outcomeLabel } from "../components/AuditEntry";
import { RefreshIcon } from "../components/common";
import { localeDateTime, localeDateTimeShort, localeNumber, t } from "../i18n";
import { friendlyResource } from "../utils";
import { auditSourceFilterValue, auditSourceLabel, isNamedAuditSource } from "../utils/audit_source";
import { useLatestRequest } from "../utils/latest_request";
import { useLogSelection } from "../shell/logDetails";
import { useToolbarTools } from "../shell/tools";
import { ConfigTable, type Column } from "../ui/table/ConfigTable";
import { useTableSettings } from "../ui/table/settings";
import type { MenuEntry } from "../ui/Menu";

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
  const root = useRef<HTMLDivElement | null>(null);

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

  const filterParams = useMemo(() => ({
    outcome: outcomeFilter || undefined,
    access_key_id: accessKeyFilter || undefined,
    since: timeWindow ? new Date(Date.now() - TIME_WINDOW_MS[timeWindow]).toISOString() : undefined,
    method: debouncedMethod || undefined,
    resource: debouncedResource || undefined,
    ip: debouncedIp ? auditSourceFilterValue(debouncedIp) : undefined,
  }), [outcomeFilter, accessKeyFilter, timeWindow, debouncedMethod, debouncedResource, debouncedIp]);

  const beginLoad = useLatestRequest();

  const load = useCallback(async (offset: number) => {
    const isLatest = beginLoad();
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
      if (isLatest()) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [beginLoad, filterParams]);

  // (Re)load from the top whenever a filter changes.
  useEffect(() => { load(0); }, [load]);

  const activeFilters = [outcomeFilter, accessKeyFilter, timeWindow, methodFilter, resourceFilter, ipFilter].filter(Boolean).length;
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
      { id: "refresh", label: t("workspace.refreshWindow", { window: t("shell.tabAudit") }), icon: "refresh", onSelect: () => { void load(0); } },
      { id: "filters", label: activeFilters ? t("logs.filtersCount", { count: localeNumber(activeFilters) }) : t("logs.filters"), icon: "filter", pressed: filtersOpen, onSelect: () => setFiltersOpen((open) => !open) },
    ],
  }]);
  const showFilters = !onToolbar || filtersOpen;

  // A column's menu opens the filter for that column.
  const openFilter = (name: string) => {
    setFiltersOpen(true);
    requestAnimationFrame(() => root.current?.querySelector<HTMLElement>(`[data-filter="${name}"]`)?.focus());
  };
  const filterItem = (name: string) => (): MenuEntry[] => [{ id: "filter", label: t("table.filterColumn"), onSelect: () => openFilter(name) }];

  const columns = useMemo((): Column<AuditEntry>[] => [
    { id: "time", label: t("audit.rowTime"), width: 170, sortValue: (e) => e.timestamp, cell: (e) => localeDateTimeShort(e.timestamp), title: (e) => localeDateTime(e.timestamp), menu: filterItem("time") },
    { id: "outcome", label: t("audit.rowOutcome"), width: 170, sortValue: (e) => outcomeLabel(e), cell: (e) => <AuditOutcome entry={e} />, menu: filterItem("outcome") },
    { id: "resource", label: t("audit.rowResource"), width: 260, sortValue: (e) => friendlyResource(e.resource), cell: (e) => friendlyResource(e.resource), title: (e) => friendlyResource(e.resource), className: "phx-cell-mono", menu: filterItem("resource") },
    { id: "accessKey", label: t("audit.rowAccessKey"), width: 170, hidden: true, sortValue: displayName, cell: (e) => formatAccessKeyNameShort(displayName(e)), title: (e) => formatAccessKeyName(displayName(e)), menu: filterItem("accessKey") },
    { id: "method", label: t("audit.rowMethod"), width: 130, hidden: true, sortValue: (e) => e.method, cell: (e) => e.method, className: "phx-cell-mono", menu: filterItem("method") },
    { id: "source", label: t("audit.rowIp"), width: 150, hidden: true, sortValue: (e) => auditSourceLabel(e.client_ip), cell: (e) => <span className={isNamedAuditSource(e.client_ip) ? "audit-source-name" : undefined}>{auditSourceLabel(e.client_ip)}</span>, title: (e) => auditSourceLabel(e.client_ip), className: "audit-cell-source", menu: filterItem("source") },
  ], [displayName]); // eslint-disable-line react-hooks/exhaustive-deps
  // Sorting by anything but the server's order covers only the entries loaded so far.
  const [tableSettings] = useTableSettings("auditLog");
  const partialSort = hasMore && !!tableSettings.sort && !(tableSettings.sort.column === SERVER_SORT.column && tableSettings.sort.dir === SERVER_SORT.dir);

  return (
    <div className="view-root" ref={root}>
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
            <button type="button" className="btn btn-ghost btn-sm" onClick={clearFilters}>{t("logs.clearFilters")}</button>
          )}
        </div>
      </div>
      {!showFilters && activeFilters > 0 && (
        <p className="phx-filters-active">
          {t("logs.activeFilters", { count: localeNumber(activeFilters) })}{" "}
          <button type="button" className="btn btn-text btn-sm" onClick={clearFilters}>{t("logs.clearFilters")}</button>
        </p>
      )}

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
          rowClass={(e) => (e.pass_through ? "pass-through-row" : "")}
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
            <BusyButton className="btn btn-ghost btn-sm" busy={loadingMore}
              onClick={() => load(entries.length)}>
              {loadingMore ? t("common.loading") : t("common.loadMore")}
            </BusyButton>
          )}
        </div>
      )}
    </div>
  );
}
