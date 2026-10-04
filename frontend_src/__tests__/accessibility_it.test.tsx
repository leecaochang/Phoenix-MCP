/**
 * Accessible names under the Italian catalog.
 *
 * Rendering against the English catalog proves a name exists, not that it is
 * localized. Italian shares a script with English, so a composed name that falls
 * back to English goes unnoticed; these tests assert the names change on the
 * controls that build them. The catalog is imported as `itCatalog` because `it`
 * is Vitest's own test function.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { render, screen } from "@testing-library/react";
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";
import itCatalog from "../../custom_components/phoenix_mcp/catalogs/it.json";
import { primeTranslations, flattenCatalog } from "../i18n";
import { PermissionSelector } from "../components/PermissionSelector";
import { AuditTable } from "../components/AuditTable";
import type { AuditEntry } from "../types";

// HA overlays the requested language on English, so a locale missing a key
// still resolves; mirror that here rather than priming Italian alone.
beforeAll(() =>
  primeTranslations({ ...flattenCatalog(en.panel), ...flattenCatalog(itCatalog.panel) }),
);
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

describe("accessible names are localized in Italian, not just present", () => {
  it("names the permission group and its selected option in Italian", () => {
    render(
      <PermissionSelector value="GREEN" onChange={() => undefined} label="Permessi di light.kitchen" />,
    );
    expect(screen.getByRole("group", { name: "Permessi di light.kitchen" })).toBeInTheDocument();
    // Built from perms.selGreen + perms.selSelected, so an untranslated half
    // shows here. The tooltip deliberately leads with the English mnemonic.
    const chosen = screen.getByRole("button", {
      name: /Write: lettura e scrittura, selezionato/,
    });
    expect(chosen).toHaveAttribute("aria-pressed", "true");
  });

  it("localizes the audit row's composed accessible name", () => {
    // audit.openEntryAria interpolates a translated outcome label; an English
    // outcome leaking through would be visible in this name.
    render(<AuditTable entries={[entry()]} />);
    const opener = screen.getByRole("button", { name: /Consentito/ });
    expect(opener).toBeInTheDocument();
  });

  it("localizes the MESA advisory badge's name and tooltip", () => {
    render(<AuditTable entries={[entry({ mesa_advisory: true })]} />);
    const badge = screen.getByLabelText(/Consultivo: si è proceduto con un avviso/);
    expect(badge).toHaveAttribute("title", expect.stringContaining("Consultivo"));
  });

  it("leaves no raw catalog keys in any accessible name", () => {
    // A missing key renders as the key itself, which reads as gibberish aloud.
    render(<AuditTable entries={[entry({ mesa_advisory: true })]} />);
    for (const el of screen.getAllByRole("button")) {
      const name = el.getAttribute("aria-label") ?? el.textContent ?? "";
      expect(name).not.toMatch(/^[a-z]+(\.[A-Za-z]+)+$/);
    }
  });
});
