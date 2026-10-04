import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";
import { ApprovalsView } from "../views/ApprovalsView";
import { api } from "../api";
import type { ApprovalRecord } from "../types";

// Review must not instantiate custom cards or run their error hooks.

vi.mock("../api", () => ({
  api: { listApprovals: vi.fn(), getApproval: vi.fn(), rejectApproval: vi.fn() },
  currentHass: () => ({}),
}));

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
    rejected_reason: null,
    result: null,
    request_id: "r1",
    client_ip: null,
    ...over,
  };
}

describe("Reject with error message", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem("phx-approval-default-view", "details");
    // Open straight into Preview mode so error detection runs immediately.
    localStorage.setItem("phx-dash-preview-mode", "preview");
  });

  it("shows an inert proposal and allows ordinary rejection without running card code", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [record()], total: 1, limit: 50, offset: 0 });
    vi.mocked(api.rejectApproval).mockResolvedValue(record({ status: "rejected" }));
    const { container, findByText, findByRole, queryByRole } = render(
      <ApprovalsView tab="pending" onTabChange={() => {}} />,
    );
    fireEvent.click(await findByText("Add 'custom:apexcharts-card' to dashboard"));
    await waitFor(() => expect(container.querySelector(".dash-preview-config")).toBeTruthy());
    expect(container.querySelector("hui-card, hui-error-card")).toBeNull();
    expect(queryByRole("button", { name: "Reject with error message" })).toBeNull();
    fireEvent.click(await findByRole("button", { name: /^Reject$/ }));
    await waitFor(() => expect(api.rejectApproval).toHaveBeenCalledTimes(1));
  });

  it("offers no such button when the preview reports no errors", async () => {
    // A non-dashboard approval renders no preview at all: the button must not exist.
    vi.mocked(api.listApprovals).mockResolvedValue({
      approvals: [record({
        tool_name: "create_automation",
        cap_name: "cap_automation_write",
        args: {},
        diff: { kind: "config_diff", summary: "Create automation 'x'", after: "{}" },
      })],
      total: 1, limit: 50, offset: 0,
    });
    const { container, findByText, queryByRole } = render(
      <ApprovalsView tab="pending" onTabChange={() => {}} />,
    );
    fireEvent.click(await findByText("Create automation 'x'"));
    await waitFor(() => expect(container.querySelector("#approval-detail-tab-diff")).toBeTruthy());
    expect(queryByRole("button", { name: "Reject with error message" })).toBeNull();
  });
});
