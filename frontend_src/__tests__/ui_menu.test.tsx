import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MenuButton, type MenuEntry } from "../ui/Menu";
import { helpFor } from "../shell/help";

function setup(items: MenuEntry[] | (() => MenuEntry[])) {
  render(<MenuButton label="Window menu" icon={<span>m</span>} items={items} />);
  return screen.getByRole("button", { name: "Window menu" });
}

describe("menu button", () => {
  it("opens from the keyboard on the first available item and skips unavailable ones", () => {
    const left = vi.fn();
    const right = vi.fn();
    const button = setup([
      { id: "left", label: "Move Left", disabled: true, onSelect: left },
      { id: "right", label: "Move Right", onSelect: right },
      "separator",
      { id: "float", label: "Float Window", onSelect: vi.fn() },
    ]);
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    expect(button).toHaveAttribute("aria-expanded", "false");
    button.focus();
    fireEvent.keyDown(button, { key: "Enter" });
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(document.activeElement).toHaveTextContent("Move Right");
    expect(screen.getByRole("menuitem", { name: "Move Left" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(document.activeElement).toHaveTextContent("Float Window");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(document.activeElement).toHaveTextContent("Move Right");
    fireEvent.keyDown(document.activeElement!, { key: "Enter" });
    expect(right).toHaveBeenCalledOnce();
    expect(left).not.toHaveBeenCalled();
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it("closes on Escape and returns focus to the button", () => {
    const button = setup([{ id: "a", label: "A", onSelect: vi.fn() }]);
    button.focus();
    fireEvent.keyDown(button, { key: "ArrowDown" });
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it("ignores clicks on an unavailable item", () => {
    const run = vi.fn();
    const button = setup([{ id: "a", label: "A", disabled: true, onSelect: run }]);
    fireEvent.click(button);
    fireEvent.click(screen.getByRole("menuitem", { name: "A" }));
    expect(run).not.toHaveBeenCalled();
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("builds its items when it opens, so availability reflects the current layout", () => {
    let available = false;
    const button = setup(() => [{ id: "a", label: "A", disabled: !available, onSelect: vi.fn() }]);
    available = true;
    fireEvent.click(button);
    expect(screen.getByRole("menuitem", { name: "A" })).not.toHaveAttribute("aria-disabled");
  });

  it("announces on/off settings as checkboxes with their hint, and lines every label up with the check column", () => {
    const button = setup([
      { id: "verbose", label: "Show Verbose Output", toggle: true, checked: true, hint: "Show tool calls.", onSelect: vi.fn() },
      { id: "icons", label: "Icons Only", checked: false, onSelect: vi.fn() },
      "separator",
      { id: "clear", label: "Clear", onSelect: vi.fn() },
    ]);
    fireEvent.click(button);
    const verbose = screen.getByRole("menuitemcheckbox", { name: "Show Verbose Output" });
    expect(verbose).toHaveAttribute("aria-checked", "true");
    expect(verbose).toHaveAttribute("title", "Show tool calls.");
    expect(screen.getByRole("menuitemradio", { name: "Icons Only" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("menuitem", { name: "Clear" }).querySelector(".phx-menu-check")).not.toBeNull();
  });

  it("reads an item's hint in Help, and keeps an unavailable item's name free of its reason", () => {
    const button = setup([
      { id: "verbose", label: "Show Verbose Output", hint: "Show tool calls.", onSelect: vi.fn() },
      { id: "after", label: "Restore After", disabled: true, hint: "This is the newest recorded change.", onSelect: vi.fn() },
      { id: "plain", label: "Clear", onSelect: vi.fn() },
    ]);
    fireEvent.click(button);
    expect(helpFor(screen.getByRole("menuitem", { name: "Show Verbose Output" }))).toBe("Show tool calls.");
    const after = screen.getByRole("menuitem", { name: "Restore After" });
    expect(after).toHaveAccessibleDescription("This is the newest recorded change.");
    expect(helpFor(after)).toBe("This is the newest recorded change.");
    expect(helpFor(screen.getByRole("menuitem", { name: "Clear" }))).toBe("Clear");
  });

  it("closes when the pointer presses outside", () => {
    const button = setup([{ id: "a", label: "A", onSelect: vi.fn() }]);
    fireEvent.click(button);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
