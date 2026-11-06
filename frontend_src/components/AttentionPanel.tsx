import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { AttentionItem } from "../types";
import { localeDateTime, t } from "../i18n";
import { ActionButton } from "../ui/ActionButton";
import { Icon } from "../ui/Icon";
import { InlineConfirm } from "../ui/InlineConfirm";

const TITLES = { agent: "attention.actionTitle", calendar: "attention.actionTitle", mesa: "attention.mesaTitle", mesa_rename: "attention.mesa_renameTitle", calendar_storage: "attention.calendar_storageTitle", audit_storage: "attention.audit_storageTitle" };
const HELP = { mesa: "attention.mesaHelp", mesa_rename: "attention.mesa_renameHelp", calendar_storage: "attention.calendar_storageHelp", audit_storage: "attention.audit_storageHelp" };

export interface AttentionState {
  items: AttentionItem[];
  error: string | null;
  reviewError: string | null;
  busy: string | null;
  load: () => Promise<void>;
  review: (item: AttentionItem) => Promise<void>;
}

/** Items needing review, polled every 15 s and on window focus; a review never dispatches the action again. */
export function useAttention(): AttentionState {
  const [items, setItems] = useState<AttentionItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const generation = useRef(0);
  const alive = useRef(true);
  const reviewing = useRef(false);
  const load = useCallback(async () => {
    if (reviewing.current) return;
    const current = ++generation.current;
    try {
      const result = await api.listAttention();
      if (!alive.current || current !== generation.current) return;
      setItems(result.items);
      setError(null);
    } catch {
      if (alive.current && current === generation.current) setError(t("attention.loadFailed"));
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void load();
    const timer = window.setInterval(() => { void load(); }, 15000);
    window.addEventListener("focus", load);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", load);
    };
  }, [load]);

  const review = useCallback(async (item: AttentionItem) => {
    const key = `${item.kind}:${item.id}`;
    if (reviewing.current || (item.kind !== "agent" && item.kind !== "calendar")) return;
    reviewing.current = true;
    ++generation.current;
    setBusy(key);
    setReviewError(null);
    try {
      await api.reviewAttention(item.kind, item.id);
      if (!alive.current) return;
      setItems(rows => rows.filter(row => `${row.kind}:${row.id}` !== key));
    } catch (e: unknown) {
      if (alive.current) setReviewError(e instanceof Error ? e.message : t("attention.saveFailed"));
    } finally {
      reviewing.current = false;
      if (alive.current) { setBusy(null); void load(); }
    }
  }, [load]);

  return { items, error, reviewError, busy, load, review };
}

/**
 * The review list: each item says what
 * happened and what to check, and Mark Reviewed asks for the acknowledgement
 * in place, like every other confirmation in the panel. The list keeps itself
 * up to date, so there is no Refresh.
 */
export function AttentionList({ state, onShowRequest }: {
  state: AttentionState;
  /** Open the Audit Log on one request; absent outside the shell. */
  onShowRequest?: (requestId: string) => void;
}) {
  const { items, error, reviewError, busy, review } = state;
  const [confirming, setConfirming] = useState<string | null>(null);
  return <div className="phx-window-pad phx-attention-list">
    <p className="phx-attention-intro">{error || (items.length ? t("attention.intro") : t("attention.empty"))}</p>
    {reviewError && <p className="banner banner-error" role="alert">{reviewError}</p>}
    {items.map(item => {
      const key = `${item.kind}:${item.id}`;
      const reviewable = item.kind === "agent" || item.kind === "calendar";
      return <article className="phx-attention-item" key={key} data-guide={reviewable ? "window.attention#actions" : "window.attention#problems"}>
        <h3 className="phx-attention-item-title"><Icon name="warning" size={16} />{TITLES[item.kind] ? t(TITLES[item.kind]) : item.kind}</h3>
        {item.access_key_name && <p className="phx-attention-meta">{item.access_key_name}{item.started_at && ` · ${localeDateTime(item.started_at)}`}</p>}
        <p className="phx-attention-help">{t(item.kind === "agent" || item.kind === "calendar" ? (item.dispatch_result === "received" ? "attention.received" : "attention.unknown") : HELP[item.kind])}</p>
        {reviewable && <p className="phx-attention-meta"><span className="stat-label">{t(item.kind === "calendar" ? "attention.calendarReceipt" : "attention.request")}</span> <code>{item.id}</code></p>}
        {reviewable && (confirming === key ? (
          <div className="log-group" data-guide="window.attention#review">
          <InlineConfirm label={t("attention.continue")} help={t("help.attentionMarkReviewed")} acknowledge={t("attention.checked")}
            confirmLabel={t("attention.continue")} cancelLabel={t("common.cancel")}
            busy={busy === key} onConfirm={() => { void review(item).finally(() => setConfirming(null)); }} onCancel={() => setConfirming(null)}>
            <p>{t("attention.noReplay")}</p>
          </InlineConfirm>
          </div>
        ) : (
          <div className="phx-attention-actions" data-guide="window.attention#review">
            <code className="phx-attention-ref">{item.tool}{item.entity_id && ` / ${item.entity_id}`}</code>
            <span className="phx-attention-buttons">
              {onShowRequest && item.kind === "agent" && (
                <ActionButton icon="logs" label={t("attention.showInAudit")} help="help.attentionShowInAudit" className="btn btn-outline btn-sm"
                  disabled={busy !== null} onClick={() => onShowRequest(item.id)} />
              )}
              <ActionButton icon="check" label={t("attention.continue")} help="help.attentionMarkReviewed" className="btn btn-outline btn-sm"
                disabled={busy !== null} onClick={() => setConfirming(key)} />
            </span>
          </div>
        ))}
      </article>;
    })}
  </div>;
}
