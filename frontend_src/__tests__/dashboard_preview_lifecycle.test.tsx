import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardPreview, wrapCardPreviewConfig } from "../components/DashboardPreview";
import { connectPreviewFrame } from "../utils/dashboard_preview_sandbox";

vi.mock("../utils/dashboard_preview_sandbox", () => ({
  previewFrameDocument: () => "<!doctype html><body></body>",
  connectPreviewFrame: vi.fn(),
}));

describe("dashboard preview lifecycle", () => {
  beforeEach(() => vi.mocked(connectPreviewFrame).mockReset());
  afterEach(() => vi.useRealTimers());

  it("works on local HTTP HA installations without the secure-context UUID API", () => {
    const uuid = vi.spyOn(crypto, "randomUUID").mockImplementation(() => { throw new Error("Secure context required"); });
    try {
      render(<DashboardPreview config={wrapCardPreviewConfig({ type: "button", name: "Local HTTP preview" })} />);
      expect(screen.getByTitle("Preview")).toBeInTheDocument();
      expect(uuid).not.toHaveBeenCalled();
    } finally { uuid.mockRestore(); }
  });

  it("disposes the old renderer and ignores its late result when the proposal changes", async () => {
    const firstDispose = vi.fn();
    const secondDispose = vi.fn();
    vi.mocked(connectPreviewFrame).mockResolvedValueOnce(firstDispose).mockResolvedValueOnce(secondDispose);
    const view = render(<DashboardPreview config={wrapCardPreviewConfig({ type: "button", name: "Before" })} />);
    fireEvent.load(screen.getByTitle("Preview"));
    await waitFor(() => expect(connectPreviewFrame).toHaveBeenCalledTimes(1));
    const first = vi.mocked(connectPreviewFrame).mock.calls[0];
    act(() => first[4]({ kind: "rendered", height: 110 }));

    view.rerender(<DashboardPreview config={wrapCardPreviewConfig({ type: "button", name: "After" })} />);
    const frame = screen.getByTitle("Preview");
    expect(frame).not.toBe(first[0]);
    expect(first[5].aborted).toBe(true);
    expect(firstDispose).toHaveBeenCalledOnce();
    fireEvent.load(frame);
    await waitFor(() => expect(connectPreviewFrame).toHaveBeenCalledTimes(2));
    const second = vi.mocked(connectPreviewFrame).mock.calls[1];
    act(() => {
      second[4]({ kind: "rendered", height: 220 });
      first[4]({ kind: "rendered", height: 999, errors: ["Stale card error"] });
    });
    expect(frame).toHaveStyle({ height: "220px" });
    expect(screen.queryByText("Stale card error")).toBeNull();
    view.unmount();
    expect(secondDispose).toHaveBeenCalledOnce();
  });

  it("removes a failed renderer and reveals escaped configuration instead of a blank panel", async () => {
    vi.mocked(connectPreviewFrame).mockResolvedValue(vi.fn());
    render(<DashboardPreview config={wrapCardPreviewConfig({ type: "custom:missing", name: "<script>proposal</script>" })} />);
    fireEvent.load(screen.getByTitle("Preview"));
    await waitFor(() => expect(connectPreviewFrame).toHaveBeenCalledOnce());
    const request = vi.mocked(connectPreviewFrame).mock.calls[0];
    act(() => request[4]({ kind: "failed" }));
    expect(request[5].aborted).toBe(true);
    expect(screen.queryByTitle("Preview")).toBeNull();
    expect(screen.getByText("Card configuration").closest("details")).toHaveAttribute("open");
    expect(document.querySelector(".dash-preview-config script")).toBeNull();
    expect(document.querySelector(".dash-preview-config")?.textContent).toContain("<script>proposal</script>");
  });

  it("falls back when a renderer never completes", async () => {
    vi.useFakeTimers();
    vi.mocked(connectPreviewFrame).mockResolvedValue(vi.fn());
    render(<DashboardPreview config={wrapCardPreviewConfig({ type: "markdown", content: "Pending renderer" })} />);
    fireEvent.load(screen.getByTitle("Preview"));
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(screen.queryByTitle("Preview")).toBeNull();
    expect(screen.getByText("Card configuration").closest("details")).toHaveAttribute("open");
  });
});
