/**
 * Behaviour: every inline confirmation is a plain UI2 box (user, 2026-10-28):
 * a heading with no icon or coloured accent, the label unless a title is
 * given, and the explanation below it. A click anywhere else cancels it,
 * as Cancel does, unless the action is running. A finger that goes down to
 * scroll is not a click elsewhere, and the box scrolls itself into view, so a
 * box opened below an iPhone screen can be reached (user, 2026-11-02).
 */
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { InlineConfirm } from "../ui/InlineConfirm";
import { helpFor } from "../shell/help";

describe("InlineConfirm", () => {
  it("heads itself with the label, with no icon", () => {
    const { container } = render(
      <InlineConfirm label="Export MESA Profiles" confirmLabel="Export" cancelLabel="Cancel" onConfirm={vi.fn()} onCancel={vi.fn()}>
        <p>Writes every profile to a file.</p>
      </InlineConfirm>,
    );
    const group = screen.getByRole("group", { name: "Export MESA Profiles" });
    expect(group).toHaveClass("is-warning");
    expect(container.querySelector(".phx-inline-confirm-title")?.textContent).toBe("Export MESA Profiles");
    expect(container.querySelector(".phx-inline-confirm-title svg")).toBeNull();
    expect(container.querySelector(".phx-inline-confirm-body")?.textContent).toBe("Writes every profile to a file.");
  });

  it("uses a given title as the heading, keeping the label as its name", () => {
    const { container } = render(
      <InlineConfirm label="Restore" title="Restore Kitchen to its Before state?" tone="danger" confirmLabel="Restore" cancelLabel="Cancel"
        onConfirm={vi.fn()} onCancel={vi.fn()}>
        <p>This replaces its current state.</p>
      </InlineConfirm>,
    );
    expect(screen.getByRole("group", { name: "Restore" })).toHaveClass("is-danger");
    expect(container.querySelector(".phx-inline-confirm-title")?.textContent).toBe("Restore Kitchen to its Before state?");
  });

  it("cancels on a click anywhere else, but not on one inside it or while running", () => {
    const outside = document.createElement("div");
    document.body.appendChild(outside);
    try {
      const onCancel = vi.fn();
      const view = render(
        <InlineConfirm label="Rotate" confirmLabel="Rotate" cancelLabel="Cancel" onConfirm={vi.fn()} onCancel={onCancel}>
          <p>Issues a new secret.</p>
        </InlineConfirm>,
      );
      fireEvent.pointerDown(screen.getByText("Issues a new secret."));
      expect(onCancel).not.toHaveBeenCalled();
      fireEvent.pointerDown(outside);
      expect(onCancel).toHaveBeenCalledOnce();

      view.rerender(
        <InlineConfirm label="Rotate" confirmLabel="Rotate" cancelLabel="Cancel" busy onConfirm={vi.fn()} onCancel={onCancel}>
          <p>Issues a new secret.</p>
        </InlineConfirm>,
      );
      fireEvent.pointerDown(outside);
      expect(onCancel).toHaveBeenCalledOnce();
    } finally {
      outside.remove();
    }
  });

  it("reads its help for its own buttons and acknowledgement, where focus lands", () => {
    render(
      <InlineConfirm label="Delete" tone="danger" help="Covered entities fall back to the next profile." acknowledge="I understand"
        confirmLabel="Delete" cancelLabel="Cancel" onConfirm={vi.fn()} onCancel={vi.fn()} />,
    );
    for (const name of ["Delete", "Cancel"]) {
      expect(helpFor(screen.getByRole("button", { name }))).toBe("Covered entities fall back to the next profile.");
    }
    expect(helpFor(screen.getByRole("checkbox"))).toBe("Covered entities fall back to the next profile.");
  });

  it("waits for a tap rather than a touch, so scrolling to the box keeps it open", () => {
    const outside = document.createElement("div");
    document.body.appendChild(outside);
    try {
      const onCancel = vi.fn();
      render(<InlineConfirm label="Clear" confirmLabel="Clear" cancelLabel="Cancel" onConfirm={vi.fn()} onCancel={onCancel} />);
      // A scroll: the finger goes down and the browser cancels it, with no click.
      fireEvent.pointerDown(outside, { pointerType: "touch" });
      fireEvent.pointerCancel(outside, { pointerType: "touch" });
      expect(onCancel).not.toHaveBeenCalled();
      // A tap ends in a click, which closes the box.
      fireEvent.pointerDown(outside, { pointerType: "touch" });
      fireEvent.click(outside);
      expect(onCancel).toHaveBeenCalledOnce();
    } finally {
      outside.remove();
    }
  });

  it("scrolls the whole box into view when it opens", () => {
    const scrolled = vi.fn();
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrolled;
    try {
      render(<InlineConfirm label="Clear" confirmLabel="Clear" cancelLabel="Cancel" onConfirm={vi.fn()} onCancel={vi.fn()} />);
      expect(scrolled).toHaveBeenCalledWith({ block: "nearest" });
      expect(scrolled.mock.contexts[0]).toBe(screen.getByRole("group", { name: "Clear" }));
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });
});
