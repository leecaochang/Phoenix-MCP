/** Escape in an open suggestion list closes the list; only the next Escape reaches the dialog around it. */
import { expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Modal } from "../components/Modal";
import { TagInput } from "../components/TagInput";
import { Combo } from "../views/MesaView";

it("closes the tag list and keeps the dialog and the typed text", () => {
  const onClose = vi.fn();
  render(<Modal titleId="t" onClose={onClose}><TagInput value={[]} onChange={() => {}} canonicalTags={["lighting.task"]} /></Modal>);
  const field = screen.getByRole("combobox") as HTMLInputElement;
  fireEvent.focus(field);
  fireEvent.change(field, { target: { value: "light" } });
  expect(screen.getByRole("listbox")).toBeInTheDocument();

  fireEvent.keyDown(field, { key: "Escape" });
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(onClose).not.toHaveBeenCalled();
  expect(field.value).toBe("light");

  fireEvent.keyDown(field, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
});

it("closes the MESA picker's list and keeps the dialog", () => {
  const onClose = vi.fn();
  render(
    <Modal titleId="t" onClose={onClose}>
      <Combo id="target" value="" options={[{ value: "light.desk", label: "Desk" }]} onChange={() => {}} />
    </Modal>,
  );
  const field = screen.getByRole("combobox");
  fireEvent.focus(field);
  fireEvent.change(field, { target: { value: "desk" } });
  expect(screen.getByRole("listbox")).toBeInTheDocument();

  fireEvent.keyDown(field, { key: "Escape" });
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(onClose).not.toHaveBeenCalled();

  fireEvent.keyDown(field, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
});
