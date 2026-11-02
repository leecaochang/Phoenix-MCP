/**
 * Reset This Workspace puts every disclosure in a window back to how it opens
 * by default: closed, or open where it says data-default-open (user,
 * 2026-10-31). Agent Chat's conversation keeps its own.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { WorkspaceController } from "../shell/dock/controller";
import { ControllerContext } from "../shell/dock/TitleBar";
import { WindowBody } from "../shell/workspaces";

function reset(controller: WorkspaceController) {
  // The controller tells its windows at the start of a reset; without Dockview there is no arrangement to replace.
  for (const listener of [...controller["resetListeners"]]) listener();
}

describe("Reset This Workspace and disclosures", () => {
  it("closes opened disclosures and opens the ones that start open", () => {
    const controller = new WorkspaceController("test", ["x", "agentChat"], () => {}, (id) => id);
    const { getByTestId } = render(
      <ControllerContext.Provider value={controller}>
        <WindowBody id="x">
          <details data-testid="closed"><summary>A</summary></details>
          <details data-testid="open" data-default-open="" open><summary>B</summary></details>
        </WindowBody>
        <WindowBody id="agentChat">
          <details data-testid="chat"><summary>Thinking</summary></details>
        </WindowBody>
      </ControllerContext.Provider>,
    );
    const closed = getByTestId("closed") as HTMLDetailsElement;
    const open = getByTestId("open") as HTMLDetailsElement;
    const chat = getByTestId("chat") as HTMLDetailsElement;
    closed.open = true;
    open.open = false;
    chat.open = true;
    reset(controller);
    expect(closed.open).toBe(false);
    expect(open.open).toBe(true);
    expect(chat.open).toBe(true);
  });
});
