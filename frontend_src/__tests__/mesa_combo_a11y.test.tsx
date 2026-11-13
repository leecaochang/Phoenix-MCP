/** The MESA target picker folds accents and announces only a list it actually shows. */
import { expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { Combo } from "../views/MesaView";

const OPTIONS = [
  { value: "area_kuche", label: "Küche" },
  { value: "area_salon", label: "Salón" },
  { value: "area_hall", label: "Hall" },
];

function picker() {
  render(<Combo id="target" value="" options={OPTIONS} onChange={() => {}} />);
  const field = screen.getByRole("combobox");
  fireEvent.focus(field);
  return field;
}

it("finds a name with accents from a query typed without them", () => {
  const field = picker();
  fireEvent.change(field, { target: { value: "kuche" } });
  expect(screen.getByRole("option").textContent).toContain("Küche");
  fireEvent.change(field, { target: { value: "SALON" } });
  expect(screen.getByRole("option").textContent).toContain("Salón");
});

it("is expanded and controls a mounted list only while it shows matches", () => {
  const field = picker();
  fireEvent.change(field, { target: { value: "hall" } });
  const listbox = screen.getByRole("listbox");
  expect(field.getAttribute("aria-expanded")).toBe("true");
  expect(field.getAttribute("aria-controls")).toBe(listbox.id);

  fireEvent.change(field, { target: { value: "nothing like it" } });
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(field.getAttribute("aria-expanded")).toBe("false");
  expect(field.hasAttribute("aria-controls")).toBe(false);
  expect(field.hasAttribute("aria-activedescendant")).toBe(false);
});

it("announces how many targets match, politely", () => {
  const field = picker();
  const status = screen.getByRole("status");
  expect(status.getAttribute("aria-live")).toBe("polite");
  fireEvent.change(field, { target: { value: "a" } });
  expect(status.textContent).toMatch(/\b3\b/);
  fireEvent.change(field, { target: { value: "nothing like it" } });
  expect(status.textContent).toMatch(/\b0\b/);
});

it("passes axe's combobox rules both with and without matches", async () => {
  const field = picker();
  for (const query of ["hall", "nothing like it"]) {
    fireEvent.change(field, { target: { value: query } });
    const result = await axe.run(document.body, { runOnly: ["aria-required-attr", "aria-valid-attr-value"] });
    expect(result.violations.map((rule) => rule.id), query).toEqual([]);
  }
});
