/**
 * The Guide: longer help in a window of its own outside the workspace, fixed
 * on screen like Agent Chat and moved, resized and snapped the same way. It
 * shows the topic for the focused window, or else the workspace's overview,
 * and keeps its topic while focus is inside it. Topics ship with the
 * integration as compiled JSON (`guide/compile.ts`), are drawn by the
 * escape-first Markdown renderer, and link to each other inside the Guide;
 * links to the website open in a new tab. On phones it heads the stack.
 *
 * While the setup wizard runs, the Guide is always shown (user, 2026-10-28):
 * pinned beside the part of the step it explains, with the step's topic, and
 * it cannot be moved, resized or closed until the wizard ends.
 */

import { createContext, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import { clampPosToViewport, snapResizeRect } from "../components/AgentCliWindow";
import { GUIDE_FORMAT, LABEL, type GuideBundle, type GuideTopic } from "../guide/compile";
import { hasMessage, t } from "../i18n";
import { Icon } from "../ui/Icon";
import { renderMarkdown } from "../utils/markdown";
import { JS_BUILD } from "../version";
import type { WorkspaceController } from "./dock/controller";
import type { GuideRect } from "./storage";

/** Languages with Guide topics; readers of any other language get English. */
export const GUIDE_LANGUAGES: readonly string[] = ["en"];
const GUIDE_URL = "/local/phoenix-mcp/guide";
/** Space kept between the Guide and the screen edges; an edge this close snaps to it. */
const MARGIN = 8;
const SNAP = 56;
const RESIZE_EDGES = ["n", "s", "e", "w", "ne", "nw", "se", "sw"] as const;

const loaded = new Map<string, Promise<GuideBundle | null>>();
/** Forget the fetched topics, so tests can serve their own. */
export function clearGuideCache(): void { loaded.clear(); }

function validBundle(raw: unknown): GuideBundle | null {
  const b = raw as Partial<GuideBundle> | null;
  if (!b || b.format !== GUIDE_FORMAT || !b.topics || typeof b.topics !== "object") return null;
  const topics: Record<string, GuideTopic> = {};
  for (const [id, topic] of Object.entries(b.topics)) {
    if (topic && typeof topic.title === "string" && typeof topic.body === "string") topics[id] = { title: topic.title, body: topic.body };
  }
  return { format: GUIDE_FORMAT, language: String(b.language ?? ""), topics };
}

/** One language's topics, fetched once per page; a failed fetch is tried again next time. */
export function loadGuide(language: string): Promise<GuideBundle | null> {
  let pending = loaded.get(language);
  if (!pending) {
    pending = fetch(`${GUIDE_URL}/${encodeURIComponent(language)}.json?v=${encodeURIComponent(JS_BUILD)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then(validBundle)
      .catch(() => null);
    loaded.set(language, pending);
    void pending.then((b) => { if (!b) loaded.delete(language); });
  }
  return pending;
}

/** The languages to read, in order: the reader's own when the Guide has it, then English. */
export function guideLanguages(language: string): string[] {
  return [...new Set([...(GUIDE_LANGUAGES.includes(language) ? [language] : []), "en"])];
}

export type FoundTopic = GuideTopic & { id: string };

/** A topic by id, in the first language that has it. */
export function topicById(bundles: ReadonlyArray<GuideBundle | null>, id: string): FoundTopic | null {
  for (const bundle of bundles) {
    const topic = bundle?.topics[id];
    if (topic) return { id, ...topic };
  }
  return null;
}

/** The focused window's topic, else the workspace overview: in the reader's language first, then in English. */
export function findTopic(bundles: ReadonlyArray<GuideBundle | null>, workspace: string, windowId: string | null): FoundTopic | null {
  const ids = [...(windowId ? [`window.${windowId}`] : []), `workspace.${workspace}`];
  for (const bundle of bundles) {
    for (const id of ids) {
      const topic = bundle?.topics[id];
      if (topic) return { id, ...topic };
    }
  }
  return null;
}

const escapeHtml = (s: string): string => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** A topic's body as safe HTML: Markdown with topic links, and UI labels taken from the catalog. */
export function renderTopic(body: string): string {
  return renderMarkdown(body, { guideLinks: true }).replace(LABEL, (_m, key: string) => (
    hasMessage(key) ? escapeHtml(t(key)) : `<code class="phx-guide-missing">${escapeHtml(key)}</code>`
  ));
}

/** Where the Guide opens the first time: along the right side, below the toolbar. */
export function defaultGuideRect(vw: number, vh: number): GuideRect {
  const w = Math.min(400, Math.max(320, vw - 2 * MARGIN));
  const h = Math.min(560, Math.max(280, vh - 160));
  return { x: Math.max(MARGIN, vw - w - 24), y: Math.min(120, Math.max(MARGIN, vh - h - MARGIN)), w, h };
}

/** The wizard step on screen: its id, the part of the step its topic explains, and the step's content. */
export interface WizardGuideStep {
  step: string;
  part: HTMLElement;
  content: HTMLElement;
}

/** How the setup wizard tells the shell its step, and whether the Guide sits beside it. */
export const WizardGuideContext = createContext<{
  /** The step on screen, or null when the wizard closes. */
  report: (step: WizardGuideStep | null) => void;
  guideBeside: boolean;
} | null>(null);

/**
 * The wizard's step in Access Keys. While there is one, the shell shows the
 * Guide pinned beside it, without touching the Guide's own open state, so the
 * Guide is back as the user left it when the wizard ends.
 */
export function useWizardGuide() {
  const [step, setStep] = useState<WizardGuideStep | null>(null);
  const report = useCallback((next: WizardGuideStep | null) => setStep(next), []);
  return { step, report };
}

type Box = { left: number; top: number; right: number; bottom: number };

/** The Guide's width beside the wizard; the wizard's stylesheet leaves room for it (`.wizard-root.is-guided`). */
export const GUIDE_BESIDE_WIDTH = 400;

/**
 * Where the Guide sits during the wizard: to the right of the step's content,
 * level with the part it explains, inside the workspace. Without room beside
 * the content it keeps to the workspace's right edge. The size is kept where it fits.
 */
export function besideRect(content: Box, part: Box, frame: Box, size: { w: number; h: number }): GuideRect {
  const GAP = 16;
  const MIN_W = 280;
  let x = content.right + GAP;
  let w = Math.min(size.w, frame.right - MARGIN - x);
  if (w < MIN_W) {
    w = Math.min(size.w, frame.right - frame.left - 2 * MARGIN);
    x = frame.right - MARGIN - w;
  }
  const h = Math.max(Math.min(240, frame.bottom - frame.top - 2 * MARGIN), Math.min(size.h, frame.bottom - frame.top - 2 * MARGIN));
  const y = Math.max(frame.top + MARGIN, Math.min(part.top, frame.bottom - MARGIN - h));
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) };
}

/** The topics and the chosen topic, shared by the floating Guide and the phone one. */
function useGuideTopic(workspace: string, windowId: string | null, language: string, topicId: string | null = null) {
  const [bundles, setBundles] = useState<Array<GuideBundle | null> | null>(null);
  useEffect(() => {
    let live = true;
    void Promise.all(guideLanguages(language).map(loadGuide)).then((b) => { if (live) setBundles(b); });
    return () => { live = false; };
  }, [language]);
  // A topic opened from a link stays until another window gets focus or the workspace changes.
  const [trail, setTrail] = useState<string[]>([]);
  useEffect(() => { setTrail([]); }, [workspace, windowId, topicId]);
  const followed = trail[trail.length - 1];
  // A wizard step's topic comes before the focused window's.
  const topic = bundles
    ? (followed ? topicById(bundles, followed) : null) ?? (topicId ? topicById(bundles, topicId) : null) ?? findTopic(bundles, workspace, windowId)
    : null;
  return {
    loading: bundles === null,
    failed: !!bundles && bundles.every((b) => !b),
    topic,
    canGoBack: trail.length > 0,
    back: () => setTrail((tr) => tr.slice(0, -1)),
    open: (id: string) => { if (bundles && topicById(bundles, id)) setTrail((tr) => [...tr, id]); },
  };
}

function GuideBody({ guide }: { guide: ReturnType<typeof useGuideTopic> }) {
  const html = useMemo(() => (guide.topic ? renderTopic(guide.topic.body) : ""), [guide.topic]);
  const onClick = (e: ReactMouseEvent<HTMLDivElement>) => {
    const link = (e.target as HTMLElement).closest<HTMLAnchorElement>("a[data-guide-topic]");
    if (!link) return;
    e.preventDefault();
    guide.open(link.dataset.guideTopic ?? "");
  };
  if (guide.loading) return <p className="phx-guide-note">{t("common.loading")}</p>;
  if (guide.failed) return <p className="phx-guide-note">{t("guide.unavailable")}</p>;
  if (!guide.topic) return <p className="phx-guide-note">{t("guide.noTopic")}</p>;
  return (
    <article className="phx-guide-topic">
      <h3>{guide.topic.title}</h3>
      {/* Escape-first renderer output: source text is escaped before the fixed set of tags is added. */}
      <div onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />
    </article>
  );
}

function BackButton({ guide }: { guide: ReturnType<typeof useGuideTopic> }) {
  if (!guide.canGoBack) return null;
  return (
    <button type="button" className="phx-window-control" aria-label={t("guide.back")} title={t("guide.back")} onClick={guide.back}>
      <Icon name="back" size={14} />
    </button>
  );
}

/** The floating Guide for a desktop workspace. */
export function GuideWindow({ workspace, controller, language, rect, onRect, onClose, topicId = null, follow = null }: {
  workspace: string;
  controller: WorkspaceController | null;
  language: string;
  rect: GuideRect | undefined;
  onRect: (rect: GuideRect) => void;
  onClose: () => void;
  /** A topic shown ahead of the focused window's, such as the wizard step's. */
  topicId?: string | null;
  /** The wizard step to sit beside: the Guide is pinned there, and cannot be moved or closed. */
  follow?: WizardGuideStep | null;
}) {
  useSyncExternalStore(controller?.subscribe ?? (() => () => {}), controller?.getVersion ?? (() => 0));
  const guide = useGuideTopic(workspace, controller?.activeWindow ?? null, language, topicId);
  const [live, setLive] = useState<GuideRect>(() => rect ?? defaultGuideRect(window.innerWidth, window.innerHeight));
  // A saved place applies unless the Guide is pinned beside the wizard; after the wizard it returns there.
  const following = !!follow;
  useEffect(() => { if (!following) setLive(rect ?? defaultGuideRect(window.innerWidth, window.innerHeight)); }, [rect, following]);
  const liveRef = useRef(live);
  liveRef.current = live;

  // Stay on screen when the browser window shrinks.
  useLayoutEffect(() => {
    const fit = () => {
      const r = liveRef.current;
      const p = clampPosToViewport({ x: r.x, y: r.y }, { w: r.w, h: r.h }, window.innerWidth, window.innerHeight);
      if (p.x !== r.x || p.y !== r.y) setLive({ ...r, ...p });
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);

  // Beside the wizard step: placed again when the step, its layout or the screen changes.
  // The placement is not saved; only a move by the user is.
  useLayoutEffect(() => {
    if (!follow) return;
    const place = () => {
      const frame = follow.content.closest(".phx-workspace")?.getBoundingClientRect()
        ?? { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
      setLive((r) => besideRect(follow.content.getBoundingClientRect(), follow.part.getBoundingClientRect(), frame, { w: GUIDE_BESIDE_WIDTH, h: r.h }));
    };
    place();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(place);
    observer?.observe(follow.content);
    const root = follow.content.closest(".wizard-root");
    if (root) observer?.observe(root);
    window.addEventListener("resize", place);
    return () => { observer?.disconnect(); window.removeEventListener("resize", place); };
  }, [follow]);

  const track = (move: (ev: PointerEvent) => GuideRect, settle: (r: GuideRect) => GuideRect) => {
    let latest = liveRef.current;
    const onMove = (ev: PointerEvent) => { latest = move(ev); setLive(latest); };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      const final = settle(latest);
      setLive(final);
      onRect(final);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  // Drag by the title bar; on release an edge near the screen's side snaps to it.
  const startDrag = (e: ReactPointerEvent<HTMLElement>) => {
    if (following || e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
    e.preventDefault();
    const o = liveRef.current;
    const x0 = e.clientX, y0 = e.clientY;
    track(
      (ev) => {
        const p = clampPosToViewport({ x: o.x + ev.clientX - x0, y: o.y + ev.clientY - y0 }, o, window.innerWidth, window.innerHeight);
        return { ...o, ...p };
      },
      (r) => {
        const vw = window.innerWidth, vh = window.innerHeight;
        let { x, y } = r;
        if (x <= SNAP) x = MARGIN;
        else if (vw - (x + r.w) <= SNAP) x = Math.max(MARGIN, vw - r.w - MARGIN);
        if (y <= SNAP) y = MARGIN;
        else if (vh - (y + r.h) <= SNAP) y = Math.max(MARGIN, vh - r.h - MARGIN);
        return { ...r, x, y };
      },
    );
  };
  // Resize from any edge or corner, snapping the dragged edge to the screen's side.
  const startResize = (dir: string) => (e: ReactPointerEvent<HTMLElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const o = liveRef.current;
    const x0 = e.clientX, y0 = e.clientY;
    track((ev) => snapResizeRect(dir, o, ev.clientX - x0, ev.clientY - y0, window.innerWidth, window.innerHeight), (r) => r);
  };

  return (
    <section className={`phx-guide${following ? " is-pinned" : ""}`} aria-label={t("guide.title")} style={{ left: live.x, top: live.y, width: live.w, height: live.h }}>
      <header className="phx-guide-titlebar" onPointerDown={startDrag} data-help={following ? undefined : "help.guideTitle"}>
        <BackButton guide={guide} />
        <h2 className="phx-guide-title">{t("guide.title")}</h2>
        {!following && (
          <button type="button" className="phx-window-control" aria-label={t("common.close")} title={t("common.close")} onClick={onClose}>
            <Icon name="close" size={14} />
          </button>
        )}
      </header>
      <div className="phx-guide-body"><GuideBody guide={guide} /></div>
      {!following && RESIZE_EDGES.map((d) => <div key={d} className={`agentcli-resize agentcli-resize-${d}`} onPointerDown={startResize(d)} />)}
    </section>
  );
}

/** The phone Guide, heading the workspace's stack; without `onClose` (during the wizard) it cannot be closed. */
export function PhoneGuide({ workspace, language, onClose, topicId = null }: { workspace: string; language: string; onClose?: () => void; topicId?: string | null }) {
  const guide = useGuideTopic(workspace, null, language, topicId);
  return (
    <section className="phx-phone-window phx-guide-phone" aria-label={t("guide.title")}>
      <header className="phx-phone-title">
        <BackButton guide={guide} />
        <h2 className="phx-window-title-text">{t("guide.title")}</h2>
        {onClose && (
          <button type="button" className="phx-window-control phx-guide-phone-close" aria-label={t("common.close")} title={t("common.close")} onClick={onClose}>
            <Icon name="close" size={14} />
          </button>
        )}
      </header>
      <div className="phx-phone-body"><GuideBody guide={guide} /></div>
    </section>
  );
}
