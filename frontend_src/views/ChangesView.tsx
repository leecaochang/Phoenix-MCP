/**
 * The Changes workspace's windows: Changes lists the recorded configuration
 * changes (search, filters and the configurable table), Comparison shows the
 * chosen change's saved Before and After with one Restore menu, and Item
 * History lists the other changes recorded for the same item. Their shared
 * state is `shell/changes.tsx`.
 */

import { useContext, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import type { VersionSummary } from "../types";
import { ErrorMsg, Loading } from "../components/common";
import { CodeToggle, diffLines, LayoutToggle, RawDiffPane, rememberCodeView, rememberDiffLayout, storedCodeView, storedDiffLayout } from "../components/DiffView";
import { collectPreviewViews, DashboardPreview, rememberPreviewMode, SegmentedToggle, storedPreviewMode, useHuiCardReady } from "../components/DashboardPreview";
import { YamlView, toYaml } from "../components/YamlView";
import { hasMessage, localeNumber, t } from "../i18n";
import { tRich } from "../i18n/rich";
import { changeLabel, RAW_CONTENT_TYPES, rawSide, restoreAvailability, useChanges, type ChangesState, type RestoreSide } from "../shell/changes";
import { ControllerContext } from "../shell/dock/TitleBar";
import { useToolbarTools, type Tool, type ToolGroup } from "../shell/tools";
import { ActionButton, DisplayModeContext } from "../ui/ActionButton";
import { Icon, type IconName } from "../ui/Icon";
import { InlineConfirm } from "../ui/InlineConfirm";
import { MenuButton, type MenuEntry } from "../ui/Menu";
import { ConfigTable, type Column } from "../ui/table/ConfigTable";
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
  return cur || v.access_key_name || (v.approved_by_user_id ? "admin" : "-");
}

// In Comparison: when the access key has since been renamed, show "current (original)".
function whoDetail(
  v: { access_key_id: string | null; access_key_name: string | null; approved_by_user_id: string | null },
  current: Map<string, string>,
): string {
  const cur = v.access_key_id ? current.get(v.access_key_id) : undefined;
  if (cur && v.access_key_name && cur !== v.access_key_name) return `${cur} (${v.access_key_name})`;
  return cur || v.access_key_name || (v.approved_by_user_id ? "admin" : "-");
}

/** A change's one-line summary, localized when the backend supplied a catalog key this bundle knows. */
function versionSummary(v: { summary?: string | null; summary_key?: string | null; summary_params?: Record<string, string | number> | null }): string {
  if (v.summary_key && hasMessage(v.summary_key)) return t(v.summary_key, v.summary_params ?? undefined);
  return v.summary ?? "";
}

/** Tools a window offers on the toolbar, or inline when it is not in the shell. */
function InlineTools({ groups }: { groups: ToolGroup[] }) {
  const tools = groups.flatMap((g) => g.tools);
  if (!tools.length) return null;
  return (
    <div className="phx-window-actions">
      {tools.map((tool: Tool) => (
        <ActionButton key={tool.id} icon={tool.icon} label={tool.label} disabled={tool.disabled} pressed={tool.pressed}
          onClick={() => tool.onSelect?.()} className="btn btn-outline btn-sm" />
      ))}
    </div>
  );
}

const noSubscribe = () => () => {};
const noVersion = () => 0;

/** A toolbar toggle that opens a closed window or closes an open one, for the window it belongs to. */
function useWindowToggle(windowId: string): { open: boolean; toggle: () => void } | null {
  const controller = useContext(ControllerContext);
  useSyncExternalStore(controller?.subscribe ?? noSubscribe, controller?.getVersion ?? noVersion);
  if (!controller) return null;
  return {
    open: !!controller.hostOf(windowId),
    toggle: () => {
      const group = controller.groupOf(windowId);
      if (group) controller.close(group);
      else controller.ensureOpen(windowId);
    },
  };
}

export function ChangesListWindow() {
  const c = useChanges();
  const root = useRef<HTMLDivElement | null>(null);
  const [search, setSearch] = useState("");
  const [type, setType] = useState("");
  const [action, setAction] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const activeFilters = (type ? 1 : 0) + (action ? 1 : 0);
  const groups: ToolGroup[] = c ? [{
    id: "changes",
    label: t("shell.tabChanges"),
    tools: [
      { id: "refresh", label: t("workspace.refreshWindow", { window: t("shell.tabChanges") }), short: t("common.refresh"), icon: "refresh", onSelect: c.refresh },
      { id: "filters", label: activeFilters ? t("logs.filtersCount", { count: localeNumber(activeFilters) }) : t("logs.filters"), short: t("logs.filters"), icon: "filter",
        pressed: filtersOpen, badge: activeFilters || undefined, onSelect: () => setFiltersOpen((open) => !open) },
    ],
  }] : [];
  const onToolbar = useToolbarTools(groups);
  if (!c) return null;
  const showFilters = !onToolbar || filtersOpen;
  const clearFilters = () => { setType(""); setAction(""); };
  // A column's menu opens the filter for that column.
  const openFilter = (name: string) => {
    setFiltersOpen(true);
    requestAnimationFrame(() => root.current?.querySelector<HTMLElement>(`[data-filter="${name}"]`)?.focus());
  };
  const filterItem = (name: string) => (): MenuEntry[] => [{ id: "filter", label: t("table.filterColumn"), onSelect: () => openFilter(name) }];

  const shown = c.feed.filter((v) => (!type || v.resource_type === type)
    && (!action || v.action === action)
    && matchesListSearch(`${changeLabel(v)} ${v.resource_id} ${v.resource_type} ${resourceTypeLabel(v.resource_type)} ${v.action} ${actionLabel(v.action)} ${whoNow(v, c.names)} ${versionSummary(v)}`, search));
  const columns: Column<VersionSummary>[] = [
    {
      id: "item", label: t("changes.colName"), width: 220, sortValue: (v) => changeLabel(v), title: (v) => changeLabel(v),
      // The compact default: what changed and who changed it when, under the item's name.
      cell: (v) => (
        <span className="change-item">
          <span>{changeLabel(v)}</span>
          {versionSummary(v) && <span className="conversation-secondary">{versionSummary(v)}</span>}
          <span className="conversation-secondary">{formatDateTime(v.timestamp)} · {whoNow(v, c.names)}</span>
        </span>
      ),
    },
    { id: "action", label: t("changes.colAction"), width: 120, sortValue: (v) => actionLabel(v.action), cell: (v) => <ChangeAction action={v.action} />, menu: filterItem("action") },
    { id: "type", label: t("changes.colType"), width: 150, hidden: true, sortValue: (v) => resourceTypeLabel(v.resource_type), cell: (v) => resourceTypeLabel(v.resource_type), menu: filterItem("type") },
    { id: "by", label: t("changes.colBy"), width: 140, hidden: true, sortValue: (v) => whoNow(v, c.names), cell: (v) => whoNow(v, c.names) },
    { id: "time", label: t("changes.colWhen"), width: 160, hidden: true, sortValue: (v) => v.timestamp, cell: (v) => formatDateTime(v.timestamp) },
  ];

  return (
    <div className="phx-list-view changes-list-view" ref={root}>
      {!onToolbar && <InlineTools groups={groups} />}
      <div className="phx-filters" hidden={!showFilters}>
        <div className="filter-row">
          <select className="input input-auto" data-filter="type" value={type} onChange={(e) => setType(e.target.value)} aria-label={t("changes.colType")}>
            <option value="">{t("changes.allTypes")}</option>
            {Object.keys(RESOURCE_TYPE_LABEL_KEYS).map((rt) => <option key={rt} value={rt}>{resourceTypeLabel(rt)}</option>)}
          </select>
          <select className="input input-auto" data-filter="action" value={action} onChange={(e) => setAction(e.target.value)} aria-label={t("changes.colAction")}>
            <option value="">{t("changes.allActions")}</option>
            {Object.keys(ACTION_LABEL_KEYS).map((a) => <option key={a} value={a}>{actionLabel(a)}</option>)}
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
      <div className="approvals-filter-row">
        <input type="search" className="input" value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder={t("changes.search")} aria-label={t("changes.search")} />
      </div>
      {c.error && <ErrorMsg msg={c.error} />}
      {c.loading && !c.feed.length && <Loading />}
      <ConfigTable
        id="changes"
        label={t("changes.title")}
        columns={columns}
        rows={shown}
        rowKey={(v) => v.id}
        selectedKey={c.selectedId}
        onSelect={(v) => c.select(v.id)}
        rowLabel={(v) => `${changeLabel(v)}, ${actionLabel(v.action)}, ${formatDateTime(v.timestamp)}`}
        empty={c.loading ? null : <p className="audit-empty">{c.feed.length ? t("changes.emptySearch") : t("changes.empty")}</p>}
      />
      {c.hasMore && (
        <div className="phx-table-footer">
          <ActionButton icon="loadMore" label={c.loadingMore ? t("common.loading") : t("common.loadMore")} busy={c.loadingMore} onClick={c.loadMore} />
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
      onSelect: () => c.askRestore(side),
    };
  };
  return (
    <MenuButton label={t("changes.restore")} icon={<Icon name="restoreVersion" size={16} />} items={() => [item("before"), item("after")]}
      className={`btn btn-outline btn-sm phx-action${iconsOnly ? " is-icon-only" : ""}`} pressed={!!c.confirm}>
      {!iconsOnly && <span>{t("changes.restore")}</span>}
    </MenuButton>
  );
}

function RestoreConfirmation({ c }: { c: ChangesState }) {
  const record = c.record!;
  const side = t(c.confirm!.side === "before" ? "common.before" : "common.after");
  return (
    <InlineConfirm label={t("changes.restore")} confirmLabel={t("changes.confirmRestoreButton")} confirmClassName="btn btn-sm phx-decision-approve"
      cancelLabel={t("changes.cancelRestore")} busy={c.restoring} onConfirm={() => { void c.restore(); }} onCancel={c.cancelRestore}>
      <p>
        <strong>{t("changes.confirmRestoreQuestion", { name: changeLabel(record), side, time: formatDateTime(record.timestamp) })}</strong>{" "}
        <span>{t("changes.restoreReplaces")}</span>
      </p>
    </InlineConfirm>
  );
}

/** The saved Before and After: a line diff or the code, side by side or stacked, or a dashboard preview. */
function VersionComparison({ c }: { c: ChangesState }) {
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
  const [side, setSide] = useState<RestoreSide>("after");

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
      <div className="change-diff-toolbar">
        <span className="change-diff-hint">
          {showPreview
            ? tRich("changes.hintPreviewSide", { strong: (s) => <strong>{s}</strong> }, { side: t(previewSide === "before" ? "common.before" : "common.after") })
            : <>{t("changes.hintDiff")}{code && <> {t("changes.hintCode")}</>}</>}
        </span>
        {canPreview && (
          <SegmentedToggle value={mode} onChange={setMode} ariaLabel={t("approvals.diffModeAria")}
            options={[{ value: "diff", label: t("common.diff") }, { value: "preview", label: t("common.preview") }]} />
        )}
        {!showPreview && <CodeToggle code={code} onToggle={toggleCode} />}
        {!showPreview && <LayoutToggle stacked={stacked} onToggle={toggleStacked} />}
      </div>
      {showPreview ? (
        <div className="dash-preview-host">
          <div className="diff-mode-row">
            <SegmentedToggle value={previewSide} onChange={setSide} ariaLabel={t("approvals.diffSideAria")}
              options={[
                { value: "before", label: t("common.before"), disabled: !beforeOk },
                { value: "after", label: t("common.after"), disabled: !afterOk },
              ]} />
          </div>
          <DashboardPreview key={previewSide} config={previewSide === "before" ? record.before : record.after} />
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
  const history = useWindowToggle("itemHistory");
  useToolbarTools(history ? [{
    id: "comparison",
    label: t("workspace.comparison"),
    tools: [{ id: "itemHistory", label: t("workspace.itemHistory"), icon: "itemHistory", pressed: history.open, onSelect: history.toggle }],
  }] : []);
  if (!c || !c.selectedId) return <p className="phx-window-empty">{t("workspace.comparisonEmpty")}</p>;
  if (c.recordError) return <div className="phx-review"><ErrorMsg msg={c.recordError} /></div>;
  if (!c.record || c.record.id !== c.selectedId) return <Loading />;
  const record = c.record;
  const summary = versionSummary(record);
  return (
    <div className="phx-review change-comparison" key={record.id}>
      <div className="phx-review-head">
        <h3 className="phx-review-title">{changeLabel(record)}</h3>
        <p className="phx-review-context">
          <ChangeAction action={record.action} />
          <span>{resourceTypeLabel(record.resource_type)}</span>
          <span>{t("changes.byWho", { who: whoDetail(record, c.names) })}</span>
          <span>{formatDateTime(record.timestamp)}</span>
        </p>
        {summary && <p className="phx-review-body">{summary}</p>}
      </div>
      <div className="phx-review-actions"><RestoreMenu c={c} /></div>
      {c.confirm && <RestoreConfirmation c={c} />}
      {c.restoreError && <div className="banner banner-error" role="alert">{c.restoreError}</div>}
      <div className="change-detail-body"><VersionComparison c={c} /></div>
    </div>
  );
}

export function ItemHistoryWindow() {
  const c = useChanges();
  if (!c || !c.selectedId || !c.record) return <p className="phx-window-empty">{t("workspace.itemHistoryEmpty")}</p>;
  const columns: Column<VersionSummary>[] = [
    { id: "time", label: t("changes.colWhen"), width: 170, sortValue: (v) => v.timestamp, cell: (v) => formatDateTime(v.timestamp) },
    { id: "action", label: t("changes.colAction"), width: 130, sortValue: (v) => actionLabel(v.action), cell: (v) => <ChangeAction action={v.action} /> },
    { id: "by", label: t("changes.colBy"), width: 140, hidden: true, sortValue: (v) => whoNow(v, c.names), cell: (v) => whoNow(v, c.names) },
    { id: "summary", label: t("changes.colSummary"), width: 260, sortValue: (v) => versionSummary(v), cell: (v) => versionSummary(v), title: (v) => versionSummary(v) || undefined },
  ];
  return (
    <div className="phx-list-view">
      <ConfigTable
        id="itemHistory"
        label={t("workspace.itemHistory")}
        columns={columns}
        rows={c.history}
        rowKey={(v) => v.id}
        selectedKey={c.selectedId}
        onSelect={(v) => c.select(v.id)}
        rowLabel={(v) => `${actionLabel(v.action)}, ${formatDateTime(v.timestamp)}`}
      />
    </div>
  );
}
