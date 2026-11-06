import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, waitFor } from "@testing-library/react";
import { renderApprovals } from "./approvals_harness";
import { api } from "../api";
import { copyToClipboard } from "../utils";
import { DashboardPreview } from "../components/DashboardPreview";
import type { ApprovalRecord } from "../types";

// A card whose configuration fails becomes HA's hui-error-card inside the live
// preview. Its message is surfaced outside the inert region as copyable text,
// so the operator can hand it back to the agent and reject in the usual way.
// This stub hui-card always spawns an error card; jsdom's custom element
// registry is shared per test file, which is why it lives apart from the other
// approval preview tests. Every error shows in one list with one copy icon,
// so several paste back to the agent at once.

vi.mock("../api", () => ({
  api: { listApprovals: vi.fn(), getApproval: vi.fn(), rejectApproval: vi.fn() },
  currentHass: () => ({}),
}));
vi.mock("../utils", async (importOriginal) => ({ ...(await importOriginal<typeof import("../utils")>()), copyToClipboard: vi.fn() }));

customElements.define("hui-card", class extends HTMLElement {
  connectedCallback() {
    const err = document.createElement("hui-error-card");
    (err as unknown as { _config: unknown })._config = {
      error: "value.series[0] is not a ChartCardSeriesExternalConfig",
    };
    this.appendChild(err);
  }
});

function record(over: Partial<ApprovalRecord> = {}): ApprovalRecord {
  return {
    id: "appr_1",
    access_key_id: "t1",
    access_key_name: "my_access_key",
    tool_name: "add_dashboard_card",
    cap_name: "cap_lovelace_write",
    args: { view_index: 0, card: { type: "custom:apexcharts-card", series: [] } },
    diff: { kind: "yaml_diff", summary: "Add 'custom:apexcharts-card' to dashboard" },
    status: "pending",
    created_at: "2026-01-01T00:00:00Z",
    expires_at: "2026-01-01T01:00:00Z",
    resolved_at: null,
    approved_by_user_id: null,
    closure_reason: null,
    result: null,
    request_id: "r1",
    client_ip: null,
    ...over,
  };
}

describe("card configuration errors in the approval preview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    // Open straight into Preview mode so error detection runs immediately.
    localStorage.setItem("phx-dash-preview-mode", "preview");
  });

  it("shows a failing card's error as copyable text and keeps ordinary rejection", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [record()], total: 1, limit: 50, offset: 0 });
    vi.mocked(api.rejectApproval).mockResolvedValue(record({ status: "rejected" }));
    const { container, findByText, findByRole, queryByRole } = renderApprovals();
    fireEvent.click(await findByText("Add 'custom:apexcharts-card' to dashboard"));
    await waitFor(() => expect(container.querySelector(".dash-preview-cards hui-card hui-error-card")).toBeTruthy());
    const message = await findByText("value.series[0] is not a ChartCardSeriesExternalConfig", { selector: ".dash-preview-error-msg" });
    expect(container.querySelector(".dash-preview-cards")).not.toContainElement(message);
    expect([...container.querySelector(".dash-preview-error-panel")!.querySelectorAll("button")].map((b) => b.getAttribute("aria-label"))).toEqual(["Copy Errors"]);
    expect(queryByRole("button", { name: "Reject with error message" })).toBeNull();
    fireEvent.click(await findByRole("button", { name: /^Reject$/ }));
    fireEvent.click(await findByRole("button", { name: "Confirm Rejection" }));
    await waitFor(() => expect(api.rejectApproval).toHaveBeenCalledTimes(1));
  });

  it("offers no extra rejection action for an approval without a preview", async () => {
    // A non-dashboard approval renders no preview at all.
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record({
        tool_name: "create_automation",
        cap_name: "cap_automation_write",
        args: {},
        diff: { kind: "config_diff", summary: "Create automation 'x'", after: "{}" },
      })],
      total: 1, limit: 50, offset: 0,
    });
    const { container, findByText, queryByRole } = renderApprovals();
    fireEvent.click(await findByText("Create automation 'x'"));
    await waitFor(() => expect(container.querySelector(".phx-review-change")).toBeTruthy());
    expect(queryByRole("button", { name: "Reject with error message" })).toBeNull();
  });

  it("lists every card's error once and copies them all as one text", async () => {
    const { container, findByRole } = render(<DashboardPreview config={{ views: [{ cards: [{ type: "tile" }, { type: "gauge" }] }] }} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-preview-error-list li")).toHaveLength(2));
    const panel = container.querySelector(".dash-preview-error-panel")!;
    expect(panel.textContent).toContain("Cards in this preview reported configuration errors.");
    expect(panel.querySelectorAll("button")).toHaveLength(1);
    fireEvent.click(await findByRole("button", { name: "Copy Errors" }));
    const error = "value.series[0] is not a ChartCardSeriesExternalConfig";
    await waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith(
      `Card configuration errors were found in the dashboard preview, please investigate:\n- tile: ${error}\n- gauge: ${error}`,
    ));
  });
});
