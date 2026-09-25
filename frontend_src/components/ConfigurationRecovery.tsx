/** Explicit administrator review of protected configuration recovery artifacts. */
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import { formatDateTime } from "../utils";
import { YamlView, toYaml } from "./YamlView";

export interface RecoveryRecord {
  id: string;
  resource: string;
  phase: string;
  active: boolean;
  created_at: string;
  before: Record<string, unknown> | null;
  source: Record<string, unknown> | null;
}

export function ConfigurationRecovery() {
  const [open, setOpen] = useState(false);
  const [records, setRecords] = useState<RecoveryRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [observed, setObserved] = useState<{ id: string; data: Record<string, unknown> } | null>(null);
  const [confirm, setConfirm] = useState<{ id: string; action: string; side?: string } | null>(null);
  const generation = useRef(0);
  const mutation = useRef(false);
  useEffect(() => () => { generation.current++; }, []);

  async function load() {
    if (mutation.current) return;
    setObserved(null);
    const mine = ++generation.current;
    setBusy(true); setError(false);
    try {
      const response = await api.listConfigurationRecovery();
      if (mine === generation.current) setRecords(response.records);
    } catch {
      if (mine === generation.current) setError(true);
    } finally {
      if (mine === generation.current) setBusy(false);
    }
  }

  async function act(id: string, action: string, side?: string) {
    if (mutation.current) return;
    mutation.current = true;
    const mine = ++generation.current;
    setBusy(true); setError(false); setObserved(null);
    try {
      const response = await api.configurationRecoveryAction(id, action, side);
      if (mine !== generation.current) return;
      setObserved({ id, data: response });
      setConfirm(null);
      if (response.result?.isError) setError(true);
      const fresh = await api.listConfigurationRecovery();
      if (mine === generation.current) setRecords(fresh.records);
    } catch {
      if (mine === generation.current) setError(true);
    } finally {
      mutation.current = false;
      if (mine === generation.current) setBusy(false);
    }
  }

  return <details className="config-recovery" open={open} onToggle={e => {
    const next = e.currentTarget.open;
    setOpen(next);
    if (next && !open) void load();
  }}>
    <summary>{t("recovery.title")}</summary>
    <p className="text-muted">{t("recovery.help")}</p>
    <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void load()}>{t("common.refresh")}</button>
    {busy && <p role="status">{t("common.loading")}</p>}
    {error && <p className="banner banner-warn" role="alert">{t("recovery.failed")}</p>}
    {!busy && !error && records.length === 0 && <p>{t("recovery.empty")}</p>}
    {records.map(record => <details key={record.id} className="config-recovery-record">
      <summary><code>{record.resource}</code> <span>{t(`recovery.${record.phase}`)}</span></summary>
      <p className="text-muted">{formatDateTime(record.created_at)}</p>
      {record.before && <><h4>{t("recovery.before")}</h4><YamlView value={toYaml(record.before)} /></>}
      {record.source && <><h4>{t("recovery.source")}</h4><YamlView value={toYaml(record.source)} /></>}
      <div className="config-recovery-actions">
        <button className="btn btn-outline btn-sm" disabled={busy || record.active} onClick={() => void act(record.id, "inspect")}>{t("recovery.inspect")}</button>
        <button className="btn btn-outline btn-sm" disabled={busy || record.active} onClick={() => setConfirm({ id: record.id, action: "reviewed" })}>{t("recovery.review")}</button>
        {(["before", "source"] as const).map(side => ["template_helper", "storage_helper"].includes(String(record[side]?.snapshot_type)) &&
          <button key={side} className="btn btn-outline btn-sm" disabled={busy || record.active || !["verified", "reviewed", "not_applied"].includes(record.phase)}
            onClick={() => setConfirm({ id: record.id, action: "restore", side })}>{t(side === "before" ? "recovery.restoreBefore" : "recovery.restoreSource")}</button>)}
        <button className="btn btn-ghost btn-sm" disabled={busy || record.active || !["verified", "reviewed", "not_applied"].includes(record.phase)}
          onClick={() => setConfirm({ id: record.id, action: "delete" })}>{t("recovery.delete")}</button>
      </div>
      {confirm?.id === record.id && <div className="banner banner-warn">
        <p>{confirm.action === "restore" && <strong>{t(confirm.side === "before" ? "recovery.before" : "recovery.source")}: </strong>}{t(`recovery.confirm_${confirm.action}`)}</p>
        <div className="config-recovery-actions">
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => void act(confirm.id, confirm.action, confirm.side)}>{t("recovery.confirm")}</button>
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => setConfirm(null)}>{t("common.cancel")}</button>
        </div>
      </div>}
      {observed?.id === record.id && <YamlView value={toYaml(observed.data)} />}
    </details>)}
  </details>;
}
