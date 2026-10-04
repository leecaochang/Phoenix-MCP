import { useState, useEffect, useCallback } from "react";
import type { AccessKeyRecord, ArchivedAccessKeyRecord } from "../types";
import { AccessKeyCreateModal } from "../components/AccessKeyCreateModal";
import { ArchivedAccessKeyTable } from "../components/ArchivedAccessKeyTable";
import { api } from "../api";
import { Loading, ErrorMsg, RefreshIcon } from "../index";
import { formatDate, accessKeyStatus, accessKeyStatusLabel } from "../utils";
import { compareStrings, localeDateTime, t } from "../i18n";

const MAX_ACTIVE_ACCESS_KEYS_WARNING = 50;

function relativeTime(iso: string | null): string {
  if (!iso) return t("common.never");
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return t("accessKeys.justNow");
  const m = Math.floor(s / 60);
  if (m < 60) return t("accessKeys.minutesAgo", { m });
  const h = Math.floor(m / 60);
  if (h < 24) return t("accessKeys.hoursAgo", { h });
  return t("accessKeys.daysAgo", { d: Math.floor(h / 24) });
}


type SortKey = "name" | "mode" | "status" | "created" | "updated" | "expires" | "last_used";
type SortDir = "asc" | "desc";

function SortArrow({ col, sortKey, sortDir }: { col: SortKey; sortKey: SortKey; sortDir: SortDir }) {
  const active = col === sortKey;
  return <span className={`sort-arrow${active ? " active" : ""}`}>{active ? (sortDir === "asc" ? "↑" : "↓") : "↕"}</span>;
}

interface Props {
  accessKeys: AccessKeyRecord[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onOpenDetail: (id: string) => void;
  onLaunchWizard: () => void;
  showCreate: boolean;
  onOpenCreate: () => void;
  onCloseCreate: () => void;
  onOpenSettings: () => void;
}

export function AccessKeyListView({ accessKeys, loading, error, onRefresh, onOpenDetail, onLaunchWizard, showCreate, onOpenCreate, onCloseCreate, onOpenSettings }: Props) {
  const [filter, setFilter] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [archived, setArchived] = useState<ArchivedAccessKeyRecord[] | null>(null);
  const [archivedLoading, setArchivedLoading] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const refreshArchived = useCallback(async () => {
    setArchivedLoading(true);
    try {
      setArchived(await api.listArchivedAccessKeys());
    } catch {
      setArchived([]);
    } finally {
      setArchivedLoading(false);
    }
  }, []);

  useEffect(() => {
    if (showArchived) refreshArchived();
  }, [showArchived, refreshArchived]);

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  }

  function ariaSort(key: SortKey): "ascending" | "descending" | "none" {
    if (sortKey !== key) return "none";
    return sortDir === "asc" ? "ascending" : "descending";
  }

  const filtered = accessKeys.filter((t) => {
    const q = filter.toLowerCase();
    if (!q) return true;
    const status = accessKeyStatus(t);
    return [t.name, status, accessKeyStatusLabel(status)]
      .some((value) => value.toLowerCase().includes(q));
  });

  const sorted = [...filtered].sort((a, b) => {
    let va: string | number = "";
    let vb: string | number = "";
    switch (sortKey) {
      case "name":     va = a.name.toLowerCase();   vb = b.name.toLowerCase(); break;
      case "mode":     va = a.pass_through ? "1" : "0"; vb = b.pass_through ? "1" : "0"; break;
      case "status":   va = accessKeyStatusLabel(accessKeyStatus(a)); vb = accessKeyStatusLabel(accessKeyStatus(b)); break;
      case "created":  va = a.created_at ?? "";      vb = b.created_at ?? ""; break;
      case "updated":  va = a.updated_at ?? "";      vb = b.updated_at ?? ""; break;
      case "expires":  va = a.expires_at ?? "9999";  vb = b.expires_at ?? "9999"; break;
      case "last_used": va = a.last_used_at ?? "";   vb = b.last_used_at ?? ""; break;
    }
    const comparison = sortKey === "status"
      ? compareStrings(String(va), String(vb))
      : va < vb ? -1 : va > vb ? 1 : 0;
    return sortDir === "asc" ? comparison : -comparison;
  });

  function handleCreated(record: AccessKeyRecord) {
    onRefresh();
    onCloseCreate();
    onOpenDetail(record.id);
  }

  function handleArchivedDeleted(id: string) {
    setArchived((prev) => prev?.filter((t) => t.id !== id) ?? null);
  }

  function th(label: string, key: SortKey, className?: string) {
    return (
      <th
        className={`sortable${sortKey === key ? " sort-active" : ""}${className ? ` ${className}` : ""}`}
        aria-sort={ariaSort(key)}
      >
        <button type="button" className="table-sort-btn" onClick={() => handleSort(key)}>
          {label}<SortArrow col={key} sortKey={sortKey} sortDir={sortDir} />
        </button>
      </th>
    );
  }

  // First run: no access keys and no filter. Show an onboarding hero that launches
  // the wizard instead of the empty table. The create modal is still mounted
  // here so the header "Create access key" button works in the empty state.
  if (!loading && !error && accessKeys.length === 0 && !filter && !showArchived) {
    return (
      <div className="view-root">
        <div className="card wizard-hero">
          <div className="wizard-hero-title">{t("accessKeys.heroTitle")}</div>
          <p className="wizard-hero-sub">
            {t("accessKeys.heroSub")}
          </p>
          <div className="wizard-hero-actions">
            <button className="btn btn-primary" onClick={onLaunchWizard}>{t("accessKeys.setUpFirstAgent")}</button>
          </div>
        </div>
        {showCreate && (
          <AccessKeyCreateModal
            existingNames={accessKeys.map((t) => t.name)}
            onCreated={handleCreated}
            onClose={onCloseCreate}
            onOpenSettings={onOpenSettings}
          />
        )}
      </div>
    );
  }

  return (
    <div className="view-root">
      {accessKeys.length >= MAX_ACTIVE_ACCESS_KEYS_WARNING && (
        <div className="banner banner-warn">
          {t("accessKeys.maxWarning", { max: MAX_ACTIVE_ACCESS_KEYS_WARNING })}
        </div>
      )}

      <div className="card">
        <div className="filter-row">
          <input
            className="input"
            aria-label={t("accessKeys.filterAria")}
            placeholder={t("accessKeys.filterPlaceholder")}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <div className="filter-row-right">
            <button
              className="btn btn-ghost btn-sm"
              onClick={onLaunchWizard}
            >
              {t("accessKeys.setUpAgent")}
            </button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setShowArchived((v) => !v)}
            >
              {showArchived ? t("accessKeys.hideArchived") : t("accessKeys.showArchived")}
            </button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={onOpenCreate}
            >
              {t("accessKeys.createAccessKey")}
            </button>
          </div>
          {/* Own flex item (not nested in filter-row-right) so mobile CSS can
              pull it up next to the filter input via order, without changing
              desktop's DOM/tab order (same trick as the tab bar's row split). */}
          <button
            className="btn btn-ghost btn-sm btn-icon filter-row-refresh"
            onClick={onRefresh}
            title={t("common.refresh")}
            aria-label={t("accessKeys.refreshAria")}
          >
            <RefreshIcon />
          </button>
        </div>

        {loading ? (
          <Loading />
        ) : error ? (
          <ErrorMsg msg={error} />
        ) : (
          <table className="data-table access-key-table">
            <thead>
              <tr>
                {th(t("accessKeys.colName"), "name")}
                {th(t("accessKeys.colMode"), "mode")}
                {th(t("accessKeys.colStatus"), "status")}
                {th(t("accessKeys.colCreated"), "created")}
                {th(t("accessKeys.colUpdated"), "updated")}
                {th(t("accessKeys.colExpires"), "expires")}
                {th(t("accessKeys.colLastUsed"), "last_used")}
              </tr>
            </thead>
            <tbody>
              {sorted.length === 0 && (
                <tr>
                  <td colSpan={7} className="access-key-table-empty">
                    {filter ? t("accessKeys.emptyFiltered") : t("accessKeys.empty")}
                  </td>
                </tr>
              )}
              {sorted.map((tok) => {
                const status = accessKeyStatus(tok);
                const statusClass = status === "Active" ? "badge-green" : status === "Expired" ? "badge-grey" : "badge-red";
                return (
                  <tr
                    key={tok.id}
                    className={`clickable${tok.pass_through ? " pass-through-row" : ""}`}
                    onClick={() => onOpenDetail(tok.id)}
                  >
                    <td className="access-key-name">
                      {/* The row's own onClick (above) makes the whole row
                          clickable for the mouse; this button keeps it
                          keyboard- and screen-reader-accessible (tab to it,
                          Enter/Space to open the access key). stopPropagation so
                          activating it does not also re-fire the row's
                          handler. */}
                      <button
                        type="button"
                        className="row-open"
                        onClick={(e) => { e.stopPropagation(); onOpenDetail(tok.id); }}
                        aria-label={t("accessKeys.editAria", { name: tok.name })}
                      >
                        {tok.name}
                      </button>
                    </td>
                    <td>
                      {tok.pass_through
                        ? <span className="badge badge-amber">{t("accessKeys.passThrough")}</span>
                        : <span className="badge badge-blue">{t("accessKeys.scoped")}</span>}
                    </td>
                    <td><span className={`badge ${statusClass}`}>{accessKeyStatusLabel(status)}</span></td>
                    <td title={tok.created_at ? localeDateTime(tok.created_at) : undefined}>{formatDate(tok.created_at)}</td>
                    <td title={tok.updated_at ? localeDateTime(tok.updated_at) : undefined}>{relativeTime(tok.updated_at)}</td>
                    <td>{formatDate(tok.expires_at)}</td>
                    <td>{relativeTime(tok.last_used_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {showArchived && (
        <div className="card">
          <div className="card-header">
            <h3 className="card-header-title">{t("accessKeys.archivedTitle")}</h3>
          </div>
          {archivedLoading ? (
            <Loading />
          ) : (
            <ArchivedAccessKeyTable
              accessKeys={archived ?? []}
              onDeleted={handleArchivedDeleted}
            />
          )}
        </div>
      )}

      {showCreate && (
        <AccessKeyCreateModal
          existingNames={accessKeys.map((t) => t.name)}
          onCreated={handleCreated}
          onClose={onCloseCreate}
          onOpenSettings={onOpenSettings}
        />
      )}
    </div>
  );
}
