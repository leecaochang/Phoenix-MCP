/**
 * The Approvals workspace's shared state for its three windows: the request
 * list (Approvals), the request under review and the decisions on it (Review),
 * and its metadata, inputs and result (Request Details). It lives at shell
 * level because a closed window's content is unmounted, while a deep link, an
 * approval event or a decision must still reach Review.
 *
 * Loading, polling, deep links, claims, batch approval and the Agent Chat
 * review signals keep the former Approvals view's behaviour; a decided request
 * stays in Review with its outcome instead of disappearing.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import type { ApprovalRecord, ApprovalStatus, BatchApproveResult } from "../types";
import { t } from "../i18n";
import { friendlyToolName } from "../utils";
import { notifyAgentChatReviewClosed, notifyAgentChatReviewDecided } from "../utils/agentchat_review";
import { clearReasonDraft, getReasonDraft, setReasonDraft } from "../utils/approval_reason_draft";
import { friendlyApprovalSummary } from "../utils/approval_summary";
import { diffSummary } from "../utils/approval_text";
import { useLatestRequest } from "../utils/latest_request";
import { matchesListSearch } from "../utils/list_search";

/** Which requests the list shows: Pending, History (every resolved one), All, or one resolved status. */
export type ApprovalsScope = "pending" | "history" | "all" | Exclude<ApprovalStatus, "pending">;
export const APPROVALS_SCOPES: readonly ApprovalsScope[] = ["pending", "history", "all", "approved", "rejected", "failed", "expired", "cancelled"];

const POLL_INTERVAL_MS = 10_000;
const PAGE = 50;

export interface ApprovalsState {
  scope: ApprovalsScope;
  setScope: (scope: ApprovalsScope) => void;
  search: string;
  setSearch: (search: string) => void;
  /** The loaded requests matching the search. */
  shown: ApprovalRecord[];
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  hasMore: boolean;
  /** The server's count of pending requests, not just the loaded ones. */
  pendingTotal: number;
  refresh: () => void;
  loadMore: () => void;
  /** Its saved action is running: from the claim event, or the record's own flag. */
  isClaimed: (record: ApprovalRecord) => boolean;

  /** The request in Review, as most recently loaded. */
  current: ApprovalRecord | null;
  /** Choose a request; `order` is the list it was chosen from, in its shown order, for Previous and Next. */
  select: (record: ApprovalRecord | null, order?: readonly string[]) => void;
  /** The request before (-1) or after (1) the chosen one in that list; null at either end. */
  neighbour: (delta: -1 | 1) => ApprovalRecord | null;
  step: (delta: -1 | 1) => void;
  /** A decision on this request is running here, or its action is running anywhere. */
  locked: (record: ApprovalRecord) => boolean;
  deciding: "approve" | "reject" | null;
  decisionError: string | null;
  rejecting: boolean;
  setRejecting: (open: boolean) => void;
  /** The rejection reason for the request in Review, shared with Agent Chat's Reject. */
  reason: string;
  setReason: (reason: string) => void;
  approve: () => Promise<void>;
  reject: () => Promise<void>;

  batchMode: boolean;
  /** At least two pending requests that are not running are shown. */
  canBatch: boolean;
  startBatch: () => void;
  endBatch: () => void;
  picked: ReadonlySet<string>;
  selectableIds: string[];
  pickedCount: number;
  allPicked: boolean;
  togglePick: (id: string) => void;
  toggleAll: () => void;
  batchBusy: boolean;
  batchResult: BatchApproveResult | null;
  dismissBatchResult: () => void;
  approveSelected: () => Promise<void>;
}

const ApprovalsContext = createContext<ApprovalsState | null>(null);

/** The Approvals workspace's state; null outside its provider. */
export function useApprovals(): ApprovalsState | null {
  return useContext(ApprovalsContext);
}

export function ApprovalsProvider({ scope, onScopeChange, onCountChange, refreshSignal = 0, claimedApprovals, openApprovalId, onConsumedDeepLink, active = true, onReveal, children }: {
  scope: ApprovalsScope;
  onScopeChange: (scope: ApprovalsScope) => void;
  /** Called when requests are decided, so the toolbar's pending count can refresh. */
  onCountChange?: () => void;
  /** Bumped on each Home Assistant approval event: the list reloads at once. */
  refreshSignal?: number;
  /** Requests whose saved action is running, from the claim event. */
  claimedApprovals?: ReadonlySet<string>;
  /** A request to open from a notification or Agent Chat link. */
  openApprovalId?: string | null;
  /** Called once the linked request is shown, so the route can be cleared. */
  onConsumedDeepLink?: () => void;
  /** The workspace is on screen: Pending polls only then, and leaving it ends a review opened from a link. */
  active?: boolean;
  /** Bring Review into view for a request opened from a link. */
  onReveal?: () => void;
  children: ReactNode;
}) {
  // Two sources, deliberately: the live claim event (instant, but only for a
  // panel that was open when it fired) and the row's own in_progress from the
  // API (covers a page loaded or reloaded mid-execution). Only a pending request
  // can be running: a claim still held after it was decided says nothing.
  const isClaimed = useCallback((r: ApprovalRecord) => r.status === "pending" && (Boolean(r.in_progress) || Boolean(claimedApprovals?.has(r.id))), [claimedApprovals]);
  const [records, setRecords] = useState<ApprovalRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [rawOffset, setRawOffset] = useState(0);
  // Pre-slice total from the pending fetch, not the loaded rows: the list pages,
  // so counting them would under-report the queue.
  const [pendingTotal, setPendingTotal] = useState(0);
  const [search, setSearch] = useState("");

  // Every loader writes the same rows, offset and paging state and is started
  // from many places with no ordering between them, so one generation covers
  // them all: a response a newer request has superseded is dropped.
  const beginLoad = useLatestRequest();

  const pendingWindow = useRef(PAGE);
  const loadPending = useCallback(async (offset = 0, quiet = false) => {
    if (offset > 0) pendingWindow.current = Math.max(pendingWindow.current, offset + PAGE);
    const isLatest = beginLoad();
    setError(null);
    try {
      const approvals: ApprovalRecord[] = [];
      let nextOffset = 0;
      let total = 0;
      let pages = 0;
      do {
        const resp = await api.listApprovals({ status: "pending", limit: PAGE, offset: nextOffset });
        if (!isLatest()) return;
        approvals.push(...resp.approvals);
        total = resp.total;
        pages += 1;
        nextOffset += resp.approvals.length;
        if (!resp.approvals.length) break;
      } while (nextOffset < total && pages < Math.ceil(pendingWindow.current / PAGE));
      setRecords([...new Map(approvals.map((record) => [record.id, record])).values()]);
      setRawOffset(nextOffset);
      setPendingTotal(total);
      setHasMore(nextOffset < total);
    } catch (e: unknown) {
      if (!isLatest()) return;
      // A quiet update keeps what is shown: the next one retries, and a restart is announced by the shell.
      if (!quiet) setError(e instanceof Error ? e.message : t("approvals.loadFailed"));
    } finally {
      if (isLatest()) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [beginLoad]);

  // History and the other scopes page by offset; History leaves pending requests out.
  const loadOthers = useCallback(async (offset: number, quiet = false) => {
    const isLatest = beginLoad();
    setError(null);
    try {
      const resp = await api.listApprovals({
        status: scope === "history" || scope === "all" ? undefined : scope,
        limit: PAGE,
        offset,
      });
      if (!isLatest()) return;
      const page = scope === "history" ? resp.approvals.filter((r) => r.status !== "pending") : resp.approvals;
      setRecords((prev) => {
        if (offset === 0 && !quiet) return page;
        // A quiet update of the first page keeps the pages already loaded below it.
        const seen = new Set(prev.map((r) => r.id));
        const fresh = new Map(page.map((r) => [r.id, r] as const));
        const kept = prev.map((r) => fresh.get(r.id) ?? r);
        return offset === 0 ? [...page.filter((r) => !seen.has(r.id)), ...kept] : [...prev, ...page.filter((r) => !seen.has(r.id))];
      });
      if (!(quiet && offset === 0)) setRawOffset(offset + resp.approvals.length);
      setHasMore(offset + resp.approvals.length < resp.total);
    } catch (e: unknown) {
      if (!isLatest()) return;
      if (!quiet) setError(e instanceof Error ? e.message : t("approvals.loadFailed"));
    } finally {
      if (isLatest()) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [scope, beginLoad]);

  const reload = useCallback(() => {
    if (scope === "pending") void loadPending();
    else void loadOthers(0);
  }, [scope, loadPending, loadOthers]);

  // (Re)load when the scope changes.
  useEffect(() => {
    setLoading(true);
    pendingWindow.current = PAGE;
    setRecords([]);
    if (scope === "pending") void loadPending();
    else void loadOthers(0);
  }, [scope, loadPending, loadOthers]);

  // Every scope updates itself while the workspace is on screen, so there is no Refresh
  // button (user, 2026-10-29); approval events make a new or decided request instant.
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => {
      if (scope === "pending") void loadPending(0, true);
      else void loadOthers(0, true);
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [scope, loadPending, loadOthers, active]);

  const lastSignal = useRef(refreshSignal);
  useEffect(() => {
    if (refreshSignal === lastSignal.current) return;
    lastSignal.current = refreshSignal;
    reload();
  }, [refreshSignal, reload]);

  const refresh = useCallback(() => {
    setLoading(true);
    reload();
  }, [reload]);

  const loadMore = useCallback(() => {
    if (loadingMore) return;
    setLoadingMore(true);
    if (scope === "pending") void loadPending(rawOffset);
    else void loadOthers(rawOffset);
  }, [loadingMore, scope, loadPending, loadOthers, rawOffset]);

  const shown = useMemo(() => records.filter((r) => matchesListSearch(
    `${r.access_key_name} ${r.tool_name} ${friendlyToolName(r.tool_name)} ${friendlyApprovalSummary(r.diff).title} ${diffSummary(r.diff)} ${r.rejected_reason ?? ""}`, search)), [records, search]);

  // ---- Review -------------------------------------------------------------

  const [selected, setSelected] = useState<ApprovalRecord | null>(null);
  // The request opened from a link, until it is decided or left: leaving it
  // tells Agent Chat its review closed, as closing the former dialog did.
  const [linkedId, setLinkedId] = useState<string | null>(null);
  const consumedDeepLink = useRef<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [deciding, setDeciding] = useState<{ id: string; kind: "approve" | "reject" } | null>(null);
  const [decisionError, setDecisionError] = useState<{ id: string; message: string } | null>(null);
  const [reason, setReasonState] = useState("");

  // A reload brings a newer copy of the request in Review (a claim, for one).
  const current = selected ? records.find((r) => r.id === selected.id && r.status === selected.status) ?? selected : null;

  const [order, setOrder] = useState<readonly string[]>([]);
  const select = useCallback((record: ApprovalRecord | null, chosenFrom?: readonly string[]) => {
    setSelected(record);
    if (chosenFrom) setOrder(chosenFrom);
    setRejecting(false);
  }, []);
  /**
   * Showing different requests takes the chosen one with it: the list has
   * nothing chosen afterwards, so Review and Request Details must not still
   * hold the request it had (user, 2026-10-29). A request opened from a link
   * changes the scope itself and keeps its choice.
   */
  const chooseScope = useCallback((next: ApprovalsScope) => {
    if (next !== scope) select(null);
    onScopeChange(next);
  }, [scope, select, onScopeChange]);

  const neighbour = (delta: -1 | 1): ApprovalRecord | null => {
    const at = selected ? order.indexOf(selected.id) : -1;
    const id = at >= 0 ? order[at + delta] : undefined;
    return (id && records.find((r) => r.id === id)) || null;
  };
  const step = (delta: -1 | 1) => {
    const next = neighbour(delta);
    if (next) select(next);
  };

  // Seeded from (and mirrored to) the shared draft store, so the reason typed in
  // Review is also what Agent Chat's Reject sends for the same request.
  const selectedId = selected?.id ?? null;
  useEffect(() => { setReasonState(selectedId ? getReasonDraft(selectedId) : ""); }, [selectedId]);
  const setReason = useCallback((text: string) => {
    setReasonState(text);
    if (selectedId) setReasonDraft(selectedId, text);
  }, [selectedId]);

  useEffect(() => {
    if (!linkedId || (selectedId === linkedId && active)) return;
    notifyAgentChatReviewClosed(linkedId);
    setLinkedId(null);
  }, [linkedId, selectedId, active]);
  const linkedRef = useRef(linkedId);
  linkedRef.current = linkedId;
  useEffect(() => () => { if (linkedRef.current) notifyAgentChatReviewClosed(linkedRef.current); }, []);

  // A request in Review that is decided elsewhere (Agent Chat, another admin,
  // expiry) shows its new state instead of still offering Approve and Reject.
  const reconcileSignal = useRef(refreshSignal);
  useEffect(() => {
    if (refreshSignal === reconcileSignal.current) return;
    reconcileSignal.current = refreshSignal;
    if (!selected || selected.status !== "pending") return;
    const id = selected.id;
    let cancelled = false;
    api.getApproval(id)
      .then((rec) => {
        if (cancelled || rec.status === "pending") return;
        notifyAgentChatReviewDecided(id);
        setLinkedId((cur) => (cur === id ? null : cur));
        setSelected((cur) => (cur?.id === id ? rec : cur));
        setRejecting(false);
        onCountChange?.();
      })
      .catch(() => {
        if (cancelled) return;
        // Gone: nothing is left to review.
        notifyAgentChatReviewDecided(id);
        setLinkedId((cur) => (cur === id ? null : cur));
        setSelected((cur) => (cur?.id === id ? null : cur));
      });
    return () => { cancelled = true; };
  }, [refreshSignal, selected, onCountChange]);

  const revealRef = useRef(onReveal);
  revealRef.current = onReveal;
  // Fetch a linked request, but do not clear its URL yet: Home Assistant can
  // replace this panel while routing, and the replacement must finish the same request.
  useEffect(() => {
    if (!openApprovalId) {
      consumedDeepLink.current = null;
      return;
    }
    let cancelled = false;
    api.getApproval(openApprovalId)
      .then((rec) => {
        if (cancelled) return;
        onScopeChange(rec.status === "pending" ? "pending" : "history");
        setLinkedId(rec.id);
        setSelected(rec);
        setRejecting(false);
        revealRef.current?.();
      })
      .catch(() => {
        if (cancelled) return;
        notifyAgentChatReviewClosed(openApprovalId);
        // A stale or unknown id cannot be opened, so it is safe to discard.
        consumedDeepLink.current = openApprovalId;
        onConsumedDeepLink?.();
      });
    return () => { cancelled = true; };
  }, [openApprovalId, onConsumedDeepLink, onScopeChange]);

  // Clear the route only after the requested record is in Review, so the panel
  // mount itself cannot consume the first navigation.
  useEffect(() => {
    if (!openApprovalId || selectedId !== openApprovalId || linkedId !== openApprovalId || consumedDeepLink.current === openApprovalId) return;
    consumedDeepLink.current = openApprovalId;
    onConsumedDeepLink?.();
  }, [linkedId, onConsumedDeepLink, openApprovalId, selectedId]);

  const locked = useCallback((r: ApprovalRecord) => deciding?.id === r.id || isClaimed(r), [deciding, isClaimed]);

  const resolved = useCallback((updated: ApprovalRecord) => {
    clearReasonDraft(updated.id);
    setSelected((cur) => (cur?.id === updated.id ? updated : cur));
    setRejecting(false);
    setRecords((prev) => (scope === "pending" ? prev.filter((r) => r.id !== updated.id) : prev.map((r) => (r.id === updated.id ? updated : r))));
    if (scope === "pending") void loadPending();
    onCountChange?.();
    notifyAgentChatReviewDecided(updated.id);
    setLinkedId((cur) => (cur === updated.id ? null : cur));
  }, [scope, loadPending, onCountChange]);

  const decide = useCallback(async (kind: "approve" | "reject") => {
    const r = current;
    if (!r || r.status !== "pending" || locked(r)) return;
    const text = reason;
    setDeciding({ id: r.id, kind });
    setDecisionError(null);
    try {
      const updated = kind === "approve"
        ? await api.approveApproval(r.id)
        : await api.rejectApproval(r.id, text ? { reason: text } : {});
      resolved(updated);
    } catch (e: unknown) {
      setDecisionError({ id: r.id, message: e instanceof Error ? e.message : t(kind === "approve" ? "approvals.approveFailed" : "approvals.rejectFailed") });
    } finally {
      setDeciding(null);
    }
  }, [current, locked, reason, resolved]);

  // ---- Batch approval -----------------------------------------------------

  const [batchMode, setBatchMode] = useState(false);
  // Ids rather than records, so a refresh that replaces the record objects does
  // not drop a tick; ids that vanish are filtered at use.
  const [picked, setPicked] = useState<ReadonlySet<string>>(() => new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchResult, setBatchResult] = useState<BatchApproveResult | null>(null);

  // A request already being executed cannot be batched: the server would answer
  // 409 on its claim and halt the run on something the operator never chose.
  const selectableIds = useMemo(() => shown.filter((r) => r.status === "pending" && !isClaimed(r)).map((r) => r.id), [shown, isClaimed]);
  const pickedCount = selectableIds.filter((id) => picked.has(id)).length;
  const allPicked = selectableIds.length > 0 && pickedCount === selectableIds.length;
  // Batching one thing is meaningless, so selection starts from two selectable requests.
  const canBatch = selectableIds.length > 1;

  // The result describes ONE completed run: retire it once a request it does not
  // describe arrives, or it reads as describing rows that came since. Rows
  // leaving the list (the run's own, as reloads and approval events catch up)
  // do not retire it.
  const resultKnown = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (!batchResult) {
      resultKnown.current = null;
    } else if (resultKnown.current === null) {
      resultKnown.current = new Set([
        ...records.map((r) => r.id),
        ...batchResult.applied.map((a) => a.approval_id),
        ...(batchResult.failed ? [batchResult.failed.approval_id] : []),
        ...batchResult.remaining,
      ]);
    } else if (records.some((r) => !resultKnown.current!.has(r.id))) {
      setBatchResult(null);
      resultKnown.current = null;
    }
  }, [batchResult, records]);

  const approveSelected = useCallback(async () => {
    // Send in the order shown, so "stopped at" names the row the operator can see.
    const ids = selectableIds.filter((id) => picked.has(id));
    if (ids.length === 0 || batchBusy) return;
    setBatchBusy(true);
    setBatchResult(null);
    try {
      const result = await api.batchApproveApprovals(ids);
      setBatchResult(result);
      // Keep only what was left untouched ticked, so a second run retries
      // exactly the remainder once the operator has dealt with the cause.
      setPicked(new Set(result.remaining));
      if (!result.remaining.length) setBatchMode(false);
      if (scope === "pending") await loadPending(0);
      else await loadOthers(0);
      onCountChange?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBatchBusy(false);
    }
  }, [selectableIds, picked, batchBusy, scope, loadPending, loadOthers, onCountChange]);

  const value: ApprovalsState = {
    scope, setScope: chooseScope, search, setSearch, shown, loading, loadingMore, error, hasMore, pendingTotal, refresh, loadMore, isClaimed,
    current, select, neighbour, step, locked,
    deciding: current && deciding?.id === current.id ? deciding.kind : null,
    decisionError: current && decisionError?.id === current.id ? decisionError.message : null,
    rejecting, setRejecting, reason, setReason,
    approve: () => decide("approve"),
    reject: () => decide("reject"),
    batchMode, canBatch,
    startBatch: () => { if (canBatch) { setPicked(new Set()); setBatchMode(true); } },
    endBatch: () => { setBatchMode(false); setPicked(new Set()); },
    picked, selectableIds, pickedCount, allPicked,
    togglePick: (id) => setPicked((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    }),
    toggleAll: () => setPicked(allPicked ? new Set() : new Set(selectableIds)),
    batchBusy, batchResult, dismissBatchResult: () => setBatchResult(null), approveSelected,
  };
  return <ApprovalsContext.Provider value={value}>{children}</ApprovalsContext.Provider>;
}
