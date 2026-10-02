/** Display dashboard proposals as text without instantiating card code. */
import { useEffect, useMemo, useState } from "react";
import { t } from "../i18n";

export type PreviewCardEntry =
  | { kind: "card"; config: Record<string, unknown> }
  | { kind: "invalid" };

export interface PreviewSection {
  title: string | null;
  cards: PreviewCardEntry[];
}

export interface PreviewView {
  title: string;
  kind: "cards" | "strategy" | "invalid" | "empty";
  sections: PreviewSection[];
}

function isDict(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function cardEntries(cards: unknown): PreviewCardEntry[] {
  if (!Array.isArray(cards)) return [];
  return cards.map((c) => (isDict(c) ? { kind: "card" as const, config: c } : { kind: "invalid" as const }));
}

/** Walks a rendered card's light DOM and open shadow roots collecting the
 * error text of every <hui-error-card> HA swapped in for a card (or card row)
 * whose config failed. The message lives on the error card's config (error +
 * optional message fields); the rendered text is the fallback. Depth-capped:
 * a pathological tree must not hang the panel. Exported for tests. */
export function collectConfigErrors(root: Element): string[] {
  const out: string[] = [];
  const walk = (el: Element, depth: number): void => {
    if (depth > 10) return;
    if (el.localName === "hui-error-card") {
      const cfg = (el as unknown as { _config?: { error?: unknown; message?: unknown } })._config;
      const parts = [cfg?.error, cfg?.message]
        .filter((p): p is string => typeof p === "string" && p.length > 0);
      out.push(
        parts.join(" - ")
        || el.shadowRoot?.textContent?.trim()
        || el.textContent?.trim()
        || t("changes.previewErrorNoDetail"),
      );
    }
    if (el.shadowRoot) {
      for (const kid of Array.from(el.shadowRoot.children)) walk(kid, depth + 1);
    }
    for (const kid of Array.from(el.children)) walk(kid, depth + 1);
  };
  walk(root, 0);
  return out;
}

/** Wraps one card config object into a one-card dashboard config previewable
 * by collectPreviewViews/DashboardPreview; null for anything but a dict. The
 * approval card feeds an add/edit's After side from the approval's own
 * args.card, which is always the full object (diff strings can truncate). */
export function wrapCardPreviewConfig(card: unknown): Record<string, unknown> | null {
  return isDict(card) ? { views: [{ cards: [card] }] } : null;
}

/** Same, from a card-tool diff side's JSON string (diff.before/diff.after).
 * Null input, unparseable JSON (a card large enough to hit the diff's
 * truncation bound), or a non-object result all return null, so callers treat
 * it exactly like "not previewable". */
export function singleCardPreviewConfig(cardJson: string | null | undefined): Record<string, unknown> | null {
  if (!cardJson) return null;
  try {
    return wrapCardPreviewConfig(JSON.parse(cardJson));
  } catch {
    return null;
  }
}

/** Normalize an agent-proposed (possibly garbage) dashboard config into
 * renderable views. Total: never throws. Returns null when the config has no
 * previewable shape at all (not a dict, no views list, or a strategy
 * dashboard whose views only exist at runtime); callers use null to hide the
 * Preview toggle entirely. */
export function collectPreviewViews(config: unknown): PreviewView[] | null {
  if (!isDict(config)) return null;
  if ("strategy" in config) return null;
  const views = config.views;
  if (!Array.isArray(views)) return null;
  return views.map((view, idx) => {
    const fallback = t("changes.previewViewFallback", { n: idx + 1 });
    if (!isDict(view)) return { title: fallback, kind: "invalid" as const, sections: [] };
    const title =
      (typeof view.title === "string" && view.title) ||
      (typeof view.path === "string" && view.path) ||
      fallback;
    if ("strategy" in view) return { title, kind: "strategy" as const, sections: [] };
    const sections: PreviewSection[] = [];
    const plain = cardEntries(view.cards);
    if (plain.length > 0) sections.push({ title: null, cards: plain });
    if (Array.isArray(view.sections)) {
      for (const section of view.sections) {
        if (!isDict(section)) continue;
        const cards = cardEntries(section.cards);
        if (cards.length > 0) {
          sections.push({ title: typeof section.title === "string" && section.title ? section.title : null, cards });
        }
      }
    }
    if (sections.length === 0) return { title, kind: "empty" as const, sections: [] };
    return { title, kind: "cards" as const, sections };
  });
}

// The Diff|Preview choice is a durable preference, not per-record state: an
// operator who prefers the visual preview wants it every time, on both diff
// surfaces (approval card and Changes tab), across reloads. One shared key,
// same localStorage pattern as the panel theme. The Before/After SIDE is
// deliberately not remembered (it is contextual: delete only has a Before,
// add only an After).
const PREVIEW_MODE_KEY = "phx-dash-preview-mode";

export type DiffPreviewMode = "diff" | "preview";

export function storedPreviewMode(): DiffPreviewMode {
  try {
    return localStorage.getItem(PREVIEW_MODE_KEY) === "preview" ? "preview" : "diff";
  } catch {
    return "diff";
  }
}

export function rememberPreviewMode(mode: DiffPreviewMode): void {
  try {
    localStorage.setItem(PREVIEW_MODE_KEY, mode);
  } catch {
    // Storage blocked: the choice still applies for this record, just not durably.
  }
}

/** Small segmented control, shared by the Diff|Preview mode switch and the
 * Changes tab's Before|After side switch. */
export function SegmentedToggle<T extends string>({ value, options, onChange, ariaLabel }: {
  value: T;
  options: { value: T; label: string; disabled?: boolean }[];
  onChange: (v: T) => void;
  ariaLabel: string;
}) {
  return (
    <div className="diff-mode-toggle" role="group" aria-label={ariaLabel}>
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          className={`diff-mode-btn${opt.value === value ? " active" : ""}`}
          aria-pressed={opt.value === value}
          disabled={opt.disabled}
          onClick={() => opt.value !== value && onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

/** Review configuration as escaped text without access to the HA client. */
export function DashboardPreview({ config }: {
  config: unknown;
}) {
  const views = useMemo(() => collectPreviewViews(config), [config]);
  const [active, setActive] = useState(0);
  useEffect(() => { setActive(0); }, [config]);
  if (views === null) return <p className="dash-preview-note">{t("changes.previewNoLayout")}</p>;
  if (views.length === 0) return <p className="dash-preview-note">{t("changes.previewNoViews")}</p>;
  const view = views[Math.min(active, views.length - 1)];
  return (
    <div className="dash-preview">
      {views.length > 1 && (
        <div className="dash-preview-tabs" role="group" aria-label={t("changes.previewViewsAria")}>
          {views.map((v, idx) => (
            <button key={idx} type="button" aria-pressed={idx === active}
              className={`approval-detail-tab${idx === active ? " active" : ""}`}
              onClick={() => setActive(idx)}>{v.title}</button>
          ))}
        </div>
      )}
      <p className="dash-preview-disclaimer">{t("changes.previewDisclaimer")}</p>
      {view.kind === "strategy" && <p className="dash-preview-note">{t("changes.previewStrategyView")}</p>}
      {view.kind === "invalid" && <p className="dash-preview-note">{t("changes.previewBadView")}</p>}
      {view.kind === "empty" && <p className="dash-preview-note">{t("changes.previewNoCards")}</p>}
      {view.sections.map((section, sIdx) => (
        <section key={sIdx}>
          {section.title && <div className="dash-preview-section-title">{section.title}</div>}
          <div className="dash-preview-grid">
            {section.cards.map((card, cIdx) => card.kind === "card"
              ? <pre key={cIdx} className="dash-preview-config">{JSON.stringify(card.config, null, 2)}</pre>
              : <p key={cIdx} className="dash-preview-card-error">{t("changes.previewBadCard")}</p>)}
          </div>
        </section>
      ))}
    </div>
  );
}
