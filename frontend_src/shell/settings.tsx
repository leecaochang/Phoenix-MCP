/**
 * The Settings workspace's shared state for its windows: the settings and the
 * coordination of changes to them, installation information, the Home
 * Assistant window's tab and Find Setting. It lives at shell level because a
 * closed window's content is unmounted, while a change, the toolbar's
 * kill-switch warning or a reveal from another workspace must still reach
 * the windows.
 *
 * Changes keep the former Settings page's ordering rules: one counter of
 * changes in flight, and a refresh asked for meanwhile runs once after the
 * last change settles, so a read started mid-change never overwrites a
 * change's answer and `saving` always clears.
 */

import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import type { GlobalSettings } from "../types";
import { t } from "../i18n";
import { injectSessionBlocked } from "../inject/compatibility";
import { useLatestRequest } from "../utils/latest_request";
import { matchesListSearch } from "../utils/list_search";
import { MenuRootContext } from "../ui/Menu";
import { Icon } from "../ui/Icon";
import type { Tool } from "./tools";
import { HOME_TAB_LABEL_KEYS, SETTING_INDEX, SETTINGS_WINDOW_TITLE_KEYS, type AriaName, type HomeTab, type SettingEntry, type SettingsWindowId } from "./settingsIndex";

export type Theme = "light" | "dark" | "auto";

export interface SettingsInfo {
  version: string;
  minHaVersion: string;
  githubUrl: string;
}

export interface SettingsState {
  settings: GlobalSettings | null;
  /** A change is being saved; controls stay focusable but do nothing meanwhile. */
  saving: boolean;
  /** Save a change; resolves to its error message, or null once saved or superseded by a later change. */
  patch: (patch: Partial<GlobalSettings>) => Promise<string | null>;
  /** Read the settings again, after any change in flight. */
  refresh: () => Promise<void>;
  info: SettingsInfo | null;
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  language: string;
  onLanguageChange: (language: string) => void;
  homeTab: HomeTab;
  setHomeTab: (tab: HomeTab) => void;
  findOpen: boolean;
  setFindOpen: (open: boolean) => void;
  /** Open Settings at a setting from the index: its window, tab and folded section, then focus it. */
  reveal: (entryId: string) => void;
  /** Open Settings at one of its windows, unfolding it, and bring it into view. */
  showWindow: (id: SettingsWindowId) => void;
}

const SettingsContext = createContext<SettingsState | null>(null);

/** The Settings workspace's state; null outside its provider. */
export function useSettings(): SettingsState | null {
  return useContext(SettingsContext);
}

/** The accessible name an index entry finds its control by. */
export function ariaText([key, params]: AriaName): string {
  return t(key, params ? Object.fromEntries(Object.entries(params).map(([name, value]) => [name, t(value)])) : undefined);
}

/** What Find Setting lists for an entry: the setting, then where it lives. */
export function entryLabel(entry: SettingEntry): string {
  return entry.aria && entry.aria[0] === entry.labelKey ? ariaText(entry.aria) : t(entry.labelKey).replace(/:$/, "");
}
export function entryPlace(entry: SettingEntry): string {
  const window = t(SETTINGS_WINDOW_TITLE_KEYS[entry.window]);
  return entry.tab ? `${window}, ${t(HOME_TAB_LABEL_KEYS[entry.tab])}` : window;
}

/** The control an entry points at, inside its window's content; null while it is not drawn yet. */
export function findControl(windowEl: Element, entry: SettingEntry): HTMLElement | null {
  if (entry.selector) return windowEl.querySelector<HTMLElement>(entry.selector);
  if (!entry.aria) return null;
  const name = ariaText(entry.aria);
  for (const el of windowEl.querySelectorAll<HTMLElement>("[aria-label]")) {
    if (el.getAttribute("aria-label") === name) return el;
  }
  return null;
}

/** How long a revealed setting stays highlighted. */
const FOUND_MS = 1600;
/** Frames a reveal waits for its window to open and draw before settling for the window. */
const REVEAL_FRAMES = 60;

export function SettingsProvider({ settings, onSettingsChange, theme, onThemeChange, language, onLanguageChange, active = true, root, revealWindow, onNavigate, children }: {
  settings: GlobalSettings | null;
  onSettingsChange: (settings: GlobalSettings) => void;
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  language: string;
  onLanguageChange: (language: string) => void;
  /** The Settings workspace is on screen: installation information loads the first time. */
  active?: boolean;
  /** Where the windows are drawn; reveals look for settings inside it. */
  root?: HTMLElement | null;
  /** Open (or unfold) a Settings window; absent outside the workspace shell. */
  revealWindow?: (id: SettingsWindowId) => void;
  /** Show the Settings workspace. */
  onNavigate?: () => void;
  children: ReactNode;
}) {
  const [saving, setSaving] = useState(false);
  const beginRefresh = useLatestRequest();
  const beginMutation = useLatestRequest();
  const mutationsInFlight = useRef(0);
  const refreshQueued = useRef(false);

  // Deleting a provider clears settings that used it server-side, and the Assist
  // setup changes voice_agent_pipeline_id server-side; re-read settings on either event.
  const refresh = useCallback(async () => {
    // A change's answer is authoritative for the write it performed: a read started
    // meanwhile must not supersede it, so refreshes wait until the last change settles.
    if (mutationsInFlight.current > 0) {
      refreshQueued.current = true;
      return;
    }
    const isLatest = beginRefresh();
    try {
      const updated = await api.getSettings();
      if (isLatest()) onSettingsChange(updated);
    } catch {
      // Event-driven refreshes are best-effort; a later event or change retries.
    }
  }, [beginRefresh, onSettingsChange]);

  useEffect(() => {
    const onEvent = () => { void refresh(); };
    window.addEventListener("phx-agentcli-providers-changed", onEvent);
    window.addEventListener("phx-settings-refresh", onEvent);
    return () => {
      window.removeEventListener("phx-agentcli-providers-changed", onEvent);
      window.removeEventListener("phx-settings-refresh", onEvent);
    };
  }, [refresh]);

  const patch = useCallback(async (requested: Partial<GlobalSettings>): Promise<string | null> => {
    const body = { ...requested };
    // In-context profile buttons stay off for this session once the injector proved incompatible.
    if ("mesa_inject_enabled" in body && injectSessionBlocked()) delete body.mesa_inject_enabled;
    if (!Object.keys(body).length) return null;
    const isLatest = beginMutation();
    // A refresh that began before this change can no longer be authoritative.
    beginRefresh();
    mutationsInFlight.current += 1;
    setSaving(true);
    try {
      const updated = await api.patchSettings(body);
      if (isLatest()) onSettingsChange(updated);
      return null;
    } catch (e: unknown) {
      refreshQueued.current = true;
      return isLatest() ? (e instanceof Error ? e.message : t("settings.saveFailed")) : null;
    } finally {
      mutationsInFlight.current -= 1;
      if (mutationsInFlight.current === 0) {
        setSaving(false);
        if (refreshQueued.current) {
          refreshQueued.current = false;
          void refresh();
        }
      }
    }
  }, [beginMutation, beginRefresh, onSettingsChange, refresh]);

  const [info, setInfo] = useState<SettingsInfo | null>(null);
  const infoAsked = useRef(false);
  useEffect(() => {
    if (!active || infoAsked.current) return;
    infoAsked.current = true;
    api.getInfo().then((r) => setInfo({ version: r.version, minHaVersion: r.min_ha_version, githubUrl: r.github_url })).catch(() => {});
  }, [active]);

  const [homeTab, setHomeTab] = useState<HomeTab>("voice");
  const [findOpen, setFindOpen] = useState(false);

  const rootRef = useRef(root);
  rootRef.current = root;
  const revealRef = useRef({ revealWindow, onNavigate });
  revealRef.current = { revealWindow, onNavigate };
  const revealing = useRef(0);
  useEffect(() => () => { revealing.current++; }, []);
  /** Show the Settings workspace, open `window`, and once it is drawn find `entry`'s control in it. */
  const open = useCallback((window: SettingsWindowId, entry: SettingEntry | null) => {
    setFindOpen(false);
    revealRef.current.onNavigate?.();
    if (entry?.tab) setHomeTab(entry.tab);
    const run = ++revealing.current;
    let frames = 0;
    const step = () => {
      if (run !== revealing.current) return;
      revealRef.current.revealWindow?.(window);
      const windowEl = rootRef.current?.querySelector<HTMLElement>(`[data-phx-window="${window}"]`) ?? null;
      const last = ++frames >= REVEAL_FRAMES;
      if (windowEl?.hidden) {
        // A folded window in the phone stack opens from its title bar.
        windowEl.closest(".phx-phone-window")?.querySelector<HTMLButtonElement>('.phx-phone-title button[aria-expanded="false"]')?.click();
        if (!last) requestAnimationFrame(step);
        return;
      }
      const control = windowEl && entry ? findControl(windowEl, entry) : null;
      if (!control) {
        if (!windowEl || (entry && !last)) { if (!last) requestAnimationFrame(step); return; }
        // The window alone was asked for, or the setting is not shown in this state (a provider not in use, say).
        windowEl.scrollIntoView?.({ block: "nearest" });
        return;
      }
      for (let d = control.parentElement?.closest("details"); d; d = d.parentElement?.closest("details")) {
        if (!d.open) d.open = true;
      }
      const row = control.closest<HTMLElement>(".toggle-row, .agentcli-settings-field, .conversation-limit") ?? control;
      control.scrollIntoView?.({ block: "center" });
      control.focus({ preventScroll: true });
      row.classList.add("phx-setting-found");
      globalThis.setTimeout(() => row.classList.remove("phx-setting-found"), FOUND_MS);
    };
    requestAnimationFrame(step);
  }, []);
  const reveal = useCallback((entryId: string) => {
    const entry = SETTING_INDEX.find((e) => e.id === entryId);
    if (entry) open(entry.window, entry);
  }, [open]);
  const showWindow = useCallback((id: SettingsWindowId) => open(id, null), [open]);

  const value = useMemo<SettingsState>(() => ({
    settings, saving, patch, refresh, info, theme, onThemeChange, language, onLanguageChange,
    homeTab, setHomeTab, findOpen, setFindOpen, reveal, showWindow,
  }), [settings, saving, patch, refresh, info, theme, onThemeChange, language, onLanguageChange, homeTab, findOpen, reveal, showWindow]);

  return (
    <SettingsContext.Provider value={value}>
      {children}
      {findOpen && root && <FindSettingPanel root={root} onClose={() => setFindOpen(false)} onPick={reveal} />}
    </SettingsContext.Provider>
  );
}

/**
 * Save changes from one window and keep that window's own error: a failed
 * change is reported where it was made, and the control shows the saved value again.
 */
export function useSettingsChange(): {
  settings: GlobalSettings | null;
  saving: boolean;
  change: (key: keyof GlobalSettings, value: GlobalSettings[keyof GlobalSettings]) => Promise<boolean>;
  changeMany: (patch: Partial<GlobalSettings>) => Promise<boolean>;
  error: string | null;
} {
  const s = useSettings();
  const [error, setError] = useState<string | null>(null);
  const patch = s?.patch;
  const changeMany = useCallback(async (p: Partial<GlobalSettings>) => {
    if (!patch) return false;
    setError(null);
    const failure = await patch(p);
    setError(failure);
    return failure === null;
  }, [patch]);
  const change = useCallback((key: keyof GlobalSettings, value: GlobalSettings[keyof GlobalSettings]) => changeMany({ [key]: value }), [changeMany]);
  return { settings: s?.settings ?? null, saving: s?.saving ?? false, change, changeMany, error };
}

/** Find Setting on the toolbar, offered by every Settings window and shown once. */
export function useFindSettingTool(): Tool | null {
  const s = useSettings();
  if (!s) return null;
  const label = t("workspace.findSetting");
  return {
    id: "findSetting", label, icon: "search", pressed: s.findOpen,
    onSelect: () => s.setFindOpen(!s.findOpen),
    onSet: (on) => s.setFindOpen(on),
    shared: { id: "findSetting", label },
  };
}

/** Recording Settings on the Logs toolbar: opens Logs and Notifications in Settings. */
export function useRecordingSettingsTool(): Tool[] {
  const s = useSettings();
  if (!s) return [];
  const label = t("logs.recordingSettings");
  return [{ id: "recordingSettings", label, icon: "recordingSettings", shared: { id: "recordingSettings", label }, onSelect: () => s.showWindow("settingsLogs") }];
}

/** Pause Updates on the Logs toolbar: a log stops updating itself while it is on; Refresh still works. */
export function pauseUpdatesTool(paused: boolean, setPaused: (paused: boolean) => void): Tool {
  const label = t("logs.pauseUpdates");
  return { id: "pause", label, icon: "paused", pressed: paused, shared: { id: "pause", label }, onSelect: () => setPaused(!paused), onSet: setPaused };
}

/** The search field and its results, beside the toolbar's Find Setting button. */
function FindSettingPanel({ root, onClose, onPick }: { root: HTMLElement; onClose: () => void; onPick: (id: string) => void }) {
  const menuRoot = useContext(MenuRootContext) ?? root;
  const [query, setQuery] = useState("");
  const panel = useRef<HTMLDivElement | null>(null);
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null);
  const anchor = () => root.querySelector<HTMLElement>('[data-tool="findSetting"]');
  useLayoutEffect(() => {
    const rect = anchor()?.getBoundingClientRect();
    const width = Math.min(360, window.innerWidth - 16);
    setPlace(rect ? { top: rect.bottom + 4, left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)) } : { top: 64, left: Math.max(8, (window.innerWidth - width) / 2) });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // The field takes focus once the panel is placed and shown; a hidden field cannot.
  const placed = place !== null;
  useEffect(() => { if (placed) panel.current?.querySelector("input")?.focus(); }, [placed]);
  useEffect(() => {
    const away = (e: PointerEvent) => {
      const path = e.composedPath();
      if (panel.current && !path.includes(panel.current) && !path.some((n) => n instanceof Element && n.matches('[data-tool="findSetting"]'))) onClose();
    };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  }, [onClose]);
  const close = () => { onClose(); anchor()?.focus(); };
  const results = SETTING_INDEX.filter((e) => matchesListSearch(`${entryLabel(e)} ${entryPlace(e)}`, query));
  const move = (from: HTMLElement, by: number) => {
    const items = [...(panel.current?.querySelectorAll<HTMLElement>("input, .phx-find-result") ?? [])];
    items[Math.max(0, Math.min(items.length - 1, items.indexOf(from) + by))]?.focus();
  };
  return createPortal(
    <div ref={panel} className="phx-find-setting" role="dialog" aria-label={t("workspace.findSetting")}
      style={place ? { top: place.top, left: place.left } : { visibility: "hidden" }}
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return; }
        if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); move(e.target as HTMLElement, e.key === "ArrowDown" ? 1 : -1); }
      }}>
      <label className="phx-find-setting-field">
        <Icon name="search" size={16} />
        <input type="search" className="input" aria-label={t("workspace.findSetting")} placeholder={t("workspace.findSettingPlaceholder")}
          value={query} onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && results[0]) { e.preventDefault(); onPick(results[0].id); } }} />
      </label>
      <div className="phx-find-setting-results" role="group" aria-label={t("workspace.findSettingResults")}>
        {results.length ? results.map((e) => (
          <button key={e.id} type="button" className="phx-find-result" onClick={() => onPick(e.id)}>
            <span>{entryLabel(e)}</span>
            <small>{entryPlace(e)}</small>
          </button>
        )) : <p className="phx-find-setting-empty">{t("workspace.findSettingNone")}</p>}
      </div>
    </div>,
    menuRoot,
  );
}
