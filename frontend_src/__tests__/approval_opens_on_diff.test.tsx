import { describe, it, expect, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ApprovalChange } from "../components/ApprovalChange";
import { rememberPreviewMode } from "../components/DashboardPreview";
import type { ApprovalRecord } from "../types";

/**
 * Every approval review opens on the text diff. A dashboard Preview runs the
 * pending card's code in the administrator's session, so it is drawn only when
 * asked for on that request, never from a choice remembered from another one.
 * The Energy preview stands in for the Lovelace one here: it needs no
 * hui-card, and it shares the same Diff and Preview switch.
 */

const ROWS = { sources: [{ type: "grid", stat_energy_from: "sensor.grid_in" }] };

function record(id: string): ApprovalRecord {
  return {
    id, access_key_id: "k", access_key_name: "key", tool_name: "edit_energy_config", cap_name: "cap_energy_write",
    args: {}, status: "pending", created_at: "", expires_at: "", resolved_at: null, approved_by_user_id: null,
    closure_reason: null, result: null, request_id: "r", client_ip: null,
    diff: { kind: "config_diff", before: "a: 1", after: "a: 2", preview: { energy: { before: ROWS, after: ROWS } } },
  } as unknown as ApprovalRecord;
}

const previewShown = () => document.querySelector(".approval-preview-pane") !== null;

describe("approval review mode", () => {
  beforeEach(() => rememberPreviewMode("preview"));

  it("opens on the diff even when Preview was chosen before", () => {
    render(<ApprovalChange record={record("a")} />);
    expect(previewShown()).toBe(false);
  });

  it("draws the Preview when asked, and the next request opens on the diff again", () => {
    const { rerender } = render(<ApprovalChange record={record("a")} />);
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(previewShown()).toBe(true);
    rerender(<ApprovalChange record={record("b")} />);
    expect(previewShown()).toBe(false);
  });
});
