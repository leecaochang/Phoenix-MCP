/**
 * Welcome to the Guide: a bright demo of what the Guide
 * does, with nothing to finish. Each tip reacts when the reader tries it, while
 * the page stays on screen: resting the pointer on a button shows that Quick
 * Help explained it; selecting something in a window invites the reader to read
 * about it; the page's own search box suggests a word and lists matches. Phones
 * have no Quick Help, so that tip is left out there. Make It Yours offers the
 * light or dark mode and the color theme from Settings. Dismiss sits in the
 * header and at the end, so the reader can leave at any time.
 *
 * The Guide opens on Welcome once, the first time it opens, and again after
 * Reset All Workspaces; a reader who ran the setup wizard met the Guide there,
 * so it does not open on Welcome afterwards. Welcome is always the first page
 * of the Guide's history, so Back reaches it, and Contents lists it. Its words
 * are the Guide topic `topic.welcome`; this module draws them, with the only
 * controls and pictures the Guide carries.
 */

import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { HEADING } from "../guide/compile";
import { searchGuide } from "../guide/search";
import { t } from "../i18n";
import { ResultCount } from "../components/common";
import { Icon, type IconName } from "../ui/Icon";
import { palettesByName, readPalette, setPalette, subscribeTheme } from "../ui/theme";
import { renderTopic, topicTitle, useSearchSections, type FoundTopic, type Guide } from "./guide";
import type { Theme } from "./settings";
import { WELCOME_STEPS, type WelcomeState, type WelcomeStep } from "./storage";
import { isImeKey } from "../utils/ime";

export const WELCOME_TOPIC = "topic.welcome";
/** The section holding the look controls. */
const LOOK = "look";
/** How many matches the page's own search lists, and the word it suggests: a name spelled alike in every language. */
const SEARCH_HITS = 3;
const SEARCH_SUGGESTION = "deepseek";

/** The tips a reader can try here: a phone has no Quick Help to point at. */
export function welcomeSteps(phone: boolean): WelcomeStep[] {
  return WELCOME_STEPS.filter((step) => !(phone && step === "point"));
}

/** Whether the Guide should open on Welcome: until the reader has seen it once. */
export function welcomeFirst(state: WelcomeState | undefined): boolean {
  return !state?.seen;
}

export interface Welcome {
  state: WelcomeState | undefined;
  phone: boolean;
  /** The tips the reader tried in this session; nothing is saved or required. */
  tried: readonly WelcomeStep[];
  /** Note that the reader tried a tip, so it shows that it worked. */
  mark: (step: WelcomeStep) => void;
  /** Welcome opened first; it does not again until Reset All Workspaces. */
  markSeen: () => void;
  /** Welcome is on screen, so the shell watches for the pointer resting on a button. */
  showing?: (on: boolean) => void;
  /** The light or dark mode from Settings, offered under Make It Yours. */
  theme?: Theme;
  setTheme?: (theme: Theme) => void;
}

/** Welcome's state and actions, from the shell; none outside it. */
export const WelcomeContext = createContext<Welcome | null>(null);
export const useWelcome = (): Welcome | null => useContext(WelcomeContext);

/** The page's opening, and its sections by anchor with their headings. */
function welcomeParts(body: string): { intro: string; sections: Map<string, { heading: string; body: string }> } {
  const parts: Array<{ anchor: string | null; heading: string; lines: string[] }> = [{ anchor: null, heading: "", lines: [] }];
  for (const line of body.split("\n")) {
    const h = HEADING.exec(line);
    if (h) parts.push({ anchor: h[3], heading: h[2], lines: [] });
    else parts[parts.length - 1].lines.push(line);
  }
  const sections = new Map(parts.slice(1).map((p) => [p.anchor!, { heading: p.heading, body: p.lines.join("\n").trim() }]));
  return { intro: parts[0].lines.join("\n"), sections };
}

const MODES: ReadonlyArray<{ mode: Theme; icon: IconName; label: string }> = [
  { mode: "light", icon: "themeLight", label: "settings.themeLight" },
  { mode: "auto", icon: "themeAuto", label: "settings.themeAuto" },
  { mode: "dark", icon: "themeDark", label: "settings.themeDark" },
];

/** A small scene for each tip, drawn in the tip's hue on its tinted tile. */
function StepPicture({ step }: { step: WelcomeStep }) {
  const pointer = "M4.04 4.69a.5.5 0 0 1 .65-.65l16 6.5a.5.5 0 0 1-.06.95l-6.13 1.58a2 2 0 0 0-1.43 1.43l-1.58 6.13a.5.5 0 0 1-.95.06z";
  return (
    <svg className="phx-welcome-picture" viewBox="0 0 64 48" aria-hidden="true" focusable="false"
      fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {step === "point" && (
        <>
          <rect x="8" y="7" width="34" height="15" rx="4" className="phx-welcome-fill" />
          <path d="M15 14.5h20" />
          <rect x="5" y="32" width="54" height="10" rx="3" className="phx-welcome-fill-soft" />
          <path d="M10 37h28" className="phx-welcome-ink" />
          <path d={pointer} transform="translate(31 11) scale(0.75)" className="phx-welcome-pointer" />
        </>
      )}
      {step === "select" && (
        <>
          <rect x="4" y="6" width="36" height="36" rx="4" className="phx-welcome-fill-soft" />
          <path d="M4 13h36" />
          <rect x="9" y="19" width="26" height="6" rx="2" className="phx-welcome-fill" />
          <path d="M10 31h18M10 36h12" className="phx-welcome-ink" />
          <rect x="45" y="6" width="15" height="36" rx="4" className="phx-welcome-fill" />
          <path d="M49 13h7M49 19h7M49 24h4" />
          <path d={pointer} transform="translate(25 19) scale(0.75)" className="phx-welcome-pointer" />
        </>
      )}
      {step === "search" && (
        <>
          <rect x="4" y="7" width="56" height="16" rx="8" className="phx-welcome-fill" />
          <circle cx="14" cy="15" r="4" />
          <path d="m17 18 2.5 2.5M25 15h16" />
          <path d="M8 31h44M8 37h30" className="phx-welcome-ink" />
        </>
      )}
    </svg>
  );
}

/** The page's own search box, suggesting a word to try; the first matches link straight to their pages. */
function SearchTry({ guide, onType }: { guide: Guide; onType: () => void }) {
  const [query, setQuery] = useState("");
  const sections = useSearchSections(guide.bundles);
  const hits = useMemo(() => searchGuide(sections, query).filter((h) => h.section.topic !== WELCOME_TOPIC).slice(0, SEARCH_HITS), [sections, query]);
  return (
    <div className="phx-welcome-finder">
      <label className="phx-welcome-search-field">
        <Icon name="search" size={14} />
        <input type="search" className="input" value={query} placeholder={t("guide.welcomeSearchHint", { word: SEARCH_SUGGESTION })}
          aria-label={t("guide.searchPlaceholder")} data-help="help.guideSearchInput"
          onChange={(e) => { setQuery(e.target.value); if (e.target.value.trim()) onType(); }}
          onKeyDown={(e) => { if (e.key === "Enter" && hits[0] && !isImeKey(e)) { e.preventDefault(); guide.open(hits[0].section.topic, hits[0].section.anchor); } }} />
      </label>
      <ResultCount count={hits.length} query={query} />
      {query.trim() && !hits.length && <p className="phx-guide-note">{t("guide.noResults")}</p>}
      {hits.length > 0 && (
        <ul className="phx-welcome-hits">
          {hits.map(({ section }) => (
            <li key={`${section.topic}#${section.anchor ?? ""}`}>
              <a href="#" className="phx-guide-link" data-help="help.guideLink" onClick={(e) => { e.preventDefault(); guide.open(section.topic, section.anchor); }}>
                {section.heading ? `${section.title}: ${section.heading}` : section.title}
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Light, Auto or Dark as icons, and the color theme as thumbnails: the same choices as Settings > General. */
function MakeItYours({ welcome }: { welcome: Welcome }) {
  const palette = useSyncExternalStore(subscribeTheme, readPalette);
  return (
    <div className="phx-welcome-look-controls">
      {welcome.theme && welcome.setTheme && (
        <div className="phx-welcome-modes" role="group" aria-label={t("settings.themeLabel")}>
          {MODES.map(({ mode, icon, label }) => (
            <button key={mode} type="button" className={`phx-welcome-mode${welcome.theme === mode ? " is-current" : ""}`} aria-pressed={welcome.theme === mode}
              aria-label={t(label)} title={t(label)} onClick={() => welcome.setTheme!(mode)}>
              <Icon name={icon} size={15} />
            </button>
          ))}
        </div>
      )}
      <div className="phx-welcome-swatches" role="group" aria-label={t("settings.colorThemeLabel")}>
        {palettesByName().map((p) => (
          <button key={p} type="button" className={`phx-welcome-swatch${palette === p ? " is-current" : ""}`} aria-pressed={palette === p}
            aria-label={t(`settings.colorTheme.${p}`)} title={t(`settings.colorTheme.${p}`)} onClick={() => setPalette(p)}>
            <span className={`phx-swatch phx-swatch-${p}`} aria-hidden="true">
              <span className="phx-swatch-bar" />
              <span className="phx-swatch-window"><span className="phx-swatch-dot" /></span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

/** The Welcome page: the topic's words drawn as bright tips that react when tried. */
export function WelcomeCard({ guide, topic, onDismiss }: { guide: Guide; topic: FoundTopic; onDismiss: () => void }) {
  const welcome = useWelcome();
  const phone = welcome?.phone ?? false;
  const showing = welcome?.showing;
  useEffect(() => {
    showing?.(true);
    return () => showing?.(false);
  }, [showing]);
  const parts = useMemo(() => welcomeParts(topic.body), [topic.body]);
  const html = (text: string) => ({ __html: renderTopic(text) });
  const steps = welcomeSteps(phone).filter((step) => parts.sections.has(step));
  const tried = new Set(welcome?.tried ?? []);
  const look = parts.sections.get(LOOK);
  return (
    <article className="phx-guide-topic phx-welcome" lang={topic.lang}>
      <header className="phx-welcome-hero">
        <div className="phx-welcome-hero-row">
          <h3>{topicTitle(topic.title)}</h3>
          <button type="button" className="phx-window-control" aria-label={t("guide.dismissWelcome")} title={t("guide.dismissWelcome")}
            data-help="help.guideDismissWelcome" onClick={onDismiss}>
            <Icon name="close" size={14} />
          </button>
        </div>
        {/* Escape-first renderer output, as every Guide page. */}
        <div className="phx-welcome-intro" dangerouslySetInnerHTML={html(parts.intro)} />
      </header>
      <ul className="phx-welcome-steps">
        {steps.map((step) => {
          const section = parts.sections.get(step)!;
          // Selecting something swaps the tip's line for an invitation to read about it, so nothing moves.
          const held = step === "select" ? guide.held : null;
          return (
            <li key={step} className={`phx-welcome-step phx-welcome-${step}${tried.has(step) ? " is-tried" : ""}`} data-welcome-step={step}>
              <span className="phx-welcome-tile">
                <StepPicture step={step} />
                {tried.has(step) && <span className="phx-welcome-badge" aria-hidden="true"><Icon name="check" size={11} /></span>}
              </span>
              <div className="phx-welcome-step-text">
                <h4>{topicTitle(section.heading)}</h4>
                {held ? (
                  <a href="#" className="phx-welcome-held" data-help="help.guideLink"
                    onClick={(e) => { e.preventDefault(); guide.open(held.id, guide.heldAnchor); }}>
                    <Icon name="documentation" size={14} />
                    <span>{t("guide.welcomeReadAbout", { page: topicTitle(held.title) })}</span>
                  </a>
                ) : section.body && <div dangerouslySetInnerHTML={html(section.body)} />}
                {step === "search" && <SearchTry guide={guide} onType={() => welcome?.mark("search")} />}
              </div>
            </li>
          );
        })}
      </ul>
      {look && welcome && (
        <section className="phx-welcome-look">
          <div className="phx-welcome-look-text">
            <h4>{topicTitle(look.heading)}</h4>
            {look.body && <div dangerouslySetInnerHTML={html(look.body)} />}
          </div>
          <MakeItYours welcome={welcome} />
        </section>
      )}
      {/* Dismiss again at the end, so it is plain the reader can leave at any time. */}
      <div className="phx-welcome-actions">
        <button type="button" className="btn btn-ghost btn-sm" data-help="help.guideDismissWelcome" onClick={onDismiss}>
          <Icon name="close" size={14} />
          <span>{t("guide.dismissWelcome")}</span>
        </button>
      </div>
    </article>
  );
}
