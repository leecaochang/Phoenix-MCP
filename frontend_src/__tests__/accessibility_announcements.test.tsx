/**
 * What a screen reader hears when something changes without focus moving: a failure toast that
 * stays and speaks at once, the count a filter leaves once typing stops, a finished chat reply as
 * plain text, a failed copy, and the previews that are pictures. Also the names that tell repeated
 * controls apart, and a key field the browser must not fill with a Home Assistant password.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { Notifications, TOAST_MS, Toasts } from "../ui/notify";
import { ResultCount } from "../components/common";
import { replyAnnouncement } from "../components/AgentCliWindow";
import { CopyButton } from "../components/AccessKeyCreate";
import { EnergyPreview } from "../components/ApprovalChange";
import { ConfigTable } from "../ui/table/ConfigTable";
import * as utils from "../utils";

describe("a failure notice", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("is spoken at once and stays until it is dismissed; a long notice stays long enough to read", () => {
    const center = new Notifications();
    render(<Toasts center={center} />);
    act(() => center.notify("Restore stopped: Kitchen changed since.", "error"));
    expect(screen.getByRole("alert")).toHaveTextContent("Restore stopped: Kitchen changed since.");
    act(() => { vi.advanceTimersByTime(TOAST_MS * 5); });
    expect(center.toast?.text).toBe("Restore stopped: Kitchen changed since.");
    act(() => center.dismissToast());
    expect(screen.getByRole("alert").textContent).toBe("");

    const long = "x".repeat(120);
    act(() => center.notify(long));
    act(() => { vi.advanceTimersByTime(TOAST_MS + 100); });
    expect(center.toast?.text).toBe(long);
  });
});

describe("ResultCount", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function Filtered({ rows }: { rows: string[] }) {
    const [query, setQuery] = useState("");
    const shown = rows.filter((r) => r.includes(query));
    return (
      <>
        <input aria-label="Filter" value={query} onChange={(e) => setQuery(e.target.value)} />
        <ResultCount count={shown.length} query={query} />
      </>
    );
  }

  it("says how many rows a filter left once typing stops, and nothing when the list only refreshes", () => {
    const view = render(<Filtered rows={["kitchen", "kettle", "hall"]} />);
    const status = screen.getByRole("status");
    act(() => { vi.advanceTimersByTime(1000); });
    expect(status.textContent).toBe("");
    fireEvent.change(screen.getByLabelText("Filter"), { target: { value: "k" } });
    fireEvent.change(screen.getByLabelText("Filter"), { target: { value: "ke" } });
    expect(status.textContent).toBe("");
    act(() => { vi.advanceTimersByTime(1000); });
    expect(status.textContent).toBe("1 result");
    view.rerender(<Filtered rows={["kitchen", "kettle", "hall", "kegs"]} />);
    act(() => { vi.advanceTimersByTime(1000); });
    expect(status.textContent).toBe("1 result");
  });
});

describe("a finished chat reply", () => {
  it("is announced as the text it renders to, cut at a word when it is long", () => {
    expect(replyAnnouncement("**Done.** The `light.kitchen` is on.")).toBe("Done. The light.kitchen is on.");
    const long = replyAnnouncement(`# Report\n\n${"word ".repeat(200)}`);
    expect(long.length).toBeLessThan(400);
    expect(long).toMatch(/The full reply is in the conversation\.$/);
    expect(long).not.toContain("#");
  });
});

describe("copy buttons", () => {
  it("say what they copy and report a copy that failed", async () => {
    vi.spyOn(utils, "copyToClipboard").mockRejectedValue(new Error("Clipboard unavailable"));
    render(<CopyButton text="hello" label="Copy" name="Copy the first prompt" />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Copy the first prompt" })); });
    expect(screen.getByRole("status")).toHaveTextContent("Copy failed");
    vi.restoreAllMocks();
  });
});

describe("previews", () => {
  it("say which Energy rows were added or removed, not only by their tint", () => {
    render(<EnergyPreview energy={{ before: { devices: [{ name: "Washer", statistic: "sensor.old" }] }, after: { devices: [{ name: "Dryer", statistic: "sensor.new" }] } }} />);
    const added = document.querySelector(".energy-preview-row.is-added")!;
    const removed = document.querySelector(".energy-preview-row.is-removed")!;
    expect(added.querySelector(".sr-only")?.textContent).toMatch(/^Added/);
    expect(removed.querySelector(".sr-only")?.textContent).toMatch(/^Removed/);
  });
});

describe("tables", () => {
  const columns = [{ id: "name", label: "Name", cell: (r: { id: string }) => r.id }];
  it("are a grid only where rows are chosen with the keys", () => {
    const { rerender } = render(<ConfigTable id="plain" label="Plain" columns={columns} rows={[{ id: "a" }]} rowKey={(r) => r.id} />);
    expect(screen.queryByRole("grid")).toBeNull();
    expect(screen.getByRole("table", { name: "Plain" })).toBeInTheDocument();
    rerender(<ConfigTable id="plain" label="Plain" columns={columns} rows={[{ id: "a" }]} rowKey={(r) => r.id} onSelect={() => undefined} />);
    expect(screen.getByRole("grid", { name: "Plain" })).toBeInTheDocument();
  });
});
