/** The profile editor saves only what it loaded, and its baseline never absorbs edits.
 *
 * Saving is a full replacement, so a form left at its blank defaults by a failed
 * read would erase every authored restriction. A new entity profile seeds its
 * modes from the inherited policy after the form is already editable; edits made
 * before that read returns must survive it and must still count as unsaved.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, setHass } from "../api";
import { ProfileEditor } from "../views/MesaView";
import type { MesaProfileDetail } from "../types";

vi.mock("../phoenix-mcp-panel.css?inline", () => ({ default: "" }));

vi.mock("../api", async importOriginal => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: {
    getMesaProfile: vi.fn(), putMesaProfile: vi.fn(), getMesaDomain: vi.fn(), putMesaDomain: vi.fn(),
  } };
});

const PROHIBITED = { semantic_profile: { operational_boundaries: { control_mode: "prohibited", enforcement_mode: "enforced" } } };

beforeEach(() => {
  vi.resetAllMocks();
  setHass({ user: { id: "test-admin" } });
});

const saveButton = () => screen.getByRole("button", { name: "Save" });

describe("an existing profile whose read failed", () => {
  it("cannot be saved until a retry loads it, and then saves the stored values", async () => {
    vi.mocked(api.getMesaDomain)
      .mockRejectedValueOnce(new Error("Service unavailable"))
      .mockResolvedValueOnce({ domain: "lock", stored: PROHIBITED } as never);
    vi.mocked(api.putMesaDomain).mockResolvedValue({} as never);
    render(<ProfileEditor scope="domain" profileKey="lock" isNew={false} entityTree={{}}
      canonicalTags={[]} onClose={() => {}} onSaved={() => {}} />);

    expect(await screen.findByText("Service unavailable")).toBeVisible();
    expect(saveButton()).toBeDisabled();
    expect(screen.queryByLabelText(/Control mode/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(saveButton()).toBeEnabled());
    expect(screen.queryByText("Service unavailable")).toBeNull();
    fireEvent.click(saveButton());

    await waitFor(() => expect(api.putMesaDomain).toHaveBeenCalledTimes(1));
    const doc = vi.mocked(api.putMesaDomain).mock.calls[0][1] as typeof PROHIBITED;
    expect(doc.semantic_profile.operational_boundaries.control_mode).toBe("prohibited");
  });

  it("makes one entity read for both the stored document and the effective panel", async () => {
    vi.mocked(api.getMesaProfile).mockRejectedValue(new Error("Service unavailable"));
    render(<ProfileEditor scope="entity" profileKey="lock.front" isNew={false} entityTree={{}}
      canonicalTags={[]} onClose={() => {}} onSaved={() => {}} />);
    expect(await screen.findByText("Service unavailable")).toBeVisible();
    expect(saveButton()).toBeDisabled();
    expect(api.getMesaProfile).toHaveBeenCalledTimes(1);
  });
});

describe("a new entity profile seeded after the user started editing", () => {
  const detail: MesaProfileDetail = {
    entity_id: "lock.front", stored: null, effective: PROHIBITED,
    explanation: { entity_id: "lock.front", explanation: [], conflicts_detected: false, warnings: [] },
  };
  const entityTree = { lock: { entity_details: { "lock.front": { friendly_name: "Front door" } } } } as never;

  function renderNew(onClose = vi.fn()) {
    let resolve!: (d: MesaProfileDetail) => void;
    vi.mocked(api.getMesaProfile).mockReturnValue(new Promise((r) => { resolve = r; }));
    const { container } = render(<ProfileEditor scope="entity" profileKey="lock.front" isNew lockedKey
      keyLabel="Front door" entityTree={entityTree} canonicalTags={[]} onClose={onClose} onSaved={() => {}} />);
    const select = (id: string) => container.querySelector(`#${id}`) as HTMLSelectElement;
    return { resolve, select, onClose };
  }

  it("keeps a mode the user chose and seeds the one they did not touch", async () => {
    const { resolve, select } = renderNew();
    fireEvent.change(select("mesa-cm"), { target: { value: "confirm" } });
    resolve(detail);
    await waitFor(() => expect(select("mesa-em").value).toBe("enforced"));
    expect(select("mesa-cm").value).toBe("confirm");
  });

  it("still asks before discarding an edit made before the seed", async () => {
    const { resolve, select, onClose } = renderNew();
    fireEvent.change(select("mesa-rev"), { target: { value: "false" } });
    resolve(detail);
    await waitFor(() => expect(select("mesa-cm").value).toBe("prohibited"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Keep editing" })).toBeVisible();
  });

  it("still seeds when another picker list arrives during the read", async () => {
    let resolve!: (d: MesaProfileDetail) => void;
    vi.mocked(api.getMesaProfile).mockReturnValue(new Promise((r) => { resolve = r; }));
    const props = { scope: "entity" as const, profileKey: "lock.front", isNew: true, lockedKey: true,
      keyLabel: "Front door", entityTree, canonicalTags: [], onClose: () => {}, onSaved: () => {} };
    const { container, rerender } = render(<ProfileEditor {...props} integrationOptions={[]} />);
    rerender(<ProfileEditor {...props} integrationOptions={[{ id: "zwave_js", name: "Z-Wave" }] as never} />);
    resolve(detail);
    const select = (id: string) => container.querySelector(`#${id}`) as HTMLSelectElement;
    await waitFor(() => expect(select("mesa-cm").value).toBe("prohibited"));
    expect(select("mesa-em").value).toBe("enforced");
  });

  it("cannot be saved until the inherited modes have loaded", async () => {
    const { resolve } = renderNew();
    expect(saveButton()).toBeDisabled();
    resolve(detail);
    await waitFor(() => expect(saveButton()).toBeEnabled());
  });

  it("stays unsaveable after a failed inherited read until a retry succeeds", async () => {
    vi.mocked(api.getMesaProfile)
      .mockRejectedValueOnce(new Error("Service unavailable"))
      .mockResolvedValueOnce(detail);
    const { container } = render(<ProfileEditor scope="entity" profileKey="lock.front" isNew lockedKey
      keyLabel="Front door" entityTree={entityTree} canonicalTags={[]} onClose={() => {}} onSaved={() => {}} />);
    expect(await screen.findByText("Failed to load profile.")).toBeVisible();
    expect(saveButton()).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(saveButton()).toBeEnabled());
    expect((container.querySelector("#mesa-cm") as HTMLSelectElement).value).toBe("prohibited");
  });

  it("keeps a mode the user set back to its starting value", async () => {
    const { resolve, select } = renderNew();
    fireEvent.change(select("mesa-cm"), { target: { value: "confirm" } });
    fireEvent.change(select("mesa-cm"), { target: { value: "autonomous" } });
    resolve(detail);
    await waitFor(() => expect(select("mesa-em").value).toBe("enforced"));
    expect(select("mesa-cm").value).toBe("autonomous");
  });

  it("closes without a prompt when only the seed changed the form", async () => {
    const { resolve, select, onClose } = renderNew();
    resolve(detail);
    await waitFor(() => expect(select("mesa-cm").value).toBe("prohibited"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
