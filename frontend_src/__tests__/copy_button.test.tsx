/** Every copy control is the one shared button: it copies, shows a check for a second, then the copy icon again. */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { CopyButton, COPIED_MS } from "../ui/CopyButton";
import * as utils from "../utils";

beforeEach(() => vi.useFakeTimers());
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

it("shows the check for one second, then the copy icon again", async () => {
  const copy = vi.spyOn(utils, "copyToClipboard").mockResolvedValue(undefined as never);
  const { container } = render(<CopyButton text={() => "hello"} label="Copy text" />);
  const button = screen.getByRole("button", { name: "Copy text" });
  await act(async () => { fireEvent.click(button); });
  expect(copy).toHaveBeenCalledWith("hello");
  expect(container.querySelector(".phx-icon-check")).not.toBeNull();
  expect(screen.getByRole("status")).toHaveTextContent("Copied to clipboard");
  act(() => { vi.advanceTimersByTime(COPIED_MS - 1); });
  expect(container.querySelector(".phx-icon-check")).not.toBeNull();
  act(() => { vi.advanceTimersByTime(1); });
  expect(container.querySelector(".phx-icon-copy")).not.toBeNull();
  expect(container.querySelector(".phx-icon-check")).toBeNull();
  expect(COPIED_MS).toBe(1000);
});

it("keeps its own accessible name while showing the check", async () => {
  vi.spyOn(utils, "copyToClipboard").mockResolvedValue(undefined as never);
  render(<CopyButton text="x" label="Copy the first prompt" />);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Copy the first prompt" })); });
  expect(screen.getByRole("button", { name: "Copy the first prompt" })).toHaveClass("is-copied");
});
