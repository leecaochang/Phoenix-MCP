/** Shared presentational helpers used across panel views and the in-context
 * profile modal. Kept out of index.tsx so reusing them (e.g. ProfileEditor in
 * the injected quick-add modal) does not pull the whole panel app into a chunk.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { t, tn } from "../i18n";
import { Icon } from "../ui/Icon";

export function Loading() {
  return (
    <div className="loading-wrap" role="status">
      <div className="spinner" aria-hidden="true" />
      <span>{t("common.loading")}</span>
    </div>
  );
}

/** A live region for a notice that comes and goes: it is in the page first, so what arrives in it is spoken. */
export function StatusRegion({ children }: { children: ReactNode }) {
  return <div className="phx-status-region" role="status">{children}</div>;
}

export function ErrorMsg({ msg }: { msg: string }) {
  return <div className="banner banner-error" role="alert">{msg}</div>;
}

export function RefreshIcon() {
  return <Icon name="refresh" size={15} style={{ display: "block" }} />;
}

/**
 * How many rows a filter or search left, said to a screen reader once the reader stops typing
 * and the rows are in. It speaks only after `query` changes, not when the list refreshes itself.
 */
export function ResultCount({ count, query, loading = false }: { count: number; query: string; loading?: boolean }) {
  const [text, setText] = useState("");
  const pending = useRef(false);
  const seen = useRef(query);
  if (seen.current !== query) {
    seen.current = query;
    pending.current = true;
  }
  useEffect(() => {
    if (!pending.current || loading) return undefined;
    const timer = window.setTimeout(() => {
      pending.current = false;
      setText(tn("common.resultCount", count));
    }, RESULT_COUNT_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [count, loading, query]);
  return <span className="sr-only" role="status">{text}</span>;
}
const RESULT_COUNT_DELAY_MS = 600;
