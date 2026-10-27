/**
 * The Approvals workspace's windows: Approvals lists the requests (with a
 * Show menu, search and the configurable table, and tick boxes while
 * approving several at once), Review shows the chosen request's exact change
 * with Approve and Reject, and Request Details shows its metadata, inputs and
 * result. Their shared state is `shell/approvals.tsx`.
 */

import { useContext, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import type { ApprovalRecord, ApprovalStatus, BatchApproveResult } from "../types";
import { ApprovalChange } from "../components/ApprovalChange";
import { ErrorMsg, Loading } from "../components/common";
import { YamlView, toYaml } from "../components/YamlView";
import { t } from "../i18n";
import { APPROVALS_SCOPES, useApprovals, type ApprovalsScope, type ApprovalsState } from "../shell/approvals";
import { ControllerContext } from "../shell/dock/TitleBar";
import { useToolbarTools, type Tool, type ToolGroup } from "../shell/tools";
import { ActionButton } from "../ui/ActionButton";
import { Icon, type IconName } from "../ui/Icon";
import { ConfigTable, type Column } from "../ui/table/ConfigTable";
import { approvalStatusLabel, formatDateTime, friendlyToolName } from "../utils";
import { approvalListTitle, approvalOutcome, diffSummary, friendlyReason } from "../utils/approval_text";
import { friendlyApprovalSummary } from "../utils/approval_summary";

const SCOPE_LABEL_KEYS: Record<ApprovalsScope, string> = {
  pending: "approvals.tabPending",
  history: "approvals.tabHistory",
  all: "approvals.scopeAll",
  approved: "approvals.filterApproved",
  rejected: "approvals.filterRejected",
  failed: "approvals.reason.execution_failed",
  expired: "approvals.filterExpired",
  cancelled: "approvals.filterCancelled",
};

const STATUS_LABEL_KEYS: Record<ApprovalStatus, string> = {
  pending: "approvals.statusPending",
  approved: "approvals.statusApproved",
  failed: "approvals.reason.execution_failed",
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
      {claimed ? t("approvals.processing") : t(STATUS_LABEL_KEYS[record.status])}
    </span>
  );
}

function useExpiresLabel(expiresAt: string, status: ApprovalStatus): string | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (status !== "pending") return;
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, [status]);
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
    <div className={`approvals-batch-result ${failed ? "is-error" : "is-ok"}`} role="status">
      <div className="approvals-batch-result-text">
        <span>{t("approvals.batchApplied", { count: result.applied.length })}</span>
        {failed && (
          <span>
            {t("approvals.batchStopped", {
              tool: friendlyToolName(failed.tool_name ?? ""),
              reason: failed.message || failed.error,
            })}
          </span>
        )}
        {result.remaining.length > 0 && (
          <span>{t("approvals.batchRemaining", { count: result.remaining.length })}</span>
        )}
      </div>
      <button type="button" className="btn btn-ghost" onClick={onDismiss}>{t("common.close")}</button>
    </div>
  );
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

function listColumns(a: ApprovalsState): Column<ApprovalRecord>[] {
  return [
    { id: "request", label: t("approvals.colRequest"), width: 220, maxWidth: 360, sortValue: (r) => approvalListTitle(r.diff), cell: (r) => approvalListTitle(r.diff), title: (r) => approvalListTitle(r.diff) },
    { id: "status", label: t("approvals.metaStatus"), width: 140, sortValue: (r) => (a.isClaimed(r) ? t("approvals.processing") : approvalStatusLabel(r.status)), cell: (r) => <ApprovalStatusLabel record={r} claimed={a.isClaimed(r)} /> },
    { id: "accessKey", label: t("approvals.metaAccessKey"), width: 140, hidden: true, sortValue: (r) => r.access_key_name, cell: (r) => r.access_key_name },
    { id: "tool", label: t("approvals.metaTool"), width: 170, hidden: true, sortValue: (r) => friendlyToolName(r.tool_name), cell: (r) => <code>{friendlyToolName(r.tool_name)}</code> },
    { id: "requested", label: t("approvals.colRequested"), width: 160, hidden: true, sortValue: (r) => r.created_at, cell: (r) => formatDateTime(r.created_at) },
    { id: "expires", label: t("approvals.metaExpires"), width: 120, hidden: true, sortValue: (r) => r.expires_at, cell: (r) => <ExpiresIn record={r} /> },
    { id: "resolved", label: t("approvals.statusResolved"), width: 160, hidden: true, sortValue: (r) => r.resolved_at ?? "", cell: (r) => (r.resolved_at ? formatDateTime(r.resolved_at) : "") },
    { id: "reason", label: t("approvals.resolvedReason"), width: 220, maxWidth: 360, hidden: true, sortValue: (r) => friendlyReason(r), cell: (r) => friendlyReason(r), title: (r) => friendlyReason(r) || undefined },
  ];
}

export function ApprovalsListWindow() {
  const a = useApprovals();
  const groups: ToolGroup[] = a ? [{
    id: "approvals",
    label: t("shell.tabApprovals"),
    tools: [
      { id: "refresh", label: t("workspace.refreshWindow", { window: t("shell.tabApprovals") }), short: t("common.refresh"), icon: "refresh", onSelect: a.refresh },
      ...(a.batchMode
        ? [
          { id: "approveSelected", label: t("approvals.approveSelected", { count: a.pickedCount }), icon: "approveSelected" as IconName, disabled: a.pickedCount === 0 || a.batchBusy, onSelect: () => { void a.approveSelected(); } },
          { id: "cancelSelection", label: t("approvals.cancelSelection"), icon: "undo" as IconName, disabled: a.batchBusy, onSelect: a.endBatch },
        ]
        : [{ id: "selectMultiple", label: t("approvals.selectMultiple"), icon: "selectMultiple" as IconName, disabled: !a.canBatch, onSelect: a.startBatch }]),
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
      <div className="approvals-filter-row">
        <label className="approvals-scope">
          <span>{t("approvals.show")}</span>
          <select className="input input-auto" value={a.scope} onChange={(e) => a.setScope(e.target.value as ApprovalsScope)}>
            {APPROVALS_SCOPES.map((scope) => <option key={scope} value={scope}>{scopeLabel(scope)}</option>)}
          </select>
        </label>
        <input type="search" className="input" placeholder={t("approvals.searchPlaceholder")} aria-label={t("approvals.searchAria")}
          value={a.search} onChange={(e) => a.setSearch(e.target.value)} />
      </div>
      {a.error && <ErrorMsg msg={a.error} />}
      {a.batchResult && <BatchResultMsg result={a.batchResult} onDismiss={a.dismissBatchResult} />}
      {a.loading && !a.shown.length && <Loading />}
      <ConfigTable
        id="approvals"
        label={t("shell.tabApprovals")}
        columns={listColumns(a)}
        rows={a.shown}
        rowKey={(r) => r.id}
        selectedKey={a.current?.id ?? null}
        onSelect={(r) => a.select(r)}
        rowLabel={(r) => `${approvalListTitle(r.diff)}, ${a.isClaimed(r) ? t("approvals.processing") : approvalStatusLabel(r.status)}`}
        empty={a.loading ? null : empty}
        selection={a.batchMode ? {
          picked: a.picked,
          selectable: (r) => a.selectableIds.includes(r.id),
          onToggle: a.togglePick,
          onToggleAll: a.toggleAll,
          all: a.allPicked,
          label: (r) => t("approvals.selectOne", { tool: friendlyToolName(r.tool_name) }),
          allLabel: t("approvals.selectAll"),
        } : undefined}
      />
      {a.hasMore && (
        <div className="phx-table-footer">
          <ActionButton icon="loadMore" label={t("common.loadMore")} busy={a.loadingMore} onClick={a.loadMore} />
        </div>
      )}
    </div>
  );
}

function ReasonRow({ a, locked }: { a: ApprovalsState; locked: boolean }) {
  const input = useRef<HTMLInputElement | null>(null);
  useEffect(() => { input.current?.focus(); }, []);
  return (
    <div className="approval-reject-row phx-review-reason">
      <label htmlFor="phx-review-reason" className="approval-reason-label">{t("approvalSummary.action.optionalReason")}</label>
      <input ref={input} id="phx-review-reason" type="text" className="approval-reason-input" value={a.reason}
        placeholder={t("approvalSummary.action.reasonPlaceholder")} disabled={locked}
        onChange={(e) => a.setReason(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); void a.reject(); }
          if (e.key === "Escape") { e.preventDefault(); a.setRejecting(false); }
        }} />
      <ActionButton icon="check" label={t("approvals.confirmReject")} busy={locked} onClick={() => { void a.reject(); }} className="btn btn-sm phx-decision-reject" />
      <ActionButton icon="undo" label={t("approvals.cancelReject")} busy={a.deciding !== null} onClick={() => a.setRejecting(false)} className="btn btn-outline btn-sm" />
    </div>
  );
}

const noSubscribe = () => () => {};
const noVersion = () => 0;

export function ReviewWindow() {
  const a = useApprovals();
  const controller = useContext(ControllerContext);
  useSyncExternalStore(controller?.subscribe ?? noSubscribe, controller?.getVersion ?? noVersion);
  const detailsOpen = !!controller?.hostOf("requestDetails");
  useToolbarTools(controller ? [{
    id: "review",
    label: t("workspace.review"),
    tools: [{
      id: "requestDetails",
      label: t("workspace.requestDetails"),
      icon: "requestDetails",
      pressed: detailsOpen,
      onSelect: () => {
        const group = controller.groupOf("requestDetails");
        if (group) controller.close(group);
        else controller.ensureOpen("requestDetails");
      },
    }],
  }] : []);
  const r = a?.current;
  if (!a || !r) return <p className="phx-window-empty">{t("workspace.reviewEmpty")}</p>;

  const pending = r.status === "pending";
  const claimed = a.isClaimed(r);
  const locked = a.locked(r);
  // The summary sentence says what deciding does, which some requests depend on (a recovery, for one).
  const summary = friendlyApprovalSummary(r.diff);
  return (
    <div className="phx-review" key={r.id}>
      <div className="phx-review-head">
        <h3 className="phx-review-title">{approvalListTitle(r.diff)}</h3>
        <p className="phx-review-context">
          <ApprovalStatusLabel record={r} claimed={claimed} />
          <span>{r.access_key_name}</span>
          <span>{pending ? <ExpiresIn record={r} /> : t("approvalSummary.history.resolvedAt", { time: formatDateTime(r.resolved_at || r.created_at) })}</span>
        </p>
      </div>
      {pending && (
        <div className="phx-review-actions" role="group" aria-label={t("workspace.review")}>
          <ActionButton icon="check" label={t("approvals.approve")} busy={locked || a.rejecting} onClick={() => { void a.approve(); }} className="btn btn-sm phx-decision-approve" />
          <ActionButton icon="reject" label={t("approvals.reject")} busy={locked} pressed={a.rejecting} onClick={() => a.setRejecting(!a.rejecting)} className="btn btn-sm phx-decision-reject" />
        </div>
      )}
      {pending && a.rejecting && <ReasonRow a={a} locked={locked} />}
      {a.decisionError && <ErrorMsg msg={a.decisionError} />}
      {summary.body && <p className="phx-review-body">{summary.body}</p>}
      <div className="phx-review-change"><ApprovalChange record={r} /></div>
      {!pending && (
        <p className={`phx-review-outcome approval-status-${r.status}`}>
          <Icon name={STATUS_ICONS[r.status]} size={16} />
          <span>{approvalOutcome(r)}</span>
        </p>
      )}
    </div>
  );
}

/** A section of Request Details; its body is built only while it is open. */
function Section({ label, defaultOpen, children }: { label: string; defaultOpen: boolean; children: () => ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details className="phx-request-section" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>{label}</summary>
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
      <Section label={t("approvals.colRequest")} defaultOpen>
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
      <Section label={t("approvals.detailTabArgs")} defaultOpen={false}>
        {() => <YamlView value={toYaml(r.args as Record<string, unknown>)} />}
      </Section>
      {!pending && (
        <Section label={t("approvals.detailTabResult")} defaultOpen>
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
