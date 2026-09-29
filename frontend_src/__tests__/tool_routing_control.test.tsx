import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ToolRoutingControl } from "../components/ToolRoutingControl";

it("requires an account and separate qualification without making model requests", () => {
  const onChange = vi.fn();
  const view = render(<ToolRoutingControl surface="chat" hasAccount={false} saving={false} onChange={onChange} />);
  expect(screen.getByRole("combobox", { name: "Tool selection review" })).toHaveValue("off");
  expect(screen.getByRole("option", { name: "Observe only" })).toBeDisabled();
  expect(screen.getByRole("option", { name: "Advise" })).toBeDisabled();
  view.rerender(<ToolRoutingControl surface="chat" hasAccount saving={false} onChange={onChange} />);
  expect(screen.getByRole("option", { name: "Observe only" })).not.toBeDisabled();
  expect(screen.getByRole("option", { name: "Advise" })).toBeDisabled();
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "observe" } });
  expect(onChange).toHaveBeenCalledWith("observe");
  view.rerender(<ToolRoutingControl surface="chat" mode="advise" hasAccount qualifiedLanguages={["en"]} saving onChange={onChange} />);
  expect(screen.getByRole("combobox")).toBeDisabled();
  expect(screen.getByRole("option", { name: "Advise" })).not.toHaveAttribute("disabled");
  expect(screen.getByText(/qualified for: en/)).toBeInTheDocument();
});

it("keeps controls associated with their own surface and explains sharing", () => {
  for (const surface of ["chat", "voice", "ai_task"] as const) {
    render(<ToolRoutingControl surface={surface} hasAccount saving={false} onChange={vi.fn()} />);
  }
  const selects = screen.getAllByRole("combobox");
  expect(new Set(selects.map(select => select.id)).size).toBe(3);
  for (const select of selects) expect(select).toHaveAccessibleDescription(/Shares task context/);
});
