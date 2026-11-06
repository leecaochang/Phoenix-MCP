/**
 * Export downloads the whole MESA policy only after an inline confirmation in
 * Profiles; Import summarizes the archive, defaults to skipping existing
 * profiles, makes replacing explicit, and reports what happened.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent, waitFor, screen, within } from "@testing-library/react";
import { MesaImport } from "../views/MesaView";
import { api, setHass } from "../api";
import { setFormatLocale, t } from "../i18n";
import { mockEmptyMesa, profilesWindow, renderMesa } from "./mesa_harness";

afterEach(() => setFormatLocale({}, "en"));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  const names = [
    "listMesaProfiles", "getMesaIssues", "listMesaDomains", "listMesaIntegrations", "listMesaAreas", "listMesaDevices",
    "getEntityTree", "getMesaVocabulary", "getMesaIntegrationOptions", "getMesaDeviceOptions", "exportMesaProfiles", "importMesaProfiles",
  ];
  return { ...actual, api: Object.fromEntries(names.map((n) => [n, vi.fn()])) };
});

const archive = {
  mesa_export: {
    format_version: "1.0",
    entities: { "light.a": {}, "lock.b": {} },
    domains: { lock: {} },
    integrations: {},
    areas: {},
  },
};

function pickFile(container: HTMLElement, contents: string) {
  const input = container.querySelector<HTMLInputElement>("input[type=file]")!;
  const file = new File([contents], "profiles.json", { type: "application/json" });
  // jsdom's File lacks text() in some versions; normalize it.
  Object.defineProperty(file, "text", { value: () => Promise.resolve(contents) });
  fireEvent.change(input, { target: { files: [file] } });
}

const document = { semantic_profile: { operational_boundaries: { control_mode: "confirm" } } };

/** Export, from the MESA Actions menu in Profiles; it is ticked while the panel is open. */
function chooseExport() {
  fireEvent.click(profilesWindow().getByRole("button", { name: "MESA Actions" }));
  fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Export MESA Profiles" }));
}

describe("Export", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setHass({ user: { id: "test-admin" } });
    mockEmptyMesa();
  });

  it("names the profile count using HA's number format and downloads nothing before the confirmation", async () => {
    setFormatLocale({ locale: { number_format: "decimal_comma" } }, "en");
    vi.mocked(api.listMesaProfiles).mockResolvedValue({ profiles: Array.from({ length: 1234 }, (_, i) => ({ entity_id: `light.l${i}`, document })), total_matched: 1234, has_more: false, next_cursor: null });
    renderMesa();
    await waitFor(() => expect(profilesWindow().getAllByRole("row").length).toBeGreaterThan(1));
    chooseExport();
    const confirm = profilesWindow().getByRole("group", { name: "Export MESA Profiles" });
    // What it downloads reads in the Help window, not inside the box.
    expect(confirm.getAttribute("data-help-text")).toMatch(/Download 1\.234 MESA profiles/);
    expect(api.exportMesaProfiles).not.toHaveBeenCalled();
    // Rendering 1234 rows takes longer than the usual budget in jsdom, far longer on a loaded runner.
  }, 60_000);

  it("downloads the archive only after the explicit confirm", async () => {
    vi.mocked(api.listMesaProfiles).mockResolvedValue({ profiles: ["a", "b", "c"].map((x) => ({ entity_id: `light.${x}`, document })), total_matched: 3, has_more: false, next_cursor: null });
    vi.mocked(api.exportMesaProfiles).mockResolvedValue(archive as never);
    const clicks: string[] = [];
    globalThis.URL.createObjectURL = vi.fn(() => "blob:x");
    globalThis.URL.revokeObjectURL = vi.fn();
    const origClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () { clicks.push(this.getAttribute("download") ?? ""); };
    try {
      renderMesa();
      await waitFor(() => expect(profilesWindow().getAllByRole("row")).toHaveLength(4));
      chooseExport();
      const confirm = profilesWindow().getByRole("group", { name: "Export MESA Profiles" });
      expect(confirm.getAttribute("data-help-text")).toMatch(/Download 3 MESA profiles/i);
      expect(api.exportMesaProfiles).not.toHaveBeenCalled();

      fireEvent.click(within(confirm).getByRole("button", { name: "Export" }));
      await waitFor(() => expect(profilesWindow().queryByRole("group", { name: "Export MESA Profiles" })).toBeNull());
      expect(api.exportMesaProfiles).toHaveBeenCalledOnce();
      expect(clicks).toHaveLength(1);
      expect(clicks[0]).toMatch(/^phoenix-mcp-mesa-profiles-\d{4}-\d{2}-\d{2}\.json$/);
    } finally {
      HTMLAnchorElement.prototype.click = origClick;
    }
  });
});

describe("Import", () => {
  beforeEach(() => vi.clearAllMocks());
  const importButton = (name = "Import") => screen.getByRole("button", { name });

  it("keeps Import unavailable until a valid archive is picked, then summarizes it", async () => {
    const { container, getByText, findByText } = render(<MesaImport onImported={() => {}} />);
    expect(importButton()).toBeDisabled();

    pickFile(container, JSON.stringify(archive));
    await findByText(/Archive contains/);
    expect(getByText(/2 entities, 1 domain/)).toBeInTheDocument();
    expect(importButton()).not.toBeDisabled();
  });

  it("rejects a file without the mesa_export root", async () => {
    const { container, findByText } = render(<MesaImport onImported={() => {}} />);
    pickFile(container, JSON.stringify({ nope: 1 }));
    await findByText(/missing the mesa_export root/i);
    expect(importButton()).toBeDisabled();
  });

  it("rejects a non-JSON file", async () => {
    const { container, findByText } = render(<MesaImport onImported={() => {}} />);
    pickFile(container, "not json {");
    await findByText(/Could not read the file as JSON/i);
  });

  it("defaults to skip; the replace checkbox turns the confirm destructive", async () => {
    vi.mocked(api.importMesaProfiles).mockResolvedValue({ imported: 2, overwritten: 0, skipped_existing: [], invalid: {} });
    const { container, getByText, findByText, getByLabelText } = render(<MesaImport onImported={() => {}} />);
    pickFile(container, JSON.stringify(archive));
    await findByText(/Archive contains/);
    // What it leaves alone reads in the Help window.
    expect(container.querySelector(".mesa-import")).toHaveAttribute("data-help-text", `${t("mesa.importIntro")} ${t("mesa.skipNote")}`);

    fireEvent.click(getByLabelText(/Replace existing profiles/i));
    expect(getByText(/Profiles with matching keys will be replaced/)).toBeInTheDocument();
    const confirm = importButton("Import and Replace");
    expect(confirm.className).toContain("btn-danger");

    fireEvent.click(confirm);
    await findByText("Import Complete");
    expect(api.importMesaProfiles).toHaveBeenCalledWith(archive, "overwrite");
  });

  it("imports with skip, reports the result and refreshes the profiles", async () => {
    vi.mocked(api.importMesaProfiles).mockResolvedValue({
      imported: 1, overwritten: 0, skipped_existing: ["entities:light.a"],
      invalid: { "entities:lock.b": "bad control_mode" },
    });
    const onImported = vi.fn();
    const { container, getByText, findByText } = render(<MesaImport onImported={onImported} />);
    pickFile(container, JSON.stringify(archive));
    await findByText(/Archive contains/);

    fireEvent.click(importButton());
    await findByText("Import Complete");
    expect(api.importMesaProfiles).toHaveBeenCalledWith(archive, "skip");
    expect(getByText(/1 profile added, 0 replaced, 1 skipped/)).toBeInTheDocument();
    expect(getByText(/failed validation/)).toBeInTheDocument();
    expect(getByText("entities:lock.b")).toBeInTheDocument();
    expect(onImported).toHaveBeenCalledOnce();
    // The same archive cannot be imported twice by accident.
    expect(importButton()).toBeDisabled();
  });
});
