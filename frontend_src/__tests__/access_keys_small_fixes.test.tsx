/**
 * The smaller Access Keys panel fixes: an archive that
 * failed to load says so, and capability rows save independently.
 */
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "../api";
import { ArchivedAccessKeysWindow } from "../views/AccessKeyList";
import { CapabilityMatrix } from "../components/CapabilityMatrix";
import type { AccessKeyRecord } from "../types";

vi.mock("../api", () => ({ api: { listArchivedAccessKeys: vi.fn(), patchAccessKey: vi.fn() }, currentHass: () => null }));

describe("smaller Access Keys fixes", () => {
  it("says the archive failed to load instead of claiming it is empty", async () => {
    vi.mocked(api.listArchivedAccessKeys).mockRejectedValue(new Error("Server unreachable."));
    render(<ArchivedAccessKeysWindow />);
    expect(await screen.findByText("Server unreachable.")).toBeTruthy();
    expect(screen.queryByText("No archived access keys.")).toBeNull();
  });

  it("saves a second capability row clicked while the first is saving", async () => {
    let finish!: () => void;
    const key = { id: "k", cap_search: "deny", cap_registry_read: "deny" } as unknown as AccessKeyRecord;
    vi.mocked(api.patchAccessKey)
      .mockImplementationOnce(() => new Promise((resolve) => { finish = () => resolve(key); }))
      .mockResolvedValue(key);
    render(<CapabilityMatrix accessKey={key} onUpdate={vi.fn()} />);
    const allow = screen.getAllByRole("button", { name: /^Allow/ });
    fireEvent.click(allow[0]);
    fireEvent.click(allow[1]);
    await waitFor(() => expect(api.patchAccessKey).toHaveBeenCalledTimes(2));
    finish();
  });
});
