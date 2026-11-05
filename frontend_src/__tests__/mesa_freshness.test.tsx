/**
 * The MESA workspace shows findings and resolutions for the profiles as they
 * are now. A profile save or delete and Rescan recompute every finding, not
 * only the suggestions, so a deleted orphan leaves Advisory at once; the quiet
 * half-minute read stays suggestions-only. Saving an existing entity profile
 * reads its Effective Resolution again.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api, setHass } from "../api";
import { ProfileEditor } from "../views/MesaView";
import type { MesaProfileDetail } from "../types";
import { MesaProvider, useMesa } from "../shell/mesa";
import { EMPTY_ISSUES, advisoryWindow, editorWindow, mockEmptyMesa, profilesWindow, renderMesa, rowsIn } from "./mesa_harness";

vi.mock("../phoenix-mcp-panel.css?inline", () => ({ default: "" }));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  const names = [
    "listMesaProfiles", "getMesaIssues", "listMesaDomains", "listMesaIntegrations", "listMesaAreas", "listMesaDevices",
    "getEntityTree", "getMesaVocabulary", "getMesaIntegrationOptions", "getMesaDeviceOptions",
    "getMesaProfile", "putMesaProfile", "deleteMesaProfile",
  ];
  return { ...actual, api: Object.fromEntries(names.map((n) => [n, vi.fn()])) };
});

const doc = (mode: string) => ({ semantic_profile: { operational_boundaries: { control_mode: mode } } });

let mesaState: ReturnType<typeof useMesa> = null;
function MesaProbe() {
  mesaState = useMesa();
  return null;
}

beforeEach(() => {
  vi.resetAllMocks();
  setHass({ user: { id: "test-admin" } });
  localStorage.clear();
  mockEmptyMesa();
});

describe("findings after a change", () => {
  it("drop a deleted orphan at once, because the delete recomputes every finding", async () => {
    vi.mocked(api.listMesaProfiles).mockResolvedValue({ profiles: [
      { entity_id: "sensor.gone", document: doc("confirm") },
    ], total_matched: 1, has_more: false, next_cursor: null });
    // The server answers what the panel asks for: only a full refresh recomputes orphans.
    let orphans = ["sensor.gone"];
    let deleted = false;
    vi.mocked(api.getMesaIssues).mockImplementation(async (refresh) => {
      if (refresh === true) orphans = orphans.filter((id) => id !== "sensor.gone" || !deleted);
      return { ...EMPTY_ISSUES, orphans };
    });
    vi.mocked(api.deleteMesaProfile).mockImplementation(async () => {
      deleted = true;
      vi.mocked(api.listMesaProfiles).mockResolvedValue({ profiles: [], total_matched: 0, has_more: false, next_cursor: null });
      return { entity_id: "sensor.gone", deleted: true };
    });
    vi.mocked(api.getMesaProfile).mockResolvedValue({ entity_id: "sensor.gone", stored: doc("confirm"), effective: doc("confirm"),
      explanation: { entity_id: "sensor.gone", explanation: [], conflicts_detected: false, warnings: [] } } as never);

    renderMesa();
    await waitFor(() => expect(advisoryWindow().getByText(/sensor\.gone/)).toBeTruthy());
    fireEvent.click(rowsIn("Profiles")[0].querySelectorAll("td")[1]!);
    await waitFor(() => expect(editorWindow().getByLabelText(/Control mode/)).toHaveValue("confirm"));
    fireEvent.click(editorWindow().getByRole("button", { name: "Delete" }));
    fireEvent.click(within(editorWindow().getByRole("group", { name: "Delete this entity profile?" }))
      .getByRole("button", { name: "Delete Entity Profile" }));
    await waitFor(() => expect(api.deleteMesaProfile).toHaveBeenCalledWith("sensor.gone"));
    await waitFor(() => expect(advisoryWindow().queryByText(/sensor\.gone/)).toBeNull());
    expect(api.getMesaIssues).toHaveBeenLastCalledWith(true);
  });

  it("Rescan recomputes every finding", async () => {
    renderMesa();
    await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalled());
    vi.mocked(api.getMesaIssues).mockClear();
    fireEvent.click(profilesWindow().getByRole("button", { name: "MESA Actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Rescan" }));
    await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalledWith(true));
  });

  it("the quiet half-minute read recomputes only the suggestions", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderMesa();
      await waitFor(() => expect(api.getMesaIssues).toHaveBeenCalledWith(true));
      vi.mocked(api.getMesaIssues).mockClear();
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
      expect(api.getMesaIssues).toHaveBeenCalledWith("suggestions");
      expect(api.getMesaIssues).not.toHaveBeenCalledWith(true);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Effective Resolution after Save", () => {
  const detail = (mode: string): MesaProfileDetail => ({
    entity_id: "lock.front", stored: doc(mode), effective: doc(mode),
    explanation: { entity_id: "lock.front", conflicts_detected: false, warnings: [], explanation: [
      { field_path: "operational_boundaries.control_mode", effective_value: mode, provided_by_level: "entity", provided_by_origin: "user", conflict: false },
    ] },
  });

  it("is read again when an existing entity profile is saved, keeping the form", async () => {
    vi.mocked(api.getMesaProfile).mockResolvedValueOnce(detail("prohibited")).mockResolvedValueOnce(detail("confirm"));
    vi.mocked(api.putMesaProfile).mockResolvedValue({ entity_id: "lock.front", stored: doc("confirm"), warnings: [] } as never);
    const { container } = render(<ProfileEditor variant="window" scope="entity" profileKey="lock.front" isNew={false}
      entityTree={{}} canonicalTags={[]} onClose={() => {}} onSaved={() => {}} onShowSaved={() => {}} />);
    const effective = () => container.querySelector(".mesa-explain tbody td:nth-child(2)")?.textContent;
    await waitFor(() => expect(effective()).toBe("prohibited"));
    fireEvent.change(screen.getByLabelText(/Control mode/), { target: { value: "confirm" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(effective()).toBe("confirm"));
    expect(api.getMesaProfile).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText(/Control mode/)).toHaveValue("confirm");
  });

  it("keeps the earlier resolution when the read after Save fails", async () => {
    vi.mocked(api.getMesaProfile).mockResolvedValueOnce(detail("prohibited")).mockRejectedValueOnce(new Error("offline"));
    vi.mocked(api.putMesaProfile).mockResolvedValue({ entity_id: "lock.front", stored: doc("confirm"), warnings: [] } as never);
    const { container } = render(<ProfileEditor variant="window" scope="entity" profileKey="lock.front" isNew={false}
      entityTree={{}} canonicalTags={[]} onClose={() => {}} onSaved={() => {}} onShowSaved={() => {}} />);
    const effective = () => container.querySelector(".mesa-explain tbody td:nth-child(2)")?.textContent;
    await waitFor(() => expect(effective()).toBe("prohibited"));
    fireEvent.change(screen.getByLabelText(/Control mode/), { target: { value: "confirm" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.getMesaProfile).toHaveBeenCalledTimes(2));
    expect(effective()).toBe("prohibited");
    expect(screen.queryByText("offline")).toBeNull();
  });
});

describe("a scope read that fails", () => {
  const stored = { domains: [{ domain: "light", document: doc("confirm") }, { domain: "lock", document: doc("confirm") }] } as never;
  const shownDomains = () => mesaState?.keysIn("domain").size;

  it("keeps the rows shown when the read is the quiet one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.mocked(api.listMesaDomains).mockResolvedValue(stored);
      render(<MesaProvider active><MesaProbe /></MesaProvider>);
      await waitFor(() => expect(shownDomains()).toBe(2));
      vi.mocked(api.listMesaDomains).mockRejectedValueOnce(new Error("Bad gateway"));
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
      expect(api.listMesaDomains).toHaveBeenCalledTimes(2);
      expect(shownDomains()).toBe(2);
      expect(mesaState?.error).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says so on a read the operator asked for, and keeps the rows there too", async () => {
    vi.mocked(api.listMesaDomains).mockResolvedValue(stored);
    render(<MesaProvider active><MesaProbe /></MesaProvider>);
    await waitFor(() => expect(shownDomains()).toBe(2));
    vi.mocked(api.listMesaDomains).mockRejectedValueOnce(new Error("Bad gateway"));
    await act(async () => { mesaState?.refresh(); });
    await waitFor(() => expect(mesaState?.error).toBe("Failed to load MESA profiles."));
    expect(shownDomains()).toBe(2);
  });
});
