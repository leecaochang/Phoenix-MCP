/**
 * Moving through a log: a row chosen with the keyboard, or by Previous and
 * Next in Log Details, is scrolled to the middle of the table; a row chosen
 * with the pointer only comes into view, so the list does not jump under the
 * pointer. Previous and Next report the step so Log Details can be resized.
 * In a phone window the table neither centres nor scrolls to the chosen row,
 * and shows its rows ten at a time (user, 2026-11-02).
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { useState } from "react";
import { ConfigTable } from "../ui/table/ConfigTable";
import { LogSelection } from "../shell/logDetails";
import { PHONE_ROWS, RowLimitContext } from "../ui/table/rowLimit";

const ROWS = Array.from({ length: 40 }, (_, i) => ({ id: `r${i}` }));
const ROW_H = 30;

function Table({ outside }: { outside?: (choose: (key: string) => void) => void }) {
  const [selected, setSelected] = useState<string | null>("r10");
  outside?.(setSelected);
  return (
    <ConfigTable id="t" label="Log" rows={ROWS} rowKey={(r) => r.id} selectedKey={selected} onSelect={(r) => setSelected(r.id)}
      columns={[{ id: "id", label: "Id", cell: (r) => r.id }]} />
  );
}

/** Lay the table out: 300 px of rows visible below a 30 px header, each row 30 px tall. */
function layout(container: HTMLElement) {
  const wrap = container.querySelector<HTMLElement>(".phx-table-wrap")!;
  Object.defineProperty(wrap, "clientHeight", { configurable: true, get: () => 330 });
  Object.defineProperty(wrap, "scrollHeight", { configurable: true, get: () => 30 + ROWS.length * ROW_H });
  wrap.getBoundingClientRect = () => ({ top: 0, bottom: 330, height: 330, left: 0, right: 500, width: 500, x: 0, y: 0, toJSON: () => ({}) });
  container.querySelector("thead")!.getBoundingClientRect = () => ({ top: 0, bottom: 30, height: 30, left: 0, right: 500, width: 500, x: 0, y: 0, toJSON: () => ({}) });
  container.querySelectorAll<HTMLElement>("tbody tr[data-row]").forEach((tr, i) => {
    tr.getBoundingClientRect = () => {
      const top = 30 + i * ROW_H - wrap.scrollTop;
      return { top, bottom: top + ROW_H, height: ROW_H, left: 0, right: 500, width: 500, x: 0, y: top, toJSON: () => ({}) };
    };
    tr.scrollIntoView = vi.fn();
  });
  return wrap;
}
const row = (container: HTMLElement, key: string) => container.querySelector<HTMLElement>(`tr[data-row="${key}"]`)!;

describe("moving through a log", () => {
  it("keeps a row chosen with the arrow keys in the middle of the table", () => {
    const { container } = render(<Table />);
    const wrap = layout(container);
    fireEvent.keyDown(row(container, "r10"), { key: "ArrowDown" });
    // r11's middle (30 + 11 * 30 + 15 = 375) lines up with the middle of the rows area (30 + 150 = 180).
    expect(wrap.scrollTop).toBe(195);
    expect(row(container, "r11").scrollIntoView).not.toHaveBeenCalled();
  });

  it("keeps the row in the middle of the window when the window scrolls rather than the table (user, 2026-10-31)", () => {
    const { container } = render(<div className="phx-window-body" style={{ overflowY: "auto" }}><Table /></div>);
    const body = container.querySelector<HTMLElement>(".phx-window-body")!;
    const wrap = container.querySelector<HTMLElement>(".phx-table-wrap")!;
    // The table is as tall as its rows; the window around it shows 300 px of them.
    Object.defineProperty(wrap, "clientHeight", { configurable: true, get: () => 30 + ROWS.length * ROW_H });
    Object.defineProperty(wrap, "scrollHeight", { configurable: true, get: () => 30 + ROWS.length * ROW_H });
    Object.defineProperty(body, "clientHeight", { configurable: true, get: () => 300 });
    Object.defineProperty(body, "scrollHeight", { configurable: true, get: () => 30 + ROWS.length * ROW_H });
    body.getBoundingClientRect = () => ({ top: 0, bottom: 300, height: 300, left: 0, right: 500, width: 500, x: 0, y: 0, toJSON: () => ({}) });
    container.querySelectorAll<HTMLElement>("tbody tr[data-row]").forEach((tr, i) => {
      tr.getBoundingClientRect = () => {
        const top = 30 + i * ROW_H - body.scrollTop;
        return { top, bottom: top + ROW_H, height: ROW_H, left: 0, right: 500, width: 500, x: 0, y: top, toJSON: () => ({}) };
      };
      tr.scrollIntoView = vi.fn();
    });
    fireEvent.keyDown(row(container, "r10"), { key: "ArrowDown" });
    // r11's middle (375) lines up with the middle of the window (150); the header scrolled away with the rows.
    expect(body.scrollTop).toBe(225);
  });

  it("leaves a phone window's scroll alone when a row is chosen (user, 2026-11-02)", () => {
    const { container } = render(<RowLimitContext.Provider value={PHONE_ROWS}><Table /></RowLimitContext.Provider>);
    const wrap = layout(container);
    fireEvent.keyDown(row(container, "r5"), { key: "ArrowDown" });
    expect(wrap.scrollTop).toBe(0);
    expect(row(container, "r6").scrollIntoView).not.toHaveBeenCalled();
  });

  it("shows at most maxRows rows, after sorting, and steps only among them", () => {
    const chosen: string[][] = [];
    const { container } = render(<ConfigTable id="t2" label="Log" rows={ROWS} rowKey={(r) => r.id} maxRows={PHONE_ROWS}
      onSelect={(_, ordered) => chosen.push(ordered.map((r) => r.id))} columns={[{ id: "id", label: "Id", cell: (r) => r.id, sortValue: (r) => -Number(r.id.slice(1)) }]}
      defaultSort={{ column: "id", dir: "asc" }} />);
    const keys = [...container.querySelectorAll<HTMLElement>("tbody tr[data-row]")].map((tr) => tr.dataset.row);
    // Sorted first (r39 leads), then cut to ten.
    expect(keys).toEqual(Array.from({ length: 10 }, (_, i) => `r${39 - i}`));
    fireEvent.click(row(container, "r39"));
    expect(chosen[0]).toHaveLength(10);
  });

  it("marks the table as keyboard-driven until the mouse moves, so a row under a still pointer is not hovered", () => {
    const { container } = render(<Table />);
    const wrap = layout(container);
    expect(wrap.hasAttribute("data-keyboard")).toBe(false);
    fireEvent.keyDown(row(container, "r10"), { key: "ArrowDown" });
    expect(wrap.hasAttribute("data-keyboard")).toBe(true);
    fireEvent.pointerMove(row(container, "r12"), { pointerType: "touch" });
    expect(wrap.hasAttribute("data-keyboard")).toBe(true);
    fireEvent.pointerMove(row(container, "r12"), { pointerType: "mouse", clientX: 50, clientY: 100 });
    expect(wrap.hasAttribute("data-keyboard")).toBe(false);
    // Rows scrolling under a still mouse report a move at the same place: not the user's.
    fireEvent.keyDown(row(container, "r11"), { key: "ArrowDown" });
    fireEvent.pointerMove(row(container, "r13"), { pointerType: "mouse", clientX: 50, clientY: 100 });
    expect(wrap.hasAttribute("data-keyboard")).toBe(true);
    fireEvent.pointerMove(row(container, "r13"), { pointerType: "mouse", clientX: 52, clientY: 100 });
    expect(wrap.hasAttribute("data-keyboard")).toBe(false);
  });

  it("only brings a row chosen with the pointer into view", () => {
    const { container } = render(<Table />);
    const wrap = layout(container);
    wrap.scrollTop = 120;
    fireEvent.click(row(container, "r12"));
    expect(wrap.scrollTop).toBe(120);
    expect(row(container, "r12").scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
  });

  it("centres a row chosen from outside, as Previous and Next do", () => {
    let choose: (key: string) => void = () => {};
    const { container, rerender } = render(<Table outside={(c) => { choose = c; }} />);
    const wrap = layout(container);
    choose("r20");
    rerender(<Table outside={(c) => { choose = c; }} />);
    expect(wrap.scrollTop).toBe(30 + 20 * ROW_H + 15 - 180);
  });

  it("reports Previous and Next, not a choice in the log, as a step", () => {
    const selection = new LogSelection();
    const onStep = vi.fn();
    selection.onStep = onStep;
    const ordered = [{ id: "a" }, { id: "b" }] as never[];
    selection.set({ kind: "conversation", entry: ordered[0], ordered, accessKeyNames: {} });
    expect(onStep).not.toHaveBeenCalled();
    selection.step(1);
    expect(onStep).toHaveBeenCalledOnce();
    selection.step(1);
    expect(onStep).toHaveBeenCalledOnce();
  });

  it("follows the log's current order, so new rows and a re-sort are reachable", () => {
    const selection = new LogSelection();
    const a = { id: "a" } as never, b = { id: "b" } as never, c = { id: "c" } as never;
    selection.set({ kind: "conversation", entry: a, ordered: [a], accessKeyNames: {} });
    selection.reorder("conversation", [c, a, b]);
    selection.step(-1);
    expect((selection.get()!.entry as { id: string }).id).toBe("c");
    // Another log's order never replaces this one.
    selection.reorder("audit", []);
    expect(selection.get()!.ordered).toHaveLength(3);
  });
});
