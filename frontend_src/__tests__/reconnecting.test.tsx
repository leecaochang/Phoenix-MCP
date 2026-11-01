/**
 * Behaviour: Home Assistant going away is announced once, over the whole
 * panel, instead of a raw fetch failure in every window (user, 2026-10-29).
 * The notice says what is happening, keeps asking, and clears itself when
 * Home Assistant answers again.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { Reconnecting } from "../shell/Reconnecting";
import { homeAssistantReachable, noteConnection, onConnectionChange, useHomeAssistantAway, __resetConnectionState } from "../utils/connection";
import { api } from "../api";

vi.mock("../api", () => ({ api: { getSettings: vi.fn() } }));

describe("the connection watcher", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetConnectionState();
    vi.mocked(api.getSettings).mockResolvedValue({} as never);
  });
  afterEach(() => { __resetConnectionState(); vi.useRealTimers(); });

  it("reports the first failure and the first reply, and nothing in between", () => {
    const seen: boolean[] = [];
    const stop = onConnectionChange((up) => seen.push(up));
    expect(homeAssistantReachable()).toBe(true);
    noteConnection(false);
    noteConnection(false);
    noteConnection(true);
    noteConnection(true);
    expect(seen).toEqual([false, true]);
    expect(homeAssistantReachable()).toBe(true);
    stop();
    noteConnection(false);
    expect(seen).toEqual([false, true]);
  });

  it("reports away to a view, and not away once it answers again", () => {
    const seen: boolean[] = [];
    function Probe() {
      seen.push(useHomeAssistantAway(() => api.getSettings()));
      return null;
    }
    render(<Probe />);
    expect(seen[seen.length - 1]).toBe(false);
    act(() => noteConnection(false));
    expect(seen[seen.length - 1]).toBe(true);
    act(() => noteConnection(true));
    expect(seen[seen.length - 1]).toBe(false);
  });

  it("reaches the other bundles through the page, since each has its own module state", () => {
    const seen: boolean[] = [];
    const stop = onConnectionChange((up) => seen.push(up));
    // What another bundle's api layer reports arrives as a page event.
    window.dispatchEvent(new CustomEvent("phx-connection", { detail: { up: false } }));
    expect(seen).toEqual([false]);
    // The same answer twice is told once.
    window.dispatchEvent(new CustomEvent("phx-connection", { detail: { up: false } }));
    expect(seen).toEqual([false]);
    window.dispatchEvent(new CustomEvent("phx-connection", { detail: { up: true } }));
    expect(seen).toEqual([false, true]);
    stop();
  });

  it("draws nothing while Home Assistant answers", () => {
    const root = document.createElement("div");
    render(<Reconnecting root={root} />);
    expect(root).toBeEmptyDOMElement();
  });

  it("names what is happening, keeps asking, and clears itself when it answers", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const root = document.createElement("div");
    document.body.appendChild(root);
    try {
      render(<Reconnecting root={root} />);
      act(() => noteConnection(false));
      const dialog = screen.getByRole("alertdialog");
      expect(dialog).toHaveTextContent("Phoenix is unavailable");
      expect(dialog).toHaveTextContent(/Retrying, please wait/);
      expect(dialog.querySelector(".phx-offline-spinner")).not.toBeNull();

      // It keeps asking on its own; one reply is what clears it.
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      expect(api.getSettings).toHaveBeenCalled();
      act(() => noteConnection(true));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());

      // Nothing is asked once it is back.
      const asked = vi.mocked(api.getSettings).mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
      expect(vi.mocked(api.getSettings).mock.calls.length).toBe(asked);
    } finally {
      root.remove();
    }
  });
});
