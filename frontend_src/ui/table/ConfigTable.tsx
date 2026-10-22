/**
 * The configurable table shared by the panel's lists: a native table whose
 * columns the user can reorder (drag a header, its menu, or Alt+Shift+Left or
 * Right on the header), resize (drag the edge, or arrow keys on it), hide,
 * show again and sort. Changes are kept per table id (`settings.ts`); hidden
 * columns keep their place for when they are shown again. Rows are chosen by
 * pointer or keyboard, and the chosen row stays marked across refreshes.
 */

import { useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { compareStrings, t } from "../../i18n";
import { Icon } from "../Icon";
import { MenuButton, useContextMenu, type MenuEntry } from "../Menu";
import { MIN_COLUMN_WIDTH, useTableSettings, type SortState, type TableSettings } from "./settings";

export interface Column<Row> {
  id: string;
  label: string;
  cell: (row: Row) => ReactNode;
  /** The value rows sort by; a column without one cannot be sorted. */
  sortValue?: (row: Row) => string | number;
  /** Starting width in pixels. */
  width?: number;
  /** Hidden until the user shows it. */
  hidden?: boolean;
  /** Full text for a cell that may be cut short. */
  title?: (row: Row) => string | undefined;
  className?: string;
  /** Extra entries for this column's menu, such as a filter. */
  menu?: () => MenuEntry[];
}

const DEFAULT_WIDTH = 140;
const RESIZE_STEP = 16;
const MAX_WIDTH = 2000;
const DRAG_THRESHOLD = 5;

/** Saved order with columns added since then placed after the column they follow by default. */
export function columnOrder(known: readonly string[], saved: readonly string[] | undefined): string[] {
  if (!saved) return [...known];
  const order = saved.filter((id) => known.includes(id));
  known.forEach((id, i) => {
    if (order.includes(id)) return;
    const previous = known.slice(0, i).reverse().find((p) => order.includes(p));
    order.splice(previous ? order.indexOf(previous) + 1 : 0, 0, id);
  });
  return order;
}

/** Settings without empty entries, so a reset leaves nothing behind. */
function clean(settings: TableSettings): TableSettings {
  const out: TableSettings = {};
  if (settings.order) out.order = settings.order;
  if (settings.widths && Object.keys(settings.widths).length) out.widths = settings.widths;
  if (settings.hidden) out.hidden = settings.hidden;
  if (settings.sort) out.sort = settings.sort;
  return out;
}

export function ConfigTable<Row>({ id, label, columns, rows, rowKey, defaultSort, selectedKey, onSelect, rowLabel, rowClass, empty }: {
  /** Settings are kept under this id. */
  id: string;
  /** Accessible name of the table. */
  label: string;
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  /** The order rows arrive in, shown as the sort until the user picks another. */
  defaultSort?: SortState;
  selectedKey?: string | null;
  /** Choosing a row; receives the rows in their shown order, for stepping through them. */
  onSelect?: (row: Row, ordered: Row[]) => void;
  rowLabel?: (row: Row) => string;
  rowClass?: (row: Row) => string;
  /** Shown under the headers when there are no rows. */
  empty?: ReactNode;
}) {
  const [settings, saveSettings] = useTableSettings(id);
  const showMenu = useContextMenu();
  const wrap = useRef<HTMLDivElement | null>(null);
  const head = useRef<HTMLTableRowElement | null>(null);
  const body = useRef<HTMLTableSectionElement | null>(null);
  const [liveWidth, setLiveWidth] = useState<{ id: string; width: number } | null>(null);
  const [drag, setDrag] = useState<{ id: string; marker: number } | null>(null);
  const justDragged = useRef(false);

  const byId = useMemo(() => new Map(columns.map((c) => [c.id, c])), [columns]);
  const order = columnOrder(columns.map((c) => c.id), settings.order);
  const hidden = settings.hidden ?? columns.filter((c) => c.hidden).map((c) => c.id);
  let visible = order.filter((cid) => !hidden.includes(cid)).map((cid) => byId.get(cid)!);
  if (!visible.length) visible = [byId.get(order[0])!];
  const sort = settings.sort && byId.get(settings.sort.column)?.sortValue ? settings.sort : defaultSort;
  const widthOf = (c: Column<Row>) => (liveWidth?.id === c.id ? liveWidth.width : settings.widths?.[c.id] ?? c.width ?? DEFAULT_WIDTH);
  const total = visible.reduce((sum, c) => sum + widthOf(c), 0);

  const sorted = useMemo(() => {
    const col = sort ? byId.get(sort.column) : undefined;
    if (!sort || !col?.sortValue) return rows;
    const dir = sort.dir === "asc" ? 1 : -1;
    return rows
      .map((row, index) => ({ row, index, value: col.sortValue!(row) }))
      .sort((a, b) => {
        const c = typeof a.value === "number" && typeof b.value === "number"
          ? a.value - b.value : compareStrings(String(a.value), String(b.value));
        return c ? c * dir : a.index - b.index;
      })
      .map((x) => x.row);
  }, [rows, sort, byId]);

  const update = (patch: Partial<TableSettings>) => saveSettings(clean({ ...settings, ...patch }));
  const focusHeader = (cid: string) => requestAnimationFrame(() => head.current?.querySelector<HTMLElement>(`th[data-column="${cid}"] .phx-th-label`)?.focus());

  /** Put a column at a position among the shown columns; hidden ones keep their places. */
  const placeAt = (cid: string, index: number) => {
    const shown = visible.map((c) => c.id).filter((x) => x !== cid);
    const rest = order.filter((x) => x !== cid);
    const before = shown[index];
    const at = before ? rest.indexOf(before) : rest.indexOf(shown[shown.length - 1]) + 1;
    rest.splice(at, 0, cid);
    update({ order: rest });
  };
  const move = (cid: string, offset: -1 | 1) => {
    const i = visible.findIndex((c) => c.id === cid);
    if (i + offset < 0 || i + offset >= visible.length) return;
    placeAt(cid, i + offset);
    focusHeader(cid);
  };
  const toggleSort = (col: Column<Row>) => {
    if (!col.sortValue) return;
    const dir = sort?.column === col.id && sort.dir === "asc" ? "desc" : "asc";
    update({ sort: { column: col.id, dir } });
  };

  const menuEntries = (col: Column<Row>): MenuEntry[] => {
    const i = visible.indexOf(col);
    const items: MenuEntry[] = [];
    if (col.sortValue) {
      items.push(
        { id: "asc", label: t("table.sortAscending"), checked: sort?.column === col.id && sort.dir === "asc", onSelect: () => update({ sort: { column: col.id, dir: "asc" } }) },
        { id: "desc", label: t("table.sortDescending"), checked: sort?.column === col.id && sort.dir === "desc", onSelect: () => update({ sort: { column: col.id, dir: "desc" } }) },
        "separator",
      );
    }
    items.push(
      { id: "left", label: t("workspace.moveLeft"), disabled: i <= 0, onSelect: () => move(col.id, -1) },
      { id: "right", label: t("workspace.moveRight"), disabled: i >= visible.length - 1, onSelect: () => move(col.id, 1) },
      { id: "hide", label: t("table.hideColumn"), disabled: visible.length <= 1, onSelect: () => update({ hidden: [...hidden, col.id] }) },
    );
    const extra = col.menu?.() ?? [];
    if (extra.length) items.push("separator", ...extra);
    const hiddenColumns = order.filter((x) => hidden.includes(x)).map((x) => byId.get(x)!);
    if (hiddenColumns.length) {
      items.push("separator", ...hiddenColumns.map((h): MenuEntry => ({
        id: `show-${h.id}`, label: t("table.showColumn", { column: h.label }), onSelect: () => update({ hidden: hidden.filter((x) => x !== h.id) }),
      })));
    }
    items.push(
      "separator",
      { id: "reset-order", label: t("table.resetOrder"), disabled: !settings.order, onSelect: () => update({ order: undefined }) },
      { id: "reset", label: t("table.resetColumns"), disabled: !Object.keys(settings).length, onSelect: () => saveSettings({}) },
    );
    return items;
  };

  // Drag a header to move its column; a line shows where it will go. A press without movement sorts.
  const startDrag = (col: Column<Row>) => (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return;
    const x0 = e.clientX;
    let dragging = false;
    let target = -1;
    const onMove = (ev: PointerEvent) => {
      if (!dragging && Math.abs(ev.clientX - x0) < DRAG_THRESHOLD) return;
      dragging = true;
      const headers = [...(head.current?.querySelectorAll<HTMLElement>("th[data-column]") ?? [])];
      const box = wrap.current?.getBoundingClientRect();
      if (!headers.length || !box || !wrap.current) return;
      target = headers.findIndex((th) => { const r = th.getBoundingClientRect(); return ev.clientX < r.left + r.width / 2; });
      if (target < 0) target = headers.length;
      const edge = target < headers.length ? headers[target].getBoundingClientRect().left : headers[headers.length - 1].getBoundingClientRect().right;
      setDrag({ id: col.id, marker: edge - box.left + wrap.current.scrollLeft });
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      setDrag(null);
      if (!dragging) return;
      justDragged.current = true;
      const from = visible.findIndex((c) => c.id === col.id);
      // Dropping just before or after itself leaves the order alone.
      if (target >= 0 && target !== from && target !== from + 1) placeAt(col.id, target > from ? target - 1 : target);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  };

  const startResize = (col: Column<Row>) => (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const x0 = e.clientX;
    const w0 = widthOf(col);
    let width = w0;
    const onMove = (ev: PointerEvent) => {
      width = Math.round(Math.min(MAX_WIDTH, Math.max(MIN_COLUMN_WIDTH, w0 + ev.clientX - x0)));
      setLiveWidth({ id: col.id, width });
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      setLiveWidth(null);
      if (width !== w0) update({ widths: { ...settings.widths, [col.id]: width } });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  };
  const resizeKeys = (col: Column<Row>) => (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" ? RESIZE_STEP : e.key === "ArrowLeft" ? -RESIZE_STEP : 0;
    if (!step) return;
    e.preventDefault();
    update({ widths: { ...settings.widths, [col.id]: Math.min(MAX_WIDTH, Math.max(MIN_COLUMN_WIDTH, widthOf(col) + step)) } });
  };

  const headerKeys = (col: Column<Row>) => (e: KeyboardEvent<HTMLButtonElement>) => {
    if (!e.altKey || !e.shiftKey || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
    e.preventDefault();
    move(col.id, e.key === "ArrowLeft" ? -1 : 1);
  };

  // Rows: one tab stop (the chosen row, or the first); arrow keys move the choice.
  const focusKey = selectedKey && sorted.some((r) => rowKey(r) === selectedKey) ? selectedKey : sorted[0] ? rowKey(sorted[0]) : null;
  const choose = (row: Row, focus: boolean) => {
    onSelect?.(row, sorted);
    if (focus) [...(body.current?.querySelectorAll<HTMLElement>("tr[data-row]") ?? [])].find((tr) => tr.dataset.row === rowKey(row))?.focus();
  };
  const rowKeys = (index: number) => (e: KeyboardEvent<HTMLTableRowElement>) => {
    if (e.target !== e.currentTarget) return;
    const next = e.key === "ArrowDown" ? index + 1 : e.key === "ArrowUp" ? index - 1 : e.key === "Home" ? 0 : e.key === "End" ? sorted.length - 1 : -2;
    if (next >= 0 && next < sorted.length) {
      e.preventDefault();
      choose(sorted[next], true);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      choose(sorted[index], false);
    }
  };

  return (
    <div ref={wrap} className={`phx-table-wrap${drag ? " is-dragging" : ""}`}>
      <table className="phx-table" role="grid" aria-label={label} style={{ minWidth: total }}>
        <colgroup>
          {visible.map((c, i) => <col key={c.id} style={i < visible.length - 1 ? { width: widthOf(c) } : undefined} />)}
        </colgroup>
        <thead>
          <tr ref={head}>
            {visible.map((col) => {
              const sorting = sort?.column === col.id ? sort.dir : null;
              return (
                <th key={col.id} data-column={col.id} className={drag?.id === col.id ? "is-dragged" : undefined}
                  aria-sort={col.sortValue ? (sorting === "asc" ? "ascending" : sorting === "desc" ? "descending" : "none") : undefined}
                  onContextMenu={(e) => { if (showMenu) showMenu(e, t("table.columnMenu", { column: col.label }), menuEntries(col)); }}>
                  <div className="phx-th">
                    <button type="button" className="phx-th-label" title={col.label}
                      onPointerDown={startDrag(col)} onKeyDown={headerKeys(col)}
                      onClick={() => { if (justDragged.current) { justDragged.current = false; return; } toggleSort(col); }}>
                      <span className="phx-th-text">{col.label}</span>
                      {sorting && <Icon name={sorting === "asc" ? "sortUp" : "sortDown"} size={13} />}
                    </button>
                    <MenuButton label={t("table.columnMenu", { column: col.label })} icon={<Icon name="more" size={14} />}
                      items={() => menuEntries(col)} className="phx-th-menu" />
                  </div>
                  <div className="phx-th-resize" role="separator" aria-orientation="vertical" tabIndex={0}
                    aria-label={t("table.resizeColumn", { column: col.label })}
                    aria-valuenow={widthOf(col)} aria-valuemin={MIN_COLUMN_WIDTH} aria-valuemax={MAX_WIDTH}
                    onPointerDown={startResize(col)} onKeyDown={resizeKeys(col)} />
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody ref={body}>
          {sorted.map((row, index) => {
            const key = rowKey(row);
            const selected = selectedKey === key;
            return (
              <tr key={key} data-row={key}
                className={`${rowClass?.(row) ?? ""}${selected ? " is-selected" : ""}`.trim() || undefined}
                aria-selected={onSelect ? selected : undefined}
                aria-label={rowLabel?.(row)}
                tabIndex={onSelect ? (key === focusKey ? 0 : -1) : undefined}
                onClick={onSelect ? () => choose(row, false) : undefined}
                onKeyDown={onSelect ? rowKeys(index) : undefined}>
                {visible.map((col) => <td key={col.id} className={col.className} title={col.title?.(row)}>{col.cell(row)}</td>)}
              </tr>
            );
          })}
        </tbody>
      </table>
      {!rows.length && empty && <div className="phx-table-empty">{empty}</div>}
      {drag && <div className="phx-table-drop" style={{ left: drag.marker }} aria-hidden="true" />}
    </div>
  );
}
