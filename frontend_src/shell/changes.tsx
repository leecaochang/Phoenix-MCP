/**
 * The Changes workspace's shared state for its three windows: the recorded
 * changes (Changes), the chosen change's saved Before and After (Comparison),
 * and the other changes recorded for the same item (Item History). It lives at
 * shell level because a closed window's content is unmounted, while a restore
 * or a config-changed event must still reach every open window.
 *
 * Feed loading, live updates and access key names keep the former Changes
 * view's behaviour. Restoring re-checks the item's latest change before it
 * applies, so a confirmation made against an older history cannot replace a
 * newer edit unseen.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import type { VersionRecord, VersionSummary } from "../types";
import { t } from "../i18n";
import { formatDateTime } from "../utils";
import { useLatestRequest } from "../utils/latest_request";
import { useNotify } from "../ui/notify";

// Resource types whose before/after payload is a raw text blob ({content, ...})
// rather than a structured config; these render as a line diff, not YAML.
// esphome_yaml must be listed: its payload has the same {content, bytes} shape,
// and the structured path would dump it as YAML, burying the whole file inside a
// "content: |-" block, hiding the too-large marker and offering Restore on a
// snapshot the backend would refuse.
export const RAW_CONTENT_TYPES = new Set(["yaml_config", "file", "esphome_yaml", "blueprint"]);
// Configuration file versions are history only: their text is shown, but
// Phoenix never writes it back, so they offer no Restore.
const HISTORY_ONLY_TYPES = new Set(["yaml_config", "file", "esphome_yaml"]);

/** Extract the raw-text snapshot from a version side, or null. content is null
 * when the snapshot was too large to store (a non-restorable marker). */
export function rawSide(value: Record<string, unknown> | null): { content: string | null; bytes?: number } | null {
  if (value == null) return null;
  return {
    content: typeof value.content === "string" ? (value.content as string) : null,
    bytes: typeof value.bytes === "number" ? (value.bytes as number) : undefined,
  };
}

/** The item a change belongs to, as the list names it. */
export function changeLabel(v: { alias: string | null; resource_id: string }): string {
  return v.alias || v.resource_id;
}

export type RestoreSide = "before" | "after";

/** Whether a side of a change can be restored, and the reason when it cannot. */
export function restoreAvailability(record: VersionRecord, history: VersionSummary[], side: RestoreSide): { ok: true } | { ok: false; reason: string } {
  if (HISTORY_ONLY_TYPES.has(record.resource_type)) return { ok: false, reason: t("changes.historyOnly") };
  const value = side === "before" ? record.before : record.after;
  if (value == null) return { ok: false, reason: t(side === "before" ? "changes.noBefore" : "changes.noAfter") };
  if (RAW_CONTENT_TYPES.has(record.resource_type) && rawSide(value)?.content == null) return { ok: false, reason: t("changes.tooLarge") };
  // The newest change's After is the item's current state, so restoring it changes nothing.
  if (side === "after" && history[0]?.id === record.id) return { ok: false, reason: t("changes.alreadyLatest") };
  return { ok: true };
}

const CHANGES_PAGE = 100;
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

export interface ChangesState {
  feed: VersionSummary[];
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  hasMore: boolean;
  refresh: () => void;
  loadMore: () => void;
  /** access_key_id to the key's current name, active and archived keys alike. */
  names: Map<string, string>;

  selectedId: string | null;
  select: (id: string | null) => void;
  /** The chosen change with its saved Before and After. */
  record: VersionRecord | null;
  recordLoading: boolean;
  recordError: string | null;
  /** Every recorded change of the chosen change's item, newest first. */
  history: VersionSummary[];

  /** The side being confirmed, with the item's latest change when Restore was chosen. */
  confirm: { side: RestoreSide; latestId: string | null } | null;
  askRestore: (side: RestoreSide) => void;
  cancelRestore: () => void;
  restore: () => Promise<void>;
  restoring: boolean;
  restoreError: string | null;
}

const ChangesContext = createContext<ChangesState | null>(null);

/** The Changes workspace's state; null outside its provider. */
export function useChanges(): ChangesState | null {
  return useContext(ChangesContext);
}

export function ChangesProvider({ hass, active = true, children }: {
  hass: unknown;
  /** The workspace is on screen: the feed polls only then. */
  active?: boolean;
  children: ReactNode;
}) {
  const notify = useNotify();
  const [feed, setFeed] = useState<VersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [names, setNames] = useState<Map<string, string>>(() => new Map());

  // access_key_id -> latest known name, so a renamed access key shows its new name here.
  // Covers archived access keys too, so a renamed-then-revoked access key resolves to its
  // final name rather than the one captured with the change.
  const loadNames = useCallback(async () => {
    try {
      const [activeKeys, archived] = await Promise.all([
        api.listAccessKeys(),
        api.listArchivedAccessKeys().catch(() => []),
      ]);
      const map = new Map<string, string>();
      for (const k of archived) map.set(k.id, k.name);
      for (const k of activeKeys) map.set(k.id, k.name);
      setNames(map);
    } catch {
      // Names fall back to the value captured with each change.
    }
  }, []);
  useEffect(() => { void loadNames(); }, [loadNames]);

  // offset === 0 always replaces the feed (a fresh top page), because the feed
  // is a live list; Load More passes the next offset and appends. The feed is
  // loaded from several places with no ordering between them, so a superseded
  // response is dropped rather than allowed to put an older page back.
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
        // A background top-page refresh can supersede a foreground one; its
        // response satisfies the same request, so it releases the loading state too.
        if (offset === 0) setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [beginLoad]);
  useEffect(() => { void loadFeed(0); }, [loadFeed]);

  // ---- The chosen change and its item's history ---------------------------

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  const [record, setRecord] = useState<VersionRecord | null>(null);
  const [recordLoading, setRecordLoading] = useState(false);
  const [recordError, setRecordError] = useState<string | null>(null);
  const [history, setHistory] = useState<VersionSummary[]>([]);
  const [confirm, setConfirm] = useState<ChangesState["confirm"]>(null);
  const [restoring, setRestoring] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);

  const beginRecord = useLatestRequest();
  useEffect(() => {
    if (!selectedId) {
      setRecord(null);
      setHistory([]);
      return;
    }
    const isLatest = beginRecord();
    setRecordLoading(true);
    setRecordError(null);
    api.getVersion(selectedId)
      .then((r) => {
        if (!isLatest()) return;
        setRecord(r);
        return api.listVersions({ resource_type: r.resource_type, resource_id: r.resource_id })
          .then((resp) => { if (isLatest()) setHistory(resp.versions); })
          .catch(() => { if (isLatest()) setHistory([]); });
      })
      .catch((e: unknown) => { if (isLatest()) { setRecord(null); setRecordError(e instanceof Error ? e.message : t("changes.loadVersionFailed")); } })
      .finally(() => { if (isLatest()) setRecordLoading(false); });
  }, [selectedId, beginRecord]);

  const beginHistory = useLatestRequest();
  const recordRef = useRef(record);
  recordRef.current = record;
  /** Reload the chosen item's history, so Restore After and the stale check stay current. */
  const reloadHistory = useCallback(async () => {
    const r = recordRef.current;
    if (!r) return;
    const isLatest = beginHistory();
    try {
      const resp = await api.listVersions({ resource_type: r.resource_type, resource_id: r.resource_id });
      if (isLatest() && recordRef.current?.id === r.id) setHistory(resp.versions);
    } catch {
      // The history shown stays as it was; the restore check reads it again anyway.
    }
  }, [beginHistory]);

  // Live: Phoenix fires phoenix_mcp_config_changed when an agent or a restore records a change.
  useEffect(() => {
    let unsub: Unsub | null = null;
    let cancelled = false;
    void subscribeConfigChanged(hass, () => { void loadFeed(0, true); void reloadHistory(); })
      .then((u) => { if (cancelled) u?.(); else unsub = u; });
    return () => { cancelled = true; unsub?.(); };
  }, [hass, loadFeed, reloadHistory]);

  // Poll as a fallback (a dropped event or a reconnect) while the workspace is on screen.
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => { void loadFeed(0, true); }, FEED_POLL_MS);
    return () => clearInterval(id);
  }, [active, loadFeed]);

  const select = useCallback((id: string | null) => {
    setSelectedId(id);
    setConfirm(null);
    setRestoreError(null);
  }, []);

  const askRestore = useCallback((side: RestoreSide) => {
    if (!record || !restoreAvailability(record, history, side).ok) return;
    setRestoreError(null);
    setConfirm({ side, latestId: history[0]?.id ?? null });
  }, [record, history]);

  const restore = useCallback(async () => {
    const r = record;
    const c = confirm;
    if (!r || !c || restoring) return;
    const name = changeLabel(r);
    const side = t(c.side === "before" ? "common.before" : "common.after");
    setRestoring(true);
    setRestoreError(null);
    try {
      // The stale check: the item's latest change must still be the one seen when Restore was chosen.
      const fresh = (await api.listVersions({ resource_type: r.resource_type, resource_id: r.resource_id })).versions;
      if (selectedRef.current !== r.id) return;
      setHistory(fresh);
      if ((fresh[0]?.id ?? null) !== c.latestId || !restoreAvailability(r, fresh, c.side).ok) {
        setConfirm(null);
        notify(t("changes.restoreStale", { name }));
        return;
      }
      await api.restoreVersion(r.id, c.side);
      setConfirm(null);
      notify(t("changes.restored", { name, side, time: formatDateTime(r.timestamp) }));
      await loadFeed(0, true);
      // A restore records a change of its own: show it.
      const after = (await api.listVersions({ resource_type: r.resource_type, resource_id: r.resource_id })).versions;
      if (selectedRef.current !== r.id) return;
      setHistory(after);
      if (after[0] && after[0].id !== c.latestId && after[0].id !== r.id) select(after[0].id);
    } catch (e: unknown) {
      setConfirm(null);
      setRestoreError(e instanceof Error ? e.message : t("changes.restoreFailed"));
    } finally {
      setRestoring(false);
    }
  }, [record, confirm, restoring, notify, loadFeed, select]);

  const value: ChangesState = {
    feed, loading, loadingMore, error, hasMore,
    refresh: () => { void loadFeed(0); void loadNames(); void reloadHistory(); },
    loadMore: () => { if (!loadingMore) void loadFeed(feed.length); },
    names,
    selectedId, select, record, recordLoading, recordError, history,
    confirm, askRestore, cancelRestore: () => setConfirm(null), restore, restoring, restoreError,
  };
  return <ChangesContext.Provider value={value}>{children}</ChangesContext.Provider>;
}
