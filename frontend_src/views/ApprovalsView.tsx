/**
 * The Approvals workspace's windows: Approvals lists the requests (with a
 * Show menu, search and the configurable table, and tick boxes while
 * approving several at once), Review shows the chosen request's exact change
 * with Approve and Reject, and Request Details shows its metadata, inputs and
 * result. Their shared state is `shell/approvals.tsx`.
 */

import { Fragment, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import type { ApprovalRecord, ApprovalStatus, BatchApproveResult } from "../types";
import { ApprovalChange } from "../components/ApprovalChange";
import { ErrorMsg, Loading } from "../components/common";
import { YamlView, toYaml } from "../components/YamlView";
import { hasMessage, localeNumber, t, type Params } from "../i18n";
import { APPROVALS_SCOPES, MAX_BATCH_APPROVALS, isOverdue, useApprovals, type ApprovalsScope, type ApprovalsState } from "../shell/approvals";
import { useToolbarTools, type Tool, type ToolGroup } from "../shell/tools";
import { useWorkspaceReset } from "../shell/windowTools";
import { ActionButton } from "../ui/ActionButton";
import { useClickAway } from "../ui/useClickAway";
import { Icon, type IconName } from "../ui/Icon";
import { ConfigTable, type Column } from "../ui/table/ConfigTable";
import { useRowLimit } from "../ui/table/rowLimit";
import { approvalStatusLabel, formatDateTime, friendlyToolName } from "../utils";
import { approvalListTitle, approvalOutcome, diffSummary, friendlyReason } from "../utils/approval_text";
import { localizedApprovalReason } from "../utils/approval_reason";
import { friendlyApprovalSummary } from "../utils/approval_summary";

const SCOPE_LABEL_KEYS: Record<ApprovalsScope, string> = {
  pending: "approvals.tabPending",
  history: "approvals.tabHistory",
  all: "approvals.scopeAll",
  approved: "approvals.filterApproved",
  rejected: "approvals.filterRejected",
  failed: "approvals.filterFailed",
  expired: "approvals.filterExpired",
  cancelled: "approvals.filterCancelled",
};

const STATUS_LABEL_KEYS: Record<ApprovalStatus, string> = {
  pending: "approvals.statusPending",
  approved: "approvals.statusApproved",
  failed: "approvals.statusFailed",
  rejected: "approvals.statusRejected",
  expired: "approvals.statusExpired",
  cancelled: "approvals.statusCancelled",
};

const STATUS_ICONS: Record<ApprovalStatus, IconName> = {
  pending: "pending",
  approved: "success",
  failed: "error",
  rejected: "statusRejected",
  expired: "statusExpired",
  cancelled: "statusCancelled",
};

/** A request's status as an icon and its name; a running request reads as Being processed. */
function ApprovalStatusLabel({ record, claimed }: { record: ApprovalRecord; claimed: boolean }) {
  const status = claimed ? "processing" : record.status;
  return (
    <span className={`phx-outcome approval-status-${status}`}>
      <Icon name={claimed ? "processing" : STATUS_ICONS[record.status]} size={14} />
      {claimed ? t("approvals.processing") : STATUS_LABEL_KEYS[record.status] ? t(STATUS_LABEL_KEYS[record.status]) : record.status}
    </span>
  );
}

/** The current time, refreshed every 30 seconds while a request is pending. */
function useNow(pending: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!pending) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, [pending]);
  return now;
}

function useExpiresLabel(expiresAt: string, status: ApprovalStatus): string | null {
  const now = useNow(status === "pending");
  if (status !== "pending") return null;
  const expiresMs = Date.parse(expiresAt);
  if (Number.isNaN(expiresMs)) return null;
  const remaining = expiresMs - now;
  if (remaining <= 0) return t("approvals.expiresExpired");
  const mins = Math.round(remaining / 60_000);
  if (mins < 60) return t("approvals.expiresMinutes", { mins });
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return t("approvals.expiresHours", { hours, mins: remMins });
}

function ExpiresIn({ record }: { record: ApprovalRecord }) {
  return <>{useExpiresLabel(record.expires_at, record.status) ?? ""}</>;
}

/**
 * Reports what a batch run did, as three separate facts. "Approved 6" alone
 * would read as success on a run that stopped at item 7, and "failed" alone
 * would hide the six that applied. The remaining count is the one an operator
 * acts on: those are untouched and still pending, not lost.
 */
function BatchResultMsg({ result, onDismiss }: { result: BatchApproveResult; onDismiss: () => void }) {
  const failed = result.failed;
  return (
    <div className={`approvals-batch-result ${failed ? "is-error" : "is-ok"}`} role="status" data-guide="window.approvals#select-multiple">
      <div className="approvals-batch-result-text">
        <span>{t("approvals.batchApplied", { count: result.applied.length })}</span>
        {failed && (
          <span>
            {t("approvals.batchStopped", {
              tool: friendlyToolName(failed.tool_name ?? ""),
              reason: batchStopReason(failed),
            })}
          </span>
        )}
        {result.remaining.length > 0 && (
          <span>{t("approvals.batchRemaining", { count: result.remaining.length })}</span>
        )}
      </div>
      <button type="button" className="btn btn-ghost" data-help="help.batchResultClose" onClick={onDismiss}>{t("common.close")}</button>
    </div>
  );
}

/** Why a batch stopped, in the operator's language: the refusal's own key, else the record's reason slug. */
function batchStopReason(failed: NonNullable<BatchApproveResult["failed"]>): string {
  if (failed.message_key && hasMessage(failed.message_key)) return t(failed.message_key, failed.message_params as Params | undefined);
  return failed.message || localizedApprovalReason(failed.error);
}

/** Tools a window offers on the toolbar, or inline when it is not in the shell. */
function InlineTools({ groups }: { groups: ToolGroup[] }) {
  const tools = groups.flatMap((g) => g.tools);
  if (!tools.length) return null;
  return (
    <div className="phx-window-actions">
      {tools.map((tool: Tool) => (
        <ActionButton key={tool.id} icon={tool.icon} label={tool.label} help={tool.help} disabled={tool.disabled} pressed={tool.pressed}
          onClick={() => tool.onSelect?.()} className="btn btn-outline btn-sm" />
      ))}
    </div>
  );
}

function listColumns(a: ApprovalsState): Column<ApprovalRecord>[] {
  return [
    { id: "request", label: t("approvals.colRequest"), width: 220, maxWidth: 360, sortValue: (r) => approvalListTitle(r.diff), cell: (r) => approvalListTitle(r.diff), title: (r) => approvalListTitle(r.diff) },
    { id: "status", label: t("approvals.metaStatus"), width: 140, sortValue: (r) => (a.isClaimed(r) ? t("approvals.processing") : approvalStatusLabel(r.status)), cell: (r) => <ApprovalStatusLabel record={r} claimed={a.isClaimed(r)} /> },
    { id: "accessKey", label: t("approvals.colAccessKey"), width: 140, hidden: true, sortValue: (r) => r.access_key_name, cell: (r) => r.access_key_name },
    { id: "tool", label: t("approvals.metaTool"), width: 170, hidden: true, sortValue: (r) => friendlyToolName(r.tool_name), cell: (r) => <code>{friendlyToolName(r.tool_name)}</code> },
    { id: "requested", label: t("approvals.colRequested"), width: 160, hidden: true, sortValue: (r) => r.created_at, cell: (r) => formatDateTime(r.created_at) },
    { id: "expires", label: t("approvals.metaExpires"), width: 120, hidden: true, sortValue: (r) => r.expires_at, cell: (r) => <ExpiresIn record={r} /> },
    { id: "resolved", label: t("approvals.statusResolved"), width: 160, hidden: true, sortValue: (r) => r.resolved_at ?? "", cell: (r) => (r.resolved_at ? formatDateTime(r.resolved_at) : "") },
    { id: "reason", label: t("approvals.resolvedReason"), width: 220, maxWidth: 360, hidden: true, sortValue: (r) => friendlyReason(r), cell: (r) => friendlyReason(r), title: (r) => friendlyReason(r) || undefined },
  ];
}

export function ApprovalsListWindow() {
  const a = useApprovals();
  const rowLimit = useRowLimit();
  // Resetting the workspace clears what it had chosen, as it clears the arrangement.
  useWorkspaceReset(() => a?.select(null));
  const groups: ToolGroup[] = a ? [{
    id: "approvals",
    label: t("shell.tabApprovals"),
    tools: [
      ...(a.batchMode
        ? [
          { id: "approveSelected", label: t("approvals.approveSelected", { count: a.pickedCount }), icon: "approveSelected" as IconName, help: "help.approveSelected", disabled: a.pickedCount === 0 || a.batchBusy, onSelect: () => { void a.approveSelected(); } },
          { id: "cancelSelection", label: t("approvals.cancelSelection"), icon: "undo" as IconName, help: "help.cancelSelection", disabled: a.batchBusy, onSelect: a.endBatch },
        ]
        : [{ id: "selectMultiple", label: t("approvals.selectMultiple"), icon: "selectMultiple" as IconName, help: "help.selectMultiple", disabled: !a.canBatch, onSelect: a.startBatch }]),
    ],
  }] : [];
  const onToolbar = useToolbarTools(groups);
  if (!a) return null;

  const empty = a.search.trim() ? t("approvals.emptySearch") : a.scope === "pending" ? t("approvals.emptyPending") : t("approvals.emptyHistory");
  const scopeLabel = (scope: ApprovalsScope) => (scope === "pending" && a.scope === "pending" && a.pendingTotal > 0
    ? t("approvals.tabPendingCount", { count: a.pendingTotal })
    : t(SCOPE_LABEL_KEYS[scope]));
  return (
    <div className="phx-list-view approvals-list-view">
      {!onToolbar && <InlineTools groups={groups} />}
      <div className="phx-filters"><div className="filter-row">
        <select className="input input-auto" data-guide="window.approvals#show" data-help="help.approvalsShow" aria-label={t("approvals.show")} value={a.scope} onChange={(e) => a.setScope(e.target.value as ApprovalsScope)}>
          {APPROVALS_SCOPES.map((scope) => <option key={scope} value={scope}>{scopeLabel(scope)}</option>)}
        </select>
        <input type="search" className="input" data-guide="window.approvals#search" data-help="help.approvalsSearch" placeholder={t("approvals.searchPlaceholder")} aria-label={t("approvals.searchAria")}
          value={a.search} onChange={(e) => a.setSearch(e.target.value)} />
      </div></div>
      {a.error && <ErrorMsg msg={a.error} />}
      {a.batchResult && <BatchResultMsg result={a.batchResult} onDismiss={a.dismissBatchResult} />}
      {a.loading && !a.shown.length && <Loading />}
      {/* Ticking requests for a batch reads about Select Multiple; choosing one, about the list. */}
      <div className="approvals-table" data-guide={a.batchMode ? "window.approvals#select-multiple" : "window.approvals#list"}>
      <ConfigTable
        id="approvals"
        label={t("shell.tabApprovals")}
        // Newest request first, as every table with a time sorts by default (user, 2026-10-27).
        defaultSort={{ column: "requested", dir: "desc" }}
        columns={listColumns(a)}
        rows={a.shown}
        maxRows={rowLimit.limit}
        rowKey={(r) => r.id}
        selectedKey={a.current?.id ?? null}
        onSelect={(r, ordered) => a.select(r, ordered.map((o) => o.id))}
        rowLabel={(r) => `${approvalListTitle(r.diff)}, ${a.isClaimed(r) ? t("approvals.processing") : approvalStatusLabel(r.status)}`}
        rowHelp="help.approvalsRow"
        empty={a.loading ? null : empty}
        selection={a.batchMode ? {
          picked: a.picked,
          selectable: (r) => a.selectableIds.includes(r.id) && (a.picked.has(r.id) || a.pickedCount < MAX_BATCH_APPROVALS),
          onToggle: a.togglePick,
          onToggleAll: a.toggleAll,
          all: a.allPicked,
          label: (r) => t("approvals.selectOne", { tool: friendlyToolName(r.tool_name) }),
          allLabel: t("approvals.selectAll"),
          help: "help.batchPick",
          allHelp: "help.batchPickAll",
        } : undefined}
      />
      </div>
      {(a.hasMore || rowLimit.hidden(a.shown.length)) && (
        <div className="phx-table-footer">
          <ActionButton icon="loadMore" label={t("common.loadMore")} guide="window.approvals#load-more" helpText={t("help.loadMore", { count: localeNumber(rowLimit.step ?? 50) })} busy={a.loadingMore}
            onClick={() => { if (!rowLimit.hidden(a.shown.length)) a.loadMore(); rowLimit.more(); }} />
        </div>
      )}
    </div>
  );
}

function ReasonRow({ a, locked, decisions }: { a: ApprovalsState; locked: boolean; decisions: RefObject<HTMLDivElement | null> }) {
  const input = useRef<HTMLInputElement | null>(null);
  useEffect(() => { input.current?.focus(); }, []);
  // A click anywhere else cancels, as Cancel does; the reason typed so far stays as the draft (user, 2026-10-28).
  // Approve and Reject are not "elsewhere": Reject closes the row itself, and Approve stays unavailable while it is open.
  const row = useRef<HTMLDivElement | null>(null);
  useClickAway(row, () => a.setRejecting(false), !locked && a.deciding === null, decisions);
  return (
    <div ref={row} className="approval-reject-row phx-review-reason" data-guide="window.review#reject">
      <label htmlFor="phx-review-reason" className="approval-reason-label">{t("approvalSummary.action.optionalReason")}</label>
      <input ref={input} id="phx-review-reason" type="text" className="approval-reason-input" data-help="help.rejectReason" value={a.reason}
        placeholder={t("approvalSummary.action.reasonPlaceholder")} readOnly={locked} aria-disabled={locked || undefined}
        onChange={(e) => a.setReason(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); if (!locked) void a.reject(); }
          if (e.key === "Escape") { e.preventDefault(); a.setRejecting(false); }
        }} />
      <ActionButton icon="check" label={t("approvals.confirmReject")} help="help.confirmReject" busy={locked} onClick={() => { void a.reject(); }} className="btn btn-sm phx-decision-reject" />
      <ActionButton icon="undo" label={t("approvals.cancelReject")} help="help.cancelReject" busy={a.deciding !== null} onClick={() => a.setRejecting(false)} className="btn btn-outline btn-sm" />
    </div>
  );
}

export function ReviewWindow() {
  const a = useApprovals();
  const decisions = useRef<HTMLDivElement | null>(null);
  const title = useRef<HTMLHeadingElement | null>(null);
  const r = a?.current;
  const now = useNow(r?.status === "pending");
  // Deciding removes Approve and Reject, with focus on one of them: focus goes to the request's
  // title, from where the outcome below is the next thing read.
  const decided = r ? `${r.id}:${r.status !== "pending"}` : "";
  useLayoutEffect(() => {
    const head = title.current;
    if (!head || !decided.endsWith(":true")) return;
    const active = (head.getRootNode() as Document | ShadowRoot).activeElement;
    if (!active || active === document.body) head.focus({ preventScroll: true });
  }, [decided]);
  if (!a || !r) return <p className="phx-window-empty">{t("workspace.reviewEmpty")}</p>;

  const pending = r.status === "pending";
  // Past its expiry the server only closes it as Expired, so Approve is not offered; Reject closes it now.
  const overdue = isOverdue(r, now);
  const claimed = a.isClaimed(r);
  const locked = a.locked(r);
  // The summary sentence says what deciding does, which some requests depend on (a recovery, for one).
  const summary = friendlyApprovalSummary(r.diff);
  return (
    // Not keyed by the request: stepping updates the head in place, so Previous and Next keep focus.
    <div className="phx-review">
      {/* Request Details is closed and reopened from Workspace Options, like any other
          window; Review has no tools of its own (user, 2026-10-29). */}
      <div className="phx-review-head">
        {/* Previous and Next step through the list the request was chosen from, as in Log Details. */}
        <div className="phx-log-details-bar">
          <h3 ref={title} className="phx-review-title" tabIndex={-1} aria-live="polite">{approvalListTitle(r.diff)}</h3>
          <div className="phx-log-details-nav" data-guide="window.review#step">
            <ActionButton icon="previous" label={t("workspace.previousRecord")} help="help.previousRecord" inactive={!a.neighbour(-1)} onClick={() => a.step(-1)} className="btn btn-outline btn-sm" />
            <ActionButton icon="next" label={t("workspace.nextRecord")} help="help.nextRecord" inactive={!a.neighbour(1)} onClick={() => a.step(1)} className="btn btn-outline btn-sm" />
          </div>
        </div>
        <p className="phx-review-context" data-guide="window.review#context">
          <ApprovalStatusLabel record={r} claimed={claimed} />
          <span>{r.access_key_name}</span>
          <span>{pending ? <ExpiresIn record={r} /> : t("approvalSummary.history.resolvedAt", { time: formatDateTime(r.resolved_at || r.created_at) })}</span>
        </p>
      </div>
      <Fragment key={r.id}>
      {pending && (
        <div ref={decisions} className="phx-review-actions" role="group" aria-label={t("workspace.review")}>
          {!overdue && <ActionButton icon="check" label={t("approvals.approve")} help="help.approve" guide="window.review#approve" busy={locked || a.rejecting} onClick={() => { void a.approve(); }} className="btn btn-sm phx-decision-approve" />}
          <ActionButton icon="reject" label={t("approvals.reject")} help="help.reject" guide="window.review#reject" busy={locked} pressed={a.rejecting} onClick={() => a.setRejecting(!a.rejecting)} className="btn btn-sm phx-decision-reject" />
        </div>
      )}
      {pending && a.rejecting && <ReasonRow a={a} locked={locked} decisions={decisions} />}
      {a.decisionError && <ErrorMsg msg={a.decisionError} />}
      {summary.body && <p className="phx-review-body">{summary.body}</p>}
      <div className="phx-review-change" data-guide="window.review#change"><ApprovalChange record={r} /></div>
      {/* Mounted while the request waits, so the outcome is spoken the moment it is decided. */}
      <div role="status">
        {!pending && (
          <p className={`phx-review-outcome approval-status-${r.status}`} data-guide="topic.approvalLifecycle#outcomes">
            <Icon name={STATUS_ICONS[r.status]} size={16} />
            <span>{approvalOutcome(r)}</span>
          </p>
        )}
      </div>
      </Fragment>
    </div>
  );
}

/**
 * Which Request Details sections the user left open. The window is built again
 * for each request, and folding a section is a standing choice about what to
 * read, so it holds for the requests after it (user, 2026-10-29).
 */
const sectionOpen: Record<string, boolean> = {};

/** A section of Request Details; its body is built only while it is open. */
function Section({ id, label, defaultOpen, guide, help, children }: { id: string; label: string; defaultOpen: boolean; guide: string; help?: string; children: () => ReactNode }) {
  const [open, setOpen] = useState(sectionOpen[id] ?? defaultOpen);
  const toggle = (next: boolean) => { sectionOpen[id] = next; setOpen(next); };
  // Reset This Workspace forgets every section's choice, including those of sections not shown now (user, 2026-10-31).
  useWorkspaceReset(() => { for (const key of Object.keys(sectionOpen)) delete sectionOpen[key]; });
  return (
    <details className="phx-request-section" open={open} data-default-open={defaultOpen ? "" : undefined} data-guide={guide} onToggle={(e) => toggle(e.currentTarget.open)}>
      <summary data-help={help}>{label}</summary>
      {open && <div className="phx-request-section-body">{children()}</div>}
    </details>
  );
}

export function RequestDetailsWindow() {
  const a = useApprovals();
  const r = a?.current;
  if (!a || !r) return <p className="phx-window-empty">{t("workspace.requestDetailsEmpty")}</p>;
  const pending = r.status === "pending";
  return (
    <div className="phx-request-details" key={r.id}>
      <Section id="request" label={t("approvals.colRequest")} guide="window.requestDetails#request" help="help.requestSection" defaultOpen>
        {() => (
          <>
            <h4 className="phx-request-title">{diffSummary(r.diff) || friendlyToolName(r.tool_name)}</h4>
            <div className="approval-detail-meta">
              <span className="stat-label">{t("approvals.metaAccessKey")}</span><span>{r.access_key_name}</span>
              <span className="stat-label">{t("approvals.metaTool")}</span><span><code>{friendlyToolName(r.tool_name)}</code></span>
              <span className="stat-label">{t("approvals.metaCapability")}</span><span><code>{r.cap_name}</code></span>
              <span className="stat-label">{t("approvals.metaCreated")}</span><span>{formatDateTime(r.created_at)}</span>
              <span className="stat-label">{t("approvals.metaExpires")}</span><span>{formatDateTime(r.expires_at)}</span>
              {r.resolved_at && <><span className="stat-label">{t("approvals.statusResolved")}</span><span>{formatDateTime(r.resolved_at)}</span></>}
              <span className="stat-label">{t("approvals.metaStatus")}</span><span><ApprovalStatusLabel record={r} claimed={a.isClaimed(r)} /></span>
            </div>
            {!pending && r.rejected_reason && (
              <div className="banner banner-error">
                <strong>{t(r.status === "rejected" ? "approvals.resolvedRejected" : r.status === "cancelled" ? "approvals.resolvedCancelled" : "approvals.resolvedReason")}:</strong>{" "}
                {friendlyReason(r)}
              </div>
            )}
          </>
        )}
      </Section>
      <Section id="args" label={t("approvals.detailTabArgs")} guide="window.requestDetails#raw-args" help="help.argumentsSection" defaultOpen={false}>
        {() => <YamlView value={toYaml(r.args as Record<string, unknown>)} />}
      </Section>
      {!pending && (
        <Section id="result" label={t("approvals.detailTabResult")} guide="window.requestDetails#result" help="help.resultSection" defaultOpen>
          {() => (r.result == null
            ? <p className="approvals-empty">{r.rejected_reason
              ? t("approvals.noResult", { status: approvalStatusLabel(r.status), reason: friendlyReason(r) })
              : t("approvals.noResultRecorded", { status: approvalStatusLabel(r.status) })}</p>
            : <YamlView value={toYaml(r.result as Record<string, unknown>)} />)}
        </Section>
      )}
    </div>
  );
}
