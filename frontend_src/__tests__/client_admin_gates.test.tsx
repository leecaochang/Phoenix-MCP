/**
 * Client-side refusals the server's admin check does not replace: the card harvester
 * imports and reports nothing for a non-admin, and the OAuth approval dialog offers no
 * approval when Phoenix is shown inside another page's frame.
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";

vi.mock("../api", () => ({
  api: { postCardCatalog: vi.fn().mockResolvedValue({}), getOAuthRequest: vi.fn(), approveOAuthRequest: vi.fn(), denyOAuthRequest: vi.fn() },
  currentHass: vi.fn(),
}));
import { api, currentHass } from "../api";
import { syncCardCatalog } from "../utils/card_harvest";
import { OAuthRequestDialog } from "../components/OAuthRequestDialog";

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});
afterEach(() => { vi.restoreAllMocks(); });

it("harvests nothing and reports nothing for a non-admin", async () => {
  const sendMessagePromise = vi.fn().mockResolvedValue([{ type: "module", url: "/local/card.js" }]);
  vi.mocked(currentHass).mockReturnValue({ user: { id: "m", is_admin: false }, connection: { sendMessagePromise } } as never);
  await syncCardCatalog(true, { minWaitMs: 0, settleTimeoutMs: 0, followUpMs: [] });
  expect(sendMessagePromise).not.toHaveBeenCalled();
  expect(api.postCardCatalog).not.toHaveBeenCalled();
  // Nor does a non-admin page claim the harvest that an admin's tab may own.
  expect((window as unknown as Record<string, unknown>).__phxCardCatalogHarvest).toBeUndefined();
});

it("offers no approval when the panel is framed by another page", async () => {
  vi.mocked(api.getOAuthRequest).mockResolvedValue({
    id: "r1", client_id: "https://app.example/c.json", client_host: "app.example", client_name: "Test App",
    verified: true, problem: null, redirect_host: "app.example", loopback_only: false,
    created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 600_000).toISOString(),
    keys: [{ id: "k1", name: "phone", persona: "custom", pass_through: false,
             window_open_until: new Date(Date.now() + 600_000).toISOString(), at_cap: false }],
  } as never);
  vi.spyOn(window, "top", "get").mockReturnValue({} as Window);
  render(<OAuthRequestDialog link="r1" onClose={() => {}} />);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(api.getOAuthRequest).not.toHaveBeenCalled();
});

it("does offer approval on the panel's own page", async () => {
  vi.mocked(api.getOAuthRequest).mockResolvedValue({
    id: "r1", client_id: "https://app.example/c.json", client_host: "app.example", client_name: "Test App",
    verified: true, problem: null, redirect_host: "app.example", loopback_only: false,
    created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 600_000).toISOString(),
    keys: [{ id: "k1", name: "phone", persona: "custom", pass_through: false,
             window_open_until: new Date(Date.now() + 600_000).toISOString(), at_cap: false }],
  } as never);
  render(<OAuthRequestDialog link="r1" onClose={() => {}} />);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
});
