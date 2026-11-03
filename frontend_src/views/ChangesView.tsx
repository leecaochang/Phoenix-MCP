/**
 * The Changes workspace's windows: Changes lists the recorded configuration
 * changes (search, filters and the configurable table), Comparison shows the
 * chosen change's saved Before and After with one Restore menu, and Item
 * History lists the other changes recorded for the same item. Their shared
 * state is `shell/changes.tsx`.
 */

import { useContext, useEffect, useState, type ReactNode } from "react";
import type { VersionSummary } from "../types";
import { ErrorMsg, Loading, ResultCount } from "../components/common";
import { CodeToggle, diffLines, LayoutToggle, RawDiffPane, rememberCodeView, rememberDiffLayout, storedCodeView, storedDiffLayout } from "../components/DiffView";
import { collectPreviewViews, DashboardPreview, rememberPreviewMode, SegmentedToggle, storedPreviewMode, useHuiCardReady } from "../components/DashboardPreview";
import { YamlView, toYaml } from "../components/YamlView";
import { hasMessage, localeNumber, t } from "../i18n";
import { changeLabel, RAW_CONTENT_TYPES, rawSide, restoreAvailability, useChanges, type ChangesState, type RestoreSide } from "../shell/changes";
import { useOnToolbar, useToolbarTools, type ToolGroup } from "../shell/tools";
import { InlineTools, useEnsureOpen, useWindowToggle, useWorkspaceReset } from "../shell/windowTools";
import { ActionButton, DisplayModeContext } from "../ui/ActionButton";
import { Icon, type IconName } from "../ui/Icon";
import { InlineConfirm } from "../ui/InlineConfirm";
import { withCardLocation, withLocaleSizes } from "../utils/dashboard_names";
import { MenuButton, type MenuEntry } from "../ui/Menu";
import { ConfigTable, type Column } from "../ui/table/ConfigTable";
import { shownCount, useRowLimit } from "../ui/table/rowLimit";
import { formatDateTime } from "../utils";
import { matchesListSearch } from "../utils/list_search";

// The backend records resource types and actions as slugs. An unknown slug falls
// through to the raw value rather than a blank label.
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
  label: "changes.typeLabel",
  category: "changes.typeCategory",
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

const ACTION_ICONS: Record<string, IconName> = { create: "add", edit: "edit", delete: "delete", rollback: "restoreVersion" };

function actionLabel(action: string): string {
  const key = ACTION_LABEL_KEYS[action];
  return key ? t(key) : action;
}

function ChangeAction({ action }: { action: string }) {
  return (
    <span className={`phx-outcome change-action-${action}`}>
      <Icon name={ACTION_ICONS[action] ?? "change"} size={14} />
      {actionLabel(action)}
    </span>
  );
}

// Author display name. Prefer the access key's CURRENT name (it may have been renamed
// since the change was recorded), falling back to the name captured at the time,
// then to "admin" for admin-driven restores.
function whoNow(
  v: { access_key_id?: string | null; access_key_name: string | null; approved_by_user_id: string | null },
  current: Map<string, string>,
): string {
  const cur = v.access_key_id ? current.get(v.access_key_id) : undefined;
  return cur || v.access_key_name || (v.approved_by_user_id ? t("changes.byAdmin") : "-");
}

// In Comparison: when the access key has since been renamed, show "current (original)".
function whoDetail(
  v: { access_key_id: string | null; access_key_name: string | null; approved_by_user_id: string | null },
  current: Map<string, string>,
): string {
  const cur = v.access_key_id ? current.get(v.access_key_id) : undefined;
  if (cur && v.access_key_name && cur !== v.access_key_name) return `${cur} (${v.access_key_name})`;
  return cur || v.access_key_name || (v.approved_by_user_id ? t("changes.byAdmin") : "-");
}

/** A change's one-line summary, localized when the backend supplied a catalog key this bundle knows. */
function versionSummary(v: { summary?: string | null; summary_key?: string | null; summary_params?: Record<string, string | number> | null }): string {
  if (v.summary_key && hasMessage(v.summary_key)) return t(v.summary_key, withLocaleSizes(v.summary_key, withCardLocation(v.summary_key, v.summary_params ?? undefined)));
  return v.summary ?? "";
}

/** Every table with a time column starts sorted by it, newest first (user, 2026-10-27). */
const NEWEST_FIRST = { column: "time", dir: "desc" } as const;

export function ChangesListWindow() {
  const c = useChanges();
  const rowLimit = useRowLimit();
  // Resetting the workspace clears what it had chosen, as it clears the arrangement.
  useWorkspaceReset(() => c?.select(null));
  // Choosing a change brings Comparison back if the user closed it, as Log Details comes back in Logs.
  const ensureOpen = useEnsureOpen();
  const [search, setSearch] = useState("");
  const [type, setType] = useState("");
  const [action, setAction] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  // Hidden filters, the search included, stop applying and come back as they were (user, 2026-10-27).
  const filtersApply = !useOnToolbar() || filtersOpen;
  const activeFilters = filtersApply ? (type ? 1 : 0) + (action ? 1 : 0) + (search.trim() ? 1 : 0) : 0;
  const groups: ToolGroup[] = c ? [{
    id: "changes",
    label: t("shell.tabChanges"),
    tools: [
      // No Refresh: the list follows Home Assistant's configuration events, and polls while shown.
      { id: "filters", label: activeFilters ? t("logs.filtersCount", { count: localeNumber(activeFilters) }) : t("logs.filters"), short: t("logs.filters"), icon: "filter", help: "help.filters",
        pressed: filtersOpen, badge: activeFilters || undefined, onSelect: () => setFiltersOpen((open) => !open) },
    ],
  }] : [];
  const onToolbar = useToolbarTools(groups);
  if (!c) return null;
  const showFilters = !onToolbar || filtersOpen;
  /**
   * Showing other changes leaves nothing chosen in the list, so Comparison and
   * Item History do not keep the change they had (user, 2026-10-29).
   */
  const forget = () => c?.select(null);
  const clearFilters = () => { forget(); setType(""); setAction(""); setSearch(""); };

  const shown = !filtersApply ? c.feed : c.feed.filter((v) => (!type || v.resource_type === type)
    && (!action || v.action === action)
    && matchesListSearch(`${changeLabel(v)} ${v.resource_id} ${v.resource_type} ${resourceTypeLabel(v.resource_type)} ${v.action} ${actionLabel(v.action)} ${whoNow(v, c.names)} ${versionSummary(v)}`, search));
  // Time first and every column shown (user, 2026-10-27); the Item cell is the item's name alone.
  const columns: Column<VersionSummary>[] = [
    { id: "time", label: t("audit.rowTime"), width: 160, sortValue: (v) => v.timestamp, cell: (v) => formatDateTime(v.timestamp) },
    { id: "item", label: t("changes.colName"), width: 220, maxWidth: 360, sortValue: (v) => changeLabel(v), title: (v) => changeLabel(v), cell: (v) => changeLabel(v) },
    { id: "action", label: t("changes.colAction"), width: 120, sortValue: (v) => actionLabel(v.action), cell: (v) => <ChangeAction action={v.action} /> },
    { id: "type", label: t("changes.colType"), width: 150, sortValue: (v) => resourceTypeLabel(v.resource_type), cell: (v) => resourceTypeLabel(v.resource_type) },
    { id: "by", label: t("changes.colBy"), width: 140, sortValue: (v) => whoNow(v, c.names), cell: (v) => whoNow(v, c.names), title: (v) => whoNow(v, c.names) },
  ];

  return (
    <div className="phx-list-view changes-list-view">
      {!onToolbar && <InlineTools groups={groups} />}
      <div className="phx-filters" hidden={!showFilters} data-guide="window.changes#filters">
        <div className="filter-row">
          <select className="input input-auto" data-filter="type" data-help="help.changesType" value={type} onChange={(e) => { forget(); setType(e.target.value); }} aria-label={t("changes.colType")}>
            <option value="">{t("changes.allTypes")}</option>
            {Object.keys(RESOURCE_TYPE_LABEL_KEYS).map((rt) => <option key={rt} value={rt}>{resourceTypeLabel(rt)}</option>)}
          </select>
          <select className="input input-auto" data-filter="action" data-help="help.changesAction" value={action} onChange={(e) => { forget(); setAction(e.target.value); }} aria-label={t("changes.colAction")}>
            <option value="">{t("changes.allActions")}</option>
            {Object.keys(ACTION_LABEL_KEYS).map((a) => <option key={a} value={a}>{actionLabel(a)}</option>)}
          </select>
          {activeFilters > 0 && <ActionButton icon="clearFilters" label={t("logs.clearFilters")} help="help.clearFilters" onClick={clearFilters} />}
        </div>
        <div className="filter-row">
          <input type="search" className="input" value={search} onChange={(e) => { forget(); setSearch(e.target.value); }}
            placeholder={t("changes.search")} aria-label={t("changes.search")} data-help="help.changesSearch" />
        </div>
      </div>
      {c.error && <ErrorMsg msg={c.error} />}
      {c.loading && !c.feed.length && <Loading />}
      {/* Grouped for the Guide only; the table stays the list's flex item. */}
      <div className="changes-table" data-guide="window.changes#list">
      <ConfigTable
        id="changes"
        label={t("changes.title")}
        defaultSort={NEWEST_FIRST}
        columns={columns}
        rows={shown}
        maxRows={rowLimit.limit}
        rowKey={(v) => v.id}
        selectedKey={c.selectedId}
        onSelect={(v, ordered) => { c.select(v.id, ordered.map((o) => o.id)); ensureOpen("comparison"); }}
        rowLabel={(v) => `${changeLabel(v)}, ${actionLabel(v.action)}, ${formatDateTime(v.timestamp)}`}
        rowHelp="help.changesRow"
        empty={c.loading ? null : <p className="audit-empty">{c.feed.length ? t("changes.emptySearch") : t("changes.empty")}</p>}
      />
      </div>
      <ResultCount count={shown.length} query={`${search}|${type}|${action}`} loading={c.loading} />
      {!c.loading && c.feed.length > 0 && (
        <div className="phx-table-footer">
          <span className="conversation-secondary">{t("conversations.showing", { shown: localeNumber(shownCount(shown.length, rowLimit.limit)), total: localeNumber(c.total) })}</span>
          {(c.hasMore || rowLimit.hidden(shown.length)) && (
            <ActionButton icon="loadMore" label={c.loadingMore ? t("common.loading") : t("common.loadMore")} guide="window.changes#load-more" helpText={t("help.loadMore", { count: localeNumber(rowLimit.step ?? 100) })} busy={c.loadingMore}
              onClick={() => { if (!rowLimit.hidden(shown.length)) c.loadMore(); rowLimit.more(); }} />
          )}
        </div>
      )}
    </div>
  );
}

/** The single Restore menu: each side, or why it cannot be restored. */
function RestoreMenu({ c }: { c: ChangesState }) {
  const iconsOnly = useContext(DisplayModeContext) === "icons";
  const record = c.record!;
  const item = (side: RestoreSide): MenuEntry => {
    const availability = restoreAvailability(record, c.history, side);
    return {
      id: side,
      label: t(side === "before" ? "changes.restoreBefore" : "changes.restoreAfter"),
      disabled: !availability.ok,
      hint: availability.ok ? undefined : availability.reason,
      help: t(side === "before" ? "help.restoreBefore" : "help.restoreAfter"),
      onSelect: () => c.askRestore(side),
    };
  };
  return (
    <span className="change-restore" data-guide="window.comparison#restore">
    <MenuButton label={t("changes.restore")} help="help.restore" icon={<Icon name="restoreVersion" size={16} />} items={() => [item("before"), item("after")]}
      className={`btn btn-outline btn-sm phx-action${iconsOnly ? " is-icon-only" : ""}`} pressed={!!c.confirm}>
      {!iconsOnly && <span>{t("changes.restore")}</span>}
    </MenuButton>
    </span>
  );
}

function RestoreConfirmation({ c }: { c: ChangesState }) {
  const record = c.record!;
  const side = t(c.confirm!.side === "before" ? "common.before" : "common.after");
  return (
    <div data-guide="window.comparison#confirm">
    <InlineConfirm label={t("changes.restore")} title={t("changes.confirmRestoreQuestion", { name: changeLabel(record), side, time: formatDateTime(record.timestamp) })} confirmLabel={t("changes.confirmRestoreButton")} confirmClassName="btn btn-sm phx-decision-approve"
      cancelLabel={t("changes.cancelRestore")} help={t("help.confirmRestore")} busy={c.restoring} onConfirm={() => { void c.restore(); }} onCancel={c.cancelRestore}>
      <p>{t("changes.restoreReplaces")}</p>
    </InlineConfirm>
    </div>
  );
}

/** The saved Before and After: a line diff or the code, side by side or stacked, or a dashboard preview. */
function VersionComparison({ c, restore }: { c: ChangesState; restore: ReactNode }) {
  const record = c.record!;
  // A stored preference wins; otherwise stacked on narrow viewports and side by side on wider ones.
  const [stacked, setStacked] = useState(() => storedDiffLayout(typeof window !== "undefined" && window.matchMedia("(max-width: 800px)").matches));
  const toggleStacked = () => { rememberDiffLayout(!stacked); setStacked(!stacked); };
  // The code view is the same preference the approval diff uses.
  const [code, setCode] = useState(() => storedCodeView());
  const toggleCode = () => { rememberCodeView(!code); setCode(!code); };
  // Dashboard changes offer an inert layout preview; Diff or Preview is remembered too.
  const huiReady = useHuiCardReady();
  const [mode, setModeState] = useState<"diff" | "preview">(() => storedPreviewMode());
  const setMode = (m: "diff" | "preview") => { rememberPreviewMode(m); setModeState(m); };
  // The Before or After choice holds for the change it was made on; the next change starts on After again.
  const [picked, setPicked] = useState<{ id: string; side: RestoreSide } | null>(null);
  const side: RestoreSide = picked?.id === record.id ? picked.side : "after";
  const setSide = (value: RestoreSide) => setPicked({ id: record.id, side: value });

  const isRaw = RAW_CONTENT_TYPES.has(record.resource_type);
  const beforeRaw = isRaw ? rawSide(record.before) : null;
  const afterRaw = isRaw ? rawSide(record.after) : null;
  const rawDiff = isRaw ? diffLines(beforeRaw?.content ?? "", afterRaw?.content ?? "") : null;
  // Structured configs get the same red and green line diff, comparing their YAML renderings.
  const structuredDiff = !isRaw ? diffLines(toYaml(record.before), toYaml(record.after)) : null;
  const isDashboard = record.resource_type === "dashboard";
  const beforeOk = isDashboard && collectPreviewViews(record.before) !== null;
  const afterOk = isDashboard && collectPreviewViews(record.after) !== null;
  const canPreview = huiReady && (beforeOk || afterOk);
  // Never render a side that has no previewable shape (a create's Before, a strategy dashboard).
  const previewSide = side === "before" ? (beforeOk ? "before" : "after") : (afterOk ? "after" : "before");
  const showPreview = canPreview && mode === "preview";

  // One place deciding how a pane renders, across raw-vs-structured payloads and diff-vs-code view.
  const renderSide = (which: RestoreSide): ReactNode => {
    const tone = which === "before" ? "remove" : "add";
    if (isRaw) {
      const raw = which === "before" ? beforeRaw : afterRaw;
      if (raw?.content == null) {
        return (
          <pre className="yaml-pre yaml-pre-empty">
            {raw ? (raw.bytes ? t("changes.snapshotTooLargeBytes", { bytes: raw.bytes }) : t("changes.snapshotTooLarge")) : t("common.none")}
          </pre>
        );
      }
      if (code) return <YamlView value={raw.content} />;
      return <RawDiffPane rows={which === "before" ? rawDiff!.beforeRows : rawDiff!.afterRows} tone={tone} />;
    }
    if (code) return <YamlView value={toYaml((which === "before" ? record.before : record.after) ?? null)} />;
    return <RawDiffPane rows={which === "before" ? structuredDiff!.beforeRows : structuredDiff!.afterRows} tone={tone} />;
  };

  return (
    <div className="change-diff-wrap">
      {/* The view controls, then Restore at the end of the same row (user, 2026-10-27). */}
      <div className="change-diff-toolbar" data-guide="window.comparison#views">
        {canPreview && (
          <SegmentedToggle value={mode} onChange={setMode} help="help.diffPreview" ariaLabel={t("approvals.diffModeAria")}
            options={[{ value: "diff", label: t("common.diff") }, { value: "preview", label: t("common.preview") }]} />
        )}
        {!showPreview && <CodeToggle code={code} onToggle={toggleCode} />}
        {!showPreview && <LayoutToggle stacked={stacked} onToggle={toggleStacked} />}
        {showPreview && (
          <SegmentedToggle value={previewSide} onChange={setSide} help="help.previewSide" ariaLabel={t("approvals.diffSideAria")}
            options={[
              { value: "before", label: t("common.before"), disabled: !beforeOk },
              { value: "after", label: t("common.after"), disabled: !afterOk },
            ]} />
        )}
        <span className="change-diff-toolbar-end">{restore}</span>
      </div>
      {showPreview ? (
        <div className="dash-preview-host">
          <DashboardPreview key={`${record.id}:${previewSide}`} config={previewSide === "before" ? record.before : record.after} />
        </div>
      ) : (
        <div className={`yaml-diff-cols${stacked ? " stacked" : ""}`}>
          <div className="yaml-diff-col">
            <div className="yaml-pane-head"><span className="approval-diff-label">{t("common.before")}</span></div>
            {renderSide("before")}
          </div>
          <div className="yaml-diff-col">
            <div className="yaml-pane-head"><span className="approval-diff-label">{t("common.after")}</span></div>
            {renderSide("after")}
          </div>
        </div>
      )}
    </div>
  );
}

export function ComparisonWindow() {
  const c = useChanges();
  // Item History opens by itself once a chosen change's item has other changes (user, 2026-10-27); once the user
  // closes it, it stays closed until reopened from Workspace Options or the workspace is reset.
  const history = useWindowToggle("itemHistory");
  const hasHistory = !!c?.record && c.record.id === c.selectedId && c.history.length > 1;
  useEffect(() => { if (hasHistory) history?.autoOpen(); }, [hasHistory, c?.selectedId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!c || !c.selectedId) return <p className="phx-window-empty">{t("workspace.comparisonEmpty")}</p>;
  if (c.recordError) return <div className="phx-review"><ErrorMsg msg={c.recordError} /></div>;
  // While the next change loads, the one before stays on screen (no flash of a loading state), marked busy.
  if (!c.record) return <Loading />;
  const record = c.record;
  const stale = record.id !== c.selectedId;
  const summary = versionSummary(record);
  return (
    // Not keyed by the change: stepping updates the window in place, so the head and its buttons stay put.
    <div className="phx-review change-comparison" aria-busy={stale}>
      <div className="phx-review-head">
        {/* Previous and Next step through the list the change was chosen from, as in Log Details. */}
        <div className="phx-log-details-bar">
          <h3 className="phx-review-title" aria-live="polite">{changeLabel(record)}</h3>
          <div className="phx-log-details-nav" data-guide="window.comparison#step">
            <ActionButton icon="previous" label={t("workspace.previousRecord")} help="help.previousRecord" inactive={!c.neighbour(-1)} onClick={() => c.step(-1)} className="btn btn-outline btn-sm" />
            <ActionButton icon="next" label={t("workspace.nextRecord")} help="help.nextRecord" inactive={!c.neighbour(1)} onClick={() => c.step(1)} className="btn btn-outline btn-sm" />
          </div>
        </div>
        <p className="phx-review-context">
          <ChangeAction action={record.action} />
          <span>{resourceTypeLabel(record.resource_type)}</span>
          <span>{t("changes.byWho", { who: whoDetail(record, c.names) })}</span>
          <span>{formatDateTime(record.timestamp)}</span>
        </p>
        {summary && <p className="phx-review-body">{summary}</p>}
      </div>
      {c.confirm && <RestoreConfirmation c={c} />}
      {c.restoreError && <div className="banner banner-error" role="alert">{c.restoreError}</div>}
      <div className="change-detail-body"><VersionComparison c={c} restore={<RestoreMenu c={c} />} /></div>
    </div>
  );
}

export function ItemHistoryWindow() {
  const c = useChanges();
  const ensureOpen = useEnsureOpen();
  if (!c || !c.selectedId || !c.record) return <p className="phx-window-empty">{t("workspace.itemHistoryEmpty")}</p>;
  const columns: Column<VersionSummary>[] = [
    { id: "time", label: t("audit.rowTime"), width: 170, sortValue: (v) => v.timestamp, cell: (v) => formatDateTime(v.timestamp) },
    { id: "action", label: t("changes.colAction"), width: 130, sortValue: (v) => actionLabel(v.action), cell: (v) => <ChangeAction action={v.action} /> },
    { id: "by", label: t("changes.colBy"), width: 140, hidden: true, sortValue: (v) => whoNow(v, c.names), cell: (v) => whoNow(v, c.names) },
    { id: "summary", label: t("changes.colSummary"), width: 260, maxWidth: 360, sortValue: (v) => versionSummary(v), cell: (v) => versionSummary(v), title: (v) => versionSummary(v) || undefined },
  ];
  return (
    <div className="phx-list-view">
      <ConfigTable
        id="itemHistory"
        rowHelp="help.itemHistoryRow"
        label={t("workspace.itemHistory")}
        defaultSort={NEWEST_FIRST}
        columns={columns}
        rows={c.history}
        rowKey={(v) => v.id}
        selectedKey={c.selectedId}
        onSelect={(v, ordered) => { c.select(v.id, ordered.map((o) => o.id)); ensureOpen("comparison"); }}
        rowLabel={(v) => `${actionLabel(v.action)}, ${formatDateTime(v.timestamp)}`}
      />
    </div>
  );
}
