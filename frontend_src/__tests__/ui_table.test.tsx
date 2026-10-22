/**
 * The configurable table: columns hidden by default and shown again in their
 * place, moved by menu, keyboard and drag, sorted, resized by keyboard, rows
 * chosen by keyboard, and settings kept in the workspace's store.
 */

import { describe, it, expect, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { ConfigTable, columnOrder, type Column } from "../ui/table/ConfigTable";
import { TableSettingsContext, TableSettingsStore, readTableSettings } from "../ui/table/settings";

interface Row { id: string; name: string; size: number }
const ROWS: Row[] = [
  { id: "a", name: "Bravo", size: 2 },
  { id: "b", name: "alpha", size: 10 },
  { id: "c", name: "Charlie", size: 1 },
];
const COLUMNS: Column<Row>[] = [
  { id: "name", label: "Name", cell: (r) => r.name, sortValue: (r) => r.name, width: 120 },
  { id: "size", label: "Size", cell: (r) => String(r.size), sortValue: (r) => r.size, width: 80 },
  { id: "id", label: "Id", cell: (r) => r.id, hidden: true },
];

function mount(store = new TableSettingsStore(), onSelect = vi.fn(), selectedKey: string | null = null) {
  const view = render(
    <TableSettingsContext.Provider value={store}>
      <ConfigTable id="things" label="Things" columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} onSelect={onSelect} selectedKey={selectedKey} />
    </TableSettingsContext.Provider>,
  );
  const headers = () => screen.getAllByRole("columnheader").map((th) => th.querySelector(".phx-th-text")!.textContent);
  const cells = (col: number) => screen.getAllByRole("row").slice(1).map((tr) => tr.children[col].textContent);
  const menu = (column: string) => fireEvent.click(screen.getByRole("button", { name: `${column} Column Options` }));
  return { ...view, store, headers, cells, menu, onSelect };
}

describe("configurable table", () => {
  it("starts with default columns, shows a hidden one in its place, and hides one again", () => {
    const t = mount();
    expect(t.headers()).toEqual(["Name", "Size"]);
    t.menu("Size");
    fireEvent.click(screen.getByRole("menuitem", { name: "Show Id" }));
    expect(t.headers()).toEqual(["Name", "Size", "Id"]);
    t.menu("Name");
    fireEvent.click(screen.getByRole("menuitem", { name: "Hide Column" }));
    expect(t.headers()).toEqual(["Size", "Id"]);
    // Shown again, Name returns to the front, where it was.
    t.menu("Size");
    fireEvent.click(screen.getByRole("menuitem", { name: "Show Name" }));
    expect(t.headers()).toEqual(["Name", "Size", "Id"]);
    expect(t.store.get("things").hidden).toEqual([]);
  });

  it("moves columns from the menu and with Alt+Shift+arrows, and Reset Column Order puts them back", () => {
    const t = mount();
    t.menu("Name");
    expect(screen.getByRole("menuitem", { name: "Move Left" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(screen.getByRole("menuitem", { name: "Move Right" }));
    expect(t.headers()).toEqual(["Size", "Name"]);
    const name = within(screen.getAllByRole("columnheader")[1]).getByText("Name").closest("button")!;
    fireEvent.keyDown(name, { key: "ArrowLeft", altKey: true, shiftKey: true });
    expect(t.headers()).toEqual(["Name", "Size"]);
    fireEvent.keyDown(name, { key: "ArrowRight", altKey: true, shiftKey: true });
    t.menu("Size");
    fireEvent.click(screen.getByRole("menuitem", { name: "Reset Column Order" }));
    expect(t.headers()).toEqual(["Name", "Size"]);
  });

  it("drags a header to a new place, showing where it will land", () => {
    const t = mount();
    const ths = screen.getAllByRole("columnheader");
    ths.forEach((th, i) => {
      th.getBoundingClientRect = () => ({ left: i * 100, right: i * 100 + 100, width: 100, top: 0, bottom: 30, height: 30, x: i * 100, y: 0, toJSON: () => ({}) });
    });
    const label = within(ths[0]).getByText("Name").closest("button")!;
    fireEvent.pointerDown(label, { button: 0, clientX: 20 });
    fireEvent.pointerMove(window, { clientX: 190 });
    expect(document.querySelector(".phx-table-drop")).not.toBeNull();
    fireEvent.pointerUp(window);
    expect(document.querySelector(".phx-table-drop")).toBeNull();
    expect(t.headers()).toEqual(["Size", "Name"]);
    // The click that ends a drag does not also sort.
    fireEvent.click(label);
    expect(t.store.get("things").sort).toBeUndefined();
  });

  it("sorts by clicking a header, numbers as numbers and text by the locale, and says so", () => {
    const t = mount();
    const size = within(screen.getAllByRole("columnheader")[1]).getByText("Size").closest("button")!;
    fireEvent.click(size);
    expect(t.cells(1)).toEqual(["1", "2", "10"]);
    expect(screen.getAllByRole("columnheader")[1]).toHaveAttribute("aria-sort", "ascending");
    fireEvent.click(size);
    expect(t.cells(1)).toEqual(["10", "2", "1"]);
    t.menu("Name");
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Sort Ascending" }));
    expect(t.cells(0)).toEqual(["alpha", "Bravo", "Charlie"]);
  });

  it("resizes a column from the keyboard and keeps the width", () => {
    const t = mount();
    const handle = screen.getByRole("separator", { name: "Resize Name Column" });
    expect(handle).toHaveAttribute("aria-valuenow", "120");
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(t.store.get("things").widths).toEqual({ name: 136 });
    expect(screen.getByRole("separator", { name: "Resize Name Column" })).toHaveAttribute("aria-valuenow", "136");
    t.menu("Size");
    fireEvent.click(screen.getByRole("menuitem", { name: "Reset Columns" }));
    expect(t.store.get("things")).toEqual({});
  });

  it("chooses rows with the pointer and the keyboard, with one tab stop", () => {
    const onSelect = vi.fn();
    const t = mount(new TableSettingsStore(), onSelect, "b");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((r) => r.getAttribute("tabindex"))).toEqual(["-1", "0", "-1"]);
    expect(rows[1]).toHaveAttribute("aria-selected", "true");
    rows[1].focus();
    fireEvent.keyDown(rows[1], { key: "ArrowDown" });
    expect(onSelect).toHaveBeenLastCalledWith(ROWS[2], ROWS);
    expect(document.activeElement).toBe(rows[2]);
    fireEvent.keyDown(rows[2], { key: "Home" });
    expect(onSelect).toHaveBeenLastCalledWith(ROWS[0], ROWS);
    fireEvent.click(rows[1]);
    expect(onSelect).toHaveBeenLastCalledWith(ROWS[1], ROWS);
    void t;
  });

  it("keeps settings in the workspace's store, so a new table with the same id picks them up", () => {
    const store = new TableSettingsStore();
    const first = mount(store);
    first.menu("Name");
    fireEvent.click(screen.getByRole("menuitem", { name: "Move Right" }));
    first.unmount();
    const second = mount(store);
    expect(second.headers()).toEqual(["Size", "Name"]);
    act(() => store.restore(undefined));
    expect(second.headers()).toEqual(["Name", "Size"]);
  });
});

describe("table settings", () => {
  it("places a column added since the order was saved after the one it follows", () => {
    expect(columnOrder(["a", "b", "c", "d"], ["c", "a"])).toEqual(["c", "d", "a", "b"]);
    expect(columnOrder(["a", "b"], undefined)).toEqual(["a", "b"]);
    expect(columnOrder(["x", "a"], ["a"])).toEqual(["x", "a"]);
  });

  it("reads saved settings defensively", () => {
    expect(readTableSettings({ order: ["a", "a", 3, ""], hidden: ["b"], widths: { a: 5, b: 9999, c: "x" }, sort: { column: "a", dir: "up" } }))
      .toEqual({ order: ["a"], hidden: ["b"], widths: { a: 60, b: 2000 } });
    expect(readTableSettings("nonsense")).toEqual({});
    const store = new TableSettingsStore();
    store.restore({ t1: { sort: { column: "a", dir: "desc" } }, t2: { bogus: true } });
    expect(store.snapshot()).toEqual({ t1: { sort: { column: "a", dir: "desc" } } });
  });
});

describe("keeping the chosen row in view", () => {
  it("scrolls the chosen row into view when the choice changes from outside", () => {
    const scrolled: string[] = [];
    const original = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = function (this: HTMLElement) { scrolled.push(this.dataset.row ?? ""); };
    try {
      const store = new TableSettingsStore();
      const view = (key: string | null) => (
        <TableSettingsContext.Provider value={store}>
          <ConfigTable id="things" label="Things" columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} onSelect={() => {}} selectedKey={key} />
        </TableSettingsContext.Provider>
      );
      const { rerender } = render(view(null));
      expect(scrolled).toEqual([]);
      rerender(view("c"));
      expect(scrolled).toEqual(["c"]);
    } finally {
      HTMLElement.prototype.scrollIntoView = original;
    }
  });
});
