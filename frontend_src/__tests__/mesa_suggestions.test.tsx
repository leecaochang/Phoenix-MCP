/**
 * Suggested Profiles: one row per suggestion with its own Dismiss, the reason
 * in the row's own name so Help reads it, and choosing a row opening the
 * suggestion in Profile Editor filled in. Nothing is applied by a scan, and
 * nothing is saved until the editor saves it.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { api, setHass } from "../api";
import type { MesaSuggestion } from "../types";
import { EMPTY_ISSUES, editorWindow, mockEmptyMesa, profilesWindow, renderMesa, rowsIn, suggestedWindow } from "./mesa_harness";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  const names = [
    "listMesaProfiles", "getMesaIssues", "listMesaDomains", "listMesaIntegrations", "listMesaAreas", "listMesaDevices",
    "getEntityTree", "getMesaVocabulary", "getMesaIntegrationOptions", "getMesaDeviceOptions",
    "putMesaDomain", "putMesaProfile", "getMesaProfile", "dismissMesaSuggestion", "restoreMesaSuggestions",
  ];
  return { ...actual, api: Object.fromEntries(names.map((n) => [n, vi.fn()])) };
});

const lockRow: MesaSuggestion = {
  key: "naked_risky:entity:lock.front",
  signal: "naked_risky",
  scope: "entity",
  subject_id: "lock.front",
  suggested_mode: "prohibited",
  reason: "No MESA profile covers this entity at any level, and it controls a physical security boundary.",
  evidence: { domain: "lock" },
};

const domainRow: MesaSuggestion = {
  key: "naked_risky:domain:update",
  signal: "naked_risky",
  scope: "domain",
  subject_id: "update",
  suggested_mode: "confirm",
  reason: "6 update entities have no MESA profile at any level.",
  evidence: { domain: "update", uncovered_count: 6, examples: [] },
};

beforeEach(() => {
  vi.resetAllMocks();
  setHass({ user: { id: "test-admin" } });
  mockEmptyMesa();
});

const rows = () => rowsIn("Suggested Profiles");
const rowFor = (id: string) => rows().find((r) => r.textContent?.includes(id))!;
/** One item of the MESA Actions menu, which holds what the window itself no longer shows. */
function mesaAction(name: string) {
  fireEvent.click(profilesWindow().getByRole("button", { name: "MESA Actions" }));
  return screen.getByRole("menuitem", { name });
}

describe("Suggested Profiles", () => {
  it("says when there is nothing to suggest", async () => {
    renderMesa();
    expect(await suggestedWindow().findByText(/No suggestions right now/)).toBeTruthy();
    expect(suggestedWindow().queryByRole("button", { name: "Dismiss" })).toBeNull();
  });

  it("lists each suggestion by target, name, mode and signal, with its reason in the row's own name", async () => {
    vi.mocked(api.getMesaIssues).mockResolvedValue({ ...EMPTY_ISSUES, suggestions: [lockRow, domainRow] });
    renderMesa();
    await waitFor(() => expect(rows()).toHaveLength(2));
    const win = suggestedWindow();
    expect(win.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Target ID", "Name", "Control Mode", "Signal", "Actions"]);
    expect(win.getByText("lock.front")).toBeTruthy();
    // The mode shows its translated label, not the raw slug.
    expect(win.getByText("Prohibited")).toBeTruthy();
    expect(win.getAllByText("Unprotected")).toHaveLength(2);
    // Domain-scope rows are labelled as such.
    expect(win.getByText("update.* (domain)")).toBeTruthy();
    // The window shows no prose; the reason is what Help reads from the row.
    expect(win.queryByText(/physical security boundary/)).toBeNull();
    expect(rowFor("lock.front").getAttribute("aria-label")).toContain("physical security boundary");
  });

  it("choosing a suggestion opens it in Profile Editor, filled in, without saving", async () => {
    vi.mocked(api.getMesaIssues).mockResolvedValue({ ...EMPTY_ISSUES, suggestions: [lockRow] });
    vi.mocked(api.getMesaProfile).mockResolvedValue({ stored: null, effective: {}, explanation: { explanation: [], warnings: [] } } as never);
    renderMesa();
    await waitFor(() => expect(rows()).toHaveLength(1));
    fireEvent.click(rows()[0]);
    const editor = editorWindow();
    await waitFor(() => expect(editor.getByLabelText(/Control mode/)).toHaveValue("prohibited"));
    expect(editor.getByDisplayValue("lock.front")).toBeDisabled();
    expect(api.putMesaProfile).not.toHaveBeenCalled();
  });

  it("saves a suggestion's own reason with the profile the editor creates from it", async () => {
    vi.mocked(api.getMesaIssues).mockResolvedValue({ ...EMPTY_ISSUES, suggestions: [lockRow] });
    vi.mocked(api.getMesaProfile).mockResolvedValue({ stored: null, effective: {}, explanation: { explanation: [], warnings: [] } } as never);
    vi.mocked(api.putMesaProfile).mockResolvedValue({ warnings: [] } as never);
    renderMesa();
    await waitFor(() => expect(rows()).toHaveLength(1));
    fireEvent.click(rows()[0]);
    const editor = editorWindow();
    await waitFor(() => expect(editor.getByLabelText(/Control mode/)).toHaveValue("prohibited"));
    fireEvent.click(editor.getByRole("button", { name: "Save" }));
    // Create-only, so a profile written since the scan is never replaced.
    await waitFor(() => expect(api.putMesaProfile).toHaveBeenCalledWith("lock.front", expect.objectContaining({
      semantic_profile: expect.objectContaining({
        operational_boundaries: expect.objectContaining({ control_mode: "prohibited", control_reason: lockRow.reason }),
      }),
    }), true));
  });

  it("dismisses a suggestion from its own row, after confirming in place", async () => {
    vi.mocked(api.getMesaIssues).mockResolvedValue({ ...EMPTY_ISSUES, suggestions: [lockRow] });
    vi.mocked(api.dismissMesaSuggestion).mockResolvedValue({ suggestions: [], dismissed_suggestions: [lockRow] } as never);
    vi.mocked(api.restoreMesaSuggestions).mockResolvedValue({ suggestions: [lockRow], dismissed_suggestions: [] } as never);
    renderMesa();
    await waitFor(() => expect(rows()).toHaveLength(1));
    const row = within(rows()[0]);
    fireEvent.click(row.getByRole("button", { name: "Dismiss" }));
    expect(api.dismissMesaSuggestion).not.toHaveBeenCalled();
    // The editor is not opened by acting on the row.
    expect(editorWindow().getByText("Select a profile to edit it, or add one.")).toBeTruthy();
    fireEvent.click(row.getByRole("button", { name: "Confirm Dismiss" }));
    await waitFor(() => expect(api.dismissMesaSuggestion).toHaveBeenCalledWith(lockRow.key));
    await waitFor(() => expect(rows()).toHaveLength(0));

    // Dismissed suggestions come back from the MESA Actions menu.
    fireEvent.click(mesaAction("Restore Dismissed Suggestions"));
    await waitFor(() => expect(api.restoreMesaSuggestions).toHaveBeenCalledWith({ all: true }));
  });

  it("rescans from the MESA Actions menu", async () => {
    vi.mocked(api.getMesaIssues).mockResolvedValue(EMPTY_ISSUES);
    renderMesa();
    expect(await suggestedWindow().findByText(/No suggestions right now/)).toBeTruthy();
    vi.mocked(api.getMesaIssues).mockResolvedValue({ ...EMPTY_ISSUES, suggestions: [lockRow] });
    fireEvent.click(mesaAction("Rescan"));
    await waitFor(() => expect(rows()).toHaveLength(1));
  });
});
