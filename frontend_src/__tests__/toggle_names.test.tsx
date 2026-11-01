/**
 * A switch's name flips it, as a label flips its checkbox (user, 2026-10-31):
 * the name's own text acts, the row's empty space and description do not, and
 * a disabled switch stays as it is.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { attachToggleNames } from "../ui/toggleNames";

function Row({ onChange, disabled = false }: { onChange: () => void; disabled?: boolean }) {
  return (
    <div className="toggle-row" data-testid="row">
      <div className="toggle-label">
        <span>Enable presets</span>
        <span className="sr-only">What presets do.</span>
        <small>A note under the name.</small>
      </div>
      <label className="toggle-switch">
        <input type="checkbox" aria-label="Enable presets" disabled={disabled} onChange={onChange} />
        <span className="toggle-switch-track" />
      </label>
    </div>
  );
}

describe("toggle names", () => {
  let stop: (() => void) | null = null;
  afterEach(() => { stop?.(); stop = null; });

  it("flips the switch from its name, and from nothing else in the row", () => {
    const onChange = vi.fn();
    const { container } = render(<Row onChange={onChange} />);
    stop = attachToggleNames(container);
    const box = screen.getByRole("checkbox", { name: "Enable presets" });
    fireEvent.click(screen.getByText("Enable presets"));
    expect(box).toBeChecked();
    expect(onChange).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("row"));
    fireEvent.click(container.querySelector(".toggle-label")!);
    fireEvent.click(screen.getByText("A note under the name."));
    fireEvent.click(screen.getByText("What presets do."));
    expect(box).toBeChecked();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("leaves a disabled switch alone", () => {
    const onChange = vi.fn();
    const { container } = render(<Row onChange={onChange} disabled />);
    stop = attachToggleNames(container);
    fireEvent.click(screen.getByText("Enable presets"));
    expect(screen.getByRole("checkbox", { name: "Enable presets" })).not.toBeChecked();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("does nothing once stopped", () => {
    const onChange = vi.fn();
    const { container } = render(<Row onChange={onChange} />);
    attachToggleNames(container)();
    fireEvent.click(screen.getByText("Enable presets"));
    expect(onChange).not.toHaveBeenCalled();
  });
});
