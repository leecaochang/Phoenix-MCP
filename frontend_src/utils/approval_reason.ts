import { t } from "../i18n";

// Backend-owned reason slugs are data, not operator copy. Keep their labels in
// the catalog and use this helper on every surface that renders a reason.
export const APPROVAL_REASON_LABEL_KEYS: Readonly<Record<string, string>> = {
  access_key_inactive: "approvals.reason.access_key_inactive",
  capability_denied: "approvals.reason.capability_denied",
  target_out_of_scope: "approvals.reason.target_out_of_scope",
  target_missing: "approvals.reason.target_missing",
  rate_limited_at_execution: "approvals.reason.rate_limited_at_execution",
  kill_switch: "approvals.reason.kill_switch",
  admin_cancelled: "approvals.reason.admin_cancelled",
  access_key_revoked: "approvals.reason.access_key_revoked",
  access_key_expired: "approvals.reason.access_key_expired",
  execution_failed: "approvals.reason.execution_failed",
  execution_interrupted: "approvals.reason.execution_interrupted",
  approval_format_incompatible: "approvals.reason.approval_format_incompatible",
  agent_chat_ended: "approvals.reason.agent_chat_ended",
  agent_chat_wait_timed_out: "approvals.reason.agent_chat_wait_timed_out",
  phoenix_mcp_restarted: "approvals.reason.phoenix_mcp_restarted",
  phoenix_mcp_data_wiped: "approvals.reason.phoenix_mcp_data_wiped",
  integration_removed: "approvals.reason.integration_removed",
  recovery_review_replaced: "approvals.reason.recovery_review_replaced",
  // Stored as English before the slug existed.
  "Configuration changed; replaced by a fresh review.": "approvals.reason.recovery_review_replaced",
};

/** Localize a backend reason slug; preserve free-form operator/tool text. */
export function localizedApprovalReason(reason: string): string {
  const key = APPROVAL_REASON_LABEL_KEYS[reason];
  return key ? t(key) : reason;
}
