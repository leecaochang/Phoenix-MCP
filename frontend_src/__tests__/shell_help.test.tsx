/**
 * The Help window and Show Help Bar: what Help says for the control under the
 * pointer or in focus, the saved preference with its per-workspace overrides,
 * and the controller keeping Help open or closed whatever the arrangement says.
 */

import { describe, it, expect, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { DockviewReact, type AddPanelOptions, type DockviewApi } from "dockview-react";
import { HELP_WINDOW, HelpContext, HelpTracker, HelpWindow, helpFor } from "../shell/help";
import { DEFAULT_HELP, helpOpen, parseRecord, readHelp, SHELL_SCHEMA, withHelpIn } from "../shell/storage";
import { mergeRecords } from "../shell/sync";
import { WorkspaceController } from "../shell/dock/controller";

describe("what Help says", () => {
  it("uses a data-help catalog key, else the control's accessible name, and ignores plain content", () => {
    document.body.innerHTML = `
      <div data-help="help.fold"><span id="inside">text</span></div>
      <button id="named" aria-label="Save Now"><svg></svg></button>
      <button id="text">Restore</button>
      <label>Retention days <input id="field"></label>
      <div id="row" tabindex="0">a whole row of text that is not a label</div>
      <p id="plain">Just a paragraph.</p>
      <button id="unknown" data-help="help.noSuchKey">X</button>`;
    const el = (id: string) => document.getElementById(id);
    expect(helpFor(el("inside"))).toBe("Fold this window into its title bar, or unfold it.");
    expect(helpFor(el("named"))).toBe("Save Now");
    expect(helpFor(el("text"))).toBe("Restore");
    expect(helpFor(el("field"))).toBe("Retention days");
    expect(helpFor(el("row"))).toBeNull();
    expect(helpFor(el("plain"))).toBeNull();
    expect(helpFor(el("unknown"))).toBeNull();
  });

  it("follows the pointer, falls back to the focused control, and keeps its text while the pointer is on Help", () => {
    const tracker = new HelpTracker();
    render(
      <HelpContext.Provider value={tracker}>
        <div data-testid="root">
          <button type="button" aria-label="First" />
          <button type="button" aria-label="Second" />
          <p data-testid="space">space</p>
          <div data-phx-window={HELP_WINDOW}><HelpWindow /></div>
        </div>
      </HelpContext.Provider>,
    );
    const stop = tracker.attach(screen.getByTestId("root"));
    const help = () => document.querySelector(".phx-help-text")!.textContent;
    expect(help()).toBe("Point at or focus a control to see what it does.");
    act(() => { fireEvent.focusIn(screen.getByLabelText("First")); });
    expect(help()).toBe("First");
    act(() => { fireEvent.pointerOver(screen.getByLabelText("Second")); });
    expect(help()).toBe("Second");
    act(() => { fireEvent.pointerOver(document.querySelector(".phx-help-text")!); });
    expect(help()).toBe("Second");
    act(() => { fireEvent.pointerOver(screen.getByTestId("space")); });
    expect(help()).toBe("First");
    act(() => { fireEvent.focusOut(screen.getByLabelText("First"), { relatedTarget: null }); });
    expect(help()).toBe("Point at or focus a control to see what it does.");
    stop();
  });
});

describe("Show Help Bar", () => {
  it("applies everywhere unless one workspace was opened or closed against it", () => {
    expect(helpOpen(DEFAULT_HELP, "logs")).toBe(true);
    const closedInLogs = withHelpIn(DEFAULT_HELP, "logs", false);
    expect(closedInLogs).toEqual({ show: true, workspaces: { logs: false } });
    expect(helpOpen(closedInLogs, "logs")).toBe(false);
    expect(helpOpen(closedInLogs, "mesa")).toBe(true);
    // Reopening where the global setting already shows it removes the override.
    expect(withHelpIn(closedInLogs, "logs", true)).toEqual(DEFAULT_HELP);
    const off = { show: false, workspaces: {} };
    expect(withHelpIn(off, "mesa", true)).toEqual({ show: false, workspaces: { mesa: true } });
  });

  it("is saved with the record, read back defensively and merged as its own section", () => {
    expect(readHelp({ show: false, workspaces: { logs: true, bogus: true, mesa: "yes" } })).toEqual({ show: false, workspaces: { logs: true } });
    expect(readHelp("nonsense")).toEqual(DEFAULT_HELP);
    const parsed = parseRecord({ schema: SHELL_SCHEMA, savedAt: 1, workspaces: {}, help: { show: false } });
    expect(parsed).toMatchObject({ help: { show: false, workspaces: {} } });
    const stored = { schema: SHELL_SCHEMA, savedAt: 1, rev: 2, workspaces: {}, help: { show: true, workspaces: {} } };
    const local = { schema: SHELL_SCHEMA, savedAt: 1, workspaces: {}, help: { show: false, workspaces: {} } };
    expect(mergeRecords(stored, local, new Set(["help"]), "w").help).toEqual({ show: false, workspaces: {} });
    expect(mergeRecords(stored, local, new Set(["toolbar"]), "w").help).toEqual({ show: true, workspaces: {} });
  });
});

describe("a window the shell keeps open or closed", () => {
  const WINDOWS = ["log", HELP_WINDOW];
  const arrange = (_: DockviewApi, add: (id: string, position?: AddPanelOptions["position"]) => void) => {
    add("log");
    add(HELP_WINDOW, { direction: "below" });
  };
  const components = Object.fromEntries(WINDOWS.map((id) => [id, () => <div>{id}</div>]));
  function mount(open: boolean) {
    const controller = new WorkspaceController("logs", WINDOWS, arrange, (id) => id);
    controller.want(HELP_WINDOW, open);
    render(<DockviewReact components={components} onReady={(e) => { controller.api = e.api; controller.load(undefined); }} />);
    return controller;
  }

  it("closes Help when it is not wanted, even after Restore Original Layout, and offers it to reopen", () => {
    const c = mount(false);
    expect(c.hostOf(HELP_WINDOW)).toBeNull();
    c.restoreOriginal();
    expect(c.hostOf(HELP_WINDOW)).toBeNull();
    expect(c.closedWindows()).toEqual([HELP_WINDOW]);
    c.want(HELP_WINDOW, true);
    expect(c.hostOf(HELP_WINDOW)).toBe("main");
  });

  it("tells the shell when the user closes or reopens a window, but not when the shell does", () => {
    const c = mount(true);
    const toggled = vi.fn();
    c.onWindowToggle = toggled;
    c.want(HELP_WINDOW, false);
    c.want(HELP_WINDOW, true);
    expect(toggled).not.toHaveBeenCalled();
    c.close(c.groupOf(HELP_WINDOW)!);
    expect(toggled).toHaveBeenLastCalledWith(HELP_WINDOW, false);
    c.reopen(HELP_WINDOW);
    expect(toggled).toHaveBeenLastCalledWith(HELP_WINDOW, true);
  });
});
