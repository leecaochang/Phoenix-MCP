/**
 * One audit entry: its stable key, outcome wording and icon, and the detail
 * Log Details shows (compact metadata, then the recorded payload). The
 * current name of a renamed access key is shown, with the name recorded on
 * the entry beside it when they differ.
 */

import type { AuditEntry, Outcome } from "../types";
import { YamlView, toYaml } from "./YamlView";
import { friendlyResource } from "../utils";
import { localeDateTime, t } from "../i18n";
import { auditSourceLabel, isNamedAuditSource } from "../utils/audit_source";
import { SignalIcon, outcomeIcon, outcomeSignal } from "./ConversationRecord";

export function auditEntryKey(entry: AuditEntry): string {
  // One approval request intentionally emits approved plus executed/failed
  // lifecycle rows under the same request id. Timestamp and method distinguish
  // those rows for React identity and keyboard detail navigation.
  return `${entry.request_id}:${entry.timestamp}:${entry.method}`;
}

export function formatAccessKeyName(name: string): string {
  return name.replace(/^(admin):(.+)$/, "$1 ($2)");
}

export function formatAccessKeyNameShort(name: string): string {
  return name.replace(/^(admin):(.{4}).+(.{4})$/, "$1 ($2...$3)");
}

const OUTCOME_LABEL_KEYS: Record<Outcome, string> = {
  allowed: "audit.outcomeAllowed",
  denied: "audit.outcomeDenied",
  not_found: "audit.outcomeNotFound",
  rate_limited: "audit.outcomeRateLimited",
  not_implemented: "audit.outcomeNotImplemented",
  invalid_request: "audit.outcomeInvalidRequest",
  pending_approval: "audit.outcomePendingApproval",
  cancelled: "audit.outcomeCancelled",
  unknown: "audit.outcomeUnknown",
};

/** Outcome label, with context for a session DELETE whose target is already gone. */
export function outcomeLabel(entry: AuditEntry): string {
  if (
    entry.method === "DELETE"
    && entry.outcome === "not_found"
    && (entry.resource === "mcp:session" || entry.resource === "[redacted]")
  ) {
    return t("audit.outcomeSessionAlreadyClosed");
  }
  const key = OUTCOME_LABEL_KEYS[entry.outcome];
  return key ? t(key) : entry.outcome;
}

/** "Advisory" when MESA only warned about the request; empty otherwise. */
export function mesaLabel(entry: AuditEntry): string {
  return entry.mesa_advisory ? t("mesa.modeAdvisory") : "";
}

/**
 * How the request's authority was decided: by the access key's permissions, or
 * not at all because the key is pass-through (MESA and approvals still apply).
 */
export function accessLabel(entry: AuditEntry): string {
  return t(entry.pass_through ? "audit.accessPassThrough" : "audit.accessPermissions");
}

/** The outcome as an icon and its words. */
export function AuditOutcome({ entry }: { entry: AuditEntry }) {
  const signal = outcomeSignal(entry.outcome);
  return (
    <span className={`phx-outcome conversation-signal-${signal}`}>
      <SignalIcon name={outcomeIcon(entry.outcome)} signal={signal} />
      <span>{outcomeLabel(entry)}</span>
    </span>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="phx-log-field">
      <dt>{label}</dt>
      <dd className={mono ? "phx-log-mono" : undefined}>{value}</dd>
    </div>
  );
}

/** One audit entry's recorded fields and payload, as Log Details shows them. */
export function AuditEntryDetail({ entry, accessKeyName }: { entry: AuditEntry; accessKeyName: string }) {
  // Render the recorded payload as YAML in HA's code editor (via YamlView);
  // if it is not valid JSON, show the raw string verbatim.
  const prettyPayload = entry.payload
    ? (() => { try { return toYaml(JSON.parse(entry.payload) as Record<string, unknown>); } catch { return entry.payload; } })()
    : null;
  return (
    <div className="phx-log-record">
      <div className="phx-log-record-head">
        <h4 className="phx-log-mono">{entry.method} · {friendlyResource(entry.resource)}</h4>
        <AuditOutcome entry={entry} />
      </div>
      <dl className="phx-log-fields">
        <Field label={t("audit.rowTime")} value={localeDateTime(entry.timestamp)} />
        <Field
          label={t("audit.rowAccessKey")}
          value={accessKeyName !== entry.access_key_name
            ? `${formatAccessKeyName(accessKeyName)} (${formatAccessKeyName(entry.access_key_name)})`
            : formatAccessKeyName(accessKeyName)}
        />
        <Field label={t("audit.rowAccess")} value={accessLabel(entry)} />
        <Field label={t("audit.rowMethod")} value={entry.method} mono />
        <Field label={t("audit.rowResource")} value={friendlyResource(entry.resource)} mono />
        <Field label={t("audit.rowOutcome")} value={outcomeLabel(entry)} />
        {entry.mesa_advisory && <Field label={t("audit.rowMesa")} value={t("audit.mesaAdvisory")} />}
        <Field label={t("audit.rowIp")} value={auditSourceLabel(entry.client_ip)} mono={!isNamedAuditSource(entry.client_ip)} />
        <Field label={t("audit.rowRequestId")} value={entry.request_id} mono />
      </dl>
      {prettyPayload && (
        <section className="audit-payload-section" aria-label={t("audit.rowPayload")}>
          <h5 className="phx-log-section-title">{t("audit.rowPayload")}</h5>
          <YamlView value={prettyPayload} label={t("audit.rowPayload")} />
        </section>
      )}
    </div>
  );
}
