/** A profile without a control mode is saved without one.
 *
 * An absent control_mode means the profile makes no control decision,
 * so the inherited or built-in mode applies. Writing a
 * default in its place would replace that baseline: a domain profile saved
 * with Autonomous makes every lock in scope autonomous. The editor therefore
 * offers "(unset)" and starts there whenever nothing was declared.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, setHass } from "../api";
import { ProfileEditor } from "../views/MesaView";

vi.mock("../phoenix-mcp-panel.css?inline", () => ({ default: "" }));

vi.mock("../api", async importOriginal => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: { getMesaDomain: vi.fn(), putMesaDomain: vi.fn(), getMesaArea: vi.fn(), putMesaArea: vi.fn() } };
});

type Doc = { semantic_profile: { operational_boundaries: Record<string, unknown>; semantic_tags?: string[] } };

beforeEach(() => {
  vi.resetAllMocks();
  setHass({ user: { id: "test-admin" } });
  vi.mocked(api.putMesaDomain).mockResolvedValue([] as never);
  vi.mocked(api.putMesaArea).mockResolvedValue([] as never);
});

const control = (c: HTMLElement) => c.querySelector('[data-field="mesa-cm"]') as HTMLSelectElement;
const savedBoundaries = (fn: typeof api.putMesaDomain | typeof api.putMesaArea) =>
  (vi.mocked(fn).mock.calls[0][1] as Doc).semantic_profile.operational_boundaries;

describe("a new profile above entity scope", () => {
  it("starts unset and saves no control mode", async () => {
    const { container } = render(<ProfileEditor scope="domain" profileKey="lock" isNew lockedKey keyLabel="lock"
      entityTree={{}} canonicalTags={[]} onClose={() => {}} onSaved={() => {}} />);
    expect(control(container).value).toBe("");
    expect(control(container).selectedOptions[0].textContent).toBe("(unset)");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putMesaDomain).toHaveBeenCalledTimes(1));
    expect(savedBoundaries(api.putMesaDomain)).not.toHaveProperty("control_mode");
  });

  it("saves a mode the operator chose", async () => {
    const { container } = render(<ProfileEditor scope="area" profileKey="hall" isNew lockedKey keyLabel="Hall"
      entityTree={{}} canonicalTags={[]} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.change(control(container), { target: { value: "confirm" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putMesaArea).toHaveBeenCalledTimes(1));
    expect(savedBoundaries(api.putMesaArea).control_mode).toBe("confirm");
  });
});

describe("a stored profile", () => {
  it("without a control mode loads unset and saves without one", async () => {
    vi.mocked(api.getMesaDomain).mockResolvedValue({
      domain: "lock", stored: { semantic_profile: { semantic_tags: ["security.lock"], operational_boundaries: { triggers_automations: "likely" } } },
    } as never);
    const { container } = render(<ProfileEditor scope="domain" profileKey="lock" isNew={false}
      entityTree={{}} canonicalTags={[]} onClose={() => {}} onSaved={() => {}} />);
    const save = screen.getByRole("button", { name: "Save" });
    await waitFor(() => expect(save).toBeEnabled());
    expect(control(container).value).toBe("");
    fireEvent.click(save);
    await waitFor(() => expect(api.putMesaDomain).toHaveBeenCalledTimes(1));
    const ob = savedBoundaries(api.putMesaDomain);
    expect(ob).not.toHaveProperty("control_mode");
    expect(ob.triggers_automations).toBe("likely");
  });

  it("can have its control mode cleared back to unset", async () => {
    vi.mocked(api.getMesaDomain).mockResolvedValue({
      domain: "lock", stored: { semantic_profile: { operational_boundaries: { control_mode: "autonomous" } } },
    } as never);
    const { container } = render(<ProfileEditor scope="domain" profileKey="lock" isNew={false}
      entityTree={{}} canonicalTags={[]} onClose={() => {}} onSaved={() => {}} />);
    const save = screen.getByRole("button", { name: "Save" });
    await waitFor(() => expect(control(container).value).toBe("autonomous"));
    fireEvent.change(control(container), { target: { value: "" } });
    fireEvent.click(save);
    await waitFor(() => expect(api.putMesaDomain).toHaveBeenCalledTimes(1));
    expect(savedBoundaries(api.putMesaDomain)).not.toHaveProperty("control_mode");
  });
});
