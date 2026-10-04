import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api } from "../api";
import type { AttentionItem } from "../types";
import { localeDateTime, localeNumber, t } from "../i18n";
import { BusyButton } from "./BusyButton";

const TITLES = { agent: "attention.actionTitle", calendar: "attention.actionTitle", mesa: "attention.mesaTitle", calendar_storage: "attention.calendar_storageTitle" };
const HELP = { mesa: "attention.mesaHelp", calendar_storage: "attention.calendar_storageHelp" };

/** Quiet, persistent review of uncertain actions, without dispatching them again. */
export function AttentionPanel() {
  const [items, setItems] = useState<AttentionItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [checked, setChecked] = useState<ReadonlySet<string>>(() => new Set());
  const [open, setOpen] = useState(() => window.location.hash === "#attention");
  const generation = useRef(0);
  const alive = useRef(true);
  const reviewing = useRef(false);
  const section = useRef<HTMLElement>(null);
  const bodyId = useId();
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
    const show = () => {
      if (window.location.hash !== "#attention") return;
      setOpen(true);
      void load();
      section.current?.scrollIntoView?.({ block: "nearest" });
    };
    window.addEventListener("hashchange", show);
    window.addEventListener("location-changed", show);
    window.addEventListener("focus", load);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
      window.removeEventListener("hashchange", show);
      window.removeEventListener("location-changed", show);
      window.removeEventListener("focus", load);
    };
  }, [load]);

  async function review(item: AttentionItem) {
    const key = `${item.kind}:${item.id}`;
    if (reviewing.current || !checked.has(key) || (item.kind !== "agent" && item.kind !== "calendar")) return;
    reviewing.current = true;
    ++generation.current;
    setBusy(key);
    setReviewError(null);
    try {
      await api.reviewAttention(item.kind, item.id);
      if (!alive.current) return;
      setItems(rows => rows.filter(row => `${row.kind}:${row.id}` !== key));
      setChecked(previous => { const next = new Set(previous); next.delete(key); return next; });
    } catch (e: unknown) {
      if (alive.current) setReviewError(e instanceof Error ? e.message : t("attention.saveFailed"));
    } finally {
      reviewing.current = false;
      if (alive.current) { setBusy(null); void load(); }
    }
  }

  if (!items.length && !error && !open) return null;
  return <section className="phx-attention" ref={section} aria-label={t("attention.title")}>
    <button className="phx-attention-toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen(!open)}>
      <span>{t("attention.title")}{items.length > 0 && <span className="phx-attention-count">{localeNumber(items.length)}</span>}</span>
      <svg className="phx-attention-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
    </button>
    <div className="phx-attention-body" id={bodyId} hidden={!open}>
      <div className="phx-attention-refresh">
        <p>{error || (items.length ? t("attention.intro") : t("attention.empty"))}</p>
        <button className="btn btn-ghost btn-sm" disabled={busy !== null} onClick={() => { void load(); }}>{t("common.refresh")}</button>
      </div>
      {reviewError && <p role="alert">{reviewError}</p>}
      {items.map(item => {
        const key = `${item.kind}:${item.id}`;
        const reviewable = item.kind === "agent" || item.kind === "calendar";
        return <article className="phx-attention-item" key={key}>
          <h3>{t(TITLES[item.kind])}</h3>
          {item.access_key_name && <p className="phx-attention-meta">{item.access_key_name}{item.started_at && ` · ${localeDateTime(item.started_at)}`}</p>}
          <p>{t(item.kind === "agent" || item.kind === "calendar" ? (item.dispatch_result === "received" ? "attention.received" : "attention.unknown") : HELP[item.kind])}</p>
          {reviewable && <>
            <details className="phx-attention-details"><summary>{t("attention.details")}</summary>
              <code>{item.tool}{item.entity_id && ` / ${item.entity_id}`}<br />{item.id}</code>
            </details>
            <div className="phx-attention-actions">
              <label><input type="checkbox" checked={checked.has(key)} disabled={busy !== null} onChange={e => {
                const selected = e.target.checked;
                setChecked(previous => { const next = new Set(previous); if (selected) next.add(key); else next.delete(key); return next; });
              }} />{t("attention.checked")}</label>
              <BusyButton className="btn btn-outline btn-sm" busy={busy === key} disabled={!checked.has(key) || (busy !== null && busy !== key)} onClick={() => { void review(item); }}>{t("attention.continue")}</BusyButton>
            </div>
            <p className="phx-attention-meta">{t("attention.noReplay")}</p>
          </>}
        </article>;
      })}
    </div>
  </section>;
}
