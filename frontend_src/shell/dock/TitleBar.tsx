/** Window title bars: the title with its fold control, the window controls, and the drag outline. */

import { createContext, useContext, useEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent } from "react";
import type { IDockviewGroupDragGhostProps, IDockviewHeaderActionsProps, IDockviewPanelHeaderProps, DockviewGroupPanel } from "dockview-react";
import { t } from "../../i18n";
import { Icon } from "../../ui/Icon";
import { MenuButton, type MenuEntry } from "../../ui/Menu";
import { neighbor, type Direction } from "./geometry";
import type { WorkspaceController } from "./controller";

export const ControllerContext = createContext<WorkspaceController | null>(null);

function useController(): WorkspaceController {
  const controller = useContext(ControllerContext);
  if (!controller) throw new Error("window title bar rendered outside its workspace");
  return controller;
}

function useFolded(controller: WorkspaceController, id: string | undefined): boolean {
  useSyncExternalStore(controller.folds.subscribe, controller.folds.getVersion);
  return id !== undefined && controller.folds.isFolded(id);
}

/** The title, with the fold control before it. The whole title bar drags the window. */
export function TitleTab(props: IDockviewPanelHeaderProps) {
  const controller = useController();
  const [title, setTitle] = useState(props.api.title ?? "");
  useEffect(() => {
    const sub = props.api.onDidTitleChange((e) => setTitle(e.title));
    return () => sub.dispose();
  }, [props.api]);
  const folded = useFolded(controller, props.api.id);
  return (
    <div className="phx-window-title">
      <button
        type="button"
        className="phx-window-control"
        aria-label={folded ? t("workspace.unfold") : t("workspace.fold")}
        title={folded ? t("workspace.unfold") : t("workspace.fold")}
        aria-expanded={!folded}
        onPointerDown={(e) => e.stopPropagation()}
        onClick={() => controller.toggleFold(props.api.group)}
      >
        <Icon name={folded ? "folded" : "expand"} size={14} />
      </button>
      <span className="phx-window-title-text">{title}</span>
    </div>
  );
}

const MOVES: Array<[Direction, string]> = [
  ["left", "workspace.moveLeft"], ["right", "workspace.moveRight"], ["up", "workspace.moveUp"], ["down", "workspace.moveDown"],
];
const RESIZE_STEP = 24;

/** Window controls: the window menu, Float or Dock, Maximize or Restore, Close. */
export function WindowControls(props: IDockviewHeaderActionsProps) {
  const controller = useController();
  const group = props.group;
  const panel = props.activePanel;
  const [floating, setFloating] = useState(group.api.location.type === "floating");
  const [maximized, setMaximized] = useState(() => group.api.isMaximized());
  const [resizing, setResizing] = useState(false);
  const menuButton = useRef<HTMLButtonElement | null>(null);
  useSyncExternalStore(controller.subscribe, controller.getVersion);

  useEffect(() => {
    const a = group.api.onDidLocationChange(() => setFloating(group.api.location.type === "floating"));
    const b = props.containerApi.onDidMaximizedGroupChange(() => setMaximized(group.api.isMaximized()));
    return () => { a.dispose(); b.dispose(); };
  }, [group, props.containerApi]);

  if (!panel) return null;
  const windowId = panel.id;

  // A move, float or dock re-parents the window: give focus back to its menu button.
  const thenRefocus = (action: () => void) => () => {
    action();
    requestAnimationFrame(() => {
      const target = controller.api?.getPanel(windowId)?.group.element.querySelector<HTMLElement>("[data-phx-window-menu]");
      if (target && target !== document.activeElement) target.focus();
    });
  };

  // Built when the menu opens: a drag or another window's move may have changed the neighbours.
  const items = (): MenuEntry[] => [
    ...MOVES.map(([dir, key]) => ({
      id: `move-${dir}`,
      label: t(key),
      disabled: floating || !controller.api || !neighbor(controller.api, group, dir),
      onSelect: thenRefocus(() => controller.move(group, dir)),
    })),
    "separator",
    { id: "resize", label: t("workspace.resizeKeyboard"), disabled: floating, onSelect: () => { setResizing(true); menuButton.current?.focus(); } },
    floating
      ? { id: "dock", label: t("workspace.dockWindow"), onSelect: thenRefocus(() => controller.dock(group)) }
      : { id: "float", label: t("workspace.floatWindow"), onSelect: thenRefocus(() => controller.float(group)) },
  ];

  const resizeKeys = (e: KeyboardEvent<HTMLButtonElement>): boolean => {
    if (!resizing) return false;
    const g: DockviewGroupPanel = group;
    if (e.key === "ArrowRight") g.api.setSize({ width: g.width + RESIZE_STEP });
    else if (e.key === "ArrowLeft") g.api.setSize({ width: g.width - RESIZE_STEP });
    else if (e.key === "ArrowDown") g.api.setSize({ height: g.height + RESIZE_STEP });
    else if (e.key === "ArrowUp") g.api.setSize({ height: g.height - RESIZE_STEP });
    else if (e.key === "Escape" || e.key === "Enter" || e.key === "Tab") { setResizing(false); return e.key !== "Tab"; }
    else return false;
    e.preventDefault();
    return true;
  };

  return (
    <div className="phx-window-controls" onPointerDown={(e) => e.stopPropagation()}>
      {resizing && <span className="phx-window-resize-hint" role="status">{t("workspace.resizeHint")}</span>}
      <MenuButton
        label={t("workspace.windowMenu")}
        icon={<Icon name="more" size={14} />}
        items={items}
        className="phx-window-control"
        buttonRef={(el) => {
          menuButton.current = el;
          el?.setAttribute("data-phx-window-menu", "");
        }}
        pressed={resizing}
        onButtonKeyDown={resizeKeys}
      />
      {floating ? (
        <button type="button" className="phx-window-control" aria-label={t("workspace.dockWindowAria")} title={t("workspace.dockWindowAria")}
          onClick={thenRefocus(() => controller.dock(group))}>
          <Icon name="dock" size={14} />
        </button>
      ) : (
        <>
          <button type="button" className="phx-window-control" aria-label={t("workspace.floatWindowAria")} title={t("workspace.floatWindowAria")}
            onClick={thenRefocus(() => controller.float(group))}>
            <Icon name="float" size={14} />
          </button>
          <button type="button" className="phx-window-control" aria-label={maximized ? t("agentchat.restore") : t("agentchat.maximize")}
            title={maximized ? t("agentchat.restore") : t("agentchat.maximize")}
            onClick={() => (maximized ? group.api.exitMaximized() : group.api.maximize())}>
            <Icon name={maximized ? "restore" : "maximize"} size={14} />
          </button>
        </>
      )}
      <button type="button" className="phx-window-control" aria-label={t("common.close")} title={t("common.close")}
        onClick={() => controller.close(group)}>
        <Icon name="close" size={14} />
      </button>
    </div>
  );
}

/** The dragged window's outline, following the pointer with the pointer on its title bar. */
export function WindowGhost({ group }: IDockviewGroupDragGhostProps) {
  return (
    <div className="phx-window-ghost">
      <div className="phx-window-ghost-title">{group.activePanel?.title ?? ""}</div>
    </div>
  );
}
