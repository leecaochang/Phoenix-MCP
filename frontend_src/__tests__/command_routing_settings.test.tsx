import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CommandRoutingToggle } from "../components/CommandRoutingToggle";

it.each(["voice", "chat"] as const)("offers independent %s routing without requiring a provider", surface => {
  const change = vi.fn();
  const view = render(<CommandRoutingToggle surface={surface} mode="off" onChange={change} saving={false} />);
  const toggle = screen.getByRole("checkbox", { name: `Handle ${surface} commands directly` });
  expect(toggle).not.toBeChecked();
  expect(toggle).toHaveAccessibleDescription(/local intents first/);
  fireEvent.click(toggle);
  expect(change).toHaveBeenCalledExactlyOnceWith("auto");
  view.rerender(<CommandRoutingToggle surface={surface} mode="auto" onChange={change} saving={false} />);
  fireEvent.click(toggle);
  expect(change).toHaveBeenLastCalledWith("off");
});

it.each(["auto", "local", "hybrid"])("preserves enabled %s routing and locks it during saves", mode => {
  render(<CommandRoutingToggle surface="chat" mode={mode} onChange={vi.fn()} saving />);
  const toggle = screen.getByRole("checkbox");
  expect(toggle).toBeChecked();
  expect(toggle).toHaveAttribute("aria-disabled", "true");
});
