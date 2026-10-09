import { BusyButton } from "../components/BusyButton";
/** Configuration version history view. */
import { useCallback, useEffect, useRef, useState, useMemo } from "react";
import type { VersionRecord, VersionSummary } from "../types";
import { api } from "../api";
import { toYaml } from "../components/YamlView";
import { CodeToggle, diffLines, RawDiffPane, LayoutToggle, rememberCodeView, rememberDiffLayout, storedCodeView, storedDiffLayout } from "../components/DiffView";
import { YamlView } from "../components/YamlView";
import { collectPreviewViews, DashboardPreview, rememberPreviewMode, SegmentedToggle, storedPreviewMode, useHuiCardReady } from "../components/DashboardPreview";
import { Loading, ErrorMsg, RefreshIcon } from "../index";
import { formatDateTime } from "../utils";
import { useLatestRequest } from "../utils/latest_request";
import { matchesListSearch } from "../utils/list_search";
import { compareStrings, hasMessage, t } from "../i18n";
import { tRich } from "../i18n/rich";

// Resource types whose before/after payload is a raw text blob ({content, ...})
// rather than a structured config; these render as a line diff, not YAML.
// esphome_yaml must be listed: its payload has the same {content, bytes} shape,
// and the structured path would dump it as YAML, burying the whole file inside a
// "content: |-" block, hiding the too-large marker and offering Restore on a
// snapshot the backend would refuse.
const RAW_CONTENT_TYPES = new Set(["yaml_config", "file", "esphome_yaml", "blueprint"]);

/** Extract the raw-text snapshot from a version side, or null. content is null
 * when the snapshot was too large to store (a non-restorable marker). */
function rawSide(value: Record<string, unknown> | null): { content: string | null; bytes?: number } | null {
  if (value == null) return null;
  return {
    content: typeof value.content === "string" ? (value.content as string) : null,
    bytes: typeof value.bytes === "number" ? (value.bytes as number) : undefined,
  };
}

const ACTION_BADGE: Record<string, string> = {
  create: "badge-green",
  edit: "badge-blue",
  delete: "badge-red",
  rollback: "badge-amber",
};

// The backend records resource types and actions as slugs. An unknown slug falls
// through to the raw value rather than a blank label or pill.
export const RESOURCE_TYPE_LABEL_KEYS: Record<string, string> = {
  automation: "changes.typeAutomation",
  script: "changes.typeScript",
  scene: "changes.typeScene",
  helper: "changes.typeHelper",
  dashboard: "changes.typeDashboard",
  blueprint: "changes.typeBlueprint",
  entity: "changes.typeEntity",
  device: "changes.typeDevice",
  yaml_config: "changes.typeYamlConfig",
  esphome_yaml: "changes.typeEsphomeYaml",
  energy: "changes.typeEnergy",
  file: "changes.typeFile",
  config_entry: "changes.typeConfigEntry",
  area: "changes.typeArea",
  floor: "changes.typeFloor",
};

function resourceTypeLabel(rt: string): string {
  const key = RESOURCE_TYPE_LABEL_KEYS[rt];
  return key ? t(key) : rt;
}

const ACTION_LABEL_KEYS: Record<string, string> = {
  create: "changes.actionCreate",
  edit: "changes.actionEdit",
  delete: "changes.actionDelete",
  rollback: "changes.actionRollback",
};

function ActionBadge({ action }: { action: string }) {
  const key = ACTION_LABEL_KEYS[action];
  return (
    <span className={`badge ${ACTION_BADGE[action] ?? "badge-grey"}`}>
      {key ? t(key) : action}
    </span>
  );
}

function label(v: { alias: string | null; resource_id: string }): string {
  return v.alias || v.resource_id;
}

// Author display name. Prefer the access key's CURRENT name (it may have been renamed
// since the change was recorded), falling back to the name captured at the time,
// then to "admin" for admin-driven restores.
function whoNow(
  v: { access_key_id?: string | null; access_key_name: string | null; approved_by_user_id: string | null },
  current: Map<string, string>,
): string {
  const cur = v.access_key_id ? current.get(v.access_key_id) : undefined;
  return cur || v.access_key_name || (v.approved_by_user_id ? "admin" : "-");
}

// Detail view: when the access key has since been renamed, show "current (original)".
function whoDetail(
  v: { access_key_id: string | null; access_key_name: string | null; approved_by_user_id: string | null },
  current: Map<string, string>,
): string {
  const cur = v.access_key_id ? current.get(v.access_key_id) : undefined;
  if (cur && v.access_key_name && cur !== v.access_key_name) return `${cur} (${v.access_key_name})`;
  return cur || v.access_key_name || (v.approved_by_user_id ? "admin" : "-");
}

const FEED_POLL_MS = 5_000;

// hass.connection.subscribeEvents, typed loosely (the panel receives an untyped
// hass). Returns an unsubscribe function, or null if unavailable.
type Unsub = () => void;
async function subscribeConfigChanged(hass: unknown, cb: () => void): Promise<Unsub | null> {
  const conn = (hass as { connection?: { subscribeEvents?: (cb: () => void, ev: string) => Promise<Unsub> } } | null)?.connection;
  if (!conn?.subscribeEvents) return null;
  try {
    return await conn.subscribeEvents(cb, "phoenix_mcp_config_changed");
  } catch {
    return null;
  }
}

const CHANGES_PAGE = 100;
type ChangeSort = "action" | "resource_type" | "name" | "author" | "timestamp";

/** A version record's one-line change summary, localized when the backend
 *  supplied a catalog key and this bundle knows it. Same fallback ladder as the
 *  approval diff: stored English otherwise, so an older record still reads. */
function versionSummary(v: { summary?: string | null; summary_key?: string | null; summary_params?: Record<string, string | number> | null }): string {
  if (v.summary_key && hasMessage(v.summary_key)) {
    return t(v.summary_key, v.summary_params ?? undefined);
  }
  return v.summary ?? "";
}

export function ChangesView({ hass }: { hass: unknown }) {
  const [feed, setFeed] = useState<VersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [accessKeyNames, setAccessKeyNames] = useState<Map<string, string>>(new Map());
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [search, setSearch] = useState("");
  const [resourceType, setResourceType] = useState("");
  const [action, setAction] = useState("");
  const [sortKey, setSortKey] = useState<ChangeSort>("timestamp");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const selectedRef = useRef<string | null>(null);
  selectedRef.current = selected;

  // access_key_id -> latest known name, so a renamed access key shows its new name here.
  // Covers archived access keys too, so a renamed-then-revoked access key resolves to its
  // final name rather than the one captured with the change.
  const loadAccessKeys = useCallback(async () => {
    try {
      const [active, archived] = await Promise.all([
        api.listAccessKeys(),
        api.listArchivedAccessKeys().catch(() => []),
      ]);
      const map = new Map<string, string>();
      for (const t of archived) map.set(t.id, t.name);
      for (const t of active) map.set(t.id, t.name);
      setAccessKeyNames(map);
    } catch {
      // Names fall back to the value captured with each change.
    }
  }, []);

  useEffect(() => { loadAccessKeys(); }, [loadAccessKeys]);

  // offset === 0 always replaces the feed (a fresh top page: initial load, manual
  // refresh, or a background poll or event), because the feed is a live list and
  // a poll tick should show what is live now, not a stale "loaded more" window.
  // A manual "Load more" click passes the next offset and appends.
  // The feed is loaded from four places with no ordering between them (mount,
  // manual refresh, config-changed event, poll tick) plus Load more, all writing
  // the same state. A superseded response is dropped rather than allowed to put
  // an older page back, or to append a "Load more" window whose offset was
  // computed against a feed that has since been replaced.
  const beginLoad = useLatestRequest();

  const loadFeed = useCallback(async (offset = 0, background = false) => {
    const isLatest = beginLoad();
    if (!background && offset === 0) setLoading(true);
    if (offset > 0) setLoadingMore(true);
    setError(null);
    try {
      const resp = await api.listVersions({ limit: CHANGES_PAGE, offset });
      if (!isLatest()) return;
      setFeed((prev) => (offset === 0 ? resp.versions : [...prev, ...resp.versions]));
      setHasMore(offset + resp.versions.length < resp.total);
    } catch (e: unknown) {
      if (!background && isLatest()) setError(e instanceof Error ? e.message : t("changes.loadFailed"));
    } finally {
      if (isLatest()) {
        // A background top-page refresh can supersede a foreground one (most
        // visibly when the config-changed event lands as a rollback returns to
        // this list). Its response satisfies the same full-feed request, so it
        // must also release the foreground loading state it inherited.
        if (offset === 0) setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [beginLoad]);

  useEffect(() => { loadFeed(0); }, [loadFeed]);

  // Refresh instantly when Phoenix MCP fires phoenix_mcp_config_changed (an agent or a restore
  // recorded a version), so the feed is live without waiting for the poll.
  useEffect(() => {
    let unsub: Unsub | null = null;
    let cancelled = false;
    subscribeConfigChanged(hass, () => { if (!selectedRef.current) loadFeed(0, true); })
      .then((u) => { if (cancelled) u?.(); else unsub = u; });
    return () => { cancelled = true; unsub?.(); };
  }, [hass, loadFeed]);

  // Poll as a fallback while the feed is the active view (covers a dropped event
  // or a reconnect). Paused while viewing a detail.
  useEffect(() => {
    if (selected) return;
    const id = setInterval(() => loadFeed(0, true), FEED_POLL_MS);
    return () => clearInterval(id);
  }, [selected, loadFeed]);

  const shown = useMemo(() => {
    const value = (v: VersionSummary, key: ChangeSort): string => {
      if (key === "name") return label(v);
      if (key === "author") return whoNow(v, accessKeyNames);
      if (key === "resource_type") return resourceTypeLabel(v.resource_type);
      if (key === "action") return t(ACTION_LABEL_KEYS[v.action]);
      return v.timestamp;
    };
    return feed.filter(v => (!resourceType || v.resource_type === resourceType)
      && (!action || v.action === action)
      && matchesListSearch(`${label(v)} ${v.resource_id} ${v.resource_type} ${resourceTypeLabel(v.resource_type)} ${v.action} ${t(ACTION_LABEL_KEYS[v.action])} ${whoNow(v, accessKeyNames)} ${versionSummary(v)}`, search))
      .sort((a, b) => compareStrings(value(a, sortKey), value(b, sortKey)) * (sortDir === "asc" ? 1 : -1));
  }, [feed, search, resourceType, action, accessKeyNames, sortKey, sortDir]);

  function sortHeader(key: ChangeSort, text: string, className = "") {
    const active = key === sortKey;
    return <th className={`sortable ${className}${active ? " sort-active" : ""}`}
      aria-sort={active ? (sortDir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className="table-sort-btn" onClick={() => {
        if (active) setSortDir(dir => dir === "asc" ? "desc" : "asc");
        else { setSortKey(key); setSortDir("asc"); }
      }}>{text}<svg className={`sort-arrow${active ? " active" : ""}`} width="8" height="12" viewBox="0 0 8 12" fill="currentColor" aria-hidden="true">
        {(!active || sortDir === "asc") && <path d="M4 0 8 4H0Z" />}
        {(!active || sortDir === "desc") && <path d="m4 12 4-4H0Z" />}
      </svg></button>
    </th>;
  }

  if (selected) {
    return (
      <ChangeDetail
        versionId={selected}
        accessKeyNames={accessKeyNames}
        onSelectVersion={setSelected}
        onBack={() => { setSelected(null); loadFeed(0); }}
        onRestored={async () => {
          // Keep the detail mounted (and its Restoring state visible) until one
          // authoritative reload has the rollback row, so onRestored and onBack
          // do not launch competing background and foreground requests.
          await loadFeed(0);
          setSelected(null);
        }}
      />
    );
  }

  return (
    <div className="view-root changes-view">
      <div className="card">
      <div className="filter-row changes-toolbar">
        <input type="search" className="input" value={search} onChange={e => setSearch(e.target.value)}
          placeholder={t("changes.search")} aria-label={t("changes.search")} />
        <select className="input input-auto" value={resourceType} onChange={e => setResourceType(e.target.value)} aria-label={t("changes.colType")}>
          <option value="">{t("changes.allTypes")}</option>
          {Object.keys(RESOURCE_TYPE_LABEL_KEYS).map(type => <option key={type} value={type}>{resourceTypeLabel(type)}</option>)}
        </select>
        <select className="input input-auto" value={action} onChange={e => setAction(e.target.value)} aria-label={t("changes.colAction")}>
          <option value="">{t("changes.allActions")}</option>
          {Object.keys(ACTION_LABEL_KEYS).map(value => <option key={value} value={value}>{t(ACTION_LABEL_KEYS[value])}</option>)}
        </select>
        <BusyButton className="btn btn-ghost btn-sm btn-icon" busy={loading || loadingMore} onClick={() => { loadFeed(0); loadAccessKeys(); }} title={t("common.refresh")} aria-label={t("changes.refreshAria")}>
          <RefreshIcon />
        </BusyButton>
      </div>
      {error && <ErrorMsg msg={error} />}
      {loading ? <Loading /> : shown.length === 0 ? (
        <p className="audit-empty">
          {feed.length ? t("changes.emptySearch") : t("changes.empty")}
        </p>
      ) : (
        <table className="data-table changes-table" aria-label={t("changes.title")}>
          <thead><tr>
            {sortHeader("action", t("changes.colAction"))}
            {sortHeader("resource_type", t("changes.colType"), "changes-col-type")}
            {sortHeader("name", t("changes.colName"))}
            {sortHeader("author", t("changes.colBy"), "changes-col-who")}
            {sortHeader("timestamp", t("changes.colWhen"), "changes-col-when")}
          </tr></thead>
          <tbody>{shown.map((v) => (
            <tr key={v.id} className="clickable" onClick={() => setSelected(v.id)}>
              <td><ActionBadge action={v.action} /></td>
              <td className="changes-col-type">{resourceTypeLabel(v.resource_type)}</td>
              <td className="changes-name">
                <button type="button" className="row-link-btn row-link-btn-stack" onClick={e => { e.stopPropagation(); setSelected(v.id); }}>
                  <span>{label(v)}</span>
                  {versionSummary(v) ? <span className="changes-summary">{versionSummary(v)}</span> : null}
                </button>
              </td>
              <td className="changes-col-who">{whoNow(v, accessKeyNames)}</td>
              <td className="changes-col-when">{formatDateTime(v.timestamp)}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
      {hasMore && (
        <div className="approval-history-more">
          <BusyButton className="btn btn-ghost btn-sm" busy={loadingMore} onClick={() => loadFeed(feed.length)}>
            {loadingMore ? t("common.loading") : t("common.loadMore")}
          </BusyButton>
        </div>
      )}
      </div>
    </div>
  );
}

function ChangeDetail(
  { versionId, accessKeyNames, onSelectVersion, onBack, onRestored }: {
    versionId: string;
    accessKeyNames: Map<string, string>;
    onSelectVersion: (id: string) => void;
    onBack: () => void;
    onRestored: () => Promise<void>;
  },
) {
  const [record, setRecord] = useState<VersionRecord | null>(null);
  const [history, setHistory] = useState<VersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Which snapshot the admin is confirming a restore of: the prior config
  // ("before") or the config this version produced ("after"). null = no prompt.
  const [confirmSide, setConfirmSide] = useState<"before" | "after" | null>(null);
  const [busy, setBusy] = useState(false);
  // A stored preference wins; otherwise default to stacked on narrow
  // viewports (matches the 800px CSS breakpoint that forces a single column
  // there) and side-by-side on wider screens.
  const [stacked, setStacked] = useState(
    () => storedDiffLayout(typeof window !== "undefined" && window.matchMedia("(max-width: 800px)").matches),
  );
  const toggleStacked = () => {
    const next = !stacked;
    rememberDiffLayout(next);
    setStacked(next);
  };
  // The code view is the same preference the approval diff uses, so choosing it
  // once applies on both surfaces.
  const [code, setCode] = useState(() => storedCodeView());
  const toggleCode = () => {
    const next = !code;
    rememberCodeView(next);
    setCode(next);
  };
  // Dashboard versions offer a live layout preview next to the text diff.
  // Diff vs Preview is a remembered preference shared with the approval card.
  const huiReady = useHuiCardReady();
  const [mode, setModeState] = useState<"diff" | "preview">(() => storedPreviewMode());
  const setMode = (m: "diff" | "preview") => {
    rememberPreviewMode(m);
    setModeState(m);
  };
  const [side, setSide] = useState<"before" | "after">("after");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setConfirmSide(null);
    setError(null);
    setModeState(storedPreviewMode());
    setSide("after");
    api.getVersion(versionId)
      .then((r) => {
        if (!active) return;
        setRecord(r);
        return api.listVersions({ resource_type: r.resource_type, resource_id: r.resource_id })
          .then((resp) => { if (active) setHistory(resp.versions); })
          .catch(() => { if (active) setHistory([]); });
      })
      .catch((e: unknown) => { if (active) setError(e instanceof Error ? e.message : t("changes.loadVersionFailed")); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [versionId]);

  async function restore(side: "before" | "after") {
    setBusy(true);
    setError(null);
    try {
      await api.restoreVersion(versionId, side);
      await onRestored();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("changes.restoreFailed"));
      setBusy(false);
      setConfirmSide(null);
    }
  }

  const resourceLabel = record ? label(record) : "";
  // The newest version's "after" is the resource's current config, so restoring
  // it is a no-op; hide that button (the Before stays useful as an undo).
  const isLatest = history.length > 0 && history[0].id === versionId;
  const isRaw = !!record && RAW_CONTENT_TYPES.has(record.resource_type);
  const beforeRaw = isRaw && record ? rawSide(record.before) : null;
  const afterRaw = isRaw && record ? rawSide(record.after) : null;
  // A raw snapshot stored as a too-large marker (content null) is not restorable.
  const beforeRestorable = isRaw ? beforeRaw?.content != null : !!record && record.before != null;
  const afterRestorable = isRaw ? afterRaw?.content != null : !!record && record.after != null;
  const showBefore = beforeRestorable;
  const showAfter = afterRestorable && !isLatest;
  const rawDiff = isRaw ? diffLines(beforeRaw?.content ?? "", afterRaw?.content ?? "") : null;
  // Structured configs (automation/script/scene/helper/dashboard) get the same
  // red/green line diff as raw files, comparing their YAML renderings.
  const structuredDiff = !isRaw && record ? diffLines(toYaml(record.before), toYaml(record.after)) : null;
  // Live layout preview, dashboards only. A side that has no previewable shape
  // (null, or a strategy dashboard) gets its chip disabled.
  const isDashboard = !!record && record.resource_type === "dashboard";
  const beforeOk = isDashboard && collectPreviewViews(record!.before) !== null;
  const afterOk = isDashboard && collectPreviewViews(record!.after) !== null;
  const canPreview = huiReady && (beforeOk || afterOk);
  // Never render a non-previewable side even if state says so (e.g. a create
  // version whose Before is null).
  const previewSide = side === "before" ? (beforeOk ? "before" : "after") : (afterOk ? "after" : "before");
  const showPreview = canPreview && mode === "preview";

  // One place deciding how a pane renders, across raw-vs-structured payloads and
  // line-diff-vs-code view, so no branch can miss the code view.
  const renderSide = (which: "before" | "after") => {
    const tone = which === "before" ? "remove" : "add";
    if (isRaw) {
      const raw = which === "before" ? beforeRaw : afterRaw;
      if (raw?.content == null) {
        return (
          <pre className="yaml-pre yaml-pre-empty">
            {raw
              ? (raw.bytes
                ? t("changes.snapshotTooLargeBytes", { bytes: raw.bytes })
                : t("changes.snapshotTooLarge"))
              : t("common.none")}
          </pre>
        );
      }
      if (code) return <YamlView value={raw.content} />;
      return <RawDiffPane rows={which === "before" ? rawDiff!.beforeRows : rawDiff!.afterRows} tone={tone} />;
    }
    if (code) return <YamlView value={toYaml((which === "before" ? record?.before : record?.after) ?? null)} />;
    return <RawDiffPane rows={which === "before" ? structuredDiff!.beforeRows : structuredDiff!.afterRows} tone={tone} />;
  };

  return (
    <div className="view-root change-detail">
      <div className="change-detail-bar">
        <button className="btn btn-text btn-sm" onClick={onBack}><span aria-hidden="true">&larr;</span> {t("changes.backToChanges")}</button>
      </div>

      {error && <div className="banner banner-error">{error}</div>}

      {loading || !record ? <Loading /> : (
        <>
          <div className="change-detail-head">
            <ActionBadge action={record.action} />
            <strong className="change-detail-name">{resourceLabel}</strong>
            <code className="change-detail-type">{resourceTypeLabel(record.resource_type)}</code>
            {versionSummary(record) ? <span className="change-detail-summary">{versionSummary(record)}</span> : null}
            <span className="change-detail-when">{formatDateTime(record.timestamp)}</span>
            <span className="change-detail-by">{t("changes.byWho", { who: whoDetail(record, accessKeyNames) })}</span>
          </div>

          <div className="change-detail-body">
            <aside className="change-timeline" aria-label={t("changes.timelineAria")}>
              {history.map((h) => (
                <button
                  key={h.id}
                  type="button"
                  className={`change-timeline-row${h.id === versionId ? " active" : ""}`}
                  onClick={() => h.id !== versionId && onSelectVersion(h.id)}
                >
                  <ActionBadge action={h.action} />
                  <span className="change-timeline-when">{formatDateTime(h.timestamp)}</span>
                </button>
              ))}
            </aside>

            <div className="change-diff-wrap">
              <div className="change-diff-toolbar">
                <span className="change-diff-hint">
                  {showPreview
                    ? tRich("changes.hintPreview", { strong: (c) => <strong>{c}</strong> },
                        { side: previewSide === "before" ? t("common.before") : t("common.after") })
                    : showBefore || showAfter
                    ? tRich("changes.hintRestorable", { strong: (c) => <strong>{c}</strong> }, { name: resourceLabel })
                    : tRich("changes.hintCurrent", { strong: (c) => <strong>{c}</strong> }, { name: resourceLabel })}
                  {!showPreview && code
                    && <> {t("changes.hintCode")}</>}
                </span>
                {canPreview && (
                  <SegmentedToggle
                    value={mode}
                    onChange={setMode}
                    ariaLabel={t("approvals.diffModeAria")}
                    options={[{ value: "diff", label: t("common.diff") }, { value: "preview", label: t("common.preview") }]}
                  />
                )}
                {!showPreview && <CodeToggle code={code} onToggle={toggleCode} />}
                {!showPreview && <LayoutToggle stacked={stacked} onToggle={toggleStacked} />}
              </div>

              {confirmSide && (
                <div className="banner banner-warn change-restore-note">
                  {tRich("changes.restoreNote",
                    { strong: (c) => <strong>{c}</strong>, code: (c) => <code>{c}</code>, em: (c) => <em>{c}</em> },
                    { side: confirmSide === "before" ? t("common.before") : t("common.after"),
                      type: record.resource_type, name: resourceLabel })}
                  <div className="change-restore-actions">
                    <BusyButton className="btn btn-text btn-sm" onClick={() => setConfirmSide(null)} busy={busy}>{t("common.cancel")}</BusyButton>
                    <BusyButton className="btn btn-primary btn-sm" onClick={() => restore(confirmSide)} busy={busy}>
                      {busy ? t("changes.restoring") : t("changes.confirmRestore", { side: confirmSide === "before" ? t("common.before") : t("common.after") })}
                    </BusyButton>
                  </div>
                </div>
              )}

              {showPreview ? (
                <div className="dash-preview-host">
                  <div className="diff-mode-row">
                    <SegmentedToggle
                      value={previewSide}
                      onChange={setSide}
                      ariaLabel={t("approvals.diffSideAria")}
                      options={[
                        { value: "before", label: t("common.before"), disabled: !beforeOk },
                        { value: "after", label: t("common.after"), disabled: !afterOk },
                      ]}
                    />
                  </div>
                  <DashboardPreview
                    key={previewSide}
                    config={previewSide === "before" ? record.before : record.after}
                  />
                </div>
              ) : (
              <div className={`yaml-diff-cols${stacked ? " stacked" : ""}`}>
                <div className="yaml-diff-col">
                  <div className="yaml-pane-head">
                    <span className="approval-diff-label">{t("common.before")}</span>
                    {showBefore && (
                      <BusyButton className="btn btn-primary btn-sm" onClick={() => setConfirmSide("before")} busy={busy} disabled={confirmSide === "before"}>
                        {t("changes.restoreThis")}
                      </BusyButton>
                    )}
                  </div>
                  {renderSide("before")}
                </div>
                <div className="yaml-diff-col">
                  <div className="yaml-pane-head">
                    <span className="approval-diff-label">{t("common.after")}</span>
                    {showAfter && (
                      <BusyButton className="btn btn-primary btn-sm" onClick={() => setConfirmSide("after")} busy={busy} disabled={confirmSide === "after"}>
                        {t("changes.restoreThis")}
                      </BusyButton>
                    )}
                  </div>
                  {renderSide("after")}
                </div>
              </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
