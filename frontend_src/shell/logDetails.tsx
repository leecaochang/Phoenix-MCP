/**
 * The Logs workspace's shared selection and the Log Details window that shows
 * it. Choosing an audit entry or a conversation in its log fills Log Details,
 * opening or unfolding it, and focus stays in the log. The details say which
 * log the record came from and step through that log's records in its shown
 * order. A refresh of the log keeps the chosen record and its open sections.
 */

import { createContext, useContext, useSyncExternalStore } from "react";
import type { AuditEntry, ConversationSummary } from "../types";
import { AuditEntryDetail, auditEntryKey } from "../components/AuditEntry";
import { ConversationDetail, EntryDetail, SignalIcon, label, outcomeIcon, outcomeSignal, rowStatus } from "../components/ConversationRecord";
import { localeDateTime, localeNumber, t, tn } from "../i18n";
import { ActionButton } from "../ui/ActionButton";

export type LogRecord =
  | {
    kind: "audit";
    entry: AuditEntry;
    /** The log's entries in their shown order, for Previous and Next. */
    ordered: AuditEntry[];
    /** The access key's current name for an entry, as the log shows it. */
    nameOf: (entry: AuditEntry) => string;
  }
  | {
    kind: "conversation";
    entry: ConversationSummary;
    ordered: ConversationSummary[];
    accessKeyNames: Record<string, string>;
  };

export type LogKind = LogRecord["kind"];

function keyOf(record: LogRecord, entry: LogRecord["entry"] = record.entry): string {
  return record.kind === "audit" ? auditEntryKey(entry as AuditEntry) : (entry as ConversationSummary).id;
}

export class LogSelection {
  private value: LogRecord | null = null;
  private listeners = new Set<() => void>();
  private version = 0;
  /** Called after a selection, so the shell can bring Log Details into view. */
  onSelect: (() => void) | null = null;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getVersion = (): number => this.version;
  get = (): LogRecord | null => this.value;

  /** The chosen record's key when it came from this log. */
  selectedKey = (kind: LogKind): string | null => (this.value?.kind === kind ? keyOf(this.value) : null);

  set(value: LogRecord | null, reveal = true): void {
    this.value = value;
    this.version++;
    for (const listener of this.listeners) listener();
    if (value && reveal) this.onSelect?.();
  }

  /**
   * After the Conversation Log refreshes: show the chosen conversation's newer
   * copy and keep the stepping order current, without moving anything.
   */
  follow(fresh: ConversationSummary[]): void {
    const v = this.value;
    if (v?.kind !== "conversation") return;
    const byId = new Map(fresh.map((e) => [e.id, e]));
    const entry = byId.get(v.entry.id) ?? v.entry;
    const ordered = v.ordered.map((e) => byId.get(e.id)).filter((e): e is ConversationSummary => !!e);
    if (entry === v.entry && ordered.length === v.ordered.length && ordered.every((e, i) => e === v.ordered[i])) return;
    this.set({ ...v, entry, ordered }, false);
  }

  /** Step to the previous or next record in the log's shown order. */
  step(offset: -1 | 1): void {
    const v = this.value;
    if (!v) return;
    const at = v.ordered.findIndex((e) => keyOf(v, e) === keyOf(v));
    const next = v.ordered[at + offset];
    if (next) this.set({ ...v, entry: next } as LogRecord, false);
  }
}

export const LogSelectionContext = createContext<LogSelection | null>(null);

/** The shared selection, following its changes; null outside the shell. */
export function useLogSelection(): LogSelection | null {
  const selection = useContext(LogSelectionContext);
  useSyncExternalStore(selection?.subscribe ?? (() => () => {}), selection?.getVersion ?? (() => 0));
  return selection;
}

function ConversationHeading({ entry }: { entry: ConversationSummary }) {
  const status = rowStatus(entry);
  const signal = outcomeSignal(status);
  const title = entry.excerpt || (entry.prompt_attachments ? t("conversations.attachmentPrompt") : entry.first_tool)
    || t(entry.coverage === "tool_exchange" ? "conversations.toolExchange" : "conversations.promptUnavailable");
  return (
    <div className="phx-log-record-head">
      <div>
        <h4>{title}</h4>
        <p className="conversation-secondary">
          {label("source", entry.source)} · {localeDateTime(entry.latest_at ?? entry.started_at)}
          {entry.turns ? ` · ${t("conversations.turns", { count: localeNumber(entry.turns.length) })}` : ""}
        </p>
        {entry.tool_count > 0 && (
          <p className="conversation-secondary">
            {[tn("conversations.toolCount", entry.tool_count), entry.change_count ? tn("conversations.changeCount", entry.change_count) : "",
              t("conversations.execution", { outcome: label("outcome", entry.execution_status) })].filter(Boolean).join(" · ")}
          </p>
        )}
      </div>
      <span className={`phx-outcome conversation-signal-${signal}`}><SignalIcon name={outcomeIcon(status)} signal={signal} />{label("outcome", status)}</span>
    </div>
  );
}

export function LogDetailsContent() {
  const selection = useLogSelection();
  const value = selection?.get() ?? null;
  if (!selection || !value) return <p className="phx-window-empty">{t("workspace.logDetailsEmpty")}</p>;
  const at = value.ordered.findIndex((e) => keyOf(value, e) === keyOf(value));
  return (
    <div className="phx-log-details">
      <div className="phx-log-details-bar">
        <h3 className="audit-section-title">{value.kind === "audit" ? t("audit.detailTitle") : t("conversations.conversation")}</h3>
        <div className="phx-log-details-nav">
          <ActionButton icon="previous" label={t("workspace.previousRecord")} disabled={at <= 0} onClick={() => selection.step(-1)} className="btn btn-outline btn-sm" />
          <ActionButton icon="next" label={t("workspace.nextRecord")} disabled={at < 0 || at >= value.ordered.length - 1} onClick={() => selection.step(1)} className="btn btn-outline btn-sm" />
        </div>
      </div>
      {value.kind === "audit"
        ? <AuditEntryDetail key={auditEntryKey(value.entry)} entry={value.entry} accessKeyName={value.nameOf(value.entry)} />
        : (
          <div className="phx-log-record" key={value.entry.id}>
            <ConversationHeading entry={value.entry} />
            {value.entry.turns
              ? <ConversationDetail entry={value.entry} accessKeyNames={value.accessKeyNames} />
              : <EntryDetail id={value.entry.id} revision={value.entry.updated_at} accessKeyNames={value.accessKeyNames} />}
          </div>
        )}
    </div>
  );
}
