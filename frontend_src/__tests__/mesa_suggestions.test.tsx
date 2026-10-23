/**
 * Suggested Profiles: one row per suggestion, its actions in the window's
 * action bar for the chosen row, its reason in full under the list, Rescan
 * always reachable, and dismissed suggestions restorable. Review fills
 * Profile Editor with the suggested values; nothing is applied by a scan.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { api, setHass } from "../api";
import type { MesaSuggestion } from "../types";
import { EMPTY_ISSUES, editorWindow, mockEmptyMesa, renderMesa, rowsIn, suggestedWindow } from "./mesa_harness";

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

const button = (name: string) => suggestedWindow().getByRole("button", { name });

describe("Suggested Profiles", () => {
  it("keeps Rescan reachable with nothing found, and the row actions unavailable", async () => {
    renderMesa();
    expect(await suggestedWindow().findByText("No open suggestions.")).toBeTruthy();
    expect(button("Rescan")).not.toBeDisabled();
    for (const name of ["Apply", "Review", "Dismiss"]) expect(button(name)).toBeDisabled();
    vi.mocked(api.getMesaIssues).mockResolvedValue({ ...EMPTY_ISSUES, suggestions: [lockRow] });
    fireEvent.click(button("Rescan"));
    await waitFor(() => expect(rowsIn("Suggested Profiles")).toHaveLength(1));
  });

  it("lists each suggestion's subject, suggested mode and signal, and shows the chosen one's reason in full", async () => {
    vi.mocked(api.getMesaIssues).mockResolvedValue({ ...EMPTY_ISSUES, suggestions: [lockRow, domainRow] });
    renderMesa();
    await waitFor(() => expect(rowsIn("Suggested Profiles")).toHaveLength(2));
    const win = suggestedWindow();
    expect(win.getByText("lock.front")).toBeTruthy();
    // The mode shows its translated label, not the raw slug.
    expect(win.getByText("Prohibited")).toBeTruthy();
    expect(win.getAllByText("Unprotected")).toHaveLength(2);
    // Domain-scope rows are labelled as such.
    expect(win.getByText("update.* (domain)")).toBeTruthy();
    expect(win.queryByText(/physical security boundary/)).toBeNull();
    expect(win.getByText("Select a suggestion to read why it was suggested.")).toBeTruthy();
    fireEvent.click(rowsIn("Suggested Profiles").find((r) => r.textContent?.includes("lock.front"))!);
    expect(win.getByText(/physical security boundary/)).toBeTruthy();
  });

  it("applies the chosen suggestion as a create-only profile carrying its reason", async () => {
    vi.mocked(api.getMesaIssues).mockResolvedValueOnce({ ...EMPTY_ISSUES, suggestions: [lockRow] }).mockResolvedValue(EMPTY_ISSUES);
    vi.mocked(api.putMesaProfile).mockResolvedValue({ warnings: [] } as never);
    renderMesa();
    await waitFor(() => expect(rowsIn("Suggested Profiles")).toHaveLength(1));
    fireEvent.click(rowsIn("Suggested Profiles")[0]);
    fireEvent.click(button("Apply"));
    await waitFor(() => expect(api.putMesaProfile).toHaveBeenCalledWith("lock.front", {
      semantic_profile: { operational_boundaries: { control_mode: "prohibited", control_reason: lockRow.reason } },
    }, true));
    await waitFor(() => expect(rowsIn("Suggested Profiles")).toHaveLength(0));
  });

  it("Review fills Profile Editor with the suggested mode for the suggested target, without saving", async () => {
    vi.mocked(api.getMesaIssues).mockResolvedValue({ ...EMPTY_ISSUES, suggestions: [lockRow] });
    vi.mocked(api.getMesaProfile).mockResolvedValue({ stored: null, effective: {}, explanation: { explanation: [], warnings: [] } } as never);
    renderMesa();
    await waitFor(() => expect(rowsIn("Suggested Profiles")).toHaveLength(1));
    fireEvent.click(rowsIn("Suggested Profiles")[0]);
    fireEvent.click(button("Review"));
    const editor = editorWindow();
    await waitFor(() => expect(editor.getByLabelText(/Control mode/)).toHaveValue("prohibited"));
    expect(editor.getByDisplayValue("lock.front")).toBeDisabled();
    expect(api.putMesaProfile).not.toHaveBeenCalled();
  });

  it("dismisses the chosen suggestion and restores dismissed ones", async () => {
    vi.mocked(api.getMesaIssues).mockResolvedValue({ ...EMPTY_ISSUES, suggestions: [lockRow] });
    vi.mocked(api.dismissMesaSuggestion).mockResolvedValue({ suggestions: [], dismissed_suggestions: [lockRow] } as never);
    vi.mocked(api.restoreMesaSuggestions).mockResolvedValue({ suggestions: [lockRow], dismissed_suggestions: [] } as never);
    renderMesa();
    await waitFor(() => expect(rowsIn("Suggested Profiles")).toHaveLength(1));
    fireEvent.click(rowsIn("Suggested Profiles")[0]);
    fireEvent.click(button("Dismiss"));
    await waitFor(() => expect(api.dismissMesaSuggestion).toHaveBeenCalledWith(lockRow.key));
    expect(await suggestedWindow().findByText("1 dismissed")).toBeTruthy();
    fireEvent.click(button("Restore All"));
    await waitFor(() => expect(rowsIn("Suggested Profiles")).toHaveLength(1));
    expect(api.restoreMesaSuggestions).toHaveBeenCalledWith({ all: true });
  });

  it("while a suggestion is being applied, only its own actions wait; Rescan stays available", async () => {
    let finish!: () => void;
    vi.mocked(api.getMesaIssues).mockResolvedValue({ ...EMPTY_ISSUES, suggestions: [lockRow] });
    vi.mocked(api.putMesaProfile).mockReturnValue(new Promise((resolve) => { finish = () => resolve({ warnings: [] } as never); }));
    renderMesa();
    await waitFor(() => expect(rowsIn("Suggested Profiles")).toHaveLength(1));
    fireEvent.click(rowsIn("Suggested Profiles")[0]);
    fireEvent.click(button("Apply"));
    for (const name of ["Apply", "Review", "Dismiss"]) expect(button(name)).toHaveAttribute("aria-busy", "true");
    expect(button("Rescan")).not.toHaveAttribute("aria-busy");
    await act(async () => finish());
    expect(within(screen.getByRole("region", { name: "Suggested Profiles window" })).getByRole("button", { name: "Apply" })).not.toHaveAttribute("aria-busy");
  });
});
