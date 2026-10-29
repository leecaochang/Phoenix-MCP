import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api } from "../api";
import type { AttentionItem } from "../types";
import { localeDateTime, localeNumber, t } from "../i18n";
import { ActionButton } from "../ui/ActionButton";
import { InlineConfirm } from "../ui/InlineConfirm";
import { Icon } from "../ui/Icon";

const TITLES = { agent: "attention.actionTitle", calendar: "attention.actionTitle", mesa: "attention.mesaTitle", calendar_storage: "attention.calendar_storageTitle" };
const HELP = { mesa: "attention.mesaHelp", calendar_storage: "attention.calendar_storageHelp" };

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
 * The review list, in the UI2 style (user, 2026-10-29): each item says what
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
      return <article className="phx-attention-item" key={key}>
        <h3 className="phx-attention-item-title">{t(TITLES[item.kind])}</h3>
        {item.access_key_name && <p className="phx-attention-meta">{item.access_key_name}{item.started_at && ` · ${localeDateTime(item.started_at)}`}</p>}
        <p className="phx-attention-help">{t(item.kind === "agent" || item.kind === "calendar" ? (item.dispatch_result === "received" ? "attention.received" : "attention.unknown") : HELP[item.kind])}</p>
        {reviewable && <p className="phx-attention-meta"><span className="stat-label">{t("attention.request")}</span> <code>{item.id}</code></p>}
        {reviewable && (confirming === key ? (
          <InlineConfirm label={t("attention.continue")} acknowledge={t("attention.checked")}
            confirmLabel={t("attention.continue")} cancelLabel={t("common.cancel")}
            busy={busy === key} onConfirm={() => { void review(item); setConfirming(null); }} onCancel={() => setConfirming(null)}>
            <p>{t("attention.noReplay")}</p>
          </InlineConfirm>
        ) : (
          <div className="phx-attention-actions">
            <code className="phx-attention-ref">{item.tool}{item.entity_id && ` / ${item.entity_id}`}</code>
            <span className="phx-attention-buttons">
              {onShowRequest && item.kind === "agent" && (
                <ActionButton icon="logs" label={t("attention.showInAudit")} className="btn btn-outline btn-sm"
                  disabled={busy !== null} onClick={() => onShowRequest(item.id)} />
              )}
              <ActionButton icon="check" label={t("attention.continue")} className="btn btn-outline btn-sm"
                disabled={busy !== null} onClick={() => setConfirming(key)} />
            </span>
          </div>
        ))}
      </article>;
    })}
  </div>;
}

/** Quiet, persistent review of uncertain actions, without dispatching them again. */
export function AttentionPanel({ onShowRequest }: { onShowRequest?: (requestId: string) => void } = {}) {
  const state = useAttention();
  const { items, error, load } = state;
  const [open, setOpen] = useState(() => window.location.hash === "#attention");
  const section = useRef<HTMLElement>(null);
  const bodyId = useId();

  useEffect(() => {
    const show = () => {
      if (window.location.hash !== "#attention") return;
      setOpen(true);
      void load();
      section.current?.scrollIntoView?.({ block: "nearest" });
    };
    window.addEventListener("hashchange", show);
    window.addEventListener("location-changed", show);
    return () => {
      window.removeEventListener("hashchange", show);
      window.removeEventListener("location-changed", show);
    };
  }, [load]);

  if (!items.length && !error && !open) return null;
  return <section className="phx-attention" ref={section} aria-label={t("attention.title")}>
    <button className="phx-attention-toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen(!open)}>
      <span>{t("attention.title")}{items.length > 0 && <span className="phx-attention-count">{localeNumber(items.length)}</span>}</span>
      <Icon name="expand" size={16} className="phx-attention-chevron" />
    </button>
    <div className="phx-attention-body" id={bodyId} hidden={!open}>
      <AttentionList state={state} onShowRequest={onShowRequest} />
    </div>
  </section>;
}
