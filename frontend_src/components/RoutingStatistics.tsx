import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { t, tn } from "../i18n";
import { localeDateTime, localeNumber } from "../i18n/format";
import { ActionButton } from "../ui/ActionButton";
import { InlineConfirm } from "../ui/InlineConfirm";
import type { RoutingSurface, RoutingSurfaceStatistics } from "../types";

type Outcome = keyof RoutingSurfaceStatistics["outcomes"];

const OUTCOME_LABELS: Partial<Record<Outcome, string>> = {
  local: "routingStats.local",
  decision: "routingStats.decision",
  provider: "routingStats.provider",
  pending: "routingStats.pending",
  failed: "routingStats.failed",
  cancelled: "routingStats.cancelled",
};

// Evaluate has no local intents and no conversational model: an answer, a refusal, a failure.
const EVALUATE_LABELS: Partial<Record<Outcome, string>> = {
  decision: "routingStats.evaluated",
  refused: "routingStats.refused",
  failed: "routingStats.failed",
  cancelled: "routingStats.cancelled",
};

const isEvaluate = (surface: RoutingSurface) => surface === "ai_task";

/** What a surface's routing counts cover, for Help over them. */
const scopeHelp = (surface: RoutingSurface) => isEvaluate(surface)
  ? `${t("routingStats.aiTaskScope")}${t("common.sentenceSeparator")}${t("routingStats.aiTaskNote")}`
  : `${t("routingStats.scope")}${t("common.sentenceSeparator")}${t("routingStats.note")}`;

/** A surface's routing counts with Refresh and Reset, loaded when first shown. */
export function RoutingStatisticsContent({ surface }: { surface: RoutingSurface }) {
  const [stats, setStats] = useState<RoutingSurfaceStatistics | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const generation = useRef(0);
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
  useEffect(() => () => { generation.current++; }, []);
  const latestLoad = useRef(load);
  latestLoad.current = load;
  useEffect(() => { void latestLoad.current(); }, []);
  const outcomes = stats?.outcomes;
  const total = outcomes ? Object.values(outcomes).reduce((a, b) => a + b, 0) : 0;
  const percent = outcomes && total ? Math.round(100 * (outcomes.local + outcomes.decision) / total) : 0;
  const evaluate = isEvaluate(surface);
  const labels = evaluate ? EVALUATE_LABELS : OUTCOME_LABELS;
  function tokens(usage: RoutingSurfaceStatistics["provider_usage"]) {
    const missing = usage.requests - usage.reports;
    return <>{missing && !usage.reports ? t("routingStats.unavailable") :
      `${localeNumber(usage.input_tokens)} / ${localeNumber(usage.output_tokens)}`}
      {missing > 0 && <small>{tn("routingStats.missing", missing, { count: localeNumber(missing) })}</small>}</>;
  }
  const headline = !total ? t(evaluate ? "routingStats.aiTaskEmpty" : "routingStats.empty")
    : evaluate ? tn("routingStats.aiTaskHeadline", total, { count: localeNumber(total) })
      : t("routingStats.headline", { percent: localeNumber(percent) });
  return <div className="routing-statistics-content" aria-busy={busy} data-help-text={scopeHelp(surface)}>
    {stats && <>
      <p>{headline}</p>
      <p className="agentcli-settings-hint">{t("routingStats.since", { date: localeDateTime(stats.since) })}</p>
      <dl className="routing-statistics-values">
        {(Object.keys(labels) as Outcome[]).map(key =>
          <Fragment key={key}><dt>{t(labels[key]!)}</dt><dd>{localeNumber(key === "provider" ? stats.provider_turns : stats.outcomes[key] ?? 0)}</dd></Fragment>)}
        <dt>{t("routingStats.decisionTokens")}</dt><dd>{tokens(stats.decision_usage)}</dd>
        {!evaluate && <><dt>{t("routingStats.providerTokens")}</dt><dd>{tokens(stats.provider_usage)}</dd></>}
      </dl>
    </>}
    {busy && !stats && <p role="status">{t("common.loading")}</p>}
    {error && !confirm && <p className="banner banner-error" role="alert">{t("routingStats.error")}</p>}
    {confirm ? <InlineConfirm label={t("routingStats.reset")} tone="danger" confirmLabel={t("routingStats.reset")} confirmIcon="restoreVersion"
      confirmClassName="btn btn-danger btn-sm" cancelLabel={t("common.cancel")} busy={busy} help={t(evaluate ? "routingStats.aiTaskResetHelp" : "routingStats.resetHelp")}
      error={error ? t("routingStats.error") : null} onConfirm={() => void load(true)} onCancel={() => setConfirm(false)}>
      <p>{t(evaluate ? "routingStats.aiTaskConfirm" : "routingStats.confirm")}</p>
    </InlineConfirm> : <div className="routing-statistics-actions" data-guide="topic.routingStatistics#refresh-and-reset">
      <ActionButton icon="refresh" label={t("common.refresh")} className="btn btn-outline btn-sm" busy={busy} help="routingStats.refreshHelp" onClick={() => void load()} />
      <ActionButton icon="restoreVersion" label={t("routingStats.reset")} className="btn btn-outline btn-sm phx-action-danger" busy={busy} help={evaluate ? "routingStats.aiTaskResetHelp" : "routingStats.resetHelp"}
        disabled={!stats} onClick={() => setConfirm(true)} />
    </div>}
  </div>;
}

/** A surface's routing counts folded under their title, loaded when opened. */
export function RoutingStatistics({ surface }: { surface: RoutingSurface }) {
  const [open, setOpen] = useState(false);
  const evaluate = isEvaluate(surface);
  return <details className="routing-statistics" data-help-text={scopeHelp(surface)} open={open}
    onToggle={event => { if (event.currentTarget.open !== open) setOpen(event.currentTarget.open); }}>
    <summary data-help={evaluate ? "routingStats.aiTaskShowHelp" : "routingStats.showHelp"}>{t(evaluate ? "routingStats.aiTaskTitle" : "routingStats.title")}</summary>
    {open && <RoutingStatisticsContent surface={surface} />}
  </details>;
}
