/**
 * The words the Approvals windows show for a request: its localized summary
 * and list title, a concise executor error, a readable rejection reason, and
 * the outcome of a resolved request. Everything is localized; raw diagnostics
 * stay in Request Details.
 */

import type { ApprovalDiff, ApprovalRecord } from "../types";
import { hasMessage, t } from "../i18n";
import { localizedApprovalReason } from "./approval_reason";
import { friendlyApprovalSummary } from "./approval_summary";

/** The approval's summary in the operator's language.
 *
 *  The backend stores the English sentence (it doubles as the audit trail) plus
 *  the catalog key and params that produced it. Falls back to the stored English
 *  for older records and for a key this bundle does not know (a newer backend
 *  behind a cached panel). */
export function diffSummary(diff: ApprovalDiff | undefined): string {
  if (diff?.summary_key && hasMessage(diff.summary_key)) {
    return t(diff.summary_key, diff.summary_params);
  }
  return diff?.summary ?? "";
}

/** The list title: the friendly summary, or the stored summary for a record too old for the resolver. */
export function approvalListTitle(diff: ApprovalDiff | undefined): string {
  const friendly = friendlyApprovalSummary(diff).title;
  const unknown = t("approvalSummary.fallback.unknown.title");
  return friendly === unknown && diffSummary(diff) ? diffSummary(diff) : friendly;
}

const FRIENDLY_DETAIL_MAX_CHARS = 280;

export function boundedFriendlyDetail(value: string): string {
  const compact = value.replace(/\s+/g, " ").trim();
  if (compact.length <= FRIENDLY_DETAIL_MAX_CHARS) return compact;
  return `${compact.slice(0, FRIENDLY_DETAIL_MAX_CHARS - 3).trimEnd()}...`;
}

function structuredErrorText(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  for (const key of ["error", "message", "reason"]) {
    const candidate = record[key];
    if (typeof candidate === "string" && candidate.trim()) return candidate;
    if (candidate && typeof candidate === "object") {
      const nested = (candidate as Record<string, unknown>).message;
      if (typeof nested === "string" && nested.trim()) return nested;
    }
  }
  return null;
}

/** Pull one concise tool error out of a resolved approval's saved result.
 *
 * A failure may be saved as a JSON document holding the short `error` plus
 * complete MESA explanations. Summary must extract the sentence, never render
 * that diagnostic document as prose; Details retains it verbatim.
 */
export function extractResultErrorText(result: unknown): string | null {
  if (!result || typeof result !== "object") return null;
  const toolResult = (result as Record<string, unknown>).tool_result;
  if (!toolResult || typeof toolResult !== "object") return null;
  const direct = structuredErrorText(toolResult);
  if (direct) return boundedFriendlyDetail(direct);
  const content = (toolResult as Record<string, unknown>).content;
  if (!Array.isArray(content)) return null;
  for (const item of content) {
    const text = item && typeof item === "object" ? (item as Record<string, unknown>).text : null;
    if (typeof text !== "string" || !text.trim()) continue;
    try {
      const parsed = JSON.parse(text);
      const structured = structuredErrorText(parsed);
      if (structured) return boundedFriendlyDetail(structured);
    } catch {
      // Plain-text executor errors are the normal shape.
    }
    return boundedFriendlyDetail(text);
  }
  return null;
}

const LOCALIZED_EXECUTOR_ERRORS: Readonly<Record<string, string>> = {
  "The integration's state, resource membership, permissions, or effective MESA profile changed after approval. Review it again.":
    "approvalSummary.history.error.integrationChanged",
  "The integration logger set, override, or visibility changed after approval. Review it again.":
    "approvalSummary.history.error.integrationLoggerChanged",
  "Disabled integrations cannot be reloaded.":
    "approvalSummary.history.error.integrationDisabled",
  "This integration does not currently support reload.":
    "approvalSummary.history.error.integrationReloadUnsupported",
  "Integration not found.":
    "approvalSummary.history.error.integrationNotFound",
  "Failed to reload integration.":
    "approvalSummary.history.error.integrationReloadFailed",
};

const INTEGRATION_STATE_NOT_RELOADABLE = /^Integration state (.+) is not reloadable\.$/;

/** Keep Summary fully localized; raw diagnostics remain available in Details. */
export function localizedResultErrorText(result: unknown): string | null {
  const detail = extractResultErrorText(result);
  if (!detail) return null;
  const key = LOCALIZED_EXECUTOR_ERRORS[detail];
  if (key) return t(key);
  const stateMatch = detail.match(INTEGRATION_STATE_NOT_RELOADABLE);
  if (stateMatch) {
    return t("approvalSummary.history.error.integrationStateNotReloadable", {
      state: stateMatch[1],
    });
  }
  return t("approvalSummary.history.error.generic");
}

/** Readable rejection/cancellation reason: slug -> label, and for an
 *  execution_failed the underlying tool error (e.g. "Forbidden.", a validation
 *  message) instead of the bare slug. Free-text admin reasons pass through. */
export function friendlyReason(record: ApprovalRecord): string {
  const reason = record.rejected_reason;
  if (!reason) return "";
  if (reason === "execution_failed") {
    const detail = localizedResultErrorText(record.result);
    if (detail) return t("approvals.reasonExecutionFailedDetail", { detail });
  }
  return localizedApprovalReason(reason);
}

/** What became of a resolved request, as one sentence. */
export function approvalOutcome(record: ApprovalRecord): string {
  if (record.rejected_reason === "execution_interrupted") {
    return t("approvalSummary.history.interrupted.body");
  }
  if (record.rejected_reason === "execution_failed") {
    return t("approvalSummary.history.failed.body", {
      error: localizedResultErrorText(record.result) || friendlyReason(record),
    });
  }
  if (record.status === "approved") return t("approvalSummary.history.approved.body");
  if (record.status === "rejected") {
    return record.rejected_reason
      ? t("approvalSummary.history.rejectedReason.body", { reason: boundedFriendlyDetail(friendlyReason(record)) })
      : t("approvalSummary.history.rejected.body");
  }
  if (record.status === "failed") {
    return t("approvalSummary.history.failed.body", {
      error: localizedResultErrorText(record.result) || friendlyReason(record),
    });
  }
  if (record.status === "expired") return t("approvalSummary.history.expired.body");
  return record.rejected_reason
    ? t("approvalSummary.history.cancelledReason.body", { reason: boundedFriendlyDetail(friendlyReason(record)) })
    : t("approvalSummary.history.cancelled.body");
}
