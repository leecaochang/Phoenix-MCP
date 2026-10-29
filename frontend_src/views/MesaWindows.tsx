/**
 * The MESA workspace's windows, around the shared state in `shell/mesa.tsx`:
 * Profiles (every profile at every scope in one list), Profile Editor (the
 * shared editor as a window), Suggested Profiles, the conditional Orphaned
 * Profiles and Advisory windows, Diagnostics with every finding, and Import.
 * The workspace's tools (Add Profile, Refresh, Suggested Profiles, Filters and
 * the MESA Actions menu) come with Profiles.
 */

import { useState, type ReactNode } from "react";
import type { MesaProfileScope, MesaSuggestion, MesaValidationIssue } from "../types";
import { ErrorMsg, Loading } from "../components/common";
import { MesaIdentity } from "../components/MesaIdentity";
import { MODE_LABEL_KEYS, SIGNAL_LABEL_KEYS, suggestionReason } from "../components/MesaSuggestions";
import { localeNumber, t, tn, tnKey } from "../i18n";
import { tRich } from "../i18n/rich";
import { useMesa, type MesaState, type ProfileRow } from "../shell/mesa";
import { useOnToolbar, useToolbarTools, type ToolGroup } from "../shell/tools";
import { InlineTools, useEnsureOpen, useWindowToggle } from "../shell/windowTools";
import { ActionButton } from "../ui/ActionButton";
import { Icon } from "../ui/Icon";
import { InlineConfirm } from "../ui/InlineConfirm";
import type { MenuEntry } from "../ui/Menu";
import { ConfigTable, type Column } from "../ui/table/ConfigTable";
import { matchesListSearch } from "../utils/list_search";
import {
  ADD_TITLE_KEYS, CONTROL_MODE_LABEL_KEYS, EDITOR_SCOPES, MesaImport, ProfileEditor, SCOPE_LABEL_KEYS, isEnforced, profileSource, rawControlMode, tagsOf,
} from "./MesaView";

const CONTROL_FILTERS = ["confirm", "prohibited", "read_only", "autonomous", "inherited"] as const;

const scopeLabel = (scope: MesaProfileScope): string => t(SCOPE_LABEL_KEYS[scope]);
const controlLabel = (mode: string): string => t(CONTROL_MODE_LABEL_KEYS[mode] ?? "mesa.modeInherited");

/** The configured control mode, as the list shows it under a profile's name. */
function ControlMode({ row }: { row: ProfileRow }) {
  const mode = rawControlMode(row.document);
  return (
    <span className={`mesa-control mesa-control-${mode}`}>
      {controlLabel(mode)}
      {isEnforced(row.document) && <>{" · "}{t("mesa.modeEnforced")}</>}
    </span>
  );
}

export function MesaProfilesWindow() {
  const m = useMesa();
  const ensureOpen = useEnsureOpen();
  const suggestions = useWindowToggle("mesaSuggestions");
  const [search, setSearch] = useState("");
  const [scope, setScope] = useState("");
  const [control, setControl] = useState("");
  const [enforcement, setEnforcement] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  // Hidden filters stop applying and come back as they were (user, 2026-10-27); the search is not one of them.
  const filtersApply = !useOnToolbar() || filtersOpen;
  const activeFilters = filtersApply ? (scope ? 1 : 0) + (control ? 1 : 0) + (enforcement ? 1 : 0) : 0;

  const openNew = (s: MesaProfileScope) => { m?.open({ scope: s, key: null, isNew: true }); ensureOpen("mesaEditor"); };
  const actionsMenu = (): MenuEntry[] => [
    { id: "import", label: t("mesa.importTitle"), onSelect: () => ensureOpen("mesaImport") },
    { id: "export", label: t("mesa.exportTitle"), disabled: !m?.rows.length, onSelect: () => m?.askExport() },
    { id: "diagnostics", label: t("workspace.diagnostics"), onSelect: () => ensureOpen("mesaDiagnostics") },
    "separator",
    { id: "restore", label: t("mesa.restoreDismissed"), disabled: !m?.issues.dismissed_suggestions.length, onSelect: () => { void m?.restoreSuggestions(); } },
  ];
  const groups: ToolGroup[] = m ? [{
    id: "mesa",
    label: t("shell.tabMesa"),
    tools: [
      { id: "add", label: t("mesa.addProfile"), icon: "add", menu: () => EDITOR_SCOPES.map((s) => ({ id: s, label: t(ADD_TITLE_KEYS[s]), onSelect: () => openNew(s) })) },
      { id: "refresh", label: t("workspace.refreshWindow", { window: t("shell.tabMesa") }), short: t("common.refresh"), icon: "refresh", onSelect: m.refresh },
      ...(suggestions ? [{ id: "suggestions", label: t("mesa.suggestTitle"), icon: "suggestions" as const, pressed: suggestions.open, onSelect: suggestions.toggle }] : []),
      { id: "filters", label: activeFilters ? t("logs.filtersCount", { count: localeNumber(activeFilters) }) : t("logs.filters"), short: t("logs.filters"), icon: "filter",
        pressed: filtersOpen, badge: activeFilters || undefined, onSelect: () => setFiltersOpen((open) => !open) },
      { id: "actions", label: t("mesa.menu"), icon: "more", menu: actionsMenu },
    ],
  }] : [];
  const onToolbar = useToolbarTools(groups);
  if (!m) return null;

  const showFilters = !onToolbar || filtersOpen;
  /**
   * Showing other profiles leaves nothing chosen in the list, so Profile Editor
   * does not keep the one it had (user, 2026-10-29). An editor holding unsaved
   * changes is left alone: a filter is not a reason to lose them.
   */
  const forget = () => { if (m.editing && !m.editorDirty()) m.open(null, { force: true }); };
  const clearFilters = () => { forget(); setScope(""); setControl(""); setEnforcement(""); };
  // The control-mode filter keeps the old summary's counts.
  const count = (mode: string) => m.rows.filter((r) => (mode === "enforced" ? isEnforced(r.document) : rawControlMode(r.document) === mode)).length;

  const shown = m.rows.filter((r) => (!filtersApply || ((!scope || r.scope === scope)
    && (!control || rawControlMode(r.document) === control)
    && (!enforcement || (enforcement === "enforced") === isEnforced(r.document))))
    && matchesListSearch(`${r.name} ${r.key} ${scopeLabel(r.scope)} ${tagsOf(r.document).join(" ")}`, search));
  const columns: Column<ProfileRow>[] = [
    {
      id: "name", label: t("mesa.colName"), width: 260, maxWidth: 360, sortValue: (r) => r.name || r.key, title: (r) => r.key,
      // The compact default: the familiar name, with the configured control and the exact ID under it.
      cell: (r) => (
        <span className="mesa-profile-cell">
          <span className="mesa-profile-name">{r.name || r.key}</span>
          <span className="conversation-secondary"><ControlMode row={r} />{r.name && r.name !== r.key && <> · <code>{r.key}</code></>}</span>
        </span>
      ),
    },
    { id: "scope", label: t("mesa.colAppliesTo"), width: 120, sortValue: (r) => scopeLabel(r.scope), cell: (r) => scopeLabel(r.scope) },
    { id: "control", label: t("mesa.colControl"), width: 130, hidden: true, sortValue: (r) => controlLabel(rawControlMode(r.document)), cell: (r) => controlLabel(rawControlMode(r.document)) },
    { id: "enforcement", label: t("mesa.fieldEnforcement"), width: 120, hidden: true, sortValue: (r) => (isEnforced(r.document) ? 1 : 0),
      cell: (r) => t(isEnforced(r.document) ? "mesa.modeEnforced" : "mesa.modeAdvisory") },
    { id: "tags", label: t("mesa.colTags"), width: 180, maxWidth: 300, hidden: true, sortValue: (r) => tagsOf(r.document).join(" "), cell: (r) => tagsOf(r.document).join(t("common.listSeparator")) },
    { id: "source", label: t("mesa.colSource"), width: 100, hidden: true, sortValue: (r) => profileSource(r.document),
      cell: (r) => (profileSource(r.document) === "developer" ? <span title={t("mesa.vendorTitle")}>{t("mesa.vendorBadge")}</span> : null) },
    { id: "id", label: t("mesa.colId"), width: 200, maxWidth: 300, hidden: true, sortValue: (r) => r.key, cell: (r) => <code>{r.key}</code> },
  ];
  const editing = m.editing && !m.editing.isNew ? `${m.editing.scope}:${m.editing.key}` : null;

  return (
    <div className="phx-list-view mesa-profiles-view">
      {!onToolbar && <InlineTools groups={groups} />}
      {m.exporting && (
        <InlineConfirm label={t("mesa.exportTitle")} confirmLabel={t("mesa.export")} confirmIcon="export" cancelLabel={t("mesa.cancel")}
          busy={m.exporting.busy} error={m.exporting.error} onConfirm={() => { void m.doExport(); }} onCancel={m.cancelExport}>
          <p>{tn("mesa.exportBody", m.rows.length)}</p>
          <p>{t("mesa.exportNote")}</p>
        </InlineConfirm>
      )}
      <div className="phx-filters" hidden={!showFilters}>
        <div className="filter-row">
          <select className="input input-auto" data-filter="scope" value={scope} onChange={(e) => { forget(); setScope(e.target.value); }} aria-label={t("mesa.colAppliesTo")}>
            <option value="">{t("mesa.allTypes")}</option>
            {EDITOR_SCOPES.map((s) => <option key={s} value={s}>{scopeLabel(s)}</option>)}
          </select>
          <select className="input input-auto" data-filter="control" value={control} onChange={(e) => { forget(); setControl(e.target.value); }} aria-label={t("mesa.colControl")}>
            <option value="">{t("mesa.allControlModes")}</option>
            {CONTROL_FILTERS.map((mode) => <option key={mode} value={mode}>{controlLabel(mode)} ({localeNumber(count(mode))})</option>)}
          </select>
          <select className="input input-auto" data-filter="enforcement" value={enforcement} onChange={(e) => { forget(); setEnforcement(e.target.value); }} aria-label={t("mesa.fieldEnforcement")}>
            <option value="">{t("mesa.anyEnforcement")}</option>
            <option value="enforced">{t("mesa.modeEnforced")} ({localeNumber(count("enforced"))})</option>
            <option value="advisory">{t("mesa.modeAdvisory")} ({localeNumber(m.rows.length - count("enforced"))})</option>
          </select>
          {activeFilters > 0 && <ActionButton icon="clearFilters" label={t("logs.clearFilters")} onClick={clearFilters} />}
        </div>
        <div className="filter-row">
          <input type="search" className="input" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder={t("mesa.searchPlaceholder")} aria-label={t("mesa.searchAria")} />
        </div>
      </div>
      {m.error && <ErrorMsg msg={m.error} />}
      {!m.loaded && m.loading && <Loading />}
      <ConfigTable
        id="mesaProfiles"
        label={t("workspace.mesaProfiles")}
        columns={columns}
        rows={shown}
        rowKey={(r) => r.id}
        selectedKey={editing}
        onSelect={(r) => { m.open({ scope: r.scope, key: r.key, isNew: false }); ensureOpen("mesaEditor"); }}
        rowLabel={(r) => `${r.name || r.key}, ${scopeLabel(r.scope)}, ${controlLabel(rawControlMode(r.document))}`}
        empty={!m.loaded ? null : <p className="audit-empty">{m.rows.length ? t("mesa.noMatch") : t("mesa.emptyState")}</p>}
      />
    </div>
  );
}

export function ProfileEditorWindow() {
  const m = useMesa();
  if (!m) return null;
  const e = m.editing;
  const pending = m.pending;
  if (!e) return <p className="phx-window-empty">{t("mesa.editorEmpty")}</p>;
  // Opening another profile while this one holds changes asks first.
  const notice = pending && (
    <InlineConfirm label={t("mesa.discardTitle")} confirmLabel={t("mesa.discardAndOpen")} confirmIcon="check" confirmClassName="btn btn-danger btn-sm"
      cancelLabel={t("mesa.keepEditing")} onConfirm={m.confirmSwitch} onCancel={m.cancelSwitch}>
      <p>{t("mesa.discardBody")}</p>
    </InlineConfirm>
  );
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
        lockedKey={e.lockedKey}
        keyLabel={e.key ? m.nameOf(e.scope, e.key) || undefined : undefined}
        onClose={() => m.open(null, { force: true })}
        onSaved={m.refresh}
        onShowSaved={(key) => m.open({ scope: e.scope, key, isNew: false }, { force: true })}
        onDirtyChange={m.setEditorDirty}
        notice={notice}
      />
    </div>
  );
}

/** A suggestion's subject: the entity's name over its ID, or the domain it covers. */
function SuggestionSubject({ s, m }: { s: MesaSuggestion; m: MesaState }) {
  return <MesaIdentity id={s.scope === "domain" ? t("mesa.suggestDomainSubject", { id: s.subject_id }) : s.subject_id} name={s.scope === "entity" ? m.friendly(s.subject_id) : ""} />;
}

export function SuggestedProfilesWindow() {
  const m = useMesa();
  const ensureOpen = useEnsureOpen();
  if (!m) return null;
  const list = m.issues.suggestions;
  const selected = list.find((s) => s.key === m.selectedSuggestion) ?? null;
  const busy = !!selected && m.suggestionBusy === selected.key;
  const review = (s: MesaSuggestion) => {
    m.open({
      scope: s.scope === "domain" ? "domain" : "entity",
      key: s.subject_id,
      isNew: true,
      seedControlMode: s.suggested_mode,
      // The subject comes from a live backend scan, which can be ahead of the
      // registry snapshot the editor validates against; it is a fact, so the
      // editor takes it as given, as the injected buttons do.
      lockedKey: true,
    });
    ensureOpen("mesaEditor");
  };
  const columns: Column<MesaSuggestion>[] = [
    { id: "subject", label: t("mesa.colTarget"), width: 240, maxWidth: 360, sortValue: (s) => m.friendly(s.subject_id) || s.subject_id, cell: (s) => <SuggestionSubject s={s} m={m} /> },
    { id: "mode", label: t("mesa.colSuggested"), width: 160, sortValue: (s) => s.suggested_mode, cell: (s) => (MODE_LABEL_KEYS[s.suggested_mode] ? t(MODE_LABEL_KEYS[s.suggested_mode]) : s.suggested_mode) },
    { id: "signal", label: t("mesa.colSignal"), width: 130, sortValue: (s) => s.signal, cell: (s) => (SIGNAL_LABEL_KEYS[s.signal] ? t(SIGNAL_LABEL_KEYS[s.signal]) : s.signal) },
  ];
  return (
    <div className="phx-list-view mesa-suggestions-view">
      <p className="phx-window-note">{t("mesa.suggestSub")}</p>
      <div className="phx-window-actions">
        <ActionButton icon="check" label={t("mesa.apply")} busy={busy} disabled={!selected} onClick={() => { if (selected) void m.applySuggestion(selected); }} className="btn btn-primary btn-sm" />
        <ActionButton icon="edit" label={t("mesa.review")} busy={busy} disabled={!selected} onClick={() => { if (selected) review(selected); }} className="btn btn-outline btn-sm" />
        <ActionButton icon="close" label={t("mesa.dismiss")} busy={busy} disabled={!selected} onClick={() => { if (selected) void m.dismissSuggestion(selected); }} className="btn btn-outline btn-sm" />
        <ActionButton icon="refresh" label={t("mesa.rescan")} busy={m.rescanning} onClick={() => { void m.rescan(); }} className="btn btn-outline btn-sm" />
      </div>
      {m.suggestionError && <ErrorMsg msg={m.suggestionError} />}
      <ConfigTable
        id="mesaSuggestions"
        label={t("mesa.suggestTitle")}
        columns={columns}
        rows={list}
        rowKey={(s) => s.key}
        selectedKey={selected?.key ?? null}
        onSelect={(s) => m.selectSuggestion(s.key)}
        rowLabel={(s) => `${s.subject_id}, ${MODE_LABEL_KEYS[s.suggested_mode] ? t(MODE_LABEL_KEYS[s.suggested_mode]) : s.suggested_mode}`}
        empty={<p className="audit-empty">{t("mesa.noOpenSuggestions")}</p>}
      />
      {/* The chosen suggestion's reason, in full. */}
      <div className="mesa-suggest-reason" aria-live="polite">
        {selected ? suggestionReason(selected) : list.length ? t("mesa.noSuggestionSelected") : null}
      </div>
      {m.issues.dismissed_suggestions.length > 0 && (
        <div className="mesa-suggest-footer">
          <span>{t("mesa.dismissedCount", { count: m.issues.dismissed_suggestions.length })}</span>
          <ActionButton icon="undo" label={t("mesa.restoreAll")} onClick={() => { void m.restoreSuggestions(); }} className="btn btn-text btn-sm" />
        </div>
      )}
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
              <button type="button" className="row-link-btn row-link-btn-stack" onClick={() => onOpen(scope, id)}>
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

/** A finding notice: its text and a permanent per-account dismissal, which changes nothing in MESA. */
function Notice({ tone, label, onDismiss, children }: { tone: "info" | "warn"; label: string; onDismiss?: () => void; children: ReactNode }) {
  return (
    <div className={`banner banner-${tone} mesa-notice`}>
      <div className="mesa-notice-content">{children}</div>
      {onDismiss && (
        <button type="button" className="btn btn-ghost btn-sm btn-icon mesa-notice-dismiss" onClick={onDismiss} aria-label={label} title={label}>
          <Icon name="close" size={16} />
        </button>
      )}
    </div>
  );
}

export function OrphanedProfilesWindow() {
  const m = useMesa();
  const ensureOpen = useEnsureOpen();
  if (!m) return null;
  const ids = m.findingIds.orphans;
  if (!ids.length || m.allDismissed(ids)) return <p className="phx-window-empty">{t("mesa.noFindings")}</p>;
  return (
    <div className="phx-window-pad">
      <Notice tone="info" label={t("mesa.dismissOrphanNotice")} onDismiss={() => m.dismiss(ids)}>
        <OrphanGroups m={m} onOpen={(scope, key) => { m.open({ scope, key, isNew: false }); ensureOpen("mesaEditor"); }} />
        <p>{t("mesa.advisoryOnly")}</p>
      </Notice>
    </div>
  );
}

function InspectionNotice({ m, onDismiss }: { m: MesaState; onDismiss?: () => void }) {
  return (
    <Notice tone="info" label={t("mesa.dismissInspectionNotice")} onDismiss={onDismiss}>
      <p>{tn("mesa.partialInspection", m.inspection.length)}</p>
      <p>{t("mesa.advisoryOnly")}</p>
      <details className="mesa-notice-details">
        <summary>{t("mesa.viewAutomationDetails")}</summary>
        <ul>
          {m.inspection.map((id) => <li key={id}><MesaIdentity id={id} name={m.automationName(id)} /></li>)}
        </ul>
      </details>
    </Notice>
  );
}

function ValidationNotice({ m, onDismiss }: { m: MesaState; onDismiss?: () => void }) {
  return (
    <Notice tone="warn" label={t("mesa.dismissValidationNotice")} onDismiss={onDismiss}>
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

export function AdvisoryWindow() {
  const m = useMesa();
  if (!m) return null;
  const { inspection, validation } = m.findingIds;
  const showInspection = inspection.length > 0 && !m.allDismissed(inspection);
  const showValidation = validation.length > 0 && !m.allDismissed(validation);
  if (!showInspection && !showValidation) return <p className="phx-window-empty">{t("mesa.noFindings")}</p>;
  return (
    <div className="phx-window-pad mesa-findings">
      {showInspection && <InspectionNotice m={m} onDismiss={() => m.dismiss(inspection)} />}
      {showValidation && <ValidationNotice m={m} onDismiss={() => m.dismiss(validation)} />}
    </div>
  );
}

/** Every finding, dismissed or not, for a closer look. Nothing here changes MESA. */
export function DiagnosticsWindow() {
  const m = useMesa();
  if (!m) return null;
  const any = m.inspection.length > 0 || m.validation.length > 0 || m.findingIds.orphans.length > 0;
  return (
    <div className="phx-window-pad mesa-findings">
      <p className="phx-window-note">{t("mesa.diagnosticsNote")}</p>
      {!any && <p className="phx-window-empty">{t("mesa.noFindings")}</p>}
      {m.inspection.length > 0 && <InspectionNotice m={m} />}
      {m.validation.length > 0 && <ValidationNotice m={m} />}
      {m.findingIds.orphans.length > 0 && (
        <Notice tone="info" label="">
          <OrphanGroups m={m} />
        </Notice>
      )}
    </div>
  );
}

export function MesaImportWindow() {
  const m = useMesa();
  return <MesaImport onImported={() => m?.refresh()} />;
}
