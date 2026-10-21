/**
 * The Logs workspace's shared selection and the Log Details window that shows
 * it. Selecting a record in a log fills Log Details, opening or unfolding it,
 * instead of opening a dialog; focus stays in the log.
 */

import { createContext, useContext, useSyncExternalStore } from "react";
import type { AuditEntry } from "../types";
import { AuditEntryDetail, auditEntryKey } from "../components/AuditTable";
import { t } from "../i18n";
import { Icon } from "../ui/Icon";

export interface AuditSelection {
  entry: AuditEntry;
  /** The log's entries in their displayed order, for Previous and Next. */
  ordered: AuditEntry[];
  /** The access key's current name for an entry, as the log shows it. */
  nameOf: (entry: AuditEntry) => string;
}

export class LogSelection {
  private value: AuditSelection | null = null;
  private listeners = new Set<() => void>();
  private version = 0;
  /** Called after a selection, so the shell can bring Log Details into view. */
  onSelect: (() => void) | null = null;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getVersion = (): number => this.version;
  get = (): AuditSelection | null => this.value;
  selectedKey = (): string | null => (this.value ? auditEntryKey(this.value.entry) : null);

  set(value: AuditSelection | null, reveal = true): void {
    this.value = value;
    this.version++;
    for (const listener of this.listeners) listener();
    if (value && reveal) this.onSelect?.();
  }

  /** Step to the previous or next entry in the log's displayed order. */
  step(offset: -1 | 1): void {
    const v = this.value;
    if (!v) return;
    const at = v.ordered.findIndex((e) => auditEntryKey(e) === auditEntryKey(v.entry));
    const next = v.ordered[at + offset];
    if (next) this.set({ ...v, entry: next }, false);
  }
}

export const LogSelectionContext = createContext<LogSelection | null>(null);

/** The shared selection while drawn in the shell; null on its own, where the log keeps its dialog. */
export function useLogSelection(): LogSelection | null {
  const selection = useContext(LogSelectionContext);
  useSyncExternalStore(selection?.subscribe ?? (() => () => {}), selection?.getVersion ?? (() => 0));
  return selection;
}

export function LogDetailsContent() {
  const selection = useLogSelection();
  const value = selection?.get() ?? null;
  if (!selection || !value) return <p className="phx-log-details-empty">{t("workspace.logDetailsEmpty")}</p>;
  const at = value.ordered.findIndex((e) => auditEntryKey(e) === auditEntryKey(value.entry));
  return (
    <div className="phx-log-details">
      <div className="phx-log-details-bar">
        <h3 className="audit-section-title">{t("audit.detailTitle")}</h3>
        <div className="phx-log-details-nav">
          <button type="button" className="phx-toolbar-button" aria-label={t("workspace.previousRecord")} title={t("workspace.previousRecord")}
            disabled={at <= 0} onClick={() => selection.step(-1)}>
            <Icon name="previous" size={16} />
          </button>
          <button type="button" className="phx-toolbar-button" aria-label={t("workspace.nextRecord")} title={t("workspace.nextRecord")}
            disabled={at < 0 || at >= value.ordered.length - 1} onClick={() => selection.step(1)}>
            <Icon name="next" size={16} />
          </button>
        </div>
      </div>
      <AuditEntryDetail key={auditEntryKey(value.entry)} entry={value.entry} accessKeyName={value.nameOf(value.entry)} />
    </div>
  );
}
