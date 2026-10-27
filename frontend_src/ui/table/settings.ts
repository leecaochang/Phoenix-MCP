/**
 * What a user changed about a configurable table: column order, widths,
 * hidden columns and sort. Settings are kept per table id. Inside a
 * workspace they belong to its arrangement (saved, synced, part of My
 * Default, cleared by Reset This Workspace); elsewhere a table keeps them
 * for as long as it is shown.
 */

import { createContext, useCallback, useContext, useState, useSyncExternalStore } from "react";

export type SortDirection = "asc" | "desc";
export interface SortState { column: string; dir: SortDirection }

export interface TableSettings {
  /** Every known column id in the user's order, hidden ones included so they keep their place. */
  order?: string[];
  widths?: Record<string, number>;
  hidden?: string[];
  sort?: SortState;
}

export const MIN_COLUMN_WIDTH = 60;
const MAX_COLUMN_WIDTH = 2000;
const MAX_COLUMNS = 50;

const isId = (v: unknown): v is string => typeof v === "string" && v.length > 0 && v.length <= 64;

/** Saved settings for one table, with anything unreadable left out. */
export function readTableSettings(raw: unknown): TableSettings {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const out: TableSettings = {};
  if (Array.isArray(r.order)) out.order = [...new Set(r.order.filter(isId))].slice(0, MAX_COLUMNS);
  if (Array.isArray(r.hidden)) out.hidden = [...new Set(r.hidden.filter(isId))].slice(0, MAX_COLUMNS);
  if (r.widths && typeof r.widths === "object") {
    const widths: Record<string, number> = {};
    for (const [id, w] of Object.entries(r.widths as Record<string, unknown>).slice(0, MAX_COLUMNS)) {
      if (isId(id) && typeof w === "number" && Number.isFinite(w)) widths[id] = Math.round(Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, w)));
    }
    out.widths = widths;
  }
  const s = r.sort as Record<string, unknown> | undefined;
  if (s && isId(s.column) && (s.dir === "asc" || s.dir === "desc")) out.sort = { column: s.column, dir: s.dir };
  return out;
}

/** Settings for every table in one workspace. */
export class TableSettingsStore {
  private tables = new Map<string, TableSettings>();
  private version = 0;
  private listeners = new Set<() => void>();

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getVersion = (): number => this.version;
  private changed(): void {
    this.version++;
    for (const listener of this.listeners) listener();
  }

  get(id: string): TableSettings { return this.tables.get(id) ?? {}; }

  /** Replace one table's settings; empty settings forget the table. */
  set(id: string, settings: TableSettings): void {
    if (Object.keys(settings).length) this.tables.set(id, settings);
    else this.tables.delete(id);
    this.changed();
  }

  /** Everything to save, or undefined when no table was changed. */
  snapshot(): Record<string, TableSettings> | undefined {
    return this.tables.size ? Object.fromEntries(this.tables) : undefined;
  }

  /** Take saved settings, or none to return every table to its defaults. */
  restore(raw: unknown): void {
    this.tables.clear();
    if (raw && typeof raw === "object") {
      for (const [id, value] of Object.entries(raw as Record<string, unknown>).slice(0, MAX_COLUMNS)) {
        if (!isId(id)) continue;
        const settings = readTableSettings(value);
        if (Object.keys(settings).length) this.tables.set(id, settings);
      }
    }
    this.changed();
  }
}

/** The workspace's table settings, provided by the window a table is drawn in. */
export const TableSettingsContext = createContext<TableSettingsStore | null>(null);

/** One table's settings and a setter: the workspace's when there is one, otherwise the table's own. */
export function useTableSettings(id: string): [TableSettings, (next: TableSettings) => void] {
  const store = useContext(TableSettingsContext);
  useSyncExternalStore(store?.subscribe ?? (() => () => {}), store?.getVersion ?? (() => 0));
  const [local, setLocal] = useState<TableSettings>({});
  const set = useCallback((next: TableSettings) => (store ? store.set(id, next) : setLocal(next)), [store, id]);
  return [store ? store.get(id) : local, set];
}
