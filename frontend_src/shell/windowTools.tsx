/**
 * Helpers for windows that offer tools: drawing a window's tools inside it
 * when it is not in the shell, toggling another window of the workspace open
 * or closed, and bringing a window into view.
 */

import { useCallback, useContext, useEffect, useRef, useSyncExternalStore } from "react";
import { ControllerContext } from "./dock/TitleBar";
import type { Tool, ToolGroup } from "./tools";
import { ActionButton, DisplayModeContext } from "../ui/ActionButton";
import { Icon } from "../ui/Icon";
import { MenuButton } from "../ui/Menu";

/** Tools a window offers on the toolbar, or inline when it is not in the shell. */
export function InlineTools({ groups }: { groups: ToolGroup[] }) {
  const iconsOnly = useContext(DisplayModeContext) === "icons";
  const tools = groups.flatMap((g) => g.tools);
  if (!tools.length) return null;
  return (
    <div className="phx-window-actions">
      {tools.map((tool: Tool) => tool.menu ? (
        <MenuButton key={tool.id} label={tool.label} icon={<Icon name={tool.icon} size={16} />} items={tool.menu}
          className={`btn btn-outline btn-sm phx-action${iconsOnly ? " is-icon-only" : ""}`}>
          {!iconsOnly && <span>{tool.short ?? tool.label}</span>}
        </MenuButton>
      ) : (
        <ActionButton key={tool.id} icon={tool.icon} label={tool.label} disabled={tool.disabled} pressed={tool.pressed}
          onClick={() => tool.onSelect?.()} className="btn btn-outline btn-sm" />
      ))}
    </div>
  );
}

const noSubscribe = () => () => {};
const noVersion = () => 0;

/**
 * A toolbar toggle that opens a closed window or closes an open one, for the
 * window it belongs to. A window the workspace floats (MESA's findings) opens
 * over the workspace rather than in the grid.
 */
export function useWindowToggle(windowId: string, floating = false): { open: boolean; toggle: () => void; autoOpen: () => void } | null {
  const controller = useContext(ControllerContext);
  useSyncExternalStore(controller?.subscribe ?? noSubscribe, controller?.getVersion ?? noVersion);
  if (!controller) return null;
  return {
    open: !!controller.hostOf(windowId),
    toggle: () => {
      if (floating) { controller.openFloating(windowId); return; }
      const group = controller.groupOf(windowId);
      if (group) controller.close(group);
      else controller.ensureOpen(windowId);
    },
    // Opens it when there is something to show, unless the user closed it.
    autoOpen: () => controller.autoOpen(windowId),
  };
}

/** Forget what this window had chosen when the workspace is reset (user, 2026-10-30). */
export function useWorkspaceReset(forget: () => void): void {
  const controller = useContext(ControllerContext);
  const latest = useRef(forget);
  latest.current = forget;
  useEffect(() => controller?.onReset(() => latest.current()), [controller]);
}

/** Open (or unfold) a window of this workspace; does nothing outside the shell. */
export function useEnsureOpen(): (windowId: string) => void {
  const controller = useContext(ControllerContext);
  return useCallback((windowId: string) => { controller?.ensureOpen(windowId); }, [controller]);
}
