import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import { localeDateTime, localeNumber } from "../i18n/format";
import { Modal } from "./Modal";
import type { RoutingSurfaceStatistics } from "../types";

const OUTCOME_LABELS = {
  local: "routingStats.local",
  decision: "routingStats.decision",
  provider: "routingStats.provider",
  pending: "routingStats.pending",
  failed: "routingStats.failed",
  cancelled: "routingStats.cancelled",
} as const;

export function RoutingStatistics({ surface }: { surface: "voice" | "chat" }) {
  const [open, setOpen] = useState(false);
  const [stats, setStats] = useState<RoutingSurfaceStatistics | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const generation = useRef(0);
  useEffect(() => () => { generation.current++; }, []);
  async function load(reset = false) {
    const current = ++generation.current;
    setBusy(true); setError(false);
    try {
      const result = reset ? await api.resetRoutingStatistics(surface) : await api.getRoutingStatistics();
      if (current !== generation.current) return;
      setStats(result[surface]); setConfirm(false);
    } catch {
      if (current === generation.current) setError(true);
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }
  const outcomes = stats?.outcomes;
  const total = outcomes ? Object.values(outcomes).reduce((a, b) => a + b, 0) : 0;
  const percent = outcomes && total ? Math.round(100 * (outcomes.local + outcomes.decision) / total) : 0;
  function tokens(usage: RoutingSurfaceStatistics["provider_usage"]) {
    const missing = usage.requests - usage.reports;
    return <>{missing && !usage.reports ? t("routingStats.unavailable") :
      `${localeNumber(usage.input_tokens)} / ${localeNumber(usage.output_tokens)}`}
      {missing > 0 && <small>{t("routingStats.missing", { count: localeNumber(missing) })}</small>}</>;
  }
  return <details className="routing-statistics" open={open} onToggle={event => {
    const expanded = event.currentTarget.open;
    if (expanded === open) return;
    setOpen(expanded);
    if (expanded) void load();
  }}>
    <summary>{t("routingStats.title")}</summary>
    {open && <div className="routing-statistics-content" aria-busy={busy}>
      <p className="agentcli-settings-hint">{t("routingStats.scope")}</p>
      {stats && <>
        <p>{total ? t("routingStats.headline", { percent: localeNumber(percent) }) : t("routingStats.empty")}</p>
        <p className="agentcli-settings-hint">{t("routingStats.since", { date: localeDateTime(stats.since) })}</p>
        <dl className="routing-statistics-values">
          {(Object.keys(OUTCOME_LABELS) as Array<keyof typeof OUTCOME_LABELS>).map(key =>
            <Fragment key={key}><dt>{t(OUTCOME_LABELS[key])}</dt><dd>{localeNumber(key === "provider" ? stats.provider_turns : stats.outcomes[key])}</dd></Fragment>)}
          <dt>{t("routingStats.decisionTokens")}</dt><dd>{tokens(stats.decision_usage)}</dd>
          <dt>{t("routingStats.providerTokens")}</dt><dd>{tokens(stats.provider_usage)}</dd>
        </dl>
        <p className="agentcli-settings-hint">{t("routingStats.note")}</p>
      </>}
      {busy && <p role="status">{t("common.loading")}</p>}
      {error && <p className="banner banner-error" role="alert">{t("routingStats.error")}</p>}
      <div className="routing-statistics-actions">
        <button type="button" className="btn btn-text btn-sm" disabled={busy} onClick={() => void load()}>{t("common.refresh")}</button>
        <button type="button" className="btn btn-text btn-sm" disabled={busy || !stats} onClick={() => setConfirm(true)}>{t("routingStats.reset")}</button>
      </div>
    </div>}
    {confirm && <Modal titleId={`routing-stats-reset-${surface}`} onClose={busy ? undefined : () => setConfirm(false)}>
      <h3 id={`routing-stats-reset-${surface}`} className="modal-title">{t("routingStats.reset")}</h3>
      <p>{t("routingStats.confirm")}</p>
      {error && <p className="banner banner-error" role="alert">{t("routingStats.error")}</p>}
      <div className="modal-actions">
        <button type="button" className="btn btn-danger" disabled={busy} onClick={() => void load(true)}>{t("routingStats.reset")}</button>
        <button type="button" className="btn btn-text" disabled={busy} onClick={() => setConfirm(false)}>{t("common.cancel")}</button>
      </div>
    </Modal>}
  </details>;
}
