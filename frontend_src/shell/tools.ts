/**
 * Toolbar tools contributed by windows. A view registers its tools while it is
 * drawn inside the shell; the toolbar's second row shows every open window's
 * tools for the visible workspace, one group per window in reading order, so
 * a tool never hides until its window is focused.
 */

import { createContext, useContext, useEffect, useRef } from "react";
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
}

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
    const signature = groups ? JSON.stringify(groups.map((g) => [g.id, g.label, g.tools.map((t) => [t.id, t.label, t.icon, !!t.disabled, !!t.pressed, t.tone])])) : "";
    if (groups) this.entries.set(key, { place, groups }); else this.entries.delete(key);
    if (this.signatures.get(key) === signature) return;
    this.signatures.set(key, signature);
    this.version++;
    for (const listener of this.listeners) listener();
  }

  /** Tool groups for a workspace's open windows, in their reading order. */
  groupsFor(workspace: string, windowOrder: readonly string[]): ToolGroup[] {
    return windowOrder.flatMap((id) => this.entries.get(`${workspace}:${id}`)?.groups ?? []).filter((g) => g.tools.length > 0);
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
