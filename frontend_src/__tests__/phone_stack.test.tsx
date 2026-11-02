/**
 * The phone stack: windows follow the saved order, with windows the order does
 * not list keeping their default place; Move Up and Move Down swap a window
 * with its neighbor on screen and leave closed windows where they were; Close
 * records the window as closed and Workspace Options offers it back; Maximize
 * shows one window and keeps the others mounted; folding and the per-window
 * labels choice are saved; Add to My Workspace is in the window menu, not the
 * title bar (user, 2026-11-02); the record keeps phone stacks through reading
 * and section merges; Help and Notification History are not stacked on a
 * phone and the open Guide comes last; and a phone is told from a tablet by its touch-only
 * screen's shorter side, so a phone keeps the stack in landscape.
 */
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { PhoneStack, closedPhoneWindows } from "../shell/PhoneStack";
import { movedOrder, phoneOrder, readPhoneLayout, type PhoneLayout } from "../shell/phoneLayout";
import { SHELL_SCHEMA, parseRecord, type ShellRecord } from "../shell/storage";
import { mergeRecords } from "../shell/sync";
import { MenuRootContext } from "../ui/Menu";
import { isPhone } from "../shell/Shell";

vi.mock("../shell/workspaces", async (original) => {
  const real = await original<typeof import("../shell/workspaces")>();
  // Each window shows its id, so the stack's order and visibility can be read without the real screens.
  const content = new Proxy({}, { get: (_, id) => () => <p data-testid={`content-${String(id)}`}>{String(id)}</p> });
  return { ...real, WINDOW_CONTENT: content };
});

const LOGS = ["auditLog", "conversationLog", "logDetails"];

function stack(layout: PhoneLayout | undefined, extra: Partial<Parameters<typeof PhoneStack>[0]> = {}) {
  const onLayout = vi.fn();
  const onMaximize = vi.fn();
  const view = render(
    <MenuRootContext.Provider value={document.body}>
      <PhoneStack workspace="logs" layout={layout} onLayout={onLayout} onMaximize={onMaximize} showAttention={false} settings={null} {...extra} />
    </MenuRootContext.Provider>,
  );
  return { onLayout, onMaximize, view };
}
const lastLayout = (fn: ReturnType<typeof vi.fn>): PhoneLayout => fn.mock.calls[fn.mock.calls.length - 1][0] as PhoneLayout;
const shownIds = (container: HTMLElement) =>
  [...container.querySelectorAll<HTMLElement>(".phx-phone-body")].map((b) => b.dataset.phxWindow);
function openMenu(container: HTMLElement, id: string) {
  const button = container.querySelector<HTMLButtonElement>(`[data-phx-window-menu="${id}"]`)!;
  act(() => { fireEvent.click(button); });
  return screen.getByRole("menu");
}

describe("phone order", () => {
  it("keeps the saved order and puts an unlisted window after its default predecessor", () => {
    // d comes after c by default, so it follows c; b follows a.
    expect(phoneOrder(["a", "b", "c", "d"], ["c", "a"])).toEqual(["c", "d", "a", "b"]);
    // A saved id that no longer exists is ignored; a and b have no listed predecessor, so they lead.
    expect(phoneOrder(["a", "b", "c"], ["c", "gone"])).toEqual(["a", "b", "c"]);
    expect(phoneOrder(["a", "b"], undefined)).toEqual(["a", "b"]);
  });

  it("moves past the neighbor on screen and leaves closed windows in place", () => {
    // b is closed: moving c up swaps it with a, the window above it on screen.
    expect(movedOrder(["a", "b", "c"], ["a", "c"], "c", -1)).toEqual(["c", "b", "a"]);
    expect(movedOrder(["a", "b"], ["a", "b"], "a", -1)).toBeNull();
    expect(movedOrder(["a", "b"], ["a", "b"], "b", 1)).toBeNull();
  });

  it("reads saved layouts leniently, dropping anything malformed", () => {
    expect(readPhoneLayout({ order: ["auditLog", 3, "x y", "auditLog"], folded: "no", closed: ["logDetails"] }))
      .toEqual({ order: ["auditLog"], closed: ["logDetails"] });
    expect(readPhoneLayout(null)).toEqual({});
  });
});

describe("phone stack", () => {
  it("starts with the windows the desktop leaves closed closed, and offers them back", () => {
    const { view } = stack(undefined);
    // Log Settings starts closed in Logs, as it does on a desktop.
    expect(shownIds(view.container)).toEqual(LOGS);
    expect(closedPhoneWindows("logs", undefined, [])).toEqual(["settingsLogs"]);
    expect(closedPhoneWindows("logs", { closed: [] }, [])).toEqual([]);
  });

  it("follows the saved order", () => {
    const { view } = stack({ order: ["logDetails", "auditLog"], closed: [] });
    // Log Settings, not in the saved order, follows Log Details as it does by default.
    expect(shownIds(view.container)).toEqual(["logDetails", "settingsLogs", "auditLog", "conversationLog"]);
  });

  it("moves a window down and saves the whole order", () => {
    const { view, onLayout } = stack(undefined);
    const menu = openMenu(view.container, "auditLog");
    expect(within(menu).getByRole("menuitem", { name: "Move Up" }).getAttribute("aria-disabled")).toBe("true");
    act(() => { fireEvent.click(within(menu).getByRole("menuitem", { name: "Move Down" })); });
    const saved = lastLayout(onLayout);
    expect(phoneOrder(LOGS, saved.order)).toEqual(["conversationLog", "auditLog", "logDetails"]);
    // The defaults come along, so the record holds a complete arrangement.
    expect(saved.closed).toEqual(["settingsLogs"]);
  });

  it("closes a window into the arrangement", () => {
    const { view, onLayout } = stack(undefined);
    const menu = openMenu(view.container, "conversationLog");
    act(() => { fireEvent.click(within(menu).getByRole("menuitem", { name: "Close" })); });
    expect((lastLayout(onLayout)).closed).toEqual(["settingsLogs", "conversationLog"]);
  });

  it("folds from the title bar and saves the fold", () => {
    const { view, onLayout } = stack(undefined);
    const fold = view.container.querySelector<HTMLButtonElement>(".phx-phone-fold")!;
    act(() => { fireEvent.click(fold); });
    expect((lastLayout(onLayout)).folded).toEqual(["auditLog"]);
  });

  it("shows only the maximized window and keeps the others mounted", () => {
    const { view } = stack({ closed: [] }, { maximized: "logDetails" });
    const root = view.container.querySelector(".phx-phone-stack")!;
    expect(root.classList.contains("has-maximized")).toBe(true);
    expect(view.container.querySelector(".phx-phone-window.is-maximized [data-phx-window]")?.getAttribute("data-phx-window")).toBe("logDetails");
    // The rest stay in the document, hidden by the stylesheet, so their drafts and scroll survive.
    expect(screen.getByTestId("content-auditLog")).toBeTruthy();
  });

  it("asks to maximize from the menu and unfolds a folded window first", () => {
    const { view, onLayout, onMaximize } = stack({ folded: ["auditLog"], closed: [] });
    const menu = openMenu(view.container, "auditLog");
    act(() => { fireEvent.click(within(menu).getByRole("menuitem", { name: "Maximize" })); });
    expect(onMaximize).toHaveBeenCalledWith("auditLog");
    expect((lastLayout(onLayout)).folded).toEqual([]);
  });

  it("saves Always Show Icons and Text for one window", () => {
    const { view, onLayout } = stack(undefined);
    const menu = openMenu(view.container, "logDetails");
    act(() => { fireEvent.click(within(menu).getByRole("menuitemcheckbox", { name: "Always Show Icons and Text" })); });
    expect((lastLayout(onLayout)).labels).toEqual(["logDetails"]);
  });

  it("offers Add to My Workspace in the window menu, with no + in the title bar", () => {
    const onAddToMy = vi.fn();
    const { view } = stack(undefined, { onAddToMy, myWindows: ["auditLog"] });
    expect(view.container.querySelector('[aria-label="Add to My Workspace"]')).toBeNull();
    const added = within(openMenu(view.container, "auditLog")).getByRole("menuitem", { name: "Add to My Workspace" });
    expect(added.getAttribute("aria-disabled")).toBe("true");
    act(() => { fireEvent.keyDown(added, { key: "Escape" }); });
    const menu = openMenu(view.container, "logDetails");
    act(() => { fireEvent.click(within(menu).getByRole("menuitem", { name: "Add to My Workspace" })); });
    expect(onAddToMy).toHaveBeenCalledWith("logDetails");
  });

  it("takes a window out of My Workspace when it is closed there", () => {
    const onRemoveFromMy = vi.fn();
    render(
      <MenuRootContext.Provider value={document.body}>
        <PhoneStack workspace="my" onLayout={vi.fn()} onMaximize={vi.fn()} showAttention={false} settings={null}
          myWindows={["auditLog"]} onRemoveFromMy={onRemoveFromMy} />
      </MenuRootContext.Provider>,
    );
    act(() => { fireEvent.click(screen.getByRole("button", { name: "Close" })); });
    expect(onRemoveFromMy).toHaveBeenCalledWith("auditLog");
  });
});

describe("Help, Notification History and the Guide on a phone", () => {
  it("never stacks Help or Notification History, whatever the arrangement says", () => {
    const { view } = stack({ closed: [], order: ["help", "notifications", "auditLog"] });
    expect(shownIds(view.container)).toEqual([...LOGS, "settingsLogs"]);
    expect(closedPhoneWindows("logs", { closed: ["help", "notifications"] }, [])).toEqual([]);
  });

  it("puts the open Guide at the bottom of the stack", () => {
    const { view } = stack(undefined, { guide: <div data-testid="guide" /> });
    expect(view.container.querySelector(".phx-phone-stack")!.lastElementChild).toBe(screen.getByTestId("guide"));
  });
});

describe("phone stacks in the record", () => {
  const base = (patch: Partial<ShellRecord> = {}): ShellRecord => ({ schema: SHELL_SCHEMA, savedAt: 0, workspaces: {}, ...patch });

  it("reads phone stacks and their My Defaults back", () => {
    const parsed = parseRecord({ ...base(), phone: { logs: { order: ["logDetails"] }, nope: {} }, phoneDefaults: { my: { windows: ["auditLog"] } } });
    expect(parsed && parsed !== "newer" ? parsed.phone : null).toEqual({ logs: { order: ["logDetails"] } });
    expect(parsed && parsed !== "newer" ? parsed.phoneDefaults : null).toEqual({ my: { windows: ["auditLog"] } });
  });

  it("merges one workspace's stack without touching another's", () => {
    const stored = base({ phone: { logs: { closed: ["auditLog"] }, mesa: { folded: ["x"] } } });
    const local = base({ phone: { logs: { closed: [] } } });
    const merged = mergeRecords(stored, local, new Set(["phone:logs"]), "w");
    expect(merged.phone).toEqual({ logs: { closed: [] }, mesa: { folded: ["x"] } });
    // A reset removes the workspace's stack from the shared copy too.
    expect(mergeRecords(stored, base(), new Set(["phone:logs", "phone:mesa"]), "w").phone).toBeUndefined();
  });
});

describe("telling a phone from a tablet", () => {
  const device = (touchOnly: boolean, width: number, height: number) =>
    ({ matchMedia: () => ({ matches: touchOnly }), screen: { width, height } }) as unknown as Window;

  it("holds a phone to the stack in either orientation", () => {
    expect(isPhone(device(true, 393, 852))).toBe(true);
    expect(isPhone(device(true, 852, 393))).toBe(true);
  });

  it("leaves tablets and desktops to the width rule", () => {
    // An iPad mini in landscape, an iPad Air in portrait, and a desktop with a mouse.
    expect(isPhone(device(true, 1133, 744))).toBe(false);
    expect(isPhone(device(true, 820, 1180))).toBe(false);
    expect(isPhone(device(false, 393, 852))).toBe(false);
  });
});
