/**
 * An automated accessibility pass over the main windows, on top of the hand-written
 * role assertions. Layout-dependent rules stay off: jsdom has no rendering, and the
 * landmark rules judge a whole page rather than the fragment mounted here.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import { api } from "../api";
import { AccessKeyCreateForm } from "../components/AccessKeyCreate";
import { PermissionSelector } from "../components/PermissionSelector";
import { LogDetailsContent, LogSelection, LogSelectionContext } from "../shell/logDetails";
import { AuditView } from "../views/AuditView";
import { renderApprovals, reviewWindow } from "./approvals_harness";
import type { ApprovalRecord, AuditEntry } from "../types";

vi.mock("../api", async (original) => {
  const real = await original<typeof import("../api")>();
  return {
    ...real,
    api: { ...real.api, getAudit: vi.fn(), listApprovals: vi.fn(), getApproval: vi.fn() },
    currentHass: () => ({}),
  };
});

const OPTIONS: axe.RunOptions = {
  rules: { "color-contrast": { enabled: false }, region: { enabled: false }, "landmark-one-main": { enabled: false }, "page-has-heading-one": { enabled: false } },
};

async function violations(root: Element): Promise<string[]> {
  const result = await axe.run(root, OPTIONS);
  return result.violations.flatMap((rule) => rule.nodes.map((node) => `${rule.id}: ${node.html.slice(0, 120)}`));
}

const APPROVAL = {
  id: "appr_a", access_key_id: "t1", access_key_name: "kitchen", tool_name: "restart_ha", cap_name: "cap_restart",
  args: {}, diff: { kind: "system_action", summary: "Restart Home Assistant", summary_key: "diff.restart_ha", summary_params: {} },
  status: "pending", created_at: "2026-08-13T04:00:00Z", expires_at: "2099-08-13T05:00:00Z",
  resolved_at: null, approved_by_user_id: null, rejected_reason: null, result: null, request_id: "r", client_ip: null,
} as unknown as ApprovalRecord;

const AUDIT = {
  timestamp: "2026-08-13T04:00:00Z", request_id: "r", access_key_id: "tok-1", access_key_name: "demo",
  method: "get_state", resource: "light.kitchen", outcome: "allowed", client_ip: "127.0.0.1",
} as unknown as AuditEntry;

beforeEach(() => { vi.clearAllMocks(); });

describe("axe", () => {
  it("passes the Approvals workspace with a request in Review", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [APPROVAL], total: 1 } as never);
    vi.mocked(api.getApproval).mockResolvedValue(APPROVAL);
    const { container } = renderApprovals({ openApprovalId: "appr_a" });
    await vi.waitFor(() => expect(reviewWindow().textContent).toContain("Restart Home Assistant"));
    expect(await violations(container)).toEqual([]);
  });

  it("passes the Audit Log", async () => {
    vi.mocked(api.getAudit).mockResolvedValue({ entries: [AUDIT], total: 1 });
    const { container } = render(
      <LogSelectionContext.Provider value={new LogSelection()}>
        <AuditView accessKeys={[{ id: "tok-1", name: "demo" } as never]} /><LogDetailsContent />
      </LogSelectionContext.Provider>,
    );
    await screen.findAllByRole("row", { name: /Audit entry:/ });
    expect(await violations(container)).toEqual([]);
  });

  it("passes the Access Key creation form", async () => {
    const { container } = render(<AccessKeyCreateForm existingNames={[]} onCreated={vi.fn()} onClose={vi.fn()} onOpenSettings={vi.fn()} />);
    expect(await violations(container)).toEqual([]);
  });

  it("passes the permission selector", async () => {
    const { container } = render(<PermissionSelector value="GREEN" onChange={vi.fn()} label="Permission for light.kitchen" />);
    expect(await violations(container)).toEqual([]);
  });

  it("reports a real violation", async () => {
    const { container } = render(<button aria-label=""><span aria-hidden="true">x</span></button>);
    expect((await violations(container)).join()).toContain("button-name");
  });
});
