/**
 * The MESA workspace's shared state for its windows: every stored profile at
 * every scope, the findings (suggestions, orphaned profiles, inspection and
 * validation notices), the profile being edited and the confirmations that
 * span windows. It lives at shell level because a closed window's content is
 * unmounted, while opening a profile, a refresh or a dismissal must reach
 * every open window. It loads when the workspace is first shown and again
 * each time it becomes active, as the former MESA tab did on every visit.
 *
 * Profile edits keep their unsaved-changes guard across windows: opening
 * another profile while the editor holds changes asks first.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError, currentHass } from "../api";
import type {
  EntityTree as EntityTreeData, MesaIssuesResponse, MesaProfileDocument, MesaProfileListItem, MesaProfileScope, MesaSuggestion, MesaValidationIssue,
} from "../types";
import { t } from "../i18n";
import { useLatestRequest } from "../utils/latest_request";
import { exportMesaArchive, type CascadingScope } from "../views/MesaView";

export interface ProfileRow {
  /** `<scope>:<key>`, unique across scopes. */
  id: string;
  scope: MesaProfileScope;
  key: string;
  /** The familiar name, or "" when only the key is known. */
  name: string;
  document: MesaProfileDocument;
}

export interface Editing {
  scope: MesaProfileScope;
  key: string | null;
  isNew: boolean;
  seedControlMode?: string;
  lockedKey?: boolean;
}
/** An opened profile; the nonce gives each opening its own editor. */
export type Opened = Editing & { nonce: number };

/** Which conditional windows have something to show. */
export interface MesaFindings {
  orphans: boolean;
  advisory: boolean;
}

// mesa-core exposes this inspection limit through its recommendation text.
// Only the known warning is advisory; other findings retain their severity.
const PARTIAL_INSPECTION_RECOMMENDATION = "templates or blueprint inputs require host expansion; reference coverage is incomplete";

const EMPTY_ISSUES: MesaIssuesResponse = { issues: [], orphans: [], orphan_devices: [], orphan_areas: [], orphan_integrations: [], suggestions: [], dismissed_suggestions: [] };

// Dismiss exact findings, so refreshes and reordering cannot resurrect them.
// Browser preferences are scoped to the authenticated HA account.
function noticeStorageKey(): string | null {
  const owner = (currentHass() as { user?: { id?: string } } | null)?.user?.id;
  return typeof owner === "string" && owner ? `phx-mesa-dismissed:${encodeURIComponent(owner)}` : null;
}

function readDismissedNotices(key: string | null): string[] {
  try {
    const value: unknown = JSON.parse(key ? localStorage.getItem(key) ?? "[]" : "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch { return []; }
}

function domainOf(entityId: string): string {
  return entityId.split(".")[0] || "other";
}

export interface MesaState {
  loaded: boolean;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  /** Every stored profile, all five scopes. */
  rows: ProfileRow[];
  entityTree: EntityTreeData | null;
  canonicalTags: string[];
  integrationOptions: { id: string; name: string }[];
  deviceOptions: { id: string; name: string }[];
  friendly: (entityId: string) => string;
  /** The familiar name of a profile's target, or "" when only its key is known. */
  nameOf: (scope: MesaProfileScope, key: string) => string;
  automationName: (id: string) => string;

  issues: MesaIssuesResponse;
  /** Automations only partially inspected (templates or blueprints). */
  inspection: string[];
  validation: MesaValidationIssue[];
  /** The exact findings each notice covers, as dismissal keys. */
  findingIds: { inspection: string[]; validation: string[]; orphans: string[] };
  /** Whether every one of these findings has been dismissed. */
  allDismissed: (findings: string[]) => boolean;
  dismiss: (findings: string[]) => void;

  editing: Opened | null;
  /** Open a profile in Profile Editor, or close it with null; asks first while the editor holds changes, unless forced. */
  open: (next: Editing | null, opts?: { force?: boolean }) => void;
  /** What opens once the user decides about the editor's unsaved changes. */
  pending: { target: Opened | null } | null;
  confirmSwitch: () => void;
  cancelSwitch: () => void;
  setEditorDirty: (dirty: boolean) => void;
  /** Whether the editor holds unsaved changes, so nothing closes it behind the user's back. */
  editorDirty: () => boolean;

  selectedSuggestion: string | null;
  selectSuggestion: (key: string | null) => void;
  applySuggestion: (s: MesaSuggestion) => Promise<void>;
  dismissSuggestion: (s: MesaSuggestion) => Promise<void>;
  restoreSuggestions: () => Promise<void>;
  rescan: () => Promise<void>;
  suggestionBusy: string | null;
  rescanning: boolean;
  suggestionError: string | null;

  /** Export awaiting confirmation, with its progress or failure. */
  exporting: { busy: boolean; error: string | null } | null;
  askExport: () => void;
  cancelExport: () => void;
  doExport: () => Promise<void>;
}

const MesaContext = createContext<MesaState | null>(null);

/** The MESA workspace's state; null outside its provider. */
export function useMesa(): MesaState | null {
  return useContext(MesaContext);
}

export function MesaProvider({ active = true, onFindings, children }: {
  /** The workspace is on screen: it loads then, and again on every return. */
  active?: boolean;
  /** Which conditional windows have something to show; null until loaded. */
  onFindings?: (findings: MesaFindings | null) => void;
  children: ReactNode;
}) {
  const [profiles, setProfiles] = useState<MesaProfileListItem[]>([]);
  const [devices, setDevices] = useState<{ device_id: string; document: MesaProfileDocument }[]>([]);
  const [areas, setAreas] = useState<{ area_id: string; document: MesaProfileDocument }[]>([]);
  const [integrations, setIntegrations] = useState<{ integration: string; document: MesaProfileDocument }[]>([]);
  const [domains, setDomains] = useState<{ domain: string; document: MesaProfileDocument }[]>([]);
  const [issues, setIssues] = useState<MesaIssuesResponse>(EMPTY_ISSUES);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [entityTree, setEntityTree] = useState<EntityTreeData | null>(null);
  const [canonicalTags, setCanonicalTags] = useState<string[]>([]);
  const [integrationOptions, setIntegrationOptions] = useState<{ id: string; name: string }[]>([]);
  const [deviceOptions, setDeviceOptions] = useState<{ id: string; name: string }[]>([]);

  const beginLoad = useLatestRequest();
  const refresh = useCallback(async () => {
    const isLatest = beginLoad();
    setLoading(true);
    setError(null);
    try {
      const [list, iss, doms, ints, ars, devs] = await Promise.all([
        api.listMesaProfiles({ all: true }),
        // Suggestions-scoped recompute: a refresh follows every profile save
        // or delete (including Apply and Review from a suggestion), and a saved
        // profile's coverage must be re-evaluated at once or the entity lingers
        // in the suggestions. Trigger issues and orphans stay cached; Rescan
        // is the way to force those.
        api.getMesaIssues("suggestions"),
        api.listMesaDomains().catch(() => ({ domains: [] })),
        api.listMesaIntegrations().catch(() => ({ integrations: [] })),
        api.listMesaAreas().catch(() => ({ areas: [] })),
        api.listMesaDevices().catch(() => ({ devices: [] })),
      ]);
      if (!isLatest()) return;
      setProfiles(list.profiles);
      setIssues(iss);
      setDomains(doms.domains);
      setIntegrations(ints.integrations);
      setAreas(ars.areas);
      setDevices(devs.devices);
      setLoaded(true);
    } catch (e) {
      if (isLatest()) setError(e instanceof Error ? e.message : t("mesa.loadFailed"));
    } finally {
      if (isLatest()) setLoading(false);
    }
  }, [beginLoad]);

  // The registry and vocabularies load once, on the first visit.
  const started = useRef(false);
  useEffect(() => {
    if (!active) return;
    void refresh();
    if (started.current) return;
    started.current = true;
    // The registry serves the editor's key search and the list's friendly names.
    api.getEntityTree().then(setEntityTree).catch(() => null);
    // The canonical MESA tag vocabulary powers the tag-input autocomplete.
    api.getMesaVocabulary().then((v) => setCanonicalTags(v.canonical_tags)).catch(() => null);
    // Installed integrations (those with entities) for the integration-profile picker.
    api.getMesaIntegrationOptions().then((r) => setIntegrationOptions(r.integrations)).catch(() => null);
    // Registered devices for the device-profile picker: a device id is opaque.
    api.getMesaDeviceOptions().then((r) => setDeviceOptions(r.devices)).catch(() => null);
  }, [active, refresh]);

  const hassStates = (currentHass() as { states?: Record<string, { attributes?: Record<string, unknown> }> } | null)?.states;
  const friendly = useCallback((eid: string): string => {
    const name = entityTree?.[domainOf(eid)]?.entity_details[eid]?.friendly_name
      || hassStates?.[eid]?.attributes?.friendly_name;
    return typeof name === "string" ? name : "";
  }, [entityTree, hassStates]);

  // Validator automation IDs are configuration IDs, not always entity IDs.
  // Resolve only known identities; never guess a name from a similar slug.
  const automationNames = useMemo(() => {
    const names = new Map<string, string>();
    for (const [eid, state] of Object.entries(hassStates ?? {})) {
      if (domainOf(eid) !== "automation") continue;
      const name = friendly(eid);
      const configId = state.attributes?.id;
      if (name && typeof configId === "string" && configId) names.set(configId, name);
    }
    for (const eid of Object.keys(entityTree?.automation?.entity_details ?? {})) {
      const name = friendly(eid);
      if (name) { names.set(eid, name); names.set(eid.slice("automation.".length), name); }
    }
    return names;
  }, [entityTree, hassStates, friendly]);
  const automationName = useCallback((id: string) => friendly(id) || automationNames.get(id) || "", [friendly, automationNames]);

  // A device key is an opaque registry id; area and integration keys are slugs
  // whose registries carry real names. A key the registry no longer knows has
  // no name, which keeps an orphaned profile readable by its key.
  const nameOf = useMemo(() => {
    const areaNames = new Map<string, string>();
    for (const dt of Object.values(entityTree ?? {})) {
      for (const info of Object.values(dt.entity_details)) {
        if (info.area_id && info.area_name) areaNames.set(info.area_id, info.area_name);
      }
    }
    const byScope: Record<CascadingScope, Map<string, string>> = {
      device: new Map(deviceOptions.map((d) => [d.id, d.name])),
      integration: new Map(integrationOptions.map((i) => [i.id, i.name])),
      area: areaNames,
      domain: new Map(),
    };
    return (scope: MesaProfileScope, key: string): string => (scope === "entity" ? friendly(key) : byScope[scope].get(key) ?? "");
  }, [entityTree, deviceOptions, integrationOptions, friendly]);

  const rows = useMemo<ProfileRow[]>(() => {
    const row = (scope: MesaProfileScope, key: string, document: MesaProfileDocument): ProfileRow => ({ id: `${scope}:${key}`, scope, key, name: nameOf(scope, key), document });
    return [
      ...profiles.map((p) => row("entity", p.entity_id, p.document)),
      ...devices.map((d) => row("device", d.device_id, d.document)),
      ...areas.map((a) => row("area", a.area_id, a.document)),
      ...integrations.map((i) => row("integration", i.integration, i.document)),
      ...domains.map((d) => row("domain", d.domain, d.document)),
    ];
  }, [profiles, devices, areas, integrations, domains, nameOf]);

  // ---- Findings and their dismissals -------------------------------------

  const { inspection, validation } = useMemo(() => {
    const automations = new Set<string>();
    const findings: MesaValidationIssue[] = [];
    for (const issue of issues.issues) {
      if (issue.severity === "warning" && issue.role === "configuration" && !issue.entity_id
        && issue.recommendation === PARTIAL_INSPECTION_RECOMMENDATION) {
        automations.add(issue.automation_id);
      } else {
        findings.push(issue);
      }
    }
    return { inspection: [...automations], validation: findings };
  }, [issues.issues]);
  const findingIds = useMemo(() => ({
    inspection: inspection.map((id) => JSON.stringify(["inspection", id])),
    validation: validation.map((i) => JSON.stringify(["validation", i.entity_id, i.declared_value, i.automation_id, i.role, i.severity, i.recommendation])),
    orphans: [
      ...issues.orphans.map((id) => JSON.stringify(["orphan", "entity", id])),
      ...issues.orphan_devices.map((id) => JSON.stringify(["orphan", "device", id])),
      ...issues.orphan_areas.map((id) => JSON.stringify(["orphan", "area", id])),
      ...issues.orphan_integrations.map((id) => JSON.stringify(["orphan", "integration", id])),
    ],
  }), [inspection, validation, issues]);

  // Dismissals made while browser storage refuses writes still hold for this visit.
  const [session, setSession] = useState<{ key: string | null; findings: string[] }>({ key: null, findings: [] });
  const storageKey = noticeStorageKey();
  const dismissed = new Set([...readDismissedNotices(storageKey), ...(session.key === storageKey ? session.findings : [])]);
  const allDismissed = (findings: string[]) => findings.every((f) => dismissed.has(f));
  const dismiss = (findings: string[]) => {
    const key = noticeStorageKey();
    const next = [...new Set([...readDismissedNotices(key), ...(session.key === key ? session.findings : []), ...findings])];
    try { if (key) localStorage.setItem(key, JSON.stringify(next)); } catch { /* retain dismissal for this visit if storage is unavailable */ }
    setSession({ key, findings: next });
  };

  const orphansShown = findingIds.orphans.length > 0 && !allDismissed(findingIds.orphans);
  const advisoryShown = (findingIds.inspection.length > 0 && !allDismissed(findingIds.inspection))
    || (findingIds.validation.length > 0 && !allDismissed(findingIds.validation));
  useEffect(() => {
    onFindings?.(loaded ? { orphans: orphansShown, advisory: advisoryShown } : null);
  }, [onFindings, loaded, orphansShown, advisoryShown]);

  // ---- The profile in Profile Editor --------------------------------------

  const [editing, setEditing] = useState<Opened | null>(null);
  const [pending, setPending] = useState<{ target: Opened | null } | null>(null);
  const nonce = useRef(0);
  const dirty = useRef(false);
  const editingRef = useRef(editing);
  editingRef.current = editing;
  const open = useCallback((next: Editing | null, opts?: { force?: boolean }) => {
    const current = editingRef.current;
    // Choosing the profile already open keeps its form as it is.
    if (next && current && !next.isNew && !current.isNew && next.scope === current.scope && next.key === current.key) return;
    const target = next ? { ...next, nonce: ++nonce.current } : null;
    if (!opts?.force && dirty.current && current) { setPending({ target }); return; }
    dirty.current = false;
    setPending(null);
    setEditing(target);
  }, []);
  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  const confirmSwitch = useCallback(() => {
    const p = pendingRef.current;
    if (!p) return;
    dirty.current = false;
    setEditing(p.target);
    setPending(null);
  }, []);
  const cancelSwitch = useCallback(() => setPending(null), []);
  const setEditorDirty = useCallback((d: boolean) => { dirty.current = d; }, []);
  const editorDirty = useCallback(() => dirty.current, []);

  // ---- Suggestions ---------------------------------------------------------

  const [selectedSuggestion, selectSuggestion] = useState<string | null>(null);
  const [suggestionBusy, setSuggestionBusy] = useState<string | null>(null);
  const [rescanning, setRescanning] = useState(false);
  const [suggestionError, setSuggestionError] = useState<string | null>(null);

  // Apply a suggestion as-is: create the profile with the suggested mode, the
  // reason preserved as control_reason so future admins (and agents, via
  // authored_restrictions) see why it exists. Never automatic; this is the
  // admin's click. Create-only, so a profile made meanwhile is never replaced.
  const applySuggestion = useCallback(async (s: MesaSuggestion) => {
    setSuggestionBusy(s.key);
    setSuggestionError(null);
    try {
      const doc: MesaProfileDocument = {
        semantic_profile: { operational_boundaries: { control_mode: s.suggested_mode, control_reason: s.reason } },
      };
      if (s.scope === "domain") await api.putMesaDomain(s.subject_id, doc, true);
      else await api.putMesaProfile(s.subject_id, doc, true);
      await refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status === 412) await refresh();
      setSuggestionError(e instanceof Error ? e.message : t("mesa.applySuggestionFailed"));
    } finally {
      setSuggestionBusy(null);
    }
  }, [refresh]);

  const dismissSuggestion = useCallback(async (s: MesaSuggestion) => {
    setSuggestionBusy(s.key);
    setSuggestionError(null);
    try {
      const resp = await api.dismissMesaSuggestion(s.key);
      setIssues((prev) => ({ ...prev, suggestions: resp.suggestions, dismissed_suggestions: resp.dismissed_suggestions }));
    } catch (e) {
      setSuggestionError(e instanceof Error ? e.message : t("mesa.dismissSuggestionFailed"));
    } finally {
      setSuggestionBusy(null);
    }
  }, []);

  const restoreSuggestions = useCallback(async () => {
    setSuggestionError(null);
    try {
      const resp = await api.restoreMesaSuggestions({ all: true });
      setIssues((prev) => ({ ...prev, suggestions: resp.suggestions, dismissed_suggestions: resp.dismissed_suggestions }));
    } catch (e) {
      setSuggestionError(e instanceof Error ? e.message : t("mesa.restoreSuggestionsFailed"));
    }
  }, []);

  const rescan = useCallback(async () => {
    setRescanning(true);
    setSuggestionError(null);
    try {
      setIssues(await api.getMesaIssues("suggestions"));
    } catch (e) {
      setSuggestionError(e instanceof Error ? e.message : t("mesa.rescanFailed"));
    } finally {
      setRescanning(false);
    }
  }, []);

  // ---- Export ----------------------------------------------------------------

  const [exporting, setExporting] = useState<{ busy: boolean; error: string | null } | null>(null);
  const doExport = useCallback(async () => {
    setExporting({ busy: true, error: null });
    try {
      await exportMesaArchive();
      setExporting(null);
    } catch (e) {
      setExporting({ busy: false, error: e instanceof ApiError ? e.message : t("mesa.exportFailed") });
    }
  }, []);

  const value: MesaState = {
    loaded, loading, error, refresh: () => { void refresh(); },
    rows, entityTree, canonicalTags, integrationOptions, deviceOptions, friendly, nameOf, automationName,
    issues, inspection, validation, findingIds, allDismissed, dismiss,
    editing, open, pending, confirmSwitch, cancelSwitch, setEditorDirty, editorDirty,
    selectedSuggestion, selectSuggestion, applySuggestion, dismissSuggestion, restoreSuggestions, rescan, suggestionBusy, rescanning, suggestionError,
    exporting, askExport: () => setExporting({ busy: false, error: null }), cancelExport: () => setExporting(null), doExport,
  };
  return <MesaContext.Provider value={value}>{children}</MesaContext.Provider>;
}
