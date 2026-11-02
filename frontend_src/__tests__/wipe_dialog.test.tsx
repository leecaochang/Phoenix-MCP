/**
 * Behaviour: the typed WIPE confirmation is a plain UI2 dialog drawn over the
 * whole workspace (user, 2026-10-28): no coloured title or banner, only the
 * Wipe button is red, and it stays locked until WIPE is typed.
 */
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MenuRootContext } from "../ui/Menu";
import { WipeConfirmModal } from "../components/WipeConfirmModal";

vi.mock("../api", () => ({ api: { wipe: vi.fn() } }));

describe("WIPE dialog", () => {
  it("sits over the workspace, plain, with only Wipe in red", () => {
    const shell = document.createElement("div");
    document.body.appendChild(shell);
    try {
      const { container } = render(
        <MenuRootContext.Provider value={shell}>
          <div className="phx-window"><WipeConfirmModal scopes={{ core: true, providers: false, mesa: false }} onWiped={vi.fn()} onClose={vi.fn()} /></div>
        </MenuRootContext.Provider>,
      );
      const dialog = screen.getByRole("dialog");
      expect(shell.contains(dialog)).toBe(true);
      expect(container.contains(dialog)).toBe(false);
      expect(dialog.querySelector(".modal-title-error, .banner")).toBeNull();
      const buttons = [...dialog.querySelectorAll(".modal-actions button")];
      expect(buttons.filter((b) => b.classList.contains("btn-danger"))).toHaveLength(1);
      for (const b of buttons) expect(b.querySelector("svg")).not.toBeNull();
      const wipe = buttons.find((b) => b.classList.contains("btn-danger"))!;
      expect(wipe).toBeDisabled();
      fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "WIPE" } });
      expect(wipe).not.toBeDisabled();
    } finally {
      shell.remove();
    }
  });
});
