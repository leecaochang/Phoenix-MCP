/**
 * The MESA workspace's windows, around the shared state in `shell/mesa.tsx`:
 * Profiles (every profile at every scope in one list, with importing and
 * exporting in place), Profile Editor, Suggested Profiles under the list, and
 * the floating Orphaned Profiles, Advisory and Diagnostics windows. The
 * workspace's tools (Add Profile, Filters and the MESA Actions menu) come with
 * Profiles.
 */

import { useRef, useState, type ReactNode, type RefObject } from "react";
import type { MesaProfileScope, MesaSuggestion, MesaValidationIssue } from "../types";
import { ErrorMsg, Loading, ResultCount } from "../components/common";
import { MesaIdentity } from "../components/MesaIdentity";
import { MODE_LABEL_KEYS, SIGNAL_LABEL_KEYS, suggestionReason } from "../components/MesaSuggestions";
import { localeNumber, t, tn, tnKey } from "../i18n";
import { tRich } from "../i18n/rich";
import { useMesa, type MesaState, type ProfileRow } from "../shell/mesa";
import { useOnToolbar, useToolbarTools, type ToolGroup } from "../shell/tools";
import { InlineTools, useEnsureOpen, useWindowToggle, useWorkspaceReset } from "../shell/windowTools";
import { ActionButton, DisplayModeContext } from "../ui/ActionButton";
import { Icon, type IconName } from "../ui/Icon";
import { InlineConfirm } from "../ui/InlineConfirm";
import type { MenuEntry } from "../ui/Menu";
import { useClickAway } from "../ui/useClickAway";
import { ConfigTable, type Column } from "../ui/table/ConfigTable";
import { matchesListSearch } from "../utils/list_search";
import {
  ADD_HELP_KEYS, ADD_TITLE_KEYS, CONTROL_MODE_LABEL_KEYS, EDITOR_SCOPES, MesaImport, ProfileEditor, SCOPE_LABEL_KEYS, deleteProfile, isEnforced, profileSource, rawControlMode,
  resetProfileEditorDisclosures, tagsOf,
} from "./MesaView";

const CONTROL_FILTERS = ["confirm", "prohibited", "read_only", "autonomous", "inherited"] as const;

const scopeLabel = (scope: MesaProfileScope): string => t(SCOPE_LABEL_KEYS[scope]);
const controlLabel = (mode: string): string => t(CONTROL_MODE_LABEL_KEYS[mode] ?? "mesa.modeInherited");
/** Each control mode's sign, so a narrow column still tells the modes apart without its color. */
const CONTROL_ICONS: Record<string, IconName> = { autonomous: "success", confirm: "pending", read_only: "preview", prohibited: "denied" };

/** A control mode in its meaning color, with its sign; an inherited mode has neither. */
function ControlLabel({ mode, children }: { mode: string; children: ReactNode }) {
  const icon = CONTROL_ICONS[mode];
  return (
    <span className={`phx-outcome mesa-control mesa-control-${mode}`}>
      {icon && <Icon name={icon} size={14} />}
      <span>{children}</span>
    </span>
  );
}

/** The control mode a profile sets, with its enforcement when it enforces. */
function ControlMode({ row }: { row: ProfileRow }) {
  const mode = rawControlMode(row.document);
  return (
    <ControlLabel mode={mode}>
      {controlLabel(mode)}
      {isEnforced(row.document) && <>{" · "}{t("mesa.modeEnforced")}</>}
    </ControlLabel>
  );
}

/** How a profile's control reads in one string, for sorting and for a row's name. */
const controlText = (row: ProfileRow): string =>
  `${controlLabel(rawControlMode(row.document))}${isEnforced(row.document) ? ` ${t("mesa.modeEnforced")}` : ""}`;

/**
 * A row's own action, asking in place before it acts: the button turns red and
 * is answered beside it, on the same line, and a click anywhere else cancels,
 * as Delete Permanently does in Archived Access Keys (user, 2026-10-30).
 */
function RowAction({ id, label, confirmLabel, icon, busy, confirming, boxRef, guide, help, onAsk, onConfirm }: {
  id: string;
  label: string;
  /** The Help key for the action, shown over it and over its Cancel. */
  help?: string;
  confirmLabel: string;
  icon: "delete" | "close";
  busy: boolean;
  confirming: string | null;
  /** The window's own click-away box, given to the row that is asking. */
  boxRef: RefObject<HTMLSpanElement | null>;
  /** The Guide section about this action. */
  guide: string;
  onAsk: (id: string | null) => void;
  onConfirm: () => void;
}) {
  const asked = confirming === id;
  return (
    // Acting on a row never chooses it: the list's own choice stays where it is.
    <span className="row-actions" ref={asked ? boxRef : undefined} data-guide={guide} onClick={(e) => e.stopPropagation()}>
      <ActionButton icon={icon} label={asked ? t("common.confirm") : label} name={asked ? confirmLabel : undefined} busy={busy} help={help}
        className={asked ? "btn btn-danger btn-sm" : "btn btn-ghost btn-sm"}
        onClick={() => (asked ? onConfirm() : onAsk(id))} />
      {asked && <ActionButton icon="undo" label={t("common.cancel")} className="btn btn-outline btn-sm" busy={busy} help={help} onClick={() => onAsk(null)} />}
    </span>
  );
}

/** What a window says when it holds nothing, with the way out of it. */
function NothingHere({ window: windowId, text, floating = false }: { window: string; text: string; floating?: boolean }) {
  const toggle = useWindowToggle(windowId, floating);
  return (
    <div className="phx-window-empty phx-window-nothing">
      <p>{text}</p>
      {toggle?.open && <ActionButton icon="close" label={t("common.close")} className="btn btn-outline btn-sm" onClick={toggle.toggle} />}
    </div>
  );
}

const BY_ID = { column: "id", dir: "asc" } as const;

export function MesaProfilesWindow() {
  const m = useMesa();
  const ensureOpen = useEnsureOpen();
  const suggestions = useWindowToggle("mesaSuggestions");
  const [search, setSearch] = useState("");
  const [scope, setScope] = useState("");
  const [control, setControl] = useState("");
  const [enforcement, setEnforcement] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  // Importing and exporting both open in place at the top of the list (user, 2026-10-30).
  const [importing, setImporting] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // One click-away for the whole list: at most one row is ever asking.
  const confirmBox = useRef<HTMLSpanElement | null>(null);
  useClickAway(confirmBox, () => setConfirmId(null), !!confirmId && !deleting);
  // Hidden filters stop applying and come back as they were (user, 2026-10-27); the search is not one of them.
  const filtersApply = !useOnToolbar() || filtersOpen;
  const activeFilters = filtersApply ? (scope ? 1 : 0) + (control ? 1 : 0) + (enforcement ? 1 : 0) : 0;
  // Resetting the workspace clears what it had chosen, as it clears the arrangement.
  useWorkspaceReset(() => { m?.open(null); m?.selectSuggestion(null); resetProfileEditorDisclosures(); });

  const openNew = (s: MesaProfileScope) => { m?.open({ scope: s, key: null, isNew: true }); ensureOpen("mesaEditor"); };
  const showImport = (open: boolean) => { setImporting(open); if (open) m?.cancelExport(); };
  const actionsMenu = (): MenuEntry[] => [
    { id: "import", label: t("mesa.importTitle"), toggle: true, checked: importing, help: t("mesa.importMenuHelp"), onSelect: () => showImport(!importing) },
    { id: "export", label: t("mesa.exportTitle"), toggle: true, checked: !!m?.exporting, disabled: !m?.rows.length, help: t("mesa.exportMenuHelp"),
      onSelect: () => { if (m?.exporting) m.cancelExport(); else { setImporting(false); m?.askExport(); } } },
    "separator",
    ...(suggestions ? [{ id: "suggestions", label: t("mesa.suggestTitle"), toggle: true, checked: suggestions.open, help: t("mesa.suggestionsMenuHelp"), onSelect: suggestions.toggle }] : []),
    "separator",
    { id: "rescan", label: t("mesa.rescan"), help: t("mesa.rescanHelp"), onSelect: () => { void m?.rescan(); } },
    { id: "restore", label: t("mesa.restoreDismissed"), help: t("mesa.restoreDismissedHelp"), disabled: !m?.issues.dismissed_suggestions.length, onSelect: () => { void m?.restoreSuggestions(); } },
    // Findings dismissed in Advisory come back from here, as dismissed suggestions do (user, 2026-10-30).
    { id: "restore-notices", label: t("mesa.restoreNotices"), help: t("mesa.restoreNoticesHelp"), disabled: !m?.anyDismissed, onSelect: () => m?.restoreNotices() },
  ];
  const groups: ToolGroup[] = m ? [{
    id: "mesa",
    label: t("shell.tabMesa"),
    tools: [
      { id: "add", label: t("mesa.addProfile"), icon: "add", help: "mesa.addProfileHelp",
        menu: () => EDITOR_SCOPES.map((s) => ({ id: s, label: t(ADD_TITLE_KEYS[s]), help: t(ADD_HELP_KEYS[s]), onSelect: () => openNew(s) })) },
      { id: "filters", label: activeFilters ? t("logs.filtersCount", { count: localeNumber(activeFilters) }) : t("logs.filters"), short: t("logs.filters"), icon: "filter", help: "mesa.filtersHelp",
        pressed: filtersOpen, badge: activeFilters || undefined,
        // Opening the filters puts Import and Export away, so the list is what is under them.
        onSelect: () => { if (!filtersOpen) { setImporting(false); m?.cancelExport(); } setFiltersOpen((open) => !open); } },
      { id: "actions", label: t("mesa.menu"), icon: "more", help: "mesa.actionsHelp", menu: actionsMenu },
    ],
  }] : [];
  const onToolbar = useToolbarTools(groups);
  if (!m) return null;

  const showFilters = !onToolbar || filtersOpen;
  /**
   * Showing other profiles leaves nothing chosen in the list, so Profile Editor
   * does not keep the one it had (user, 2026-10-29).
   */
  const forget = () => { if (m.editing) m.open(null); };
  const clearFilters = () => { forget(); setScope(""); setControl(""); setEnforcement(""); };
  // The control-mode filter keeps the old summary's counts.
  const count = (mode: string) => m.rows.filter((r) => (mode === "enforced" ? isEnforced(r.document) : rawControlMode(r.document) === mode)).length;

  async function remove(row: ProfileRow) {
    setDeleting(row.id);
    setDeleteError(null);
    try {
      await deleteProfile(row.scope, row.key);
      if (m && m.editing && !m.editing.isNew && m.editing.scope === row.scope && m.editing.key === row.key) m.open(null);
      m?.refresh();
    } catch (e: unknown) {
      setDeleteError(e instanceof Error ? e.message : t("mesa.deleteProfileFailed"));
    } finally {
      setDeleting(null);
      setConfirmId(null);
    }
  }

  const shown = m.rows.filter((r) => (!filtersApply || ((!scope || r.scope === scope)
    && (!control || rawControlMode(r.document) === control)
    && (!enforcement || (enforcement === "enforced") === isEnforced(r.document))))
    && matchesListSearch(`${r.name} ${r.key} ${scopeLabel(r.scope)} ${tagsOf(r.document).join(" ")}`, search));
  // ID, Name, Control Mode, Type, Actions, sorted by ID (user, 2026-10-30). Each column is as
  // wide as what it holds, up to a ceiling, and Actions always fits Confirm beside Cancel: a
  // narrow window scrolls the list sideways rather than cutting Name and Type short.
  const columns: Column<ProfileRow>[] = [
    { id: "id", label: t("mesa.colId"), width: 140, maxWidth: 260, sortValue: (r) => r.key, title: (r) => r.key, cell: (r) => <code>{r.key}</code> },
    { id: "name", label: t("mesa.colName"), width: 120, maxWidth: 240, sortValue: (r) => (r.name || "").toLowerCase(), title: (r) => r.name, cell: (r) => r.name },
    { id: "control", label: t("mesa.colControl"), width: 140, maxWidth: 180, sortValue: controlText, cell: (r) => <ControlMode row={r} /> },
    { id: "scope", label: t("mesa.colAppliesTo"), width: 90, maxWidth: 130, sortValue: (r) => scopeLabel(r.scope), cell: (r) => scopeLabel(r.scope) },
    { id: "tags", label: t("mesa.colTags"), width: 180, maxWidth: 300, hidden: true, sortValue: (r) => tagsOf(r.document).join(" "), cell: (r) => tagsOf(r.document).join(t("common.listSeparator")) },
    { id: "source", label: t("mesa.colSource"), width: 100, hidden: true, sortValue: (r) => profileSource(r.document),
      cell: (r) => (profileSource(r.document) === "developer" ? <span title={t("mesa.vendorTitle")}>{t("mesa.vendorBadge")}</span> : null) },
    { id: "actions", label: t("mesa.colActions"), width: 205, maxWidth: 205, className: "phx-td-actions",
      cell: (r) => (
        <RowAction id={r.id} icon="delete" label={t("mesa.delete")} confirmLabel={t("mesa.confirmDelete")} busy={deleting === r.id}
          confirming={confirmId} boxRef={confirmBox} guide="window.mesaProfiles#delete" help="mesa.deleteHelp" onAsk={setConfirmId} onConfirm={() => { void remove(r); }} />
      ) },
  ];
  const editing = m.editing && !m.editing.isNew ? `${m.editing.scope}:${m.editing.key}` : null;

  return (
    <div className="phx-list-view mesa-profiles-view">
      {!onToolbar && <InlineTools groups={groups} />}
      {m.exporting && (
        <div data-guide="window.mesaProfiles#export">
        <InlineConfirm label={t("mesa.exportTitle")} confirmLabel={t("mesa.export")} confirmIcon="export" cancelLabel={t("common.cancel")}
          confirmClassName="btn btn-outline btn-sm" help={`${tn("mesa.exportBody", m.rows.length)} ${t("mesa.exportNote")}`}
          busy={m.exporting.busy} error={m.exporting.error} onConfirm={() => { void m.doExport(); }} onCancel={m.cancelExport} />
        </div>
      )}
      {importing && (
        <div className="phx-inline-panel mesa-import-panel" data-guide="window.mesaProfiles#import">
          <div className="phx-inline-panel-head">
            <h3 className="phx-section-title">{t("mesa.importTitle")}</h3>
          </div>
          <MesaImport onImported={() => m.refresh()} onCancel={() => showImport(false)} />
        </div>
      )}
      <div className="phx-filters" hidden={!showFilters} data-guide="window.mesaProfiles#filters">
        <div className="filter-row">
          <select className="input input-auto" data-filter="scope" data-help="mesa.filterTypeHelp" value={scope} onChange={(e) => { forget(); setScope(e.target.value); }} aria-label={t("mesa.colAppliesTo")}>
            <option value="">{t("mesa.allTypes")}</option>
            {EDITOR_SCOPES.map((s) => <option key={s} value={s}>{scopeLabel(s)}</option>)}
          </select>
          <select className="input input-auto" data-filter="control" data-help="mesa.filterControlHelp" value={control} onChange={(e) => { forget(); setControl(e.target.value); }} aria-label={t("mesa.colControl")}>
            <option value="">{t("mesa.allControlModes")}</option>
            {CONTROL_FILTERS.map((mode) => <option key={mode} value={mode}>{controlLabel(mode)} ({localeNumber(count(mode))})</option>)}
          </select>
          <select className="input input-auto" data-filter="enforcement" data-help="mesa.filterEnforcementHelp" value={enforcement} onChange={(e) => { forget(); setEnforcement(e.target.value); }} aria-label={t("mesa.fieldEnforcement")}>
            <option value="">{t("mesa.anyEnforcement")}</option>
            <option value="enforced">{t("mesa.modeEnforced")} ({localeNumber(count("enforced"))})</option>
            <option value="advisory">{t("mesa.modeAdvisory")} ({localeNumber(m.rows.length - count("enforced"))})</option>
          </select>
          {activeFilters > 0 && <ActionButton icon="clearFilters" label={t("logs.clearFilters")} help="mesa.clearFiltersHelp" onClick={clearFilters} />}
        </div>
        <div className="filter-row">
          <input type="search" className="input" data-help="mesa.searchHelp" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder={t("mesa.searchPlaceholder")} aria-label={t("mesa.searchAria")} />
          <ResultCount count={shown.length} query={`${search}|${control}|${enforcement}|${scope}`} />
        </div>
      </div>
      {m.error && <ErrorMsg msg={m.error} />}
      {deleteError && <ErrorMsg msg={deleteError} />}
      {!m.loaded && m.loading && <Loading />}
      <div className="mesa-table" data-guide="window.mesaProfiles#list">
      <ConfigTable
        id="mesaProfiles"
        label={t("workspace.mesaProfiles")}
        defaultSort={BY_ID}
        columns={columns}
        rows={shown}
        rowKey={(r) => r.id}
        selectedKey={editing}
        onSelect={(r) => { m.open({ scope: r.scope, key: r.key, isNew: false }); ensureOpen("mesaEditor"); }}
        rowLabel={(r) => `${r.key}, ${r.name || ""}, ${controlText(r)}, ${scopeLabel(r.scope)}`}
        rowHelp="mesa.profileRowHelp"
        empty={!m.loaded ? null : <p className="audit-empty">{m.rows.length ? t("mesa.noMatch") : t("mesa.emptyState")}</p>}
      />
      </div>
    </div>
  );
}

export function ProfileEditorWindow() {
  const m = useMesa();
  if (!m) return null;
  const e = m.editing;
  if (!e) return <p className="phx-window-empty">{t("mesa.editorEmpty")}</p>;
  return (
    <div className="phx-window-pad">
      <ProfileEditor
        key={e.nonce}
        variant="window"
        scope={e.scope}
        profileKey={e.key}
        isNew={e.isNew}
        entityTree={m.entityTree}
        canonicalTags={m.canonicalTags}
        integrationOptions={m.integrationOptions}
        deviceOptions={m.deviceOptions}
        seedControlMode={e.seedControlMode}
        seedReason={e.seedReason}
        taken={m.keysIn(e.scope)}
        lockedKey={e.lockedKey}
        keyLabel={e.key ? m.nameOf(e.scope, e.key) || undefined : undefined}
        onClose={() => m.open(null)}
        onSaved={m.refresh}
        onShowSaved={(key) => m.open({ scope: e.scope, key, isNew: false })}
      />
    </div>
  );
}

export function SuggestedProfilesWindow() {
  const m = useMesa();
  const ensureOpen = useEnsureOpen();
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const confirmBox = useRef<HTMLSpanElement | null>(null);
  useClickAway(confirmBox, () => setConfirmId(null), !!confirmId);
  if (!m) return null;
  const list = m.issues.suggestions;
  // Choosing a suggestion opens it in the editor, already filled in (user, 2026-10-30).
  const review = (s: MesaSuggestion) => {
    m.open({
      scope: s.scope === "domain" ? "domain" : "entity",
      key: s.subject_id,
      isNew: true,
      seedControlMode: s.suggested_mode,
      seedReason: s.reason,
      // The subject comes from a live backend scan, which can be ahead of the
      // registry snapshot the editor validates against; it is a fact, so the
      // editor takes it as given, as the injected buttons do.
      lockedKey: true,
    });
    ensureOpen("mesaEditor");
  };
  const mode = (s: MesaSuggestion) => (MODE_LABEL_KEYS[s.suggested_mode] ? t(MODE_LABEL_KEYS[s.suggested_mode]) : s.suggested_mode);
  const columns: Column<MesaSuggestion>[] = [
    { id: "subject", label: t("mesa.colTarget"), width: 140, maxWidth: 260, sortValue: (s) => s.subject_id,
      cell: (s) => <code>{s.scope === "domain" ? t("mesa.suggestDomainSubject", { id: s.subject_id }) : s.subject_id}</code> },
    { id: "name", label: t("mesa.colName"), width: 120, maxWidth: 240, sortValue: (s) => m.friendly(s.subject_id).toLowerCase(), cell: (s) => m.friendly(s.subject_id) },
    { id: "mode", label: t("mesa.colControl"), width: 140, maxWidth: 180, sortValue: (s) => s.suggested_mode,
      cell: (s) => <ControlLabel mode={s.suggested_mode}>{mode(s)}</ControlLabel> },
    { id: "signal", label: t("mesa.colSignal"), width: 100, maxWidth: 140, sortValue: (s) => s.signal, cell: (s) => (SIGNAL_LABEL_KEYS[s.signal] ? t(SIGNAL_LABEL_KEYS[s.signal]) : s.signal) },
    { id: "actions", label: t("mesa.colActions"), width: 205, maxWidth: 205, className: "phx-td-actions",
      cell: (s) => (
        <RowAction id={s.key} icon="close" label={t("mesa.dismiss")} confirmLabel={t("mesa.confirmDismiss")} busy={m.suggestionBusy === s.key}
          confirming={confirmId} boxRef={confirmBox} guide="window.mesaSuggestions#dismiss" help="mesa.dismissSuggestionHelp" onAsk={setConfirmId} onConfirm={() => { void m.dismissSuggestion(s); setConfirmId(null); }} />
      ) },
  ];
  return (
    <div className="phx-list-view mesa-suggestions-view" data-help="mesa.suggestSub">
      {m.suggestionError && <ErrorMsg msg={m.suggestionError} />}
      <div className="mesa-table" data-guide="window.mesaSuggestions#review">
      <ConfigTable
        id="mesaSuggestions"
        label={t("mesa.suggestTitle")}
        columns={columns}
        rows={list}
        rowKey={(s) => s.key}
        selectedKey={m.selectedSuggestion}
        onSelect={(s) => { m.selectSuggestion(s.key); review(s); }}
        // Why it was suggested reads in Help while the row is under the pointer (user, 2026-10-30).
        rowLabel={(s) => `${s.subject_id}, ${mode(s)}. ${suggestionReason(s)}`}
        empty={<NothingHere window="mesaSuggestions" text={t("mesa.noSuggestionsNow")} />}
      />
      </div>
    </div>
  );
}

/** One group of orphaned profiles: its count line, then each profile's target, which opens its stored profile. */
function OrphanGroup({ m, scope, ids, textKey, onOpen }: { m: MesaState; scope: MesaProfileScope; ids: string[]; textKey: string; onOpen?: (scope: MesaProfileScope, key: string) => void }) {
  if (!ids.length) return null;
  return (
    <div className="mesa-orphan-group">
      {tRich(tnKey(textKey, ids.length), { strong: (c) => <strong>{c}</strong> }, { count: ids.length })}
      <ul className="mesa-notice-identities">
        {ids.map((id) => (
          <li key={id}>
            {onOpen ? (
              <button type="button" className="row-link-btn row-link-btn-stack" data-help="mesa.openOrphanHelp" onClick={() => onOpen(scope, id)}>
                <MesaIdentity id={id} name={m.nameOf(scope, id)} />
              </button>
            ) : <MesaIdentity id={id} name={m.nameOf(scope, id)} />}
          </li>
        ))}
      </ul>
    </div>
  );
}

function OrphanGroups({ m, onOpen }: { m: MesaState; onOpen?: (scope: MesaProfileScope, key: string) => void }) {
  return (
    <>
      <OrphanGroup m={m} scope="entity" ids={m.issues.orphans} textKey="mesa.orphanEntities" onOpen={onOpen} />
      <OrphanGroup m={m} scope="device" ids={m.issues.orphan_devices} textKey="mesa.orphanDevices" onOpen={onOpen} />
      <OrphanGroup m={m} scope="area" ids={m.issues.orphan_areas} textKey="mesa.orphanAreas" onOpen={onOpen} />
      <OrphanGroup m={m} scope="integration" ids={m.issues.orphan_integrations} textKey="mesa.orphanIntegrations" onOpen={onOpen} />
    </>
  );
}

/**
 * A finding, in a plain bordered box like every other UI2 item. Dismissing one
 * is a named button under it rather than a cross in its corner (user,
 * 2026-10-30), and it is permanent, per account, and changes nothing in MESA.
 */
function Notice({ tone, label, guide, onDismiss, children }: { tone: "info" | "warn"; label: string; guide: string; onDismiss?: () => void; children: ReactNode }) {
  return (
    <div className={`mesa-notice mesa-notice-${tone}`} data-guide={guide}>
      {tone === "warn" && <Icon name="warning" size={16} className="mesa-notice-icon" />}
      <div className="mesa-notice-content">
        {children}
        {onDismiss && (
          // Named in words whatever the toolbar's display mode: a cross alone was what
          // the user asked to be rid of here (user, 2026-10-30).
          <DisplayModeContext.Provider value="icon-text">
            <div className="mesa-notice-actions" data-guide="window.mesaAdvisory#dismiss">
              <ActionButton icon="close" label={t("mesa.dismiss")} name={label} help="mesa.dismissNoticeHelp" className="btn btn-outline btn-sm" onClick={onDismiss} />
            </div>
          </DisplayModeContext.Provider>
        )}
      </div>
    </div>
  );
}

function InspectionNotice({ m, label, onDismiss }: { m: MesaState; label: string; onDismiss: () => void }) {
  return (
    <Notice tone="info" label={label} guide="window.mesaAdvisory#inspection" onDismiss={onDismiss}>
      <p>{tn("mesa.partialInspection", m.inspection.length)}</p>
      <p>{t("mesa.advisoryOnly")}</p>
      <details className="mesa-notice-details">
        <summary data-help="mesa.viewAutomationsHelp">{t("mesa.viewAutomationDetails")}</summary>
        <ul>
          {m.inspection.map((id) => <li key={id}><MesaIdentity id={id} name={m.automationName(id)} /></li>)}
        </ul>
      </details>
    </Notice>
  );
}

function ValidationNotice({ m, label, onDismiss }: { m: MesaState; label: string; onDismiss: () => void }) {
  return (
    <Notice tone="warn" label={label} guide="window.mesaAdvisory#validation" onDismiss={onDismiss}>
      <strong>{t("mesa.triggerIssues", { count: m.validation.length })}</strong>
      <ul>
        {m.validation.map((i: MesaValidationIssue, idx) => (
          <li key={idx}>
            <strong>{i.severity === "error" ? t("mesa.validationError") : t("mesa.validationWarning")}</strong>{" "}
            {i.role === "configuration"
              ? tRich("mesa.validationAutomation", { code: (c) => <MesaIdentity id={c} name={m.automationName(c)} /> }, { automationId: i.automation_id })
              : tRich("mesa.triggerIssueRow", { code: (c) => c === i.entity_id ? <MesaIdentity id={c} name={m.friendly(c)} /> : <code>{c}</code>, automation: (c) => <MesaIdentity id={c} name={m.automationName(c)} /> }, { entityId: i.entity_id, declared: i.declared_value, automationId: i.automation_id, role: i.role })}
            <div>{i.recommendation}</div>
          </li>
        ))}
      </ul>
    </Notice>
  );
}

/**
 * Everything MESA found that has not been dismissed, in one window: automations
 * it could only partly inspect, trigger declarations that do not match, and
 * profiles whose target is gone. Each finding is dismissed from here, and the
 * MESA Actions menu shows dismissed ones again; nothing here changes MESA
 * itself (user, 2026-10-30, one window instead of Advisory, Orphaned Profiles
 * and Diagnostics).
 */
export function AdvisoryWindow() {
  const m = useMesa();
  const ensureOpen = useEnsureOpen();
  if (!m) return null;
  const { inspection, validation, orphans } = m.findingIds;
  const shown = (ids: string[]) => ids.length > 0 && !m.allDismissed(ids);
  if (!shown(inspection) && !shown(validation) && !shown(orphans)) {
    return <NothingHere window="mesaAdvisory" floating text={t("mesa.noAdvisoriesNow")} />;
  }
  return (
    <div className="phx-window-pad mesa-findings">
      {shown(inspection) && <InspectionNotice m={m} label={t("mesa.dismissInspectionNotice")} onDismiss={() => m.dismiss(inspection)} />}
      {shown(validation) && <ValidationNotice m={m} label={t("mesa.dismissValidationNotice")} onDismiss={() => m.dismiss(validation)} />}
      {shown(orphans) && (
        <Notice tone="info" label={t("mesa.dismissOrphanNotice")} guide="window.mesaAdvisory#orphans" onDismiss={() => m.dismiss(orphans)}>
          <OrphanGroups m={m} onOpen={(scope, key) => { m.open({ scope, key, isNew: false }); ensureOpen("mesaEditor"); }} />
          <p>{t("mesa.advisoryOnly")}</p>
        </Notice>
      )}
    </div>
  );
}
