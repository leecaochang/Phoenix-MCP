/**
 * The main toolbar: workspaces, Agent Chat or the kill-switch warning,
 * documentation and workspace options, plus a second row of tools for the
 * visible workspace. It docks to any edge or floats by its grip, lies
 * horizontal or stands vertical, collapses to its grip and options, and shows
 * icons only or icons with text. Its options menu opens from its own button,
 * from the grip and by right-click. Horizontal with icons and text, labels
 * sit under the icons and the main row keeps to one line (toolbarFit.ts);
 * vertical, every button shares one width.
 */

import { createContext, useContext, useEffect, useLayoutEffect, useRef, useSyncExternalStore, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import { localeNumber, t } from "../i18n";
import { Icon } from "../ui/Icon";
import { MenuButton, useContextMenu, type MenuEntry } from "../ui/Menu";
import type { WorkspaceController } from "./dock/controller";
import { usePanelData } from "./PanelData";
import { toolbarVertical, withToolbarDock, WORKSPACE_IDS, type Env, type ToolbarDock, type ToolbarState, type WorkspaceId } from "./storage";
import { chooseFit, fitClass, rowFits, singleRowFits } from "./toolbarFit";
import { SHARED_GROUP, ToolRegistryContext, type Tool, type ToolGroup, type ToolRegistry } from "./tools";
import { useSettings } from "./settings";
import { HELP_WINDOW } from "./help";
import { WORKSPACES, windowTitle } from "./workspaces";

/** The shell's arrangement actions, shared by every Workspace Options menu. */
export interface WorkspaceActions {
  saveMyDefault: (controller: WorkspaceController) => void;
  restoreMyDefault: (controller: WorkspaceController) => void;
  hasMyDefault: (controller: WorkspaceController) => boolean;
  restoreOriginal: (controller: WorkspaceController) => void;
  /** Reset All Workspaces: the toolbar, Quick Help, Guide and every arrangement, never My Defaults or settings. */
  restorePanelDefaults: () => void;
  /** Items are waiting and Needs Attention was closed in this workspace: offer it back (user, 2026-10-29). */
  attentionHidden: (controller: WorkspaceController) => boolean;
  showAttention: (controller: WorkspaceController) => void;
  /** Open a window a toolbar tool opens (Archived Access Keys, Log Settings), as its tool would. */
  openToolWindow: (controller: WorkspaceController, id: string) => void;
}
export const WorkspaceActionsContext = createContext<WorkspaceActions | null>(null);

/**
 * Workspace options: reopen closed windows, save or restore My Default,
 * Reset This Workspace (which empties My Workspace), and Reset All Workspaces
 * (every arrangement plus the toolbar, Quick Help and Guide).
 */
export function workspaceMenu(controller: WorkspaceController, actions: WorkspaceActions | null): MenuEntry[] {
  const toolWindows: readonly string[] = WORKSPACES[controller.id as WorkspaceId]?.toolWindows ?? [];
  // Quick Help is shown and hidden from the Help menu; Notifications keeps its own wording below.
  const items: MenuEntry[] = controller.closedWindows().filter((id) => id !== NOTIFICATIONS && id !== HELP_WINDOW && !toolWindows.includes(id)).map((id) => ({
    id: `reopen-${id}`,
    label: t("workspace.reopen", { window: windowTitle(id) }),
    help: t("help.menuReopen", { window: windowTitle(id) }),
    onSelect: () => controller.reopen(id),
  }));
  // A window a toolbar tool opens is offered here too while it is hidden (user, 2026-10-31).
  for (const id of toolWindows) {
    if (!controller.groupOf(id) && controller.windows.includes(id)) {
      items.push({ id: `reopen-${id}`, label: t("workspace.reopen", { window: windowTitle(id) }), help: t("help.menuReopen", { window: windowTitle(id) }), onSelect: () => (actions ? actions.openToolWindow(controller, id) : controller.reopen(id)) });
    }
  }
  // Needs Attention is closed per workspace: while items wait, every workspace offers it back.
  if (actions?.attentionHidden(controller)) {
    items.push({ id: "show-attention", label: t("workspace.showAttention"), help: t("help.menuShowAttention"), onSelect: () => actions.showAttention(controller) });
  }
  // Notifications is offered back like any other closed window, in its own words; it is
  // closed from its title bar, not from here (user, 2026-10-29).
  if (controller.windows.includes(NOTIFICATIONS) && !controller.groupOf(NOTIFICATIONS)) {
    items.push({ id: "show-notifications", label: t("notifications.showHistory"), help: t("help.menuShowNotifications"), onSelect: () => controller.reopen(NOTIFICATIONS) });
  }
  if (items.length) items.push("separator");
  if (actions) {
    items.push(
      { id: "save-default", label: t("workspace.saveMyDefault"), help: t("help.menuSaveDefault"), onSelect: () => actions.saveMyDefault(controller) },
      { id: "restore-default", label: t("workspace.restoreMyDefault"), help: t("help.menuRestoreDefault"), disabled: !actions.hasMyDefault(controller), onSelect: () => actions.restoreMyDefault(controller) },
    );
  }
  // My Workspace's reset empties it.
  items.push({ id: "restore", label: t("workspace.restoreOriginal"), hint: t(controller.id === "my" ? "workspace.resetMyHint" : "workspace.resetWorkspaceHint"), onSelect: () => (actions ? actions.restoreOriginal(controller) : controller.restoreOriginal()) });
  if (actions) items.push("separator", { id: "panel-defaults", label: t("workspace.restorePanelDefaults"), hint: t("workspace.resetAllHint"), onSelect: actions.restorePanelDefaults });
  return items;
}

const NOTIFICATIONS = "notifications";

const DOCKS: Array<[ToolbarDock, string]> = [
  ["top", "workspace.dockTop"], ["bottom", "workspace.dockBottom"], ["left", "workspace.dockLeft"], ["right", "workspace.dockRight"], ["floating", "workspace.floatToolbar"],
];
const DOCK_HELP: Record<ToolbarDock, string> = {
  top: "help.menuDockTop", bottom: "help.menuDockBottom", left: "help.menuDockLeft", right: "help.menuDockRight", floating: "help.menuFloatToolbar",
};
const MOVE_STEP = 16;
/** Space kept between a floating toolbar and the panel's edges. */
const FLOAT_MARGIN = 8;

/** A floating toolbar's position kept inside the panel. */
function within(shell: DOMRect, bar: DOMRect, x: number, y: number): { x: number; y: number } {
  return {
    x: Math.round(Math.max(FLOAT_MARGIN, Math.min(shell.width - bar.width - FLOAT_MARGIN, x))),
    y: Math.round(Math.max(FLOAT_MARGIN, Math.min(shell.height - bar.height - FLOAT_MARGIN, y))),
  };
}

/**
 * The toolbar's own menu: display mode, one row or two, where it sits, and
 * Collapse or Expand. While floating it offers the orientation it is not in,
 * and no Float Toolbar. Desktop only: phones keep the toolbar docked at the
 * top, with icons only, and never collapsed.
 */
function appearanceMenu(state: ToolbarState, onChange: (next: ToolbarState) => void, env: Env): MenuEntry[] {
  const update = (patch: Partial<ToolbarState>) => onChange({ ...state, ...patch });
  const items: MenuEntry[] = [
    { id: "icons", label: t("workspace.iconsOnly"), help: t("help.menuIconsOnly"), checked: state.mode === "icons", onSelect: () => update({ mode: "icons" }) },
    { id: "icon-text", label: t("workspace.iconsAndText"), help: t("help.menuIconsAndText"), checked: state.mode === "icon-text", onSelect: () => update({ mode: "icon-text" }) },
  ];
  // Single Row, checked while chosen (user, 2026-11-01); an upright toolbar has columns instead. It stays
  // checked while the toolbar shows two rows because one does not fit.
  if (env === "desktop" && !toolbarVertical(state)) {
    items.push("separator", { id: "single-row", label: t("workspace.singleRow"), help: t("help.menuSingleRow"), checked: state.singleRow, toggle: true, onSelect: () => update({ singleRow: !state.singleRow }) });
  }
  if (env === "desktop") {
    items.push("separator");
    const floating = state.dock === "floating";
    for (const [dock, key] of DOCKS) {
      if (dock === "floating" && floating) continue;
      items.push({ id: `dock-${dock}`, label: t(key), help: t(DOCK_HELP[dock]), checked: state.dock === dock, onSelect: () => onChange(withToolbarDock(state, dock)) });
    }
    if (floating) {
      const vertical = state.vertical;
      items.push({ id: vertical ? "horizontal" : "vertical", label: t(vertical ? "workspace.horizontal" : "workspace.vertical"), help: t(vertical ? "help.menuToolbarHorizontal" : "help.menuToolbarVertical"), onSelect: () => update({ vertical: !vertical }) });
    }
  }
  items.push("separator", state.collapsed
    ? { id: "expand", label: t("workspace.expandToolbar"), help: t("help.menuExpandToolbar"), onSelect: () => update({ collapsed: false }) }
    : { id: "collapse", label: t("workspace.collapseToolbar"), help: t("help.menuCollapseToolbar"), onSelect: () => update({ collapsed: true }) });
  return items;
}

function ToolRow({ registry, workspace, order, groups }: { registry: ToolRegistry; workspace: string; order: readonly string[]; groups: ToolGroup[] }) {
  const run = (group: ToolGroup, tool: Tool) => (group.id === SHARED_GROUP
    ? registry.runShared(workspace, order, tool.id)
    : registry.latest(workspace, group.id, tool.id)?.onSelect?.());
  return (
    <div className="phx-toolbar-tools" role="group" aria-label={t("workspace.tools")}>
      {groups.map((group) => (
        <div key={group.id} className="phx-tool-group" role="group" aria-label={group.label}>
          {group.tools.map((tool) => tool.menu ? (
            <MenuButton key={tool.id} label={tool.label} help={tool.help} icon={<Icon name={tool.icon} size={16} />} items={() => registry.latest(workspace, group.id, tool.id)?.menu?.() ?? []}
              className={`phx-toolbar-button${tool.tone === "danger" ? " is-danger" : ""}`} disabled={tool.disabled}>
              <span className="phx-toolbar-label">{tool.short ?? tool.label}</span>
            </MenuButton>
          ) : (
            <button key={tool.id} type="button" className={`phx-toolbar-button${tool.tone === "danger" ? " is-danger" : ""}`} data-tool={tool.id}
              aria-label={tool.label} title={tool.label} data-help={tool.help} disabled={tool.disabled} aria-pressed={tool.pressed}
              onClick={() => run(group, tool)}>
              <Icon name={tool.icon} size={16} />
              <span className="phx-toolbar-label">{tool.short ?? tool.label}</span>
              {tool.badge ? <span className="phx-toolbar-badge" aria-hidden="true">{localeNumber(tool.badge)}</span> : null}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

export function Toolbar({ controller, shellRoot, env, state, onChange, documentation, onAgentChat, options }: {
  /** The visible workspace's controller; absent in the phone stack. */
  controller: WorkspaceController | null;
  /** Workspace Options for the phone stack, which has no controller. */
  options?: () => MenuEntry[];
  shellRoot: RefObject<HTMLElement | null>;
  env: Env;
  state: ToolbarState;
  onChange: (next: ToolbarState) => void;
  /** The Help menu: Quick Help and the Guide. */
  documentation: () => MenuEntry[];
  /** Agent Chat: its docked window in My Workspace, or the floating one. */
  onAgentChat?: () => void;
}) {
  const d = usePanelData();
  const menuRef = useRef<HTMLElement | null>(null);
  const self = useRef<HTMLDivElement | null>(null);
  const showMenu = useContextMenu();
  const registry = useContext(ToolRegistryContext);
  const actions = useContext(WorkspaceActionsContext);
  useSyncExternalStore(registry?.subscribe ?? (() => () => {}), registry?.getVersion ?? (() => 0));
  useSyncExternalStore(controller?.subscribe ?? (() => () => {}), controller?.getVersion ?? (() => 0));

  useEffect(() => {
    if (menuRef.current) {
      (menuRef.current as unknown as Record<string, unknown>).hass = d.hass;
      (menuRef.current as unknown as Record<string, unknown>).narrow = d.narrow;
    }
  }, [d.hass, d.narrow]);

  // Phones: docked at the top, icons only, never collapsed, and no toolbar menu (user, 2026-11-02).
  const effective: ToolbarState = env === "mobile" ? { ...state, dock: "top", mode: "icons", collapsed: false } : state;
  const update = (patch: Partial<ToolbarState>) => onChange({ ...state, ...patch });

  // Every open window's tools, grouped per window in reading order; tools several windows share show once.
  const order = controller ? controller.windowOrder() : WORKSPACES[d.workspace].windows;
  const groups: ToolGroup[] = registry ? registry.groupsFor(d.workspace, order, t(WORKSPACES[d.workspace].labelKey)) : [];

  // The kill-switch warning opens Settings at the kill switch, in the Safety window.
  const settings = useSettings();
  const openKillSwitch = () => settings?.reveal("killSwitch");

  // Drag the grip to float the toolbar; a docked toolbar floats where it is dropped.
  const startMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (env !== "desktop" || e.button !== 0) return;
    const shell = shellRoot.current?.getBoundingClientRect();
    const bar = self.current?.getBoundingClientRect();
    if (!shell || !bar) return;
    const x0 = e.clientX, y0 = e.clientY;
    let moved = false;
    let next = { x: bar.left - shell.left, y: bar.top - shell.top };
    const move = (ev: PointerEvent) => {
      if (!moved && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 4) return;
      moved = true;
      // A docked toolbar spans the panel; once floating it is only as wide as its buttons, so keep it inside by its live size.
      const size = self.current?.getBoundingClientRect() ?? bar;
      next = within(shell, size, bar.left - shell.left + ev.clientX - x0, bar.top - shell.top + ev.clientY - y0);
      onChange({ ...withToolbarDock(state, "floating"), ...next });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (!moved) return;
      // A drag ends with a click on the grip; it moved the toolbar, so it must not also open the menu.
      const swallow = (ev: MouseEvent) => { ev.stopPropagation(); ev.preventDefault(); };
      window.addEventListener("click", swallow, true);
      window.setTimeout(() => window.removeEventListener("click", swallow, true), 0);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  // Arrow keys move a floating toolbar from its grip.
  const moveByKeys = (e: KeyboardEvent<HTMLButtonElement>): boolean => {
    if (effective.dock !== "floating") return false;
    const dx = e.key === "ArrowLeft" ? -MOVE_STEP : e.key === "ArrowRight" ? MOVE_STEP : 0;
    const dy = e.key === "ArrowUp" ? -MOVE_STEP : e.key === "ArrowDown" ? MOVE_STEP : 0;
    if (!dx && !dy) return false;
    e.preventDefault();
    const shell = shellRoot.current?.getBoundingClientRect();
    const bar = self.current?.getBoundingClientRect();
    update(shell && bar ? within(shell, bar, state.x + dx, state.y + dy) : { x: Math.max(0, state.x + dx), y: Math.max(0, state.y + dy) });
    return true;
  };

  // A floating toolbar stays inside the panel when the panel or the toolbar changes size, as when it
  // turns, expands or shows its text: it moves left or up as far as it needs to be drawn in full.
  useLayoutEffect(() => {
    // On the first render the shell's ref is attached only after this effect runs; the toolbar's own
    // parent is the same element.
    const shellEl = shellRoot.current ?? self.current?.parentElement ?? null;
    if (effective.dock !== "floating" || !shellEl) return;
    const fit = () => {
      const shell = shellEl.getBoundingClientRect();
      const bar = self.current?.getBoundingClientRect();
      if (!bar || !shell.width) return;
      const p = within(shell, bar, state.x, state.y);
      if (p.x !== state.x || p.y !== state.y) update(p);
    };
    fit();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(fit);
    observer.observe(shellEl);
    if (self.current) observer.observe(self.current);
    return () => observer.disconnect();
  }, [effective.dock, state.x, state.y, state.mode, state.collapsed, state.vertical]); // eslint-disable-line react-hooks/exhaustive-deps

  // A horizontal toolbar with icons and text keeps the main row on one line (toolbarFit.ts), and one set
  // to a single row takes the tools onto it while they fit. Both are set on the toolbar directly, only
  // when the panel's width or the rows' text changes.
  const vertical = env === "desktop" && toolbarVertical(effective);
  const fitOneLine = env === "desktop" && effective.mode === "icon-text" && !effective.collapsed && !vertical;
  // Phones always put the tools beside Agent Chat, Help and Workspace Options, so the toolbar is at
  // most two rows: the workspaces, then the rest (user, 2026-11-02).
  const oneRow = (env === "mobile" || (env === "desktop" && effective.singleRow)) && !effective.collapsed && !vertical;
  const fitKey = useRef("");
  const refit = useRef<(force?: boolean) => void>(() => {});
  useLayoutEffect(() => {
    refit.current = (force = false) => {
      const bar = self.current;
      const body = bar?.querySelector<HTMLElement>(".phx-toolbar-body");
      const main = bar?.querySelector<HTMLElement>(".phx-toolbar-main");
      if (!bar || !body || !main) return;
      const tools = bar.querySelector<HTMLElement>(".phx-toolbar-tools");
      const key = `${shellRoot.current?.clientWidth ?? 0}|${effective.dock}|${vertical}|${effective.mode}|${oneRow}|${d.workspace}|${main.textContent}|${tools?.textContent ?? ""}`;
      if (!force && key === fitKey.current) return;
      fitKey.current = key;
      if (oneRow && tools) {
        bar.dataset.rows = "1";
        if (fitOneLine) bar.dataset.fit = "0";
        if (env === "mobile" || singleRowFits(body)) return;
      }
      delete bar.dataset.rows;
      if (!fitOneLine) { delete bar.dataset.fit; return; }
      chooseFit((step) => { bar.dataset.fit = String(step); }, () => rowFits(main));
    };
    refit.current();
  });
  useEffect(() => {
    const shellEl = shellRoot.current;
    if (!shellEl || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => refit.current());
    observer.observe(shellEl);
    // Text measured before the panel font loads is narrower than it will be.
    void document.fonts?.ready.then(() => refit.current(true));
    return () => observer.disconnect();
  }, [shellRoot]);

  const onContextMenu = (e: React.MouseEvent) => {
    if (env !== "desktop" || (e.target as HTMLElement).closest("input, textarea, select")) return;
    showMenu?.(e, t("workspace.toolbarMenu"), appearanceMenu(state, onChange, env));
  };

  const collapsed = effective.collapsed;
  return (
    <div
      ref={self}
      className="phx-toolbar"
      data-dock={effective.dock}
      data-orient={vertical ? "vertical" : "horizontal"}
      data-mode={effective.mode}
      data-collapsed={collapsed || undefined}
      style={effective.dock === "floating" ? { left: effective.x, top: effective.y } : undefined}
      onContextMenu={onContextMenu}
    >
      {/* The grip spans both rows (both columns standing upright) and stays while collapsed, so a
          collapsed toolbar can still be moved and its menu opened. */}
      {env === "desktop" && (
        <MenuButton
          label={t("workspace.moveToolbar")}
          help="help.moveToolbar"
          icon={<Icon name={vertical ? "gripHorizontal" : "grip"} size={16} />}
          items={() => appearanceMenu(state, onChange, env)}
          className="phx-toolbar-handle"
          onButtonKeyDown={moveByKeys}
          onPointerDown={startMove}
        />
      )}
      {(!collapsed || d.narrow || env !== "desktop") && (
        <div className="phx-toolbar-body">
          <div className="phx-toolbar-main">
            {d.narrow && <ha-menu-button ref={menuRef as RefObject<HTMLElement>} />}
            {!collapsed && (
              <nav className="phx-toolbar-nav" aria-label={t("shell.sectionsAria")}>
                {WORKSPACE_IDS.map((id) => {
                  const def = WORKSPACES[id];
                  const active = d.workspace === id;
                  const count = id === "approvals" ? d.pendingCount : 0;
                  const label = t(def.labelKey);
                  return (
                    <button key={id} type="button"
                      className={`phx-toolbar-button ${fitClass(4)}${active ? " is-current" : ""}`}
                      data-toolbar-workspace={id}
                      aria-current={active ? "page" : undefined}
                      aria-label={count > 0 ? t("shell.approvalsTabAria", { count }) : undefined}
                      title={label}
                      data-help={def.helpKey}
                      onClick={() => d.navigate(id)}>
                      <Icon name={def.icon} size={16} />
                      <span className="phx-toolbar-label">{label}</span>
                      {count > 0 && <span className="phx-toolbar-badge" aria-hidden="true">{localeNumber(count)}</span>}
                    </button>
                  );
                })}
              </nav>
            )}
            {!collapsed && (
              <div className="phx-toolbar-group">
                {d.settings?.kill_switch ? (
                  <button type="button" className="phx-toolbar-button phx-toolbar-warning" title={t("shell.killSwitchTitle")} data-help="shell.killSwitchHelp" onClick={openKillSwitch}>
                    <Icon name="killSwitch" size={16} />
                    <span className="phx-toolbar-label">{t(env === "mobile" ? "shell.killSwitchShort" : "shell.killSwitchFull")}</span>
                  </button>
                ) : d.accessKeys.length > 0 && d.agentCliInstances.length > 0 ? (
                  // Only with an AI provider to talk to; a heavier border makes it easy to find.
                  <button type="button" className={`phx-toolbar-button phx-toolbar-chat ${fitClass(3)}`} title={t("shell.agentChat")} data-help="help.agentChat" onClick={() => (onAgentChat ? onAgentChat() : d.openAgentCli())}>
                    <Icon name="agentChat" size={16} />
                    <span className="phx-toolbar-label">{t("shell.agentChat")}</span>
                  </button>
                ) : null}
                <MenuButton label={t("shell.help")} help="help.helpMenu" icon={<Icon name="documentation" size={16} />}
                  items={documentation} className={`phx-toolbar-button ${fitClass(2)}`}>
                  <span className="phx-toolbar-label">{t("shell.help")}</span>
                </MenuButton>
                {(controller || options) && (
                  <MenuButton label={t("workspace.options")} help="help.workspaceOptions" icon={<Icon name="workspaceOptions" size={16} />}
                    items={() => (controller ? workspaceMenu(controller, actions) : options!())} className={`phx-toolbar-button ${fitClass(1)}`}>
                    <span className="phx-toolbar-label">{t("workspace.options")}</span>
                  </MenuButton>
                )}
              </div>
            )}
            {/* The toolbar's options, the same menu as the grip and right-click. Collapsed, the grip holds it.
                Phones have nothing to choose, so no menu. */}
            {env === "desktop" && !collapsed && (
              <MenuButton label={t("workspace.toolbarOptions")} help="help.toolbarOptions" icon={<Icon name="more" size={16} />}
                items={() => appearanceMenu(state, onChange, env)} className="phx-toolbar-button phx-toolbar-plain phx-toolbar-options" />
            )}
          </div>
          {/* The open windows' tools always show (user, 2026-11-01): hiding them only made them harder to find. */}
          {!collapsed && registry && groups.length > 0 && (
            <ToolRow registry={registry} workspace={d.workspace} order={order} groups={groups} />
          )}
        </div>
      )}
    </div>
  );
}
