/**
 * Narrow screens: the workspace's windows as one scrolling stack. Each window
 * folds into its title bar, moves up or down, maximizes over the stack and
 * closes, as its menu offers; the shell saves the result as the workspace's
 * phone arrangement (see PhoneLayout).
 */

import { useContext, useId, useMemo, type ReactNode } from "react";
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

/** Windows a phone never stacks: their content shows inside the window that opens them. */
const INLINE_ON_PHONES: readonly string[] = ["chatRoutingStatistics", "voiceRoutingStatistics"];

/** The phone arrangement a workspace starts from: folded and closed as its definition says. */
export function phoneDefaults(workspace: WorkspaceId): { folded: string[]; closed: string[] } {
  const def = WORKSPACES[workspace];
  return { folded: [...(def.folded ?? [])], closed: Object.keys(def.closedAt ?? {}) };
}

/** The windows a workspace can stack on a phone, before any is closed or found unavailable. */
export function phoneWindows(workspace: WorkspaceId, myWindows: readonly string[]): WindowId[] {
  const ids: WindowId[] = workspace === "my" ? [...myWindows.filter(canAddToMy) as WindowId[], ATTENTION_WINDOW as WindowId] : WORKSPACES[workspace].windows;
  return ids.filter((id) => !SHARED_WINDOWS.includes(id) && !INLINE_ON_PHONES.includes(id));
}

/** The windows closed in a phone arrangement that Workspace Options can bring back. */
export function closedPhoneWindows(workspace: WorkspaceId, layout: PhoneLayout | undefined, myWindows: readonly string[]): WindowId[] {
  const closed = layout?.closed ?? phoneDefaults(workspace).closed;
  // Needs Attention and the archived keys come back their own ways.
  return phoneWindows(workspace, myWindows).filter((id) => closed.includes(id) && id !== ATTENTION_WINDOW && id !== ARCHIVED_WINDOW);
}

function PhoneWindow({ id, workspace, folded, labels, maximized, menu, onFold, onClose, onAdd, added = false }: {
  id: WindowId; workspace: WorkspaceId; folded: boolean; labels: boolean; maximized: boolean;
  /** The window menu's entries, built when it opens. */
  menu: () => MenuEntry[];
  onFold: () => void;
  onClose?: () => void;
  /** Add this window to My Workspace; given outside My Workspace for a window that can be added. */
  onAdd?: () => void;
  added?: boolean;
}) {
  const place = useMemo(() => ({ workspace, window: id }), [workspace, id]);
  const mode = useContext(DisplayModeContext);
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
        {onAdd && (
          <button type="button" className="phx-window-control" aria-label={t("workspace.addToMy")} title={t("workspace.addToMy")}
            data-help="help.menuAddToMy" disabled={added} onClick={onAdd}>
            <Icon name="add" size={14} />
          </button>
        )}
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
        <div className="phx-phone-body" data-phx-window={id} data-labels={labels ? "" : undefined} hidden={folded}>
          <ErrorBoundary><Content /></ErrorBoundary>
        </div>
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
  /** Add a window to My Workspace from its title bar, and take one out by closing it there (user, 2026-11-01). */
  onAddToMy?: (id: string) => void;
  onRemoveFromMy?: (id: string) => void;
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
      {/* The Guide heads the stack when it is open. A maximized window hides the rest, which keep their state. */}
      {guide}
      {windows.map((id) => (
        <PhoneWindow key={`${workspace}:${id}`} id={id} workspace={workspace}
          folded={folded.includes(id) && current !== id} labels={labels.includes(id)} maximized={current === id}
          menu={() => menu(id)} onFold={() => (current === id ? onMaximize(null) : fold(id))} onClose={close(id)}
          onAdd={workspace !== "my" && onAddToMy && canAddToMy(id) ? () => onAddToMy(id) : undefined}
          added={myWindows.includes(id)} />
      ))}
      {workspace === "my" && !windows.length && <p className="phx-workspace-empty phx-phone-empty">{t("workspace.myEmptyPhone")}</p>}
    </div>
  );
}
