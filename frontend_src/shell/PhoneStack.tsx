/** Narrow screens: the workspace's windows as one scrolling stack, each foldable into its title bar. */

import { useId, useMemo, useState, type ReactNode } from "react";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { t } from "../i18n";
import { Icon } from "../ui/Icon";
import type { WorkspaceId } from "./storage";
import { WindowVisibleContext } from "./visibility";
import { WindowPlaceContext } from "./tools";
import type { GlobalSettings } from "../types";
import { ATTENTION_WINDOW, SHARED_WINDOWS, WINDOW_CONTENT, WORKSPACES, canAddToMy, windowAvailable, windowTitle, type WindowId } from "./workspaces";
import type { MesaFindings } from "./mesa";

function PhoneWindow({ id, workspace, onClose, onAdd, added = false }: {
  id: WindowId; workspace: WorkspaceId; onClose?: () => void;
  /** Add this window to My Workspace; given outside My Workspace for a window that can be added. */
  onAdd?: () => void;
  added?: boolean;
}) {
  const [folded, setFolded] = useState(() => WORKSPACES[workspace].folded?.includes(id) ?? false);
  const place = useMemo(() => ({ workspace, window: id }), [workspace, id]);
  const titleId = useId();
  const Content = WINDOW_CONTENT[id];
  return (
    <section className="phx-phone-window" aria-labelledby={titleId}>
      <header className="phx-phone-title">
        <button
          type="button"
          className="phx-window-control"
          aria-label={folded ? t("workspace.unfold") : t("workspace.fold")}
          aria-expanded={!folded}
          onClick={() => setFolded(!folded)}
        >
          <Icon name={folded ? "folded" : "expand"} size={14} />
        </button>
        <h2 id={titleId} className="phx-window-title-text">{windowTitle(id)}</h2>
        {onAdd && (
          <button type="button" className="phx-window-control" aria-label={t("workspace.addToMy")} title={t("workspace.addToMy")}
            disabled={added} onClick={onAdd}>
            <Icon name="add" size={14} />
          </button>
        )}
        {onClose && (
          <button type="button" className="phx-window-control" aria-label={t("common.close")} onClick={onClose}>
            <Icon name="close" size={14} />
          </button>
        )}
      </header>
      <WindowPlaceContext.Provider value={place}>
      <WindowVisibleContext.Provider value={!folded}>
        <div className="phx-phone-body" data-phx-window={id} hidden={folded}>
          <ErrorBoundary><Content /></ErrorBoundary>
        </div>
      </WindowVisibleContext.Provider>
      </WindowPlaceContext.Provider>
    </section>
  );
}

export function PhoneStack({ workspace, showAttention, onCloseAttention, settings, mesa = null, showArchived = false, myWindows = [], onAddToMy, onRemoveFromMy, guide }: {
  workspace: WorkspaceId; showAttention: boolean; settings: GlobalSettings | null; mesa?: MesaFindings | null;
  /** Closes Needs Attention here, as its close control does on a desktop. */
  onCloseAttention?: () => void;
  /** Whether Show Archived is on: the archived keys head no stack until it is.  */
  showArchived?: boolean;
  /** The windows added to My Workspace on phones. */
  myWindows?: readonly string[];
  /** Add a window to My Workspace from its title bar, and take one out by closing it there (user, 2026-11-01). */
  onAddToMy?: (id: string) => void;
  onRemoveFromMy?: (id: string) => void;
  guide?: ReactNode;
}) {
  const def = WORKSPACES[workspace];
  // My Workspace stacks the windows added to it; phones add them once their windows have menus.
  const ids: WindowId[] = workspace === "my" ? [...myWindows.filter(canAddToMy) as WindowId[], ATTENTION_WINDOW as WindowId] : def.windows;
  const windows = ids.filter((id) => !SHARED_WINDOWS.includes(id) && (id === ATTENTION_WINDOW ? showAttention : windowAvailable(id, settings, mesa, showArchived) === true));
  // Needs Attention leads the stack when it is shown.
  windows.sort((a, b) => Number(b === ATTENTION_WINDOW) - Number(a === ATTENTION_WINDOW));
  return (
    <div className="phx-phone-stack" data-workspace={workspace}>
      {/* The Guide heads the stack when it is open. */}
      {guide}
      {windows.map((id) => (
        <PhoneWindow key={`${workspace}:${id}`} id={id} workspace={workspace}
          onClose={id === ATTENTION_WINDOW ? onCloseAttention : workspace === "my" && onRemoveFromMy ? () => onRemoveFromMy(id) : undefined}
          onAdd={workspace !== "my" && onAddToMy && canAddToMy(id) ? () => onAddToMy(id) : undefined}
          added={myWindows.includes(id)} />
      ))}
      {workspace === "my" && !windows.length && <p className="phx-workspace-empty phx-phone-empty">{t("workspace.myEmptyPhone")}</p>}
    </div>
  );
}
