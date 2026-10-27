/**
 * Toolbar tools contributed by windows. A view registers its tools while it is
 * drawn inside the shell; the toolbar's second row shows every open window's
 * tools for the visible workspace, one group per window in reading order, so
 * a tool never hides until its window is focused. Tools that several open
 * windows share, such as Refresh in both logs, show once and act on all of them.
 */

import { createContext, useContext, useEffect, useRef } from "react";
import { localeNumber, t } from "../i18n";
import type { IconName } from "../ui/Icon";
import type { MenuEntry } from "../ui/Menu";

export interface Tool {
  id: string;
  label: string;
  icon: IconName;
  onSelect?: () => void;
  /** A tool that opens a menu instead of acting directly. */
  menu?: () => MenuEntry[];
  disabled?: boolean;
  /** A toggle that is currently on. */
  pressed?: boolean;
  tone?: "danger";
  /** Shorter visible text for icons-and-text mode; `label` stays the accessible name and tooltip. */
  short?: string;
  /** A small count on the button, such as active filters. */
  badge?: number;
  /**
   * Tools with the same shared id in a workspace's open windows show as one
   * button that acts on every one of them. `countLabel` is a catalog key with
   * `{count}`, naming the button while the members' badges add up to more than 0.
   */
  shared?: { id: string; label: string; countLabel?: string };
  /** For a shared toggle: turn it on or off in this window. */
  onSet?: (on: boolean) => void;
}

/** The group that holds tools shared by several windows. */
export const SHARED_GROUP = "shared";

export interface ToolGroup {
  id: string;
  /** Accessible name of the group, naming the window or selection it acts on. */
  label: string;
  tools: Tool[];
}

/** Which window a view is drawn in, so its tools are filed under it. */
export interface WindowPlace {
  workspace: string;
  window: string;
}
export const WindowPlaceContext = createContext<WindowPlace | null>(null);

type Entry = { place: WindowPlace; groups: ToolGroup[] };

export class ToolRegistry {
  private entries = new Map<string, Entry>();
  private listeners = new Set<() => void>();
  private version = 0;
  private signatures = new Map<string, string>();

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getVersion = (): number => this.version;

  set(place: WindowPlace, groups: ToolGroup[] | null): void {
    const key = `${place.workspace}:${place.window}`;
    // Closures change on every render; only labels and states decide whether the toolbar redraws.
    const signature = groups ? JSON.stringify(groups.map((g) => [g.id, g.label, g.tools.map((t) => [t.id, t.label, t.icon, !!t.disabled, !!t.pressed, t.tone, t.short, t.badge, t.shared?.id, t.shared?.label])])) : "";
    if (groups) this.entries.set(key, { place, groups }); else this.entries.delete(key);
    if (this.signatures.get(key) === signature) return;
    this.signatures.set(key, signature);
    this.version++;
    for (const listener of this.listeners) listener();
  }

  /**
   * Tool groups for a workspace's open windows, in their reading order. A tool
   * shared by two or more of them moves into one leading group, named
   * `sharedLabel`, as a single button.
   */
  groupsFor(workspace: string, windowOrder: readonly string[], sharedLabel = ""): ToolGroup[] {
    const groups = windowOrder.flatMap((id) => this.entries.get(`${workspace}:${id}`)?.groups ?? []);
    const members = new Map<string, Tool[]>();
    for (const tool of groups.flatMap((g) => g.tools)) {
      if (tool.shared) members.set(tool.shared.id, [...(members.get(tool.shared.id) ?? []), tool]);
    }
    const merged: Tool[] = [];
    for (const [id, tools] of members) {
      if (tools.length < 2) continue;
      const shared = tools[0].shared!;
      const badge = tools.reduce((sum, tool) => sum + (tool.badge ?? 0), 0);
      merged.push({
        id,
        label: badge && shared.countLabel ? t(shared.countLabel, { count: localeNumber(badge) }) : shared.label,
        short: shared.label,
        icon: tools[0].icon,
        disabled: tools.every((tool) => tool.disabled),
        pressed: tools.some((tool) => tool.pressed !== undefined) ? tools.some((tool) => tool.pressed) : undefined,
        badge: badge || undefined,
        shared,
      });
    }
    const sharedIds = new Set(merged.map((tool) => tool.id));
    const own = groups
      .map((g) => ({ ...g, tools: g.tools.filter((tool) => !(tool.shared && sharedIds.has(tool.shared.id))) }))
      .filter((g) => g.tools.length > 0);
    return merged.length ? [{ id: SHARED_GROUP, label: sharedLabel, tools: merged }, ...own] : own;
  }

  /** Run a shared tool in every open window offering it; a shared toggle turns on everywhere unless it is on somewhere. */
  runShared(workspace: string, windowOrder: readonly string[], sharedId: string): void {
    const tools = windowOrder
      .flatMap((id) => this.entries.get(`${workspace}:${id}`)?.groups ?? [])
      .flatMap((g) => g.tools)
      .filter((tool) => tool.shared?.id === sharedId && !tool.disabled);
    const on = !tools.some((tool) => tool.pressed);
    for (const tool of tools) {
      if (tool.onSet) tool.onSet(on);
      else tool.onSelect?.();
    }
  }

  /** The newest version of a tool, so a click always runs the view's current handler. */
  latest(workspace: string, groupId: string, toolId: string): Tool | undefined {
    for (const entry of this.entries.values()) {
      if (entry.place.workspace !== workspace) continue;
      const tool = entry.groups.find((g) => g.id === groupId)?.tools.find((t) => t.id === toolId);
      if (tool) return tool;
    }
    return undefined;
  }

}

export const ToolRegistryContext = createContext<ToolRegistry | null>(null);

/**
 * Offer tools on the toolbar while this view is drawn in a shell window.
 * Returns true when they are offered, so the view can leave out its own copy.
 */
/** Whether this window's tools are on the workspace toolbar, as useToolbarTools reports, without registering any. */
export function useOnToolbar(): boolean {
  const registry = useContext(ToolRegistryContext);
  const place = useContext(WindowPlaceContext);
  return !!registry && !!place;
}

export function useToolbarTools(groups: ToolGroup[]): boolean {
  const registry = useContext(ToolRegistryContext);
  const place = useContext(WindowPlaceContext);
  const latest = useRef(groups);
  latest.current = groups;
  useEffect(() => {
    if (registry && place) registry.set(place, latest.current);
  });
  useEffect(() => {
    if (!registry || !place) return;
    return () => registry.set(place, null);
  }, [registry, place?.workspace, place?.window]); // eslint-disable-line react-hooks/exhaustive-deps
  return !!registry && !!place;
}
