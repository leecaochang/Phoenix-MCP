/**
 * Check a saved workspace layout before Dockview loads it.
 *
 * Saved layouts outlive builds: a window may be retired, a save may be cut
 * short, or another client may have written something unexpected. Anything
 * not understood is dropped rather than repaired, and a layout with no usable
 * window is refused so the caller falls back to the default arrangement.
 */

import type { SerializedDockview } from "dockview-react";

type Json = Record<string, unknown>;
const isObject = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);
const isSize = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;

/** Returns a cleaned copy, or null when nothing usable remains. `known` lists the current window ids. */
export function sanitizeLayout(raw: unknown, known: ReadonlySet<string>): SerializedDockview | null {
  try {
    if (!isObject(raw) || !isObject(raw.grid) || !isObject(raw.grid.root) || !isObject(raw.panels)) return null;
    const source = JSON.parse(JSON.stringify(raw)) as Json & { grid: Json & { root: Json }; panels: Json };

    // A panel is kept when it names a current window and is that window.
    const panels: Record<string, Json> = {};
    for (const [id, panel] of Object.entries(source.panels)) {
      if (!isObject(panel) || panel.id !== id || typeof panel.contentComponent !== "string") continue;
      if (!known.has(id) || panel.contentComponent !== id) continue;
      panels[id] = panel;
    }

    const placed = new Set<string>();
    const groupIds = new Set<string>();
    // One window per slot: a leaf keeps its first window not already placed elsewhere.
    const leafView = (data: unknown): Json | null => {
      if (!isObject(data) || !Array.isArray(data.views) || typeof data.id !== "string" || groupIds.has(data.id)) return null;
      const view = data.views.find((v): v is string => typeof v === "string" && v in panels && !placed.has(v));
      if (!view) return null;
      placed.add(view);
      groupIds.add(data.id);
      return { ...data, views: [view], activeView: view };
    };
    const prune = (node: unknown, depth: number): Json | null => {
      if (!isObject(node) || depth > 32) return null;
      if (node.type === "leaf") {
        const data = leafView(node.data);
        return data ? { ...node, data, ...(isSize(node.size) ? {} : { size: 0 }) } : null;
      }
      if (node.type === "branch" && Array.isArray(node.data)) {
        const children = node.data.map((child) => prune(child, depth + 1)).filter((c): c is Json => c !== null);
        return children.length ? { ...node, data: children, ...(isSize(node.size) ? {} : { size: 0 }) } : null;
      }
      return null;
    };
    const root = prune(source.grid.root, 0);

    const floating = (Array.isArray(source.floatingGroups) ? source.floatingGroups : [])
      .map((entry): Json | null => {
        if (!isObject(entry) || !isObject(entry.position)) return null;
        const data = leafView(entry.data);
        return data ? { ...entry, data } : null;
      })
      .filter((entry): entry is Json => entry !== null);

    if (!root && !floating.length) return null;
    const keptPanels = Object.fromEntries(Object.entries(panels).filter(([id]) => placed.has(id)));
    const grid = {
      ...source.grid,
      // An empty grid with only floating windows is still a valid layout.
      root: root ?? { type: "branch", data: [], size: 0 },
    };
    const result: Json = { grid, panels: keptPanels };
    if (floating.length) result.floatingGroups = floating;
    if (typeof source.activeGroup === "string" && groupIds.has(source.activeGroup)) result.activeGroup = source.activeGroup;
    return result as unknown as SerializedDockview;
  } catch {
    return null;
  }
}
