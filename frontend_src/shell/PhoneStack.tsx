/**
 * Narrow screens: the workspace's windows as one scrolling stack. Each window
 * folds into its title bar, moves up or down, maximizes over the stack and
 * closes, as its menu offers; the shell saves the result as the workspace's
 * phone arrangement (see PhoneLayout). Help and Notification History are not
 * offered on phones, and the Guide sits at the bottom of the stack while it is
 * open.
 */

import { useContext, useEffect, useId, useMemo, useRef, type MouseEvent, type ReactNode } from "react";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { t } from "../i18n";
import { DisplayModeContext } from "../ui/ActionButton";
import { Icon } from "../ui/Icon";
import { MenuButton, type MenuEntry } from "../ui/Menu";
import type { WorkspaceId } from "./storage";
import { WindowVisibleContext } from "./visibility";
import { WindowPlaceContext } from "./tools";
import type { GlobalSettings } from "../types";
import { ARCHIVED_WINDOW, ATTENTION_WINDOW, SHARED_WINDOWS, WINDOW_CONTENT, WORKSPACES, canAddToMy, windowAvailable, windowTitle, type WindowId } from "./workspaces";
import type { MesaFindings } from "./mesa";
import { movedOrder, phoneOrder, withMember, type PhoneLayout } from "./phoneLayout";
import { PHONE_ROWS, RowLimitContext } from "../ui/table/rowLimit";

/** Windows a phone never stacks: their content shows inside the window that opens them. */
const INLINE_ON_PHONES: readonly string[] = ["chatRoutingStatistics", "voiceRoutingStatistics", "aiTaskRoutingStatistics"];

/** Previous and Next in a details window (Log Details, Comparison, Review), by their Help keys. */
const STEP_BUTTONS = '[data-help="help.previousRecord"], [data-help="help.nextRecord"]';
/** Shown while a record loads; the window keeps its height until none is left. */
const LOADING = "[data-loading], .spinner, .loading-wrap";
/** The content must stay this long without changing size before the held height is let go. */
const SETTLE_MS = 300;
const HOLD_MAX_MS = 4000;

/**
 * Keep a window's height while it loads. A loading
 * placeholder is short, and on a phone the page is the scroller, so the
 * browser pulled the page up as the window shrank (Safari keeps no scroll
 * anchor) and the reader lost their place: stepping with Previous or Next,
 * choosing a row, refreshing. The window holds the height it last had with
 * nothing loading, from the moment a placeholder appears (a mutation, so
 * before the browser lays the shorter page out) or Previous or Next is
 * pressed, until nothing is loading and its size has settled.
 */
function useLoadHold(): { body: React.RefObject<HTMLDivElement | null>; onClickCapture: (e: MouseEvent<HTMLDivElement>) => void } {
  const body = useRef<HTMLDivElement | null>(null);
  const hold = useRef<(() => void) | null>(null);
  useEffect(() => {
    const el = body.current;
    if (!el) return undefined;
    let settled = el.hidden ? 0 : el.offsetHeight;
    let quiet = 0;
    let cap = 0;
    const letGo = () => {
      window.clearTimeout(quiet);
      window.clearTimeout(cap);
      el.style.minHeight = "";
      settled = el.offsetHeight;
    };
    const holding = () => el.style.minHeight !== "";
    const settle = () => {
      window.clearTimeout(quiet);
      quiet = window.setTimeout(() => (el.querySelector(LOADING) ? settle() : letGo()), SETTLE_MS);
    };
    const start = () => {
      if (!holding() && settled > 0 && !el.hidden) {
        el.style.minHeight = `${settled}px`;
        cap = window.setTimeout(letGo, HOLD_MAX_MS);
      }
      settle();
    };
    hold.current = start;
    const mutations = new MutationObserver(() => {
      if (el.querySelector(LOADING)) start();
    });
    mutations.observe(el, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-loading", "class"] });
    const sizes = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      if (holding()) settle();
      else if (!el.hidden && !el.querySelector(LOADING)) settled = el.offsetHeight;
    });
    sizes?.observe(el);
    return () => {
      mutations.disconnect();
      sizes?.disconnect();
      window.clearTimeout(quiet);
      window.clearTimeout(cap);
      el.style.minHeight = "";
      hold.current = null;
    };
  }, []);
  // Previous and Next may swap the record without a placeholder at first: hold from the press.
  const onClickCapture = (e: MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest(STEP_BUTTONS)) hold.current?.();
  };
  return { body, onClickCapture };
}

/** The phone arrangement a workspace starts from: folded and closed as its definition says. */
export function phoneDefaults(workspace: WorkspaceId): { folded: string[]; closed: string[] } {
  const def = WORKSPACES[workspace];
  return { folded: [...(def.folded ?? [])], closed: Object.keys(def.closedAt ?? {}) };
}

/** The windows a workspace can stack on a phone, before any is closed or found unavailable. */
export function phoneWindows(workspace: WorkspaceId, myWindows: readonly string[]): WindowId[] {
  const ids: WindowId[] = workspace === "my" ? [...myWindows.filter(canAddToMy) as WindowId[], ATTENTION_WINDOW as WindowId] : WORKSPACES[workspace].windows;
  // Help explains what is being touched, which a phone cannot show while the finger is down,
  // so neither it nor Notification History is offered on phones.
  return ids.filter((id) => !SHARED_WINDOWS.includes(id) && !INLINE_ON_PHONES.includes(id));
}

/** The windows closed in a phone arrangement that Workspace Options can bring back. */
export function closedPhoneWindows(workspace: WorkspaceId, layout: PhoneLayout | undefined, myWindows: readonly string[]): WindowId[] {
  const closed = layout?.closed ?? phoneDefaults(workspace).closed;
  // Needs Attention and the archived keys come back their own ways.
  return phoneWindows(workspace, myWindows).filter((id) => closed.includes(id) && id !== ATTENTION_WINDOW && id !== ARCHIVED_WINDOW);
}

function PhoneWindow({ id, workspace, folded, labels, maximized, menu, onFold, onClose }: {
  id: WindowId; workspace: WorkspaceId; folded: boolean; labels: boolean; maximized: boolean;
  /** The window menu's entries, built when it opens. */
  menu: () => MenuEntry[];
  onFold: () => void;
  onClose?: () => void;
}) {
  const place = useMemo(() => ({ workspace, window: id }), [workspace, id]);
  const mode = useContext(DisplayModeContext);
  const hold = useLoadHold();
  const titleId = useId();
  const Content = WINDOW_CONTENT[id];
  return (
    <section className={`phx-phone-window${maximized ? " is-maximized" : ""}`} aria-labelledby={titleId}>
      <header className="phx-phone-title">
        <button
          type="button"
          className="phx-window-control phx-phone-fold"
          aria-label={folded ? t("workspace.unfold") : t("workspace.fold")}
          data-help="help.fold"
          aria-expanded={!folded}
          onClick={onFold}
        >
          <Icon name={folded ? "folded" : "expand"} size={14} />
        </button>
        <h2 id={titleId} className="phx-window-title-text">{windowTitle(id)}</h2>
        <MenuButton label={t("workspace.windowMenu")} help="help.phoneWindowMenu" icon={<Icon name="more" size={14} />} items={menu}
          className="phx-window-control" buttonRef={(el) => el?.setAttribute("data-phx-window-menu", id)} />
        {onClose && (
          <button type="button" className="phx-window-control" aria-label={t("common.close")} title={t("common.close")}
            data-help="help.closeWindow" onClick={onClose}>
            <Icon name="close" size={14} />
          </button>
        )}
      </header>
      <WindowPlaceContext.Provider value={place}>
      <WindowVisibleContext.Provider value={!folded}>
      <DisplayModeContext.Provider value={labels ? "icon-text" : mode}>
      <RowLimitContext.Provider value={PHONE_ROWS}>
        <div ref={hold.body} className="phx-phone-body" data-phx-window={id} data-labels={labels ? "" : undefined} hidden={folded}
          onClickCapture={hold.onClickCapture}>
          <ErrorBoundary><Content /></ErrorBoundary>
        </div>
      </RowLimitContext.Provider>
      </DisplayModeContext.Provider>
      </WindowVisibleContext.Provider>
      </WindowPlaceContext.Provider>
    </section>
  );
}

export function PhoneStack({ workspace, layout, onLayout, maximized = null, onMaximize, showAttention, onCloseAttention, settings, mesa = null,
  showArchived = false, onCloseArchived, myWindows = [], onAddToMy, onRemoveFromMy, guide }: {
  workspace: WorkspaceId;
  /** The user's arrangement of this workspace; absent until they change it. */
  layout?: PhoneLayout;
  onLayout: (next: PhoneLayout) => void;
  /** The window maximized over the stack; never saved. */
  maximized?: string | null;
  onMaximize: (id: string | null) => void;
  showAttention: boolean; settings: GlobalSettings | null; mesa?: MesaFindings | null;
  /** Closes Needs Attention here, as its close control does on a desktop. */
  onCloseAttention?: () => void;
  /** Whether Show Archived is on: the archived keys head no stack until it is.  */
  showArchived?: boolean;
  /** Closing the archived keys turns Show Archived off. */
  onCloseArchived?: () => void;
  /** The windows added to My Workspace on phones. */
  myWindows?: readonly string[];
  /** Add a window to My Workspace from its window menu, and take one out by closing it there. */
  onAddToMy?: (id: string) => void;
  onRemoveFromMy?: (id: string) => void;
  /** The Guide, at the bottom of the stack while it is open. */
  guide?: ReactNode;
}) {
  const defaults = phoneDefaults(workspace);
  const folded = layout?.folded ?? defaults.folded;
  const closed = layout?.closed ?? defaults.closed;
  const labels = layout?.labels ?? [];
  const all = phoneOrder(phoneWindows(workspace, myWindows), layout?.order) as WindowId[];
  const windows = all.filter((id) => {
    if (id === ATTENTION_WINDOW) return showAttention;
    return !closed.includes(id) && windowAvailable(id, settings, mesa, showArchived) === true;
  });
  // Needs Attention leads the stack when it is shown, and the others move below it.
  windows.sort((a, b) => Number(b === ATTENTION_WINDOW) - Number(a === ATTENTION_WINDOW));
  const movable: string[] = windows.filter((id) => id !== ATTENTION_WINDOW);
  const current = maximized && (windows as string[]).includes(maximized) ? maximized : null;

  const change = (next: Partial<PhoneLayout>) => onLayout({ ...layout, folded, closed, ...next });
  const fold = (id: string) => change({ folded: withMember(folded, id, !folded.includes(id)) });
  const move = (id: string, by: -1 | 1) => {
    const order = movedOrder(all, movable, id, by);
    if (order) change({ order });
  };
  const close = (id: string): (() => void) | undefined => {
    if (id === ATTENTION_WINDOW) return onCloseAttention;
    // Closing a window in My Workspace takes it out, as on a desktop.
    if (workspace === "my") return onRemoveFromMy ? () => onRemoveFromMy(id) : undefined;
    if (id === ARCHIVED_WINDOW) return onCloseArchived;
    return () => {
      if (current === id) onMaximize(null);
      change({ closed: withMember(closed, id, true) });
    };
  };
  const menu = (id: WindowId): MenuEntry[] => {
    const at = movable.indexOf(id);
    const isFolded = folded.includes(id);
    const isMax = current === id;
    const shut = close(id);
    const entries: MenuEntry[] = [];
    if (at >= 0) {
      entries.push(
        { id: "move-up", label: t("workspace.moveUp"), help: t("help.menuMoveWindow"), disabled: at === 0 || !!current, onSelect: () => move(id, -1) },
        { id: "move-down", label: t("workspace.moveDown"), help: t("help.menuMoveWindow"), disabled: at === movable.length - 1 || !!current, onSelect: () => move(id, 1) },
        "separator",
      );
    }
    // Add to My Workspace sits in the window menu on phones too, as on a desktop.
    if (workspace !== "my" && onAddToMy && canAddToMy(id)) {
      entries.push({ id: "add-my", label: t("workspace.addToMy"), help: t("help.menuAddToMy"), disabled: myWindows.includes(id), onSelect: () => onAddToMy(id) });
    }
    const hasLabels = labels.includes(id);
    entries.push(
      { id: "labels", label: t("workspace.alwaysLabels"), help: t("help.menuAlwaysLabels"), checked: hasLabels, toggle: true, onSelect: () => change({ labels: withMember(labels, id, !hasLabels) }) },
      "separator",
      { id: "fold", label: isFolded ? t("workspace.unfold") : t("workspace.fold"), help: t("help.fold"), disabled: isMax, onSelect: () => fold(id) },
      { id: "maximize", label: isMax ? t("agentchat.restore") : t("agentchat.maximize"), help: t("help.maximizeWindow"), onSelect: () => {
        if (!isMax && isFolded) fold(id);
        onMaximize(isMax ? null : id);
      } },
    );
    if (shut) entries.push({ id: "close", label: t("common.close"), help: t("help.closeWindow"), onSelect: shut });
    return entries;
  };

  return (
    <div className={`phx-phone-stack${current ? " has-maximized" : ""}`} data-workspace={workspace}>
      {/* A maximized window hides the rest, which keep their state. */}
      {windows.map((id) => (
        <PhoneWindow key={`${workspace}:${id}`} id={id} workspace={workspace}
          folded={folded.includes(id) && current !== id} labels={labels.includes(id)} maximized={current === id}
          menu={() => menu(id)} onFold={() => (current === id ? onMaximize(null) : fold(id))} onClose={close(id)} />
      ))}
      {workspace === "my" && !windows.length && <p className="phx-workspace-empty phx-phone-empty">{t("workspace.myEmptyPhone")}</p>}
      {guide}
    </div>
  );
}
