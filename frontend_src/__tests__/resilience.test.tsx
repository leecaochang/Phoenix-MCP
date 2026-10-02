import { expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BusyButton, BusyInput, BusySelect } from "../components/BusyButton";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { t } from "../i18n";

it("keeps busy action focus and prevents mouse and keyboard duplicate dispatch", async () => {
  const click = vi.fn();
  const { rerender } = render(<BusyButton busy={false} onClick={click}>Save</BusyButton>);
  const button = screen.getByRole("button", { name: "Save" });
  button.focus();
  rerender(<BusyButton busy onClick={click}>Save</BusyButton>);
  expect(button).toHaveFocus();
  expect(button).not.toBeDisabled();
  expect(button).toHaveAttribute("aria-disabled", "true");
  await userEvent.keyboard("{Enter} ");
  fireEvent.click(button);
  expect(click).not.toHaveBeenCalled();
  rerender(<BusyButton busy={false} onClick={click}>Save</BusyButton>);
  await userEvent.keyboard("{Enter}");
  expect(click).toHaveBeenCalledTimes(1);
});

it("keeps busy form controls focused and suppresses edits until completion", async () => {
  const change = vi.fn();
  const controls = (busy: boolean) => <><BusyInput aria-label="Name" value="Desk" busy={busy} onChange={change} />
    <BusySelect aria-label="Mode" value="allow" busy={busy} onChange={change}>
      <option value="allow">Allow</option><option value="deny">Deny</option>
    </BusySelect></>;
  const { rerender } = render(controls(false));
  const input = screen.getByRole("textbox", { name: "Name" });
  input.focus();
  rerender(controls(true));
  expect(input).toHaveFocus();
  expect(input).not.toBeDisabled();
  await userEvent.keyboard("changed");
  expect(change).not.toHaveBeenCalled();
  const select = screen.getByRole("combobox", { name: "Mode" });
  select.focus();
  expect(select).toHaveFocus();
  expect(select).not.toBeDisabled();
  fireEvent.change(select, { target: { value: "deny" } });
  expect(change).not.toHaveBeenCalled();
  await userEvent.keyboard("{Tab}");
  expect(select).not.toHaveFocus();
  rerender(controls(false));
  fireEvent.change(select, { target: { value: "deny" } });
  expect(change).toHaveBeenCalledTimes(1);
});

it("contains a rendering failure, preserves outside navigation and can recover", () => {
  let broken = true;
  function View() { if (broken) throw new Error("render fixture"); return <p>Recovered</p>; }
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    render(<><button>Settings</button><ErrorBoundary><View /></ErrorBoundary></>);
    expect(screen.getByRole("alert")).toHaveTextContent(t("shell.renderFailed"));
    expect(screen.getByRole("button", { name: "Settings" })).toBeEnabled();
    broken = false;
    fireEvent.click(screen.getByRole("button", { name: t("common.refresh") }));
    expect(screen.getByText("Recovered")).toBeVisible();
  } finally { consoleError.mockRestore(); }
});
