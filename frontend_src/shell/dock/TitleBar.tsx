/** Window title bars: the title with its fold control, the window controls, and the drag outline. */

import { createContext, useContext, useEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent } from "react";
import type { IDockviewGroupDragGhostProps, IDockviewHeaderActionsProps, IDockviewPanelHeaderProps, DockviewGroupPanel } from "dockview-react";
import { t } from "../../i18n";
import { Icon } from "../../ui/Icon";
import { HELP_WINDOW } from "../help";
import { MenuButton, type MenuEntry } from "../../ui/Menu";
import { neighbor, type Direction } from "./geometry";
import { tooManyAcross } from "./rows";
import type { WorkspaceController } from "./controller";

export const ControllerContext = createContext<WorkspaceController | null>(null);

/** Items the shell adds to every window's menu, such as Add to My Workspace. */
export type WindowMenuExtras = (controller: WorkspaceController, windowId: string) => MenuEntry[];
export const WindowMenuExtrasContext = createContext<WindowMenuExtras | null>(null);

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
  // Dockview draws the focusable tab around this title; give it the title bar's help too, so
  // keyboard focus on the tab reads it instead of the window's name.
  const titleRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => { titleRef.current?.closest(".dv-tab")?.setAttribute("data-help", "help.windowTitle"); }, []);
  return (
    <div ref={titleRef} className="phx-window-title" data-help="help.windowTitle">
      <button
        type="button"
        className="phx-window-control"
        aria-label={folded ? t("workspace.unfold") : t("workspace.fold")}
        title={folded ? t("workspace.unfold") : t("workspace.fold")}
        data-help="help.fold"
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

/** How far Move Left, Right, Up and Down take a floating window. */
const FLOAT_STEP = 40;
const FLOAT_MOVES: Record<Direction, { left?: number; top?: number }> = {
  left: { left: -FLOAT_STEP }, right: { left: FLOAT_STEP }, up: { top: -FLOAT_STEP }, down: { top: FLOAT_STEP },
};

/** Asks a window's menu button to enter keyboard resizing, which its arrow keys then drive. */
const RESIZE_EVENT = "phx-window-resize";

function startResize(controller: WorkspaceController, windowId: string) {
  const button = controller.groupOf(windowId)?.element.querySelector<HTMLElement>("[data-phx-window-menu]");
  button?.focus();
  button?.dispatchEvent(new CustomEvent(RESIZE_EVENT));
}

/** A move, float or dock re-parents the window: afterwards give focus back to its menu button. */
function refocusWindowMenu(controller: WorkspaceController, windowId: string) {
  requestAnimationFrame(() => {
    const target = controller.groupOf(windowId)?.element.querySelector<HTMLElement>("[data-phx-window-menu]");
    if (target && target !== document.activeElement) target.focus();
  });
}

/**
 * The window menu, the same from the title bar's menu button and from a
 * right-click on the title bar: moves, Resize with Keyboard, Float or Dock,
 * pinning, the shell's `extras`, then Fold, Maximize and Close. Built when it
 * opens so availability reflects the current layout. Moves stay within the
 * window's own area: the scrolling grid or its pinned band.
 */
export function windowMenuEntries(controller: WorkspaceController, group: DockviewGroupPanel, extras?: WindowMenuExtras | null): MenuEntry[] {
  const windowId = group.activePanel?.id;
  if (!windowId) return [];
  const host = controller.hostOfGroup(group);
  const hostApi = host ? controller.apiOf(host) : null;
  const floating = group.api.location.type === "floating";
  const pinned = host === "top" || host === "bottom";
  const then = (action: () => void) => () => { action(); refocusWindowMenu(controller, windowId); };
  const entries: MenuEntry[] = MOVES.map(([dir, key]) => {
    // A floating window moves by a step over the workspace, so the keyboard can place it too.
    if (floating && host === "float") return { id: `move-${dir}`, label: t(key), help: t("help.menuMoveFloat"), onSelect: then(() => controller.nudgeFloat(group, FLOAT_MOVES[dir])) };
    const target = !floating && hostApi ? neighbor(hostApi, group, dir) : null;
    // Moving beside a window in a row that is already four across is refused, as a drop there is.
    const crowded = !!target && (dir === "left" || dir === "right") && tooManyAcross(hostApi!, target, group);
    return { id: `move-${dir}`, label: t(key), help: t("help.menuMoveWindow"), disabled: !target || crowded, onSelect: then(() => controller.move(group, dir)) };
  });
  entries.push("separator");
  entries.push({ id: "resize", label: t("workspace.resizeKeyboard"), help: t("help.menuResizeKeyboard"), disabled: floating && host !== "float", onSelect: () => startResize(controller, windowId) });
  entries.push(floating
    ? { id: "dock", label: t("workspace.dockWindow"), help: t("help.dockWindow"), onSelect: then(() => controller.dock(group)) }
    : { id: "float", label: t("workspace.floatWindow"), help: t("help.floatWindow"), onSelect: then(() => controller.float(group)) });
  // Pinned windows stay in place while the grid scrolls.
  if (controller.apiOf("top") && controller.apiOf("bottom")) {
    if (pinned) entries.push({ id: "unpin", label: t("workspace.unpin"), help: t("help.menuUnpin"), onSelect: then(() => controller.unpin(group)) });
    if (host !== "top") entries.push({ id: "pin-top", label: t("workspace.pinTop"), help: t("help.menuPinTop"), onSelect: then(() => controller.pin(group, "top")) });
    if (host !== "bottom") entries.push({ id: "pin-bottom", label: t("workspace.pinBottom"), help: t("help.menuPinBottom"), onSelect: then(() => controller.pin(group, "bottom")) });
  }
  const extra = extras?.(controller, windowId) ?? [];
  if (extra.length) entries.push("separator", ...extra);
  const folded = controller.folds.isFolded(windowId);
  const maximized = group.api.isMaximized();
  entries.push("separator",
    { id: "fold", label: folded ? t("workspace.unfold") : t("workspace.fold"), help: t("help.fold"), onSelect: () => controller.toggleFold(group) });
  if (!floating && !pinned) entries.push({ id: "maximize", label: maximized ? t("agentchat.restore") : t("agentchat.maximize"), help: t("help.maximizeWindow"), onSelect: () => (maximized ? controller.restoreMaximized(group) : controller.maximize(group)) });
  entries.push({ id: "close", label: t("common.close"), help: t(windowId === HELP_WINDOW ? "help.closeHelpWindow" : "help.closeWindow"), onSelect: () => controller.close(group) });
  return entries;
}
const RESIZE_STEP = 24;

/** Window controls: the window menu, Float or Dock, Maximize or Restore, Close. */
export function WindowControls(props: IDockviewHeaderActionsProps) {
  const controller = useController();
  const extras = useContext(WindowMenuExtrasContext);
  const group = props.group;
  const panel = props.activePanel;
  const [floating, setFloating] = useState(group.api.location.type === "floating");
  const [maximized, setMaximized] = useState(() => group.api.isMaximized());
  const [resizing, setResizing] = useState(false);
  const menuButton = useRef<HTMLButtonElement | null>(null);
  useSyncExternalStore(controller.subscribe, controller.getVersion);

  // Resize with Keyboard, chosen from either menu, arrives as an event on the menu button.
  useEffect(() => {
    const button = menuButton.current;
    if (!button) return undefined;
    const begin = () => setResizing(true);
    button.addEventListener(RESIZE_EVENT, begin);
    return () => button.removeEventListener(RESIZE_EVENT, begin);
  }, [panel]);

  useEffect(() => {
    const a = group.api.onDidLocationChange(() => setFloating(group.api.location.type === "floating"));
    const b = props.containerApi.onDidMaximizedGroupChange(() => setMaximized(group.api.isMaximized()));
    return () => { a.dispose(); b.dispose(); };
  }, [group, props.containerApi]);

  if (!panel) return null;
  const windowId = panel.id;
  const host = controller.hostOfGroup(group);
  const pinned = host === "top" || host === "bottom";
  const thenRefocus = (action: () => void) => () => { action(); refocusWindowMenu(controller, windowId); };
  const items = () => windowMenuEntries(controller, group, extras);

  const resizeKeys = (e: KeyboardEvent<HTMLButtonElement>): boolean => {
    if (!resizing) return false;
    const g: DockviewGroupPanel = group;
    const delta = { ArrowRight: { width: RESIZE_STEP }, ArrowLeft: { width: -RESIZE_STEP }, ArrowDown: { height: RESIZE_STEP }, ArrowUp: { height: -RESIZE_STEP } }[e.key];
    if (floating && delta) {
      // Laid out again at its new size, the window gets a new title bar: resizing carries on there.
      controller.nudgeFloat(g, delta);
      requestAnimationFrame(() => startResize(controller, windowId));
      e.preventDefault();
      return true;
    }
    if (e.key === "ArrowRight") g.api.setSize({ width: g.width + RESIZE_STEP });
    else if (e.key === "ArrowLeft") g.api.setSize({ width: g.width - RESIZE_STEP });
    else if (e.key === "ArrowDown") controller.resizeHeight(g, RESIZE_STEP);
    else if (e.key === "ArrowUp") controller.resizeHeight(g, -RESIZE_STEP);
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
        help="help.windowMenu"
        icon={<Icon name="more" size={14} />}
        items={items}
        className="phx-window-control"
        buttonRef={(el) => {
          menuButton.current = el;
          // The window it belongs to, so F6 can find the window from its title bar.
          el?.setAttribute("data-phx-window-menu", windowId);
        }}
        pressed={resizing}
        onButtonKeyDown={resizeKeys}
      />
      {floating ? (
        <button type="button" className="phx-window-control" aria-label={t("workspace.dockWindowAria")} title={t("workspace.dockWindowAria")}
          data-help="help.dockWindow" onClick={thenRefocus(() => controller.dock(group))}>
          <Icon name="dock" size={14} />
        </button>
      ) : (
        <>
          <button type="button" className="phx-window-control" aria-label={t("workspace.floatWindowAria")} title={t("workspace.floatWindowAria")}
            data-help="help.floatWindow" onClick={thenRefocus(() => controller.float(group))}>
            <Icon name="float" size={14} />
          </button>
          {!pinned && (
            <button type="button" className="phx-window-control" aria-label={maximized ? t("agentchat.restore") : t("agentchat.maximize")}
              title={maximized ? t("agentchat.restore") : t("agentchat.maximize")}
              data-help="help.maximizeWindow" onClick={() => (maximized ? controller.restoreMaximized(group) : controller.maximize(group))}>
              <Icon name={maximized ? "restore" : "maximize"} size={14} />
            </button>
          )}
        </>
      )}
      <button type="button" className="phx-window-control" aria-label={t("common.close")} title={t("common.close")}
        data-help={panel?.id === HELP_WINDOW ? "help.closeHelpWindow" : "help.closeWindow"} onClick={() => controller.close(group)}>
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
