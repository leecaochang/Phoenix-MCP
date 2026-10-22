/**
 * The main toolbar: workspaces, Agent Chat or the kill-switch warning,
 * documentation and workspace options, plus a second row of tools for the
 * visible workspace. It docks to any edge or floats by its grip, collapses to
 * one icon, and shows icons only or icons with text (chosen by right-click).
 */

import { createContext, useContext, useEffect, useLayoutEffect, useRef, useSyncExternalStore, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import { DOCS_BASE_URL } from "../components/common";
import { localeNumber, t } from "../i18n";
import { Icon } from "../ui/Icon";
import { MenuButton, useContextMenu, type MenuEntry } from "../ui/Menu";
import type { WorkspaceController } from "./dock/controller";
import { usePanelData } from "./PanelData";
import { WORKSPACE_IDS, type Env, type ToolbarDock, type ToolbarState } from "./storage";
import { ToolRegistryContext, type ToolGroup, type ToolRegistry } from "./tools";
import { WORKSPACES, windowTitle } from "./workspaces";

/** The shell's arrangement actions, shared by every Workspace Options menu. */
export interface WorkspaceActions {
  saveMyDefault: (controller: WorkspaceController) => void;
  restoreMyDefault: (controller: WorkspaceController) => void;
  hasMyDefault: (controller: WorkspaceController) => boolean;
  restoreOriginal: (controller: WorkspaceController) => void;
  /** Asks first; resets the toolbar and every arrangement, never My Defaults or settings. */
  restorePanelDefaults: () => void;
}
export const WorkspaceActionsContext = createContext<WorkspaceActions | null>(null);

/**
 * Workspace options: reopen closed windows, save or restore My Default,
 * restore the original arrangement, and restore the panel's default settings.
 */
export function workspaceMenu(controller: WorkspaceController, actions: WorkspaceActions | null): MenuEntry[] {
  const items: MenuEntry[] = controller.closedWindows().map((id) => ({
    id: `reopen-${id}`,
    label: t("workspace.reopen", { window: windowTitle(id) }),
    onSelect: () => controller.reopen(id),
  }));
  if (items.length) items.push("separator");
  if (actions) {
    items.push(
      { id: "save-default", label: t("workspace.saveMyDefault"), onSelect: () => actions.saveMyDefault(controller) },
      { id: "restore-default", label: t("workspace.restoreMyDefault"), disabled: !actions.hasMyDefault(controller), onSelect: () => actions.restoreMyDefault(controller) },
    );
  }
  items.push({ id: "restore", label: t("workspace.restoreOriginal"), onSelect: () => (actions ? actions.restoreOriginal(controller) : controller.restoreOriginal()) });
  if (actions) items.push("separator", { id: "panel-defaults", label: t("workspace.restorePanelDefaults"), onSelect: actions.restorePanelDefaults });
  return items;
}

const DOCKS: Array<[ToolbarDock, string]> = [
  ["top", "workspace.dockTop"], ["bottom", "workspace.dockBottom"], ["left", "workspace.dockLeft"], ["right", "workspace.dockRight"], ["floating", "workspace.floatToolbar"],
];
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

/** The toolbar's own menu: display mode, then where it sits. Phones keep it docked at the top. */
function appearanceMenu(state: ToolbarState, update: (patch: Partial<ToolbarState>) => void, env: Env): MenuEntry[] {
  const items: MenuEntry[] = [
    { id: "icons", label: t("workspace.iconsOnly"), checked: state.mode === "icons", onSelect: () => update({ mode: "icons" }) },
    { id: "icon-text", label: t("workspace.iconsAndText"), checked: state.mode === "icon-text", onSelect: () => update({ mode: "icon-text" }) },
  ];
  if (env === "desktop") {
    items.push("separator");
    for (const [dock, key] of DOCKS) items.push({ id: `dock-${dock}`, label: t(key), checked: state.dock === dock, onSelect: () => update({ dock }) });
  }
  return items;
}

function ToolRow({ registry, workspace, groups }: { registry: ToolRegistry; workspace: string; groups: ToolGroup[] }) {
  return (
    <div className="phx-toolbar-tools" role="group" aria-label={t("workspace.tools")}>
      {groups.map((group) => (
        <div key={group.id} className="phx-tool-group" role="group" aria-label={group.label}>
          {group.tools.map((tool) => tool.menu ? (
            <MenuButton key={tool.id} label={tool.label} icon={<Icon name={tool.icon} size={16} />} items={() => registry.latest(workspace, group.id, tool.id)?.menu?.() ?? []}
              className={`phx-toolbar-button${tool.tone === "danger" ? " is-danger" : ""}`}>
              <span className="phx-toolbar-label">{tool.label}</span>
            </MenuButton>
          ) : (
            <button key={tool.id} type="button" className={`phx-toolbar-button${tool.tone === "danger" ? " is-danger" : ""}`}
              aria-label={tool.label} title={tool.label} disabled={tool.disabled} aria-pressed={tool.pressed}
              onClick={() => registry.latest(workspace, group.id, tool.id)?.onSelect?.()}>
              <Icon name={tool.icon} size={16} />
              <span className="phx-toolbar-label">{tool.label}</span>
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

export function Toolbar({ controller, shellRoot, env, state, onChange }: {
  /** The visible workspace's controller; absent in the phone stack. */
  controller: WorkspaceController | null;
  shellRoot: RefObject<HTMLElement | null>;
  env: Env;
  state: ToolbarState;
  onChange: (next: ToolbarState) => void;
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

  const effective: ToolbarState = env === "mobile" ? { ...state, dock: "top" } : state;
  const update = (patch: Partial<ToolbarState>) => onChange({ ...state, ...patch });

  // Every open window's tools, grouped per window in reading order.
  const groups: ToolGroup[] = registry
    ? registry.groupsFor(d.workspace, controller ? controller.windowOrder() : WORKSPACES[d.workspace].windows)
    : [];

  // The kill-switch warning opens Settings at the kill switch.
  const openKillSwitch = () => {
    d.navigate("settings");
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const card = shellRoot.current?.querySelector<HTMLElement>("#phx-kill-switch-card");
      card?.scrollIntoView?.({ block: "start" });
      card?.querySelector<HTMLElement>("input, button")?.focus();
    }));
  };

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
      next = within(shell, bar, bar.left - shell.left + ev.clientX - x0, bar.top - shell.top + ev.clientY - y0);
      onChange({ ...state, dock: "floating", ...next });
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

  // A floating toolbar stays inside the panel when the panel or the toolbar changes size.
  useLayoutEffect(() => {
    const shellEl = shellRoot.current;
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
  }, [effective.dock, state.x, state.y, state.mode, state.collapsed]); // eslint-disable-line react-hooks/exhaustive-deps

  const onContextMenu = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest("input, textarea, select")) return;
    showMenu?.(e, t("workspace.toolbarMenu"), appearanceMenu(state, update, env));
  };

  const collapsed = effective.collapsed;
  const vertical = effective.dock === "left" || effective.dock === "right";
  return (
    <div
      ref={self}
      className="phx-toolbar"
      data-dock={effective.dock}
      data-mode={effective.mode}
      data-collapsed={collapsed || undefined}
      style={effective.dock === "floating" ? { left: effective.x, top: effective.y } : undefined}
      onContextMenu={onContextMenu}
    >
      <div className="phx-toolbar-main">
        {d.narrow && <ha-menu-button ref={menuRef as RefObject<HTMLElement>} />}
        {!collapsed && env === "desktop" && (
          <MenuButton
            label={t("workspace.moveToolbar")}
            icon={<Icon name="grip" size={16} />}
            items={() => appearanceMenu(state, update, env)}
            className="phx-toolbar-handle"
            onButtonKeyDown={moveByKeys}
            onPointerDown={startMove}
          />
        )}
        {!collapsed && (
          <nav className="phx-toolbar-nav" aria-label={t("shell.sectionsAria")}>
            {WORKSPACE_IDS.map((id) => {
              const def = WORKSPACES[id];
              const active = d.workspace === id;
              const count = id === "approvals" ? d.pendingCount : 0;
              const label = t(def.labelKey);
              return (
                <button key={id} type="button"
                  className={`phx-toolbar-button${active ? " is-current" : ""}`}
                  aria-current={active ? "page" : undefined}
                  aria-label={count > 0 ? t("shell.approvalsTabAria", { count }) : undefined}
                  title={label}
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
              <button type="button" className="phx-toolbar-button phx-toolbar-warning" title={t("shell.killSwitchTitle")} onClick={openKillSwitch}>
                <Icon name="killSwitch" size={16} />
                <span className="phx-toolbar-label">{t(env === "mobile" ? "shell.killSwitchShort" : "shell.killSwitchFull")}</span>
              </button>
            ) : d.accessKeys.length > 0 ? (
              <button type="button" className="phx-toolbar-button" title={t("shell.agentChat")} onClick={() => d.openAgentCli()}>
                <Icon name="agentChat" size={16} />
                <span className="phx-toolbar-label">{t("shell.agentChat")}</span>
              </button>
            ) : null}
            <a className="phx-toolbar-button" href={DOCS_BASE_URL} target="_blank" rel="noopener noreferrer" title={t("shell.documentation")}>
              <Icon name="documentation" size={16} />
              <span className="phx-toolbar-label">{t("shell.documentation")}</span>
              <Icon name="externalLink" size={11} className="phx-toolbar-external" />
            </a>
            {controller && (
              <MenuButton label={t("workspace.options")} icon={<Icon name="workspaceOptions" size={16} />}
                items={() => workspaceMenu(controller, actions)} className="phx-toolbar-button" />
            )}
          </div>
        )}
        {!collapsed && groups.length > 0 && (
          <button type="button" className="phx-toolbar-button phx-toolbar-plain"
            aria-label={effective.toolsShown ? t("workspace.hideTools") : t("workspace.showTools")}
            title={effective.toolsShown ? t("workspace.hideTools") : t("workspace.showTools")}
            aria-expanded={effective.toolsShown}
            onClick={() => update({ toolsShown: !state.toolsShown })}>
            <Icon name={effective.toolsShown ? "hideTools" : "showTools"} size={16} />
          </button>
        )}
        <button type="button" className="phx-toolbar-button phx-toolbar-plain"
          aria-label={collapsed ? t("workspace.expandToolbar") : t("workspace.collapseToolbar")}
          title={collapsed ? t("workspace.expandToolbar") : t("workspace.collapseToolbar")}
          aria-expanded={!collapsed}
          onClick={() => update({ collapsed: !state.collapsed })}>
          <Icon name={collapsed ? (vertical ? "showTools" : "expandToolbar") : (vertical ? "hideTools" : "collapseToolbar")} size={16} />
        </button>
      </div>
      {!collapsed && effective.toolsShown && registry && groups.length > 0 && (
        <ToolRow registry={registry} workspace={d.workspace} groups={groups} />
      )}
    </div>
  );
}
