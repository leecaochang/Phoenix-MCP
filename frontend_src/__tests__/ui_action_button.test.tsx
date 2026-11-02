/** Window action buttons follow the toolbar's display mode: icon and label, or icon alone named by its label. */

import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ActionButton, DisplayModeContext } from "../ui/ActionButton";

describe("action button", () => {
  it("shows its label in icons-and-text mode and outside the shell", () => {
    render(<ActionButton icon="loadMore" label="Load More" onClick={() => {}} />);
    const button = screen.getByRole("button", { name: "Load More" });
    expect(button).toHaveTextContent("Load More");
    expect(button.querySelector("svg")).not.toBeNull();
  });

  it("shows only the icon in icons-only mode, still named and with a tooltip", () => {
    const onClick = vi.fn();
    render(<DisplayModeContext.Provider value="icons"><ActionButton icon="loadMore" label="Load More" onClick={onClick} /></DisplayModeContext.Provider>);
    const button = screen.getByRole("button", { name: "Load More" });
    expect(button).toHaveTextContent("");
    expect(button).toHaveAttribute("title", "Load More");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("does nothing while busy", () => {
    const onClick = vi.fn();
    render(<ActionButton icon="loadMore" label="Loading" busy onClick={onClick} />);
    fireEvent.click(screen.getByRole("button", { name: "Loading" }));
    expect(onClick).not.toHaveBeenCalled();
  });
});
