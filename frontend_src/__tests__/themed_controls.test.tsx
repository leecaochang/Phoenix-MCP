/** Phoenix's own dropdown list, number field and segmented control keep the native controls' behavior. */
import { useRef, useState } from "react";
import { expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { DisplayModeContext } from "../ui/ActionButton";
import { NumberField } from "../ui/NumberField";
import { Segmented } from "../ui/Segmented";
import { SelectPopupHost } from "../ui/SelectPopup";
import { useClickAway } from "../ui/useClickAway";

function Dropdown({ onPick = () => {}, disabled = false, busy = false }: { onPick?: (value: string) => void; disabled?: boolean; busy?: boolean }) {
  const [root, setRoot] = useState<HTMLDivElement | null>(null);
  const [value, setValue] = useState("b");
  return (
    <div ref={setRoot}>
      <SelectPopupHost root={root} />
      <select className="input" aria-label="Letter" value={value} disabled={disabled} aria-disabled={busy || undefined}
        onChange={(e) => { setValue(e.target.value); onPick(e.target.value); }}>
        <option value="a">Alpha</option>
        <option value="b">Bravo</option>
        <option value="c" disabled>Charlie</option>
        <option value="d">Delta</option>
      </select>
    </div>
  );
}

const select = () => screen.getByRole("combobox", { name: "Letter" }) as HTMLSelectElement;
// The native select's own options share the role, so look inside Phoenix's list.
const choice = (name: string) => within(screen.getByRole("listbox")).getByRole("option", { name });

it("opens Phoenix's list on a mouse press, with the current choice checked", () => {
  render(<Dropdown />);
  fireEvent.pointerDown(select(), { button: 0, pointerType: "mouse" });
  const list = screen.getByRole("listbox", { name: "Letter" });
  const chosen = within(list).getByRole("option", { selected: true });
  expect(chosen.textContent).toBe("Bravo");
  expect(list.getAttribute("aria-activedescendant")).toBe(chosen.id);
  expect(select().getAttribute("aria-expanded")).toBe("true");
  expect(select().hasAttribute("data-phx-open")).toBe(true);
});

it("chooses a clicked option through the dropdown's own change handler", () => {
  const pick = vi.fn();
  render(<Dropdown onPick={pick} />);
  fireEvent.pointerDown(select(), { button: 0, pointerType: "mouse" });
  fireEvent.click(choice("Delta"));
  expect(pick).toHaveBeenCalledExactlyOnceWith("d");
  expect(select().value).toBe("d");
  expect(select().getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(select());
});

it("ignores a disabled option", () => {
  const pick = vi.fn();
  render(<Dropdown onPick={pick} />);
  fireEvent.pointerDown(select(), { button: 0, pointerType: "mouse" });
  fireEvent.click(choice("Charlie"));
  expect(pick).not.toHaveBeenCalled();
});

it("opens from the keyboard, skips disabled options, and Escape leaves the value alone", () => {
  const pick = vi.fn();
  render(<Dropdown onPick={pick} />);
  select().focus();
  fireEvent.keyDown(select(), { key: "Enter" });
  const list = screen.getByRole("listbox");
  expect(document.activeElement).toBe(list);
  fireEvent.keyDown(list, { key: "ArrowDown" });
  expect(choice("Delta").id).toBe(list.getAttribute("aria-activedescendant"));
  fireEvent.keyDown(list, { key: "Escape" });
  expect(pick).not.toHaveBeenCalled();
  expect(document.activeElement).toBe(select());
});

it("chooses the highlighted option with Enter and jumps by typed letters", () => {
  const pick = vi.fn();
  render(<Dropdown onPick={pick} />);
  select().focus();
  fireEvent.keyDown(select(), { key: " " });
  const list = screen.getByRole("listbox");
  fireEvent.keyDown(list, { key: "a" });
  expect(choice("Alpha").id).toBe(list.getAttribute("aria-activedescendant"));
  fireEvent.keyDown(list, { key: "Enter" });
  expect(pick).toHaveBeenCalledExactlyOnceWith("a");
});

it("leaves touch to the system picker and never opens an unavailable dropdown", () => {
  const { unmount } = render(<Dropdown />);
  fireEvent.pointerDown(select(), { button: 0, pointerType: "touch" });
  expect(screen.queryByRole("listbox")).toBeNull();
  unmount();
  render(<Dropdown busy />);
  fireEvent.pointerDown(select(), { button: 0, pointerType: "mouse" });
  fireEvent.keyDown(select(), { key: "Enter" });
  expect(screen.queryByRole("listbox")).toBeNull();
});

function Count({ initial = "5", onValue = () => {} }: { initial?: string; onValue?: (value: string) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <label>
      <span>Limit</span>
      <NumberField className="input" min={0} max={6} step={1} value={value} onChange={(e) => { setValue(e.target.value); onValue(e.target.value); }} />
    </label>
  );
}

it("labels the number input itself and keeps the step buttons out of Tab order and the accessibility tree", () => {
  render(<Count />);
  const input = screen.getByLabelText("Limit") as HTMLInputElement;
  expect(input.type).toBe("number");
  const buttons = input.parentElement!.querySelectorAll("button");
  expect(buttons).toHaveLength(2);
  for (const button of buttons) {
    expect(button.tabIndex).toBe(-1);
    expect(button.getAttribute("aria-hidden")).toBe("true");
  }
});

it("steps through the field's change handler and stops at its bounds", () => {
  const seen = vi.fn();
  render(<Count onValue={seen} />);
  const input = screen.getByLabelText("Limit") as HTMLInputElement;
  const [down, up] = input.parentElement!.querySelectorAll("button");
  fireEvent.pointerDown(up, { button: 0 });
  fireEvent.pointerUp(window);
  expect(seen).toHaveBeenLastCalledWith("6");
  expect(input.value).toBe("6");
  expect((up as HTMLButtonElement).disabled).toBe(true);
  fireEvent.pointerDown(down, { button: 0 });
  fireEvent.pointerUp(window);
  expect(input.value).toBe("5");
});

it("shows segment icons alone in icons-only mode, named by their labels", () => {
  const change = vi.fn();
  const segments = [{ value: "light" as const, label: "Light", icon: "themeLight" as const }, { value: "dark" as const, label: "Dark", icon: "themeDark" as const }];
  const { rerender } = render(
    <DisplayModeContext.Provider value="icons"><Segmented label="Theme" value="light" onChange={change} segments={segments} /></DisplayModeContext.Provider>,
  );
  const dark = screen.getByRole("button", { name: "Dark" });
  expect(dark.textContent).toBe("");
  expect(screen.getByRole("button", { name: "Light" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(dark);
  expect(change).toHaveBeenCalledWith("dark");
  rerender(<DisplayModeContext.Provider value="icon-text"><Segmented label="Theme" value="light" onChange={change} segments={segments} /></DisplayModeContext.Provider>);
  expect(screen.getByRole("button", { name: "Dark" }).textContent).toBe("Dark");
});

function ClosingForm({ onAway }: { onAway: () => void }) {
  const [root, setRoot] = useState<HTMLDivElement | null>(null);
  const form = useRef<HTMLDivElement | null>(null);
  useClickAway(form, onAway, true, undefined, "click");
  return (
    <div ref={setRoot}>
      <SelectPopupHost root={root} />
      <div ref={form}>
        <select className="input" aria-label="Expiry" defaultValue="none">
          <option value="none">No Expiry</option>
          <option value="days">Days</option>
        </select>
      </div>
      <button type="button">Elsewhere</button>
    </div>
  );
}

it("keeps a form that closes on a click elsewhere open while its dropdown's list is used", () => {
  const away = vi.fn();
  render(<ClosingForm onAway={away} />);
  const select = screen.getByRole("combobox", { name: "Expiry" });
  fireEvent.pointerDown(select, { button: 0, pointerType: "mouse" });
  const list = screen.getByRole("listbox");
  // The press that opened the list ends on the list, outside the form.
  fireEvent.click(list);
  expect(away).not.toHaveBeenCalled();
  fireEvent.click(choice("Days"));
  expect(away).not.toHaveBeenCalled();
  expect((select as HTMLSelectElement).value).toBe("days");
  fireEvent.click(screen.getByRole("button", { name: "Elsewhere" }));
  expect(away).toHaveBeenCalledTimes(1);
});
