import { BusyButton } from "./BusyButton";
import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { t, tn } from "../i18n";
import { localeDateTime, localeNumber } from "../i18n/format";
import { InlineConfirm } from "../ui/InlineConfirm";
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
      {missing > 0 && <small>{tn("routingStats.missing", missing, { count: localeNumber(missing) })}</small>}</>;
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
      {error && !confirm && <p className="banner banner-error" role="alert">{t("routingStats.error")}</p>}
      {confirm ? <InlineConfirm label={t("routingStats.reset")} tone="danger" confirmLabel={t("routingStats.reset")} confirmIcon="restoreVersion"
        confirmClassName="btn btn-danger btn-sm" cancelLabel={t("common.cancel")} busy={busy}
        error={error ? t("routingStats.error") : null} onConfirm={() => void load(true)} onCancel={() => setConfirm(false)}>
        <p>{t("routingStats.confirm")}</p>
      </InlineConfirm> : <div className="routing-statistics-actions">
        <BusyButton type="button" className="btn btn-text btn-sm" busy={busy} onClick={() => void load()}>{t("common.refresh")}</BusyButton>
        <BusyButton type="button" className="btn btn-text btn-sm" busy={busy} disabled={!stats} onClick={() => setConfirm(true)}>{t("routingStats.reset")}</BusyButton>
      </div>}
    </div>}
  </details>;
}
