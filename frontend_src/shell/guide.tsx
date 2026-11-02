/**
 * The Guide: longer help in a window of its own outside the workspace, fixed
 * on screen like Agent Chat and moved, resized and snapped the same way. It
 * shows the page for the window the reader works in, scrolled to and
 * highlighting the section about the control they selected (see
 * guideTarget.ts), or else the workspace's overview, and keeps its page while
 * focus is inside it. Folded at its top, Contents lists every page and Search
 * finds any section. Breadcrumbs place each page in Contents, and Back and
 * Forward step through every page it showed, whatever opened it. Topics ship with the integration as compiled JSON
 * (`guide/compile.ts`), are drawn by the escape-first Markdown renderer, and
 * link to each other inside the Guide; links to the web open in a new tab. On
 * phones it heads the stack.
 *
 * While the setup wizard runs, the Guide is always shown (user, 2026-10-28):
 * pinned beside the part of the step it explains, with the step's topic, and
 * it cannot be moved, resized or closed until the wizard ends.
 */

import { createPortal } from "react-dom";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import { clampPosToViewport, snapResizeRect } from "../components/AgentCliWindow";
import { ANCHOR, GUIDE_FORMAT, HEADING, LABEL, type GuideBundle, type GuideTopic } from "../guide/compile";
import { searchGuide, topicSections, type SearchSection } from "../guide/search";
import type { GuideTarget, GuideTargetTracker } from "./guideTarget";
import { hasMessage, t } from "../i18n";
import { compareStrings } from "../i18n/format";
import { Icon } from "../ui/Icon";
import { MenuButton, useContextMenu, type MenuEntry } from "../ui/Menu";
import { openPopupWindow, type OpenPopup } from "../ui/popup";
// The title bar's buttons are window controls, styled with the workspace windows.
import DOCK_CSS from "./dock/dock.css?inline";
import { applyThemeClass } from "../ui/theme";
import { renderMarkdown } from "../utils/markdown";
import { JS_BUILD } from "../version";
import type { WorkspaceController } from "./dock/controller";
import type { GuideRect } from "./storage";

declare const __GUIDE_LANGUAGES__: string[];
/** Languages with Guide topics, read from the topic folders at build time; readers of any other language get English. */
export const GUIDE_LANGUAGES: readonly string[] = __GUIDE_LANGUAGES__;
const GUIDE_URL = "/local/phoenix-mcp/guide";
/** Space kept between the Guide and the screen edges; an edge this close snaps to it. */
const MARGIN = 8;
const SNAP = 56;
const RESIZE_EDGES = ["n", "s", "e", "w", "ne", "nw", "se", "sw"] as const;

const loaded = new Map<string, Promise<GuideBundle | null>>();
/** Forget the fetched topics, so tests can serve their own. */
export function clearGuideCache(): void { loaded.clear(); }

/** Section keywords as shipped, with anything that is not a list of words left out. */
function sectionKeywordsOf(raw: object): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [anchor, words] of Object.entries(raw)) {
    if (Array.isArray(words)) out[anchor] = words.filter((w): w is string => typeof w === "string");
  }
  return out;
}

function validBundle(raw: unknown): GuideBundle | null {
  const b = raw as Partial<GuideBundle> | null;
  if (!b || b.format !== GUIDE_FORMAT || !b.topics || typeof b.topics !== "object") return null;
  const topics: Record<string, GuideTopic> = {};
  for (const [id, topic] of Object.entries(b.topics)) {
    if (!topic || typeof topic.title !== "string" || typeof topic.body !== "string") continue;
    topics[id] = {
      title: topic.title, body: topic.body,
      anchors: Array.isArray(topic.anchors) ? topic.anchors.filter((a) => typeof a === "string") : [],
      ...(typeof topic.parent === "string" ? { parent: topic.parent } : {}),
      ...(typeof topic.order === "number" ? { order: topic.order } : {}),
      ...(Array.isArray(topic.keywords) ? { keywords: topic.keywords.filter((k) => typeof k === "string") } : {}),
      ...(topic.sectionKeywords && typeof topic.sectionKeywords === "object" ? { sectionKeywords: sectionKeywordsOf(topic.sectionKeywords) } : {}),
    };
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

/** A UI label from the catalog, or the key itself when the catalog lacks it. */
const labelText = (key: string): string => (hasMessage(key) ? t(key) : key);

/** A topic's title as shown: its `{{label:<key>}}` placeholders in the reader's language. */
export function topicTitle(title: string): string {
  return title.replace(LABEL, (_m, key: string) => labelText(key));
}

/**
 * A topic's body as safe HTML: Markdown with topic links and UI labels taken
 * from the catalog, each `## Heading {#anchor}` opening a section the panel's
 * controls can point at, and each `###` a part inside it.
 */
export function renderTopic(body: string): string {
  const out: string[] = [];
  const open: number[] = [];
  let chunk: string[] = [];
  let inCode = false;
  const flush = () => { if (chunk.length) { out.push(renderMarkdown(chunk.join("\n"), { guideLinks: true })); chunk = []; } };
  for (const line of body.split("\n")) {
    if (/^```/.test(line)) inCode = !inCode;
    const h = inCode ? null : HEADING.exec(line);
    if (!h || !ANCHOR.test(h[3])) { chunk.push(line); continue; }
    flush();
    const level = h[1].length;
    while (open.length && open[open.length - 1] >= level) { out.push("</section>"); open.pop(); }
    out.push(`<section class="phx-guide-section" data-guide-section="${h[3]}">`);
    out.push(renderMarkdown(`${"#".repeat(level + 2)} ${h[2]}`, { guideLinks: true }));
    open.push(level);
  }
  flush();
  while (open.pop() !== undefined) out.push("</section>");
  return out.join("\n").replace(LABEL, (_m, key: string) => (
    hasMessage(key) ? escapeHtml(t(key)) : `<code class="phx-guide-missing">${escapeHtml(key)}</code>`
  ));
}

/** The Guide's pages as a tree: top-level pages first, each with the pages under it, in order. */
export interface ContentsNode { id: string; title: string; children: ContentsNode[] }
export function guideContents(topics: Record<string, GuideTopic>): ContentsNode[] {
  const byParent = new Map<string, string[]>();
  for (const [id, topic] of Object.entries(topics)) {
    const parent = topic.parent && topics[topic.parent] ? topic.parent : "";
    byParent.set(parent, [...(byParent.get(parent) ?? []), id]);
  }
  const build = (parent: string, seen: Set<string>): ContentsNode[] => (byParent.get(parent) ?? [])
    .filter((id) => !seen.has(id))
    .sort((a, b) => (topics[a].order ?? 100) - (topics[b].order ?? 100) || compareStrings(topicTitle(topics[a].title), topicTitle(topics[b].title)))
    .map((id) => ({ id, title: topicTitle(topics[id].title), children: build(id, new Set([...seen, id])) }));
  return build("", new Set());
}

/** Every topic, in the reader's language where it has one and in English otherwise. */
export function mergedTopics(bundles: ReadonlyArray<GuideBundle | null>): Record<string, GuideTopic> {
  const all: Record<string, GuideTopic> = {};
  for (const bundle of [...bundles].reverse()) Object.assign(all, bundle?.topics ?? {});
  return all;
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

/** A page the Guide showed: a page and section, Contents or Search. */
type Step = { kind: "topic"; id: string; anchor: string | null } | { kind: "contents" } | { kind: "search" };
const stepKey = (step: Step): string => (step.kind === "topic" ? `${step.id}#${step.anchor ?? ""}` : step.kind);
/** How many pages Back can return through. */
const HISTORY_LIMIT = 50;

/** One browser-like history: a new page drops the pages ahead of it, and repeating the page shown adds nothing. */
interface History { steps: Step[]; at: number }
export function pushStep(history: History, step: Step): History {
  const current = history.steps[history.at];
  if (current && stepKey(current) === stepKey(step)) return history;
  const steps = [...history.steps.slice(0, history.at + 1), step].slice(-HISTORY_LIMIT);
  return { steps, at: steps.length - 1 };
}

/** The pages above a page in Contents, from the top: a breadcrumb trail. */
export function topicAncestors(topics: Record<string, GuideTopic>, id: string): string[] {
  const chain: string[] = [];
  for (let at = topics[id]?.parent; at && topics[at] && !chain.includes(at); at = topics[at].parent) chain.unshift(at);
  return chain;
}

/** What the reader's selection points at, from the shell; none outside it. */
export const GuideTargetContext = createContext<GuideTargetTracker | null>(null);

const noSubscribe = () => () => {};
const noVersion = () => 0;

/** The topics and the chosen page and section, shared by the floating Guide and the phone one. */
function useGuideTopic(workspace: string, windowId: string | null, language: string, topicId: string | null = null) {
  const [bundles, setBundles] = useState<Array<GuideBundle | null> | null>(null);
  useEffect(() => {
    let live = true;
    void Promise.all(guideLanguages(language).map(loadGuide)).then((b) => { if (live) setBundles(b); });
    return () => { live = false; };
  }, [language]);
  const tracker = useContext(GuideTargetContext);
  const selectionVersion = useSyncExternalStore(tracker?.subscribe ?? noSubscribe, tracker?.getVersion ?? noVersion);
  // The page follows whichever moved last: the reader's selection, or the workspace's active window
  // (a keyboard move, or a window the shell opened). Dockview does not see every press inside a
  // window's content, so the selection, which does, names its window itself.
  const [lead, setLead] = useState<"selection" | "window">("window");
  useEffect(() => { setLead("window"); }, [workspace, windowId]);
  useEffect(() => { if (selectionVersion) setLead("selection"); }, [selectionVersion]);
  const latest = tracker?.current() ?? null;
  const selected: GuideTarget | null = latest && (lead === "selection" || latest.windowId === windowId) ? latest : null;
  if (selected?.windowId) windowId = selected.windowId;
  // What the workspace points at now: the wizard step's page, the selected control's section, or the window's page.
  let auto: Step | null = null;
  if (bundles) {
    const found = (topicId ? topicById(bundles, topicId) : null)
      ?? (selected?.topic ? topicById(bundles, selected.topic) : null)
      ?? findTopic(bundles, workspace, windowId);
    if (found) auto = { kind: "topic", id: found.id, anchor: !topicId && selected && (!selected.topic || found.id === selected.topic) ? selected.anchor : null };
  }
  // One history, as in a browser (user, 2026-11-01): every page shown, whether the reader followed a
  // link, used Contents or Search, or selected a control, so Back always returns to the page seen before.
  const [history, setHistory] = useState<History>({ steps: [], at: -1 });
  const autoRef = useRef(auto);
  autoRef.current = auto;
  const autoKey = auto ? stepKey(auto) : "";
  // The automatic page is shown before the effect below records it, so a step the reader takes in
  // between records it first: otherwise the late effect would push it on top of the reader's page.
  const recordedAuto = useRef("");
  useEffect(() => {
    if (!autoRef.current || recordedAuto.current === autoKey) return;
    recordedAuto.current = autoKey;
    setHistory((h) => pushStep(h, autoRef.current!));
  }, [autoKey]);
  const step = history.steps[history.at] ?? auto;
  const view = step?.kind === "contents" || step?.kind === "search" ? step.kind : "topic";
  let topic: FoundTopic | null = null;
  let anchor: string | null = null;
  if (bundles && step?.kind === "topic") {
    topic = topicById(bundles, step.id);
    anchor = step.anchor;
  }
  if (topic && anchor && !topic.anchors.includes(anchor)) anchor = null;
  const go = (next: Step) => {
    const shown = autoRef.current;
    const unrecorded = shown && recordedAuto.current !== autoKey ? shown : null;
    recordedAuto.current = autoKey;
    setHistory((h) => pushStep(unrecorded ? pushStep(h, unrecorded) : h, next));
  };
  return {
    loading: bundles === null,
    failed: !!bundles && bundles.every((b) => !b),
    bundles,
    view,
    topic,
    anchor,
    canGoBack: history.at > 0,
    canGoForward: history.at < history.steps.length - 1,
    back: () => setHistory((h) => ({ ...h, at: Math.max(0, h.at - 1) })),
    forward: () => setHistory((h) => ({ ...h, at: Math.min(h.steps.length - 1, h.at + 1) })),
    /** Open a page, or a section of it; a section alone stays on the page shown. */
    open: (id: string | null, at: string | null = null) => {
      const to = id || topic?.id;
      if (bundles && to && topicById(bundles, to)) go({ kind: "topic", id: to, anchor: at });
    },
    show: (kind: "contents" | "search") => go({ kind }),
  };
}

type Guide = ReturnType<typeof useGuideTopic>;

/** Follow a link inside the Guide: to another page, or a section of this one. */
function followLink(guide: Guide, e: ReactMouseEvent<HTMLElement>): void {
  const link = (e.target as HTMLElement).closest<HTMLAnchorElement>("a.phx-guide-link");
  if (!link) return;
  e.preventDefault();
  guide.open(link.dataset.guideTopic ?? null, link.dataset.guideAnchor ?? null);
}

/** Contents and Search, folded at the top of every page (user, 2026-11-01). */
function GuideTools({ guide }: { guide: Guide }) {
  return (
    <details className="phx-guide-tools">
      <summary data-help="help.guideMore">{t("guide.more")}</summary>
      <div className="phx-guide-tools-links">
        <button type="button" className="phx-guide-tool" data-help="help.guideContents" onClick={() => guide.show("contents")}>{t("guide.contents")}</button>
        <button type="button" className="phx-guide-tool" data-help="help.guideSearch" onClick={() => guide.show("search")}>{t("guide.search")}</button>
      </div>
    </details>
  );
}

function ContentsList({ nodes, guide }: { nodes: ContentsNode[]; guide: Guide }) {
  return (
    <ul className="phx-guide-contents">
      {nodes.map((node) => (
        <li key={node.id}>
          <a href="#" className="phx-guide-link" data-help="help.guideLink" onClick={(e) => { e.preventDefault(); guide.open(node.id); }}>{node.title}</a>
          {node.children.length > 0 && <ContentsList nodes={node.children} guide={guide} />}
        </li>
      ))}
    </ul>
  );
}

function ContentsView({ guide }: { guide: Guide }) {
  const nodes = useMemo(() => guideContents(mergedTopics(guide.bundles ?? [])), [guide.bundles]);
  return (
    <article className="phx-guide-topic">
      <h3>{t("guide.contents")}</h3>
      <ContentsList nodes={nodes} guide={guide} />
    </article>
  );
}

function SearchView({ guide }: { guide: Guide }) {
  const [query, setQuery] = useState("");
  // In Contents order, so equally good matches are listed as the pages are.
  const sections = useMemo<SearchSection[]>(() => {
    const topics = mergedTopics(guide.bundles ?? []);
    const order: string[] = [];
    const walk = (nodes: ContentsNode[]) => { for (const n of nodes) { order.push(n.id); walk(n.children); } };
    walk(guideContents(topics));
    return order.flatMap((id) => topicSections(id, topics[id].title, topics[id].body, topics[id].keywords ?? [], labelText, topics[id].sectionKeywords));
  }, [guide.bundles]);
  const hits = useMemo(() => searchGuide(sections, query), [sections, query]);
  return (
    <article className="phx-guide-topic">
      <h3>{t("guide.search")}</h3>
      <input type="search" className="input phx-guide-search" data-help="help.guideSearchInput" autoFocus value={query} placeholder={t("guide.searchPlaceholder")}
        aria-label={t("guide.searchPlaceholder")} onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && hits[0]) { e.preventDefault(); guide.open(hits[0].section.topic, hits[0].section.anchor); } }} />
      {query.trim() && !hits.length && <p className="phx-guide-note">{t("guide.noResults")}</p>}
      <ul className="phx-guide-results">
        {hits.map(({ section, snippet }) => (
          <li key={`${section.topic}#${section.anchor ?? ""}`}>
            <a href="#" className="phx-guide-link" data-help="help.guideLink" onClick={(e) => { e.preventDefault(); guide.open(section.topic, section.anchor); }}>
              {section.heading ? `${section.title}: ${section.heading}` : section.title}
            </a>
            {snippet && <p className="phx-guide-snippet">{snippet}</p>}
          </li>
        ))}
      </ul>
    </article>
  );
}

function GuideBody({ guide }: { guide: Guide }) {
  const html = useMemo(() => (guide.topic ? renderTopic(guide.topic.body) : ""), [guide.topic]);
  const body = useRef<HTMLDivElement | null>(null);
  // The selected section is scrolled to the top of the Guide and highlighted until something else is selected.
  useLayoutEffect(() => {
    const el = body.current;
    if (!el) return;
    for (const s of el.querySelectorAll(".phx-guide-section.is-target")) s.classList.remove("is-target");
    const scroller = el.closest(".phx-guide-body, .phx-phone-body");
    const section = guide.anchor ? el.querySelector<HTMLElement>(`[data-guide-section="${guide.anchor}"]`) : null;
    if (section) {
      section.classList.add("is-target");
      if (scroller) scroller.scrollTop += section.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 8;
    } else if (scroller) {
      scroller.scrollTop = 0;
    }
  }, [html, guide.anchor]);
  if (guide.loading) return <p className="phx-guide-note">{t("common.loading")}</p>;
  if (guide.failed) return <p className="phx-guide-note">{t("guide.unavailable")}</p>;
  if (guide.view === "contents") return <><GuideTools guide={guide} /><ContentsView guide={guide} /></>;
  if (guide.view === "search") return <><GuideTools guide={guide} /><SearchView guide={guide} /></>;
  if (!guide.topic) return <><GuideTools guide={guide} /><p className="phx-guide-note">{t("guide.noTopic")}</p></>;
  return (
    <>
      <GuideTools guide={guide} />
      <article className="phx-guide-topic">
        <Breadcrumbs guide={guide} id={guide.topic.id} />
        <h3>{topicTitle(guide.topic.title)}</h3>
        {/* Escape-first renderer output: source text is escaped before the fixed set of tags is added. */}
        <div ref={body} onClick={(e) => followLink(guide, e)} dangerouslySetInnerHTML={{ __html: html }} />
      </article>
    </>
  );
}

/** Back and Forward through the pages the Guide showed, as in a browser; each is greyed out at its end. */
function HistoryButtons({ guide, iconSize = WINDOW_ICON }: { guide: Guide; iconSize?: number }) {
  return (
    <>
      <button type="button" className="phx-window-control" aria-label={t("guide.back")} title={t("guide.back")} data-help="help.guideBack" disabled={!guide.canGoBack} onClick={guide.back}>
        <Icon name="back" size={iconSize} />
      </button>
      <button type="button" className="phx-window-control" aria-label={t("guide.forward")} title={t("guide.forward")} data-help="help.guideForward" disabled={!guide.canGoForward} onClick={guide.forward}>
        <Icon name="forward" size={iconSize} />
      </button>
    </>
  );
}

/** Where the page sits in Contents, each page above it a link; none on a top-level page (user, 2026-11-01). */
function Breadcrumbs({ guide, id }: { guide: Guide; id: string }) {
  const topics = useMemo(() => mergedTopics(guide.bundles ?? []), [guide.bundles]);
  const chain = topicAncestors(topics, id);
  if (!chain.length) return null;
  return (
    <nav className="phx-guide-crumbs" aria-label={t("guide.breadcrumbs")}>
      {chain.map((at, i) => (
        <span key={at} className="phx-guide-crumb">
          {i > 0 && <Icon name="folded" size={12} className="phx-guide-crumb-sep" />}
          <a href="#" className="phx-guide-link" data-help="help.guideLink" onClick={(e) => { e.preventDefault(); guide.open(at); }}>{topicTitle(topics[at].title)}</a>
        </span>
      ))}
    </nav>
  );
}

const NO_WINDOWS: ReadonlySet<string> = new Set();
const POPUP_NAME = "phoenix-mcp-guide";
// Inside the panel the Guide's controls match the workspace windows'; popped out, they match
// Agent Chat's popped-out window, the other window of its own (user, 2026-11-01).
const WINDOW_ICON = 14;
const POPUP_ICON = 16;
const POPUP_MOUNT_ID = "phx-guide-popout-root";

/** How far one arrow key moves or resizes the Guide. */
const KEY_STEP = 24;
const MIN_W = 320;
const MIN_H = 280;

/**
 * The floating Guide on a desktop: one window over every workspace. It has
 * the window controls every window has (user, 2026-11-01): Fold, the window
 * menu with keyboard Move and Resize, Maximize and Close, but no Dock, since
 * a Guide docked in one workspace would not fit the others.
 */
export function GuideWindow({ workspace, controller, language, rect, onRect, onClose, folded = false, onFold, topicId = null, follow = null, opensItself = NO_WINDOWS }: {
  workspace: string;
  controller: WorkspaceController | null;
  language: string;
  rect: GuideRect | undefined;
  onRect: (rect: GuideRect) => void;
  onClose: () => void;
  /** Folded into its title bar; remembered with its place. */
  folded?: boolean;
  onFold?: (folded: boolean) => void;
  /** A topic shown ahead of the focused window's, such as the wizard step's. */
  topicId?: string | null;
  /** The wizard step to sit beside: the Guide is pinned there, and cannot be moved or closed. */
  follow?: WizardGuideStep | null;
  /**
   * Windows the shell opens by itself, such as Needs Attention while items wait. Their opening is
   * not the reader turning to them, so they lead the Guide only when the reader selects something
   * in them; otherwise the workspace's own page stays.
   */
  opensItself?: ReadonlySet<string>;
}) {
  useSyncExternalStore(controller?.subscribe ?? (() => () => {}), controller?.getVersion ?? (() => 0));
  const active = controller?.activeWindow ?? null;
  const guide = useGuideTopic(workspace, active && opensItself.has(active) ? null : active, language, topicId);
  const [live, setLive] = useState<GuideRect>(() => rect ?? defaultGuideRect(window.innerWidth, window.innerHeight));
  // A saved place applies unless the Guide is pinned beside the wizard; after the wizard it returns there.
  const following = !!follow;
  useEffect(() => { if (!following) setLive(rect ?? defaultGuideRect(window.innerWidth, window.innerHeight)); }, [rect, following]);
  const liveRef = useRef(live);
  liveRef.current = live;
  const frame = useRef<HTMLElement | null>(null);
  const menuButton = useRef<HTMLButtonElement | null>(null);
  // Maximized over the workspace area, below the toolbar; never saved, as no window's is.
  const [maxRect, setMaxRect] = useState<GuideRect | null>(null);
  const maximized = !!maxRect && !following;
  const isFolded = folded && !following;
  // Moving or resizing with the arrow keys, from the window menu.
  const [keys, setKeys] = useState<"move" | "resize" | null>(null);
  // Popped out into a browser window of its own, like Agent Chat (user, 2026-11-01). The same
  // component renders there, so it keeps following selections in the panel, with its history.
  const [popup, setPopup] = useState<OpenPopup | null>(null);
  const [popupBlocked, setPopupBlocked] = useState(false);
  const popupRef = useRef<OpenPopup | null>(null);
  popupRef.current = popup;
  // Set while Phoenix closes the popup itself, so its closing is not read as the reader's Close.
  const closingPopup = useRef(false);
  // The panel element, whose theme class the popup follows.
  const themeHost = useRef<Element | null>(null);

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

  // Maximized: follow the workspace area as the screen or the toolbar changes.
  const workspaceArea = () => frame.current?.closest(".phx-shell")?.querySelector<HTMLElement>(".phx-workspaces") ?? null;
  const areaRect = (): GuideRect => {
    const r = workspaceArea()?.getBoundingClientRect() ?? { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
    return { x: r.left + MARGIN, y: r.top + MARGIN, w: Math.max(MIN_W, r.width - 2 * MARGIN), h: Math.max(MIN_H, r.height - 2 * MARGIN) };
  };
  const isMaximized = !!maxRect;
  useLayoutEffect(() => {
    if (!isMaximized) return undefined;
    const place = () => setMaxRect(areaRect());
    const area = workspaceArea();
    const observer = area && typeof ResizeObserver !== "undefined" ? new ResizeObserver(place) : null;
    if (area) observer?.observe(area);
    window.addEventListener("resize", place);
    return () => { observer?.disconnect(); window.removeEventListener("resize", place); };
  }, [isMaximized]); // eslint-disable-line react-hooks/exhaustive-deps

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
    if (following || maximized || e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
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

  const toggleFold = () => {
    setKeys(null);
    if (!isFolded) setMaxRect(null);
    onFold?.(!isFolded);
  };
  const toggleMaximize = () => {
    setKeys(null);
    if (maximized) { setMaxRect(null); return; }
    if (isFolded) onFold?.(false);
    setMaxRect(areaRect());
  };
  const startKeys = (mode: "move" | "resize") => {
    setKeys(mode);
    requestAnimationFrame(() => menuButton.current?.focus());
  };
  // The arrow keys move or resize by a step, kept on screen; Esc, Enter or Tab finishes and saves.
  const keyStep = (e: KeyboardEvent<HTMLButtonElement>): boolean => {
    if (!keys) return false;
    const dx = e.key === "ArrowRight" ? KEY_STEP : e.key === "ArrowLeft" ? -KEY_STEP : 0;
    const dy = e.key === "ArrowDown" ? KEY_STEP : e.key === "ArrowUp" ? -KEY_STEP : 0;
    if (dx || dy) {
      const vw = window.innerWidth, vh = window.innerHeight;
      setLive((r) => (keys === "move"
        ? { ...r, ...clampPosToViewport({ x: r.x + dx, y: r.y + dy }, r, vw, vh) }
        : { ...r, w: Math.min(Math.max(MIN_W, r.w + dx), vw - r.x - MARGIN), h: Math.min(Math.max(MIN_H, r.h + dy), vh - r.y - MARGIN) }));
      e.preventDefault();
      return true;
    }
    if (e.key === "Escape" || e.key === "Enter" || e.key === "Tab") {
      setKeys(null);
      onRect(liveRef.current);
      return e.key !== "Tab";
    }
    return false;
  };
  const popOut = () => {
    if (following) return;
    const root = frame.current?.getRootNode();
    themeHost.current = root instanceof ShadowRoot ? root.host : null;
    const opened = openPopupWindow({
      name: POPUP_NAME, mountId: POPUP_MOUNT_ID, width: liveRef.current.w, height: liveRef.current.h,
      title: t("guide.title"), language: document.documentElement.lang, background: "var(--phx-surface)", extraCss: DOCK_CSS,
    });
    setPopupBlocked(!opened);
    if (!opened) return;
    closingPopup.current = false;
    setKeys(null);
    setMaxRect(null);
    if (isFolded) onFold?.(false);
    setPopup(opened);
  };
  const popIn = useCallback(() => {
    const current = popupRef.current;
    if (!current) return;
    closingPopup.current = true;
    setPopup(null);
    // Let React take the content out before its window goes away.
    window.setTimeout(() => current.window.close(), 0);
  }, []);
  // Beside the wizard the Guide stays in the panel.
  useEffect(() => { if (following) popIn(); }, [following, popIn]);
  // The popup follows the panel's theme, and its own close button closes the Guide, as Agent Chat's does.
  useEffect(() => {
    if (!popup) return undefined;
    const sync = () => {
      const host = themeHost.current;
      applyThemeClass(popup.window.document.documentElement,
        host?.classList.contains("phx-theme-dark") ? "phx-theme-dark" : host?.classList.contains("phx-theme-light") ? "phx-theme-light" : null);
    };
    sync();
    const observer = themeHost.current ? new MutationObserver(sync) : null;
    if (themeHost.current) observer?.observe(themeHost.current, { attributes: true, attributeFilter: ["class", "data-phx-palette"] });
    const closed = () => {
      if (closingPopup.current) return;
      setPopup(null);
      onClose();
    };
    popup.window.addEventListener("pagehide", closed);
    return () => {
      observer?.disconnect();
      popup.window.removeEventListener("pagehide", closed);
    };
  }, [popup, onClose]);
  // The popup belongs to this page: leaving or reloading it, or closing the Guide, closes the popup.
  useEffect(() => {
    const end = () => {
      const current = popupRef.current;
      if (!current) return;
      closingPopup.current = true;
      current.window.close();
    };
    window.addEventListener("pagehide", end);
    return () => { window.removeEventListener("pagehide", end); end(); };
  }, []);

  const menu = (): MenuEntry[] => [
    { id: "move", label: t("workspace.moveKeyboard"), disabled: maximized, onSelect: () => startKeys("move") },
    { id: "resize", label: t("workspace.resizeKeyboard"), disabled: maximized || isFolded, onSelect: () => startKeys("resize") },
    "separator",
    { id: "fold", label: isFolded ? t("workspace.unfold") : t("workspace.fold"), onSelect: toggleFold },
    { id: "maximize", label: maximized ? t("agentchat.restore") : t("agentchat.maximize"), onSelect: toggleMaximize },
    { id: "close", label: t("common.close"), onSelect: onClose },
  ];
  const showMenu = useContextMenu();

  if (popup) {
    return createPortal(
      <section className="phx-guide is-popped" aria-label={t("guide.title")}>
        <header className="phx-guide-titlebar">
          <HistoryButtons guide={guide} iconSize={POPUP_ICON} />
          <h2 className="phx-guide-title">{t("guide.title")}</h2>
          <div className="phx-window-controls">
            <button type="button" className="phx-window-control" aria-label={t("agentchat.popIn")} title={t("agentchat.popIn")} onClick={popIn}>
              <Icon name="popIn" size={POPUP_ICON} />
            </button>
            <button type="button" className="phx-window-control" aria-label={t("common.close")} title={t("common.close")} onClick={onClose}>
              <Icon name="close" size={POPUP_ICON} />
            </button>
          </div>
        </header>
        <div className="phx-guide-body"><GuideBody guide={guide} /></div>
      </section>,
      popup.mount,
    );
  }

  const shown = maximized ? maxRect! : live;
  const classes = ["phx-guide", following && "is-pinned", isFolded && "is-folded", maximized && "is-maximized"].filter(Boolean).join(" ");
  return (
    <section ref={frame} className={classes} aria-label={t("guide.title")}
      style={{ left: shown.x, top: shown.y, width: shown.w, height: isFolded ? undefined : shown.h }}>
      <header className="phx-guide-titlebar" onPointerDown={startDrag} data-help={following ? undefined : "help.guideTitle"}
        onContextMenu={following || !showMenu ? undefined : (e) => showMenu(e, t("workspace.windowMenu"), menu())}>
        {!following && (
          <button type="button" className="phx-window-control" aria-label={isFolded ? t("workspace.unfold") : t("workspace.fold")}
            title={isFolded ? t("workspace.unfold") : t("workspace.fold")} data-help="help.fold" aria-expanded={!isFolded} onClick={toggleFold}>
            <Icon name={isFolded ? "folded" : "expand"} size={14} />
          </button>
        )}
        <HistoryButtons guide={guide} />
        <h2 className="phx-guide-title">{t("guide.title")}</h2>
        {!following && (
          <div className="phx-window-controls">
            {keys && <span className="phx-window-resize-hint" role="status">{t(keys === "move" ? "workspace.moveHint" : "workspace.resizeHint")}</span>}
            <MenuButton label={t("workspace.windowMenu")} help="help.guideMenu" icon={<Icon name="more" size={14} />} items={menu}
              className="phx-window-control" buttonRef={(el) => { menuButton.current = el; }} pressed={!!keys} onButtonKeyDown={keyStep} />
            <button type="button" className="phx-window-control" aria-label={t("agentchat.popOut")} title={t("agentchat.popOut")} data-help="help.guidePopOut" onClick={popOut}>
              <Icon name="popOut" size={14} />
            </button>
            <button type="button" className="phx-window-control" aria-label={maximized ? t("agentchat.restore") : t("agentchat.maximize")}
              title={maximized ? t("agentchat.restore") : t("agentchat.maximize")} data-help="help.maximizeWindow" onClick={toggleMaximize}>
              <Icon name={maximized ? "restore" : "maximize"} size={14} />
            </button>
            <button type="button" className="phx-window-control" aria-label={t("common.close")} title={t("common.close")} data-help="help.closeGuide" onClick={onClose}>
              <Icon name="close" size={14} />
            </button>
          </div>
        )}
      </header>
      <div className="phx-guide-body" hidden={isFolded}>
        {popupBlocked && <p className="banner banner-error" role="alert">{t("agentchat.popOutBlocked")}</p>}
        <GuideBody guide={guide} />
      </div>
      {!following && !isFolded && !maximized && RESIZE_EDGES.map((d) => <div key={d} className={`agentcli-resize agentcli-resize-${d}`} onPointerDown={startResize(d)} />)}
    </section>
  );
}

/** The phone Guide, heading the workspace's stack; without `onClose` (during the wizard) it cannot be closed. */
export function PhoneGuide({ workspace, language, onClose, topicId = null }: { workspace: string; language: string; onClose?: () => void; topicId?: string | null }) {
  const guide = useGuideTopic(workspace, null, language, topicId);
  return (
    <section className="phx-phone-window phx-guide-phone" aria-label={t("guide.title")}>
      <header className="phx-phone-title">
        <HistoryButtons guide={guide} />
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
