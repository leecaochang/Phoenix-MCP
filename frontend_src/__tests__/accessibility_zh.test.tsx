/**
 * Accessible names under a non-English catalog.
 *
 * Rendering against the English catalog proves a name exists, not that it is
 * localized: a control whose visible label comes from the catalog while its
 * aria-label stays a hardcoded English literal would pass there. These tests
 * prime zh-Hans and assert the names change on the controls that build them.
 */
import { describe, it, expect, beforeAll, afterAll, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("../api", () => ({ api: { getAudit: vi.fn() } }));
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";
import zh from "../../custom_components/phoenix_mcp/catalogs/zh-Hans.json";
import { primeTranslations, flattenCatalog } from "../i18n";
import { PermissionSelector } from "../components/PermissionSelector";
import { AuditView } from "../views/AuditView";
import { api } from "../api";
import type { AuditEntry } from "../types";

// HA overlays the requested language on English, so a locale missing a key
// still resolves; mirror that here rather than priming zh alone.
beforeAll(() => primeTranslations({ ...flattenCatalog(en.panel), ...flattenCatalog(zh.panel) }));
afterAll(() => primeTranslations(en.panel));

function entry(over: Partial<AuditEntry> = {}): AuditEntry {
  return {
    request_id: "r1",
    timestamp: new Date(0).toISOString(),
    access_key_id: "t1",
    access_key_name: "daily",
    method: "POST",
    resource: "call_service",
    outcome: "allowed",
    client_ip: "127.0.0.1",
    ...(over as object),
  } as AuditEntry;
}

describe("accessible names are localized, not just present", () => {
  it("names the permission group and its selected option in Chinese", () => {
    render(
      <PermissionSelector value="GREEN" onChange={() => undefined} label="light.kitchen 的权限" />,
    );
    expect(screen.getByRole("group", { name: "light.kitchen 的权限" })).toBeInTheDocument();
    // Built from perms.selGreen; its chosen state is aria-pressed, not words in the name.
    const chosen = screen.getByRole("button", { name: /读写/, pressed: true });
    expect(chosen).toHaveAttribute("aria-pressed", "true");
  });

  it("localizes the audit row's composed accessible name", async () => {
    // audit.openEntryAria interpolates a translated outcome label; an English
    // outcome leaking through would be visible in this name.
    vi.mocked(api.getAudit).mockResolvedValue({ entries: [entry()], total: 1 });
    render(<AuditView accessKeys={[]} />);
    const row = await screen.findByRole("row", { name: /已允许/ });
    expect(row).toBeInTheDocument();
  });

  it("localizes the MESA column's word and its tooltip", async () => {
    vi.mocked(api.getAudit).mockResolvedValue({ entries: [entry({ mesa_advisory: true })], total: 1 });
    render(<AuditView accessKeys={[]} />);
    await screen.findAllByRole("row");
    const cell = screen.getByText("建议", { selector: "td" });
    expect(cell).toHaveAttribute("title", expect.stringContaining("建议模式"));
  });

  it("leaves no raw catalog keys in any accessible name", async () => {
    // A missing key renders as the key itself, which reads as gibberish aloud.
    vi.mocked(api.getAudit).mockResolvedValue({ entries: [entry({ mesa_advisory: true })], total: 1 });
    render(<AuditView accessKeys={[]} />);
    await screen.findAllByRole("row");
    for (const el of screen.getAllByRole("button")) {
      const name = el.getAttribute("aria-label") ?? el.textContent ?? "";
      expect(name).not.toMatch(/^[a-z]+(\.[A-Za-z]+)+$/);
    }
  });
});
