/**
 * Shell services shared by windows: the toolbar's tool registry, the Logs
 * selection that fills Log Details, the saved toolbar placement, the
 * toolbar's fit rule, and the right-click menus on panel chrome.
 */

import { describe, it, expect, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ToolRegistry, ToolRegistryContext, WindowPlaceContext, useToolbarTools, type ToolGroup } from "../shell/tools";
import { LogDetailsContent, LogSelection, LogSelectionContext } from "../shell/logDetails";
import { DEFAULT_TOOLBAR, readToolbar, toolbarVertical, withToolbarDock } from "../shell/storage";
import { FIT_STEPS, chooseFit, rowFits, singleRowFits } from "../shell/toolbarFit";
import { auditEntryKey } from "../components/AuditEntry";
import { ConfigTable } from "../ui/table/ConfigTable";
import { ContextMenuHost, useContextMenu, type MenuEntry } from "../ui/Menu";
import type { AuditEntry } from "../types";

const group = (id: string, toolLabel: string, onSelect = vi.fn()): ToolGroup => ({
  id, label: id, tools: [{ id: "refresh", label: toolLabel, icon: "refresh", onSelect }],
});

function entry(over: Partial<AuditEntry> = {}): AuditEntry {
  return {
    request_id: "r1",
    timestamp: "2026-01-01T00:00:00Z",
    access_key_id: "t1",
    access_key_name: "kitchen",
    method: "tools/call",
    resource: "get_state",
    outcome: "allowed",
    client_ip: "127.0.0.1",
    pass_through: false,
    ...over,
  };
}

describe("tools several windows share", () => {
  const logTools = (window: string, opts: { pressed?: boolean; badge?: number; onSelect?: () => void; onSet?: (on: boolean) => void; clear?: boolean } = {}) => [{
    id: window,
    label: window,
    tools: [
      { id: "refresh", label: `Refresh ${window}`, short: "Refresh", icon: "refresh" as const, help: "help.refresh", shared: { id: "refresh", label: "Refresh" }, onSelect: opts.onSelect },
      { id: "filters", label: "Filters", icon: "filter" as const, pressed: opts.pressed ?? false, badge: opts.badge,
        shared: { id: "filters", label: "Filters", countLabel: "logs.filtersCount" }, onSet: opts.onSet },
      ...(opts.clear ? [{ id: "clear", label: "Clear Conversation History", short: "Clear History", icon: "delete" as const }] : []),
    ],
  }];

  it("shows a shared tool once, ahead of each window's own tools, adding up badges", () => {
    const registry = new ToolRegistry();
    registry.set({ workspace: "logs", window: "auditLog" }, logTools("auditLog", { badge: 1 }));
    registry.set({ workspace: "logs", window: "conversationLog" }, logTools("conversationLog", { badge: 2, clear: true }));
    const groups = registry.groupsFor("logs", ["auditLog", "conversationLog"], "Logs");
    expect(groups.map((g) => [g.label, g.tools.map((tool) => tool.short ?? tool.label)])).toEqual([
      ["Logs", ["Refresh", "Filters"]],
      ["conversationLog", ["Clear History"]],
    ]);
    expect(groups[0].tools[1]).toMatchObject({ badge: 3, label: "Filters (3)", pressed: false });
    expect(groups[0].tools[0].help).toBe("help.refresh");
  });

  it("keeps a tool in its own window's group while only one window offers it", () => {
    const registry = new ToolRegistry();
    registry.set({ workspace: "logs", window: "auditLog" }, logTools("auditLog"));
    const groups = registry.groupsFor("logs", ["auditLog"], "Logs");
    expect(groups.map((g) => g.id)).toEqual(["auditLog"]);
    expect(groups[0].tools[0].label).toBe("Refresh auditLog");
  });

  it("runs a shared tool in every window, and turns a shared toggle the same way everywhere", () => {
    const registry = new ToolRegistry();
    const refreshA = vi.fn(), refreshB = vi.fn(), setA = vi.fn(), setB = vi.fn();
    registry.set({ workspace: "logs", window: "auditLog" }, logTools("auditLog", { onSelect: refreshA, onSet: setA, pressed: true }));
    registry.set({ workspace: "logs", window: "conversationLog" }, logTools("conversationLog", { onSelect: refreshB, onSet: setB }));
    const order = ["auditLog", "conversationLog"];
    registry.runShared("logs", order, "refresh");
    expect(refreshA).toHaveBeenCalledOnce();
    expect(refreshB).toHaveBeenCalledOnce();
    // One panel is open, so the shared button reads as on and closes both.
    expect(registry.groupsFor("logs", order, "Logs")[0].tools[1].pressed).toBe(true);
    registry.runShared("logs", order, "filters");
    expect(setA).toHaveBeenCalledWith(false);
    expect(setB).toHaveBeenCalledWith(false);
  });
});

describe("toolbar tool registry", () => {
  it("lists each open window's tools in reading order and skips windows with none", () => {
    const registry = new ToolRegistry();
    registry.set({ workspace: "logs", window: "conversationLog" }, [group("conversation", "Refresh Conversation Log")]);
    registry.set({ workspace: "logs", window: "auditLog" }, [group("audit", "Refresh Audit Log")]);
    registry.set({ workspace: "logs", window: "logDetails" }, [{ id: "details", label: "details", tools: [] }]);
    registry.set({ workspace: "settings", window: "settings" }, [group("settings", "Elsewhere")]);
    const order = (ids: string[]) => registry.groupsFor("logs", ids).map((g) => g.id);
    expect(order(["auditLog", "conversationLog", "logDetails"])).toEqual(["audit", "conversation"]);
    expect(order(["conversationLog", "auditLog"])).toEqual(["conversation", "audit"]);
    // A closed window is left out of the order, so its tools disappear with it.
    expect(order(["auditLog"])).toEqual(["audit"]);
  });

  it("redraws only when a label or state changes, but always runs the newest handler", () => {
    const registry = new ToolRegistry();
    const listener = vi.fn();
    registry.subscribe(listener);
    const place = { workspace: "logs", window: "auditLog" };
    const first = vi.fn();
    const second = vi.fn();
    registry.set(place, [group("audit", "Refresh")]);
    registry.set(place, [group("audit", "Refresh", first)]);
    registry.set(place, [group("audit", "Refresh", second)]);
    expect(listener).toHaveBeenCalledTimes(1);
    registry.latest("logs", "audit", "refresh")?.onSelect?.();
    expect(second).toHaveBeenCalledOnce();
    expect(first).not.toHaveBeenCalled();
    registry.set(place, [{ ...group("audit", "Refresh"), tools: [{ id: "refresh", label: "Refresh", icon: "refresh", disabled: true }] }]);
    expect(listener).toHaveBeenCalledTimes(2);
    registry.set(place, null);
    expect(listener).toHaveBeenCalledTimes(3);
    expect(registry.latest("logs", "audit", "refresh")).toBeUndefined();
  });

  it("offers tools only inside a shell window and withdraws them when the view goes away", () => {
    const registry = new ToolRegistry();
    const seen: boolean[] = [];
    function View() {
      seen.push(useToolbarTools([group("audit", "Refresh Audit Log")]));
      return null;
    }
    render(<View />);
    expect(seen[seen.length - 1]).toBe(false);
    const { unmount } = render(
      <ToolRegistryContext.Provider value={registry}>
        <WindowPlaceContext.Provider value={{ workspace: "logs", window: "auditLog" }}>
          <View />
        </WindowPlaceContext.Provider>
      </ToolRegistryContext.Provider>,
    );
    expect(seen[seen.length - 1]).toBe(true);
    expect(registry.groupsFor("logs", ["auditLog"]).map((g) => g.id)).toEqual(["audit"]);
    unmount();
    expect(registry.groupsFor("logs", ["auditLog"])).toEqual([]);
  });
});

describe("Log Details", () => {
  const entries = [entry({ request_id: "a" }), entry({ request_id: "b" }), entry({ request_id: "c" })];
  const nameOf = (e: AuditEntry) => e.access_key_name;

  it("brings Log Details into view on a selection, but not while stepping through records", () => {
    const selection = new LogSelection();
    const reveal = vi.fn();
    selection.onSelect = reveal;
    selection.set({ kind: "audit", entry: entries[0], ordered: entries, nameOf });
    expect(reveal).toHaveBeenCalledOnce();
    selection.step(1);
    expect(selection.get()?.entry).toBe(entries[1]);
    selection.step(1);
    selection.step(1);
    expect(selection.get()?.entry).toBe(entries[2]);
    selection.step(-1);
    expect(selection.get()?.entry).toBe(entries[1]);
    expect(reveal).toHaveBeenCalledOnce();
  });

  it("selecting a row fills Log Details instead of opening a dialog, and marks the row", () => {
    const selection = new LogSelection();
    function Logs() {
      return (
        <LogSelectionContext.Provider value={selection}>
          <ConfigTable id="audit" label="Audit Log" columns={[{ id: "resource", label: "Resource", cell: (e: AuditEntry) => e.request_id }]}
            rows={entries} rowKey={auditEntryKey}
            onSelect={(e, ordered) => selection.set({ kind: "audit", entry: e, ordered, nameOf })} selectedKey={selection.selectedKey("audit")} />
          <LogDetailsContent />
        </LogSelectionContext.Provider>
      );
    }
    const { rerender } = render(<Logs />);
    expect(screen.getByText("Select a record in a log to see its details.")).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    fireEvent.click(rows[1]);
    rerender(<Logs />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("heading", { name: "Audit Entry" })).toBeInTheDocument();
    expect(screen.getAllByRole("row").slice(1)[1]).toHaveClass("is-selected");
    expect(screen.getAllByRole("row").slice(1)[1]).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Previous Record" })).not.toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: "Next Record" })).not.toHaveAttribute("aria-disabled", "true");
    fireEvent.click(screen.getByRole("button", { name: "Next Record" }));
    expect(screen.getByRole("button", { name: "Next Record" })).toHaveAttribute("aria-disabled", "true");
  });
});

describe("saved toolbar placement", () => {
  it("keeps valid values and replaces anything else with the defaults", () => {
    expect(readToolbar(undefined)).toEqual(DEFAULT_TOOLBAR);
    // Icons and text unless the user chose otherwise (user, 2026-11-05).
    expect(DEFAULT_TOOLBAR.mode).toBe("icon-text");
    expect(readToolbar({ mode: "icons" }).mode).toBe("icons");
    // A saved choice to hide the tools is dropped with the button that made it (user, 2026-11-01).
    expect(readToolbar({ dock: "left", x: 40, y: 50, collapsed: true, toolsShown: false, mode: "icon-text", vertical: true, singleRow: true }))
      .toEqual({ dock: "left", x: 40, y: 50, collapsed: true, mode: "icon-text", vertical: true, singleRow: true });
    expect(readToolbar({ dock: "sideways", x: -5, y: Number.NaN, collapsed: "yes", toolsShown: 1, mode: "huge", vertical: "up", singleRow: "yes" })).toEqual(DEFAULT_TOOLBAR);
  });

  it("keeps the orientation a docked toolbar had when it floats, and follows the edge when docked", () => {
    const at = (dock: "top" | "bottom" | "left" | "right" | "floating", vertical = false) => ({ ...DEFAULT_TOOLBAR, dock, vertical });
    expect(withToolbarDock(at("left"), "floating")).toMatchObject({ dock: "floating", vertical: true });
    expect(withToolbarDock(at("right"), "floating")).toMatchObject({ dock: "floating", vertical: true });
    expect(withToolbarDock(at("top", true), "floating")).toMatchObject({ dock: "floating", vertical: false });
    expect(toolbarVertical(at("floating", true))).toBe(true);
    expect(toolbarVertical(withToolbarDock(at("floating", true), "top"))).toBe(false);
    expect(toolbarVertical(withToolbarDock(at("floating", false), "left"))).toBe(true);
  });
});

describe("toolbar fit rule", () => {
  /** A row whose items report the given boxes, as [top, height] pairs. */
  function row(boxes: Array<[number, number]>): HTMLElement {
    const el = document.createElement("div");
    for (const [top, height] of boxes) {
      const item = document.createElement("button");
      item.getBoundingClientRect = () => ({ top, bottom: top + height, height, width: height ? 40 : 0, left: 0, right: 40, x: 0, y: top, toJSON: () => ({}) });
      el.appendChild(item);
    }
    return el;
  }

  it("counts items of different heights centred on one line as fitting, and a second line as not", () => {
    expect(rowFits(row([[5, 36], [0, 46], [5, 36]]))).toBe(true);
    expect(rowFits(row([[5, 36], [0, 46], [50, 46]]))).toBe(false);
  });

  it("ignores items without a box, so a row it cannot measure counts as fitting", () => {
    expect(rowFits(row([[0, 0], [0, 0]]))).toBe(true);
    expect(rowFits(row([[0, 46], [80, 0]]))).toBe(true);
  });

  it("counts a one-row toolbar as fitting only with every button on one line and nothing past its end", () => {
    const body = (boxes: Array<[number, number]>, scroll = 900, client = 1000) => {
      const el = row(boxes);
      for (const b of el.children) b.className = "phx-toolbar-button";
      Object.defineProperty(el, "scrollWidth", { value: scroll });
      Object.defineProperty(el, "clientWidth", { value: client });
      return el;
    };
    expect(singleRowFits(body([[5, 36], [0, 46], [5, 36]]))).toBe(true);
    expect(singleRowFits(body([[5, 36], [50, 46]]))).toBe(false);
    expect(singleRowFits(body([[5, 36], [0, 46]], 1200, 1000))).toBe(false);
  });

  it("tries each step from full text and keeps the first that fits", () => {
    const applied: number[] = [];
    let step = -1;
    expect(chooseFit((s) => { applied.push(s); step = s; }, () => step >= 2)).toBe(2);
    expect(applied).toEqual([0, 1, 2]);
  });

  it("ends on the last step when nothing fits", () => {
    const applied: number[] = [];
    expect(chooseFit((s) => { applied.push(s); }, () => false)).toBe(FIT_STEPS);
    expect(applied[applied.length - 1]).toBe(FIT_STEPS);
    expect(applied).toHaveLength(FIT_STEPS + 1);
  });
});

describe("right-click menus", () => {
  function Chrome({ entries }: { entries: MenuEntry[] }) {
    const show = useContextMenu();
    return <button type="button" onContextMenu={(e) => show?.(e, "Toolbar menu", entries)}>Toolbar</button>;
  }

  it("opens at the pointer instead of the browser's menu and marks the current choice", () => {
    const pick = vi.fn();
    render(
      <ContextMenuHost>
        <Chrome entries={[
          { id: "icons", label: "Icons Only", checked: true, onSelect: vi.fn() },
          { id: "text", label: "Icons and Text", checked: false, onSelect: pick },
        ]} />
      </ContextMenuHost>,
    );
    const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 120, clientY: 80 });
    act(() => { screen.getByRole("button", { name: "Toolbar" }).dispatchEvent(event); });
    expect(event.defaultPrevented).toBe(true);
    const menu = screen.getByRole("menu", { name: "Toolbar menu" });
    expect(menu.style.top).toBe("80px");
    expect(screen.getByRole("menuitemradio", { name: /Icons Only/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("menuitemradio", { name: /Icons and Text/ })).toHaveAttribute("aria-checked", "false");
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Icons and Text/ }));
    expect(pick).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("opens from the keyboard's menu key on the first item and returns focus on Escape", () => {
    render(<ContextMenuHost><Chrome entries={[{ id: "a", label: "Dock at Left", onSelect: vi.fn() }]} /></ContextMenuHost>);
    const button = screen.getByRole("button", { name: "Toolbar" });
    button.focus();
    fireEvent.contextMenu(button, { clientX: 0, clientY: 0 });
    expect(document.activeElement).toHaveTextContent("Dock at Left");
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it("leaves the browser's menu alone when there is nothing to offer", () => {
    render(<ContextMenuHost><Chrome entries={[]} /></ContextMenuHost>);
    const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 10, clientY: 10 });
    act(() => { screen.getByRole("button", { name: "Toolbar" }).dispatchEvent(event); });
    expect(event.defaultPrevented).toBe(false);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
