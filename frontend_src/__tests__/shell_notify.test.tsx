/**
 * Toasts and the Notifications window: a notice shows briefly and stays in the
 * session's history, the pointer or focus holds a toast on screen, and the
 * window clears one notice or all of them.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { HISTORY_LIMIT, Notifications, NotificationsContext, TOAST_MS, Toasts } from "../ui/notify";
import { NotificationsWindow } from "../shell/notifications";
import { ToolRegistry, ToolRegistryContext, WindowPlaceContext } from "../shell/tools";

describe("notification history", () => {
  it("keeps notices newest first, shows the latest as the toast, and caps the history", () => {
    const center = new Notifications();
    for (let i = 1; i <= HISTORY_LIMIT + 5; i++) center.notify(`notice ${i}`);
    expect(center.history).toHaveLength(HISTORY_LIMIT);
    expect(center.history[0].text).toBe(`notice ${HISTORY_LIMIT + 5}`);
    expect(center.toast?.text).toBe(`notice ${HISTORY_LIMIT + 5}`);
    center.dismissToast(center.history[1].id);
    expect(center.toast).not.toBeNull();
    center.dismissToast();
    expect(center.toast).toBeNull();
    expect(center.history).toHaveLength(HISTORY_LIMIT);
  });
});

describe("toasts", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("announces a notice and hides it after a few seconds", () => {
    const center = new Notifications();
    render(<Toasts center={center} />);
    const region = screen.getByRole("status");
    expect(region.textContent).toBe("");
    act(() => center.notify("Saved."));
    expect(region.textContent).toContain("Saved.");
    act(() => { vi.advanceTimersByTime(TOAST_MS - 100); });
    expect(region.textContent).toContain("Saved.");
    act(() => { vi.advanceTimersByTime(200); });
    expect(region.textContent).toBe("");
    expect(center.history.map((n) => n.text)).toEqual(["Saved."]);
  });

  it("stays while the pointer or focus is on it, and goes once both leave", () => {
    const center = new Notifications();
    render(<Toasts center={center} />);
    act(() => center.notify("Saved."));
    const toast = screen.getByText("Saved.").parentElement!;
    fireEvent.pointerEnter(toast);
    act(() => { vi.advanceTimersByTime(TOAST_MS * 3); });
    expect(center.toast).not.toBeNull();
    fireEvent.focus(screen.getByRole("button"));
    fireEvent.pointerLeave(toast);
    act(() => { vi.advanceTimersByTime(TOAST_MS * 3); });
    expect(center.toast).not.toBeNull();
    fireEvent.blur(screen.getByRole("button"));
    act(() => { vi.advanceTimersByTime(TOAST_MS); });
    expect(center.toast).toBeNull();
  });

  it("gives each new notice its full time and can be dismissed", () => {
    const center = new Notifications();
    render(<Toasts center={center} />);
    act(() => center.notify("First."));
    act(() => { vi.advanceTimersByTime(TOAST_MS - 500); });
    act(() => center.notify("Second."));
    act(() => { vi.advanceTimersByTime(TOAST_MS - 500); });
    expect(center.toast?.text).toBe("Second.");
    fireEvent.click(screen.getByRole("button"));
    expect(center.toast).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("");
  });
});

describe("the Notifications window", () => {
  it("lists notices with their time, clears one, and offers Clear All on the toolbar", () => {
    const center = new Notifications();
    center.notify("First.");
    center.notify("Second.");
    const registry = new ToolRegistry();
    render(
      <ToolRegistryContext.Provider value={registry}>
        <NotificationsContext.Provider value={center}>
          <WindowPlaceContext.Provider value={{ workspace: "logs", window: "notifications" }}>
            <NotificationsWindow />
          </WindowPlaceContext.Provider>
        </NotificationsContext.Provider>
      </ToolRegistryContext.Provider>,
    );
    expect(screen.getAllByRole("listitem").map((li) => li.querySelector("p")!.textContent)).toEqual(["Second.", "First."]);
    expect(screen.getAllByRole("listitem")[0].querySelector("time")).not.toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "Clear Notification" })[0]);
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    const [group] = registry.groupsFor("logs", ["notifications"]);
    expect(group.tools[0]).toMatchObject({ id: "clear-all", disabled: false });
    act(() => registry.latest("logs", group.id, "clear-all")!.onSelect!());
    expect(screen.getByText("No notifications in this session.")).toBeTruthy();
    expect(registry.groupsFor("logs", ["notifications"])[0].tools[0].disabled).toBe(true);
  });
});
