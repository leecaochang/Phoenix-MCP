/** Narrow screens: the workspace's windows as one scrolling stack, each foldable into its title bar. */

import { useId, useMemo, useState } from "react";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { t } from "../i18n";
import { Icon } from "../ui/Icon";
import type { WorkspaceId } from "./storage";
import { WindowVisibleContext } from "./visibility";
import { WindowPlaceContext } from "./tools";
import { ATTENTION_WINDOW, WINDOW_CONTENT, WORKSPACES, windowTitle, type WindowId } from "./workspaces";

function PhoneWindow({ id, workspace }: { id: WindowId; workspace: WorkspaceId }) {
  const [folded, setFolded] = useState(false);
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

export function PhoneStack({ workspace, showAttention }: { workspace: WorkspaceId; showAttention: boolean }) {
  const windows = WORKSPACES[workspace].windows.filter((id) => id !== ATTENTION_WINDOW || showAttention);
  // Needs Attention leads the stack when it is shown.
  windows.sort((a, b) => Number(b === ATTENTION_WINDOW) - Number(a === ATTENTION_WINDOW));
  return (
    <div className="phx-phone-stack" data-workspace={workspace}>
      {windows.map((id) => <PhoneWindow key={`${workspace}:${id}`} id={id} workspace={workspace} />)}
    </div>
  );
}
