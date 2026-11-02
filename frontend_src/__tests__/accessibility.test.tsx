/** Focused accessibility regressions for custom Phoenix MCP panel controls. */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { PermissionSelector } from "../components/PermissionSelector";
import { TagInput } from "../components/TagInput";
import { AuditView } from "../views/AuditView";
import { LogDetailsContent, LogSelection, LogSelectionContext } from "../shell/logDetails";
import { api } from "../api";
import { AccessKeyCreateForm } from "../components/AccessKeyCreate";
import { Loading } from "../components/common";
import type { AuditEntry } from "../types";

vi.mock("../api", async (original) => {
  const real = await original<typeof import("../api")>();
  return { ...real, api: { ...real.api, getAudit: vi.fn() } };
});

/** The Audit Log with Log Details beside it, as in the Logs workspace. */
async function auditLog(entries: AuditEntry[]) {
  vi.mocked(api.getAudit).mockResolvedValue({ entries, total: entries.length });
  const view = render(
    <LogSelectionContext.Provider value={new LogSelection()}>
      <AuditView accessKeys={[{ id: "tok-1", name: "demo" } as never]} /><LogDetailsContent />
    </LogSelectionContext.Provider>,
  );
  await screen.findAllByRole("row", { name: /Audit entry:/ });
  const details = () => view.container.querySelector<HTMLElement>(".phx-log-details");
  return { ...view, details };
}

describe("accessibility regressions", () => {
  it("names permission selectors and each permission button", () => {
    render(
      <PermissionSelector
        value="GREEN"
        onChange={() => undefined}
        label="Permission for light.kitchen"
      />,
    );

    expect(screen.getByRole("group", { name: "Permission for light.kitchen" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Read and write, selected/ })).toHaveAttribute("aria-pressed", "true");
    // Each button names the entity, not just its own letter.
    expect(screen.getByRole("button", { name: /^Permission for light\.kitchen: Read and write/ })).toBeInTheDocument();
  });

  it("connects the tag combobox to its active suggestion", () => {
    render(
      <TagInput
        value={[]}
        onChange={() => undefined}
        canonicalTags={["lighting.ambient", "lighting.task"]}
      />,
    );

    const combo = screen.getByRole("combobox", { name: "Semantic tags" });
    fireEvent.change(combo, { target: { value: "light" } });
    fireEvent.keyDown(combo, { key: "ArrowDown" });

    expect(combo).toHaveAttribute("aria-controls");
    expect(combo).toHaveAttribute("aria-activedescendant");
    expect(screen.getByRole("listbox")).toHaveAttribute("id", combo.getAttribute("aria-controls"));
  });

  it("opens audit entry details from a named row", async () => {
    const entry: AuditEntry = {
      request_id: "req-1",
      timestamp: "2026-06-30T00:00:00Z",
      access_key_id: "tok-1",
      access_key_name: "demo",
      method: "GET",
      resource: "/api/phoenix-mcp/states",
      outcome: "allowed",
      client_ip: "127.0.0.1",
      pass_through: false,
      payload: null,
    };

    const log = await auditLog([entry]);
    fireEvent.click(screen.getByRole("row", { name: /Audit entry: Allowed, demo/ }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(within(log.details()!).getByText("Audit Entry")).toBeInTheDocument();
  });

  it("uses arrows to scan audit entries in their visible sort order", async () => {
    const older: AuditEntry = {
      request_id: "req-older",
      timestamp: "2026-06-29T00:00:00Z",
      access_key_id: "tok-1",
      access_key_name: "demo",
      method: "GET",
      resource: "/api/older",
      outcome: "allowed",
      client_ip: "127.0.0.1",
      pass_through: false,
      payload: null,
    };
    const newer: AuditEntry = { ...older, request_id: "req-newer", timestamp: "2026-06-30T00:00:00Z", resource: "/api/newer" };
    const log = await auditLog([newer, older]);
    const rows = screen.getAllByRole("row", { name: /Audit entry: Allowed, demo/ });
    fireEvent.click(rows[0]);
    expect(log.details()).toHaveTextContent("/api/newer");

    fireEvent.keyDown(rows[0], { key: "ArrowDown" });
    expect(log.details()).toHaveTextContent("/api/older");
    expect(log.details()).not.toHaveTextContent("/api/newer");
    expect(document.activeElement).toBe(rows[1]);
    fireEvent.keyDown(rows[1], { key: "ArrowUp" });
    expect(log.details()).toHaveTextContent("/api/newer");
  });

  it("keeps approval lifecycle rows distinct when they share a request id", async () => {
    const approved: AuditEntry = {
      request_id: "approve-request",
      timestamp: "2026-08-29T07:00:00Z",
      access_key_id: "admin",
      access_key_name: "admin:user",
      method: "approval/approved",
      resource: "approval:restart_ha:appr-1",
      outcome: "allowed",
      client_ip: "",
      pass_through: false,
      payload: null,
    };
    const executed: AuditEntry = {
      ...approved,
      timestamp: "2026-08-29T07:00:01Z",
      method: "approval/executed",
    };
    const log = await auditLog([executed, approved]);

    const rows = screen.getAllByRole("row", { name: /Audit entry: Allowed, admin/ });
    expect(rows).toHaveLength(2);
    fireEvent.click(rows[0]);
    expect(log.details()).toHaveTextContent("approval/executed");
    fireEvent.keyDown(rows[0], { key: "ArrowDown" });
    expect(log.details()).toHaveTextContent("approval/approved");
  });

  it("wires the whole row as a single accessible click target, not one per cell", async () => {
    // Mouse clicks anywhere in the row reach the <tr> onClick by bubbling, which
    // jsdom hit-testing cannot exercise. This checks the accessible part: exactly
    // one focusable control per row (no per-cell tab stops) and the .row-open
    // button that the panel CSS styles.
    const entry: AuditEntry = {
      request_id: "req-2",
      timestamp: "2026-06-30T00:00:00Z",
      access_key_id: "tok-1",
      access_key_name: "demo",
      method: "GET",
      resource: "/api/phoenix-mcp/states",
      outcome: "allowed",
      client_ip: "127.0.0.1",
      pass_through: false,
      payload: null,
    };

    await auditLog([entry]);

    const row = screen.getByRole("row", { name: /Audit entry:/ });
    expect(row).toHaveAttribute("tabindex", "0");
    expect(row.querySelectorAll("button, a, input, [tabindex]")).toHaveLength(0);
  });

  it("associates the access-key-name validation error with the input", () => {
    render(
      <AccessKeyCreateForm
        existingNames={[]}
        onCreated={() => undefined}
        onClose={() => undefined}
        onOpenSettings={() => undefined}
      />,
    );

    const input = screen.getByLabelText("Name (required)");
    // Valid name: no error state advertised.
    fireEvent.change(input, { target: { value: "my_access_key" } });
    expect(input).not.toHaveAttribute("aria-invalid");
    // Too-short name: aria-invalid + described-by the alert-role error.
    fireEvent.change(input, { target: { value: "ab" } });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAttribute("aria-describedby", "access-key-name-error");
    const error = screen.getByRole("alert");
    expect(error).toHaveAttribute("id", "access-key-name-error");
    expect(error.textContent).toMatch(/3-32 characters/);
  });

  it("exposes the shared loading indicator as a status with a decorative spinner", () => {
    const { container } = render(<Loading />);
    const status = screen.getByRole("status");
    expect(status.textContent).toContain("Loading...");
    expect(container.querySelector(".spinner")).toHaveAttribute("aria-hidden", "true");
  });
});

it("lets Tab leave a tag query without committing or trapping focus", () => {
  const changed = () => { throw new Error("Tab must not commit a tag"); };
  const { getByRole } = render(<TagInput value={[]} onChange={changed} canonicalTags={["lighting.task"]} />);
  const combo = getByRole("combobox");
  fireEvent.change(combo, { target: { value: "no-match" } });
  expect(fireEvent.keyDown(combo, { key: "Tab" })).toBe(true);
});

it("labels raw diff additions and removals without relying on color", async () => {
  const { RawDiffPane } = await import("../components/DiffView");
  const { container } = render(<><RawDiffPane rows={[{ text: "new value", changed: true }]} tone="add" />
    <RawDiffPane rows={[{ text: "old value", changed: true }]} tone="remove" /></>);
  expect(container.textContent).toContain("Added: new value");
  expect(container.textContent).toContain("Removed: old value");
  expect([...container.querySelectorAll(".diff-line-marker")].map((el) => el.textContent)).toEqual(["+", "-"]);
});
