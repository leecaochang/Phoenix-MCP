/**
 * Behaviour: the typed WIPE confirmation is a plain dialog drawn over the
 * whole workspace: no coloured title or banner, only the
 * Wipe button is red, and it stays locked until WIPE is typed.
 */
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MenuRootContext } from "../ui/Menu";
import { WipeConfirmModal } from "../components/WipeConfirmModal";
import { api } from "../api";
import { setAgentCliOwner, setSessionTurns } from "../utils/agentcli_state";

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

describe("what a wipe does to this browser's chat", () => {
  const confirm = async (scopes: { core: boolean; providers: boolean; mesa: boolean }) => {
    const onWiped = vi.fn();
    vi.mocked(api.wipe).mockResolvedValue({} as never);
    render(<WipeConfirmModal scopes={scopes} onWiped={onWiped} onClose={vi.fn()} />);
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "WIPE" } });
    fireEvent.click(dialog.querySelector(".btn-danger")!);
    await waitFor(() => expect(onWiped).toHaveBeenCalled());
  };
  const chatKeys = () => Object.keys(localStorage).filter((key) => key.startsWith("phx-agentcli-conversation"));

  it("goes with a wipe of the core data", async () => {
    setSessionTurns([{ text: "tool output" }]);
    await confirm({ core: true, providers: false, mesa: false });
    expect(chatKeys()).toEqual([]);
  });

  it("stays through a wipe of something else", async () => {
    setAgentCliOwner(null);
    setAgentCliOwner("test-admin");
    setSessionTurns([{ text: "tool output" }]);
    await confirm({ core: false, providers: true, mesa: true });
    expect(chatKeys()).toHaveLength(1);
  });
});
