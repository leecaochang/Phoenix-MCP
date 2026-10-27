/**
 * Moving through a log: a row chosen with the keyboard, or by Previous and
 * Next in Log Details, is scrolled to the middle of the table; a row chosen
 * with the pointer only comes into view, so the list does not jump under the
 * pointer. Previous and Next report the step so Log Details can be resized.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { useState } from "react";
import { ConfigTable } from "../ui/table/ConfigTable";
import { LogSelection } from "../shell/logDetails";

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
});
