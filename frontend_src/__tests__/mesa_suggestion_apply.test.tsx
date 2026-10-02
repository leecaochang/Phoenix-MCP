/** Suggestions create profiles without replacing a concurrent administrator edit. */
import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, ApiError } from "../api";
import { MesaView } from "../views/MesaView";
import type { MesaIssuesResponse } from "../types";

vi.mock("../api", async importOriginal => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: {
    listMesaProfiles: vi.fn(), getMesaIssues: vi.fn(), listMesaDomains: vi.fn(),
    listMesaIntegrations: vi.fn(), listMesaAreas: vi.fn(), listMesaDevices: vi.fn(),
    getEntityTree: vi.fn(), getMesaVocabulary: vi.fn(), getMesaIntegrationOptions: vi.fn(),
    getMesaDeviceOptions: vi.fn(), putMesaDomain: vi.fn(), putMesaProfile: vi.fn(),
  } };
});
const empty: MesaIssuesResponse = { issues: [], orphans: [], orphan_devices: [], orphan_areas: [], orphan_integrations: [], suggestions: [], dismissed_suggestions: [] };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listMesaProfiles).mockResolvedValue({ profiles: [], total_matched: 0, has_more: false, next_cursor: null });
  vi.mocked(api.listMesaDomains).mockResolvedValue({ domains: [] });
  vi.mocked(api.listMesaIntegrations).mockResolvedValue({ integrations: [] });
  vi.mocked(api.listMesaAreas).mockResolvedValue({ areas: [] });
  vi.mocked(api.listMesaDevices).mockResolvedValue({ devices: [] });
  vi.mocked(api.getEntityTree).mockResolvedValue({});
  vi.mocked(api.getMesaVocabulary).mockResolvedValue({ canonical_tags: [], canonical_roots: [] });
  vi.mocked(api.getMesaIntegrationOptions).mockResolvedValue({ integrations: [] });
  vi.mocked(api.getMesaDeviceOptions).mockResolvedValue({ devices: [] });
});

it.each(["entity", "domain"] as const)("does not replace a %s profile created after the suggestion loaded", async scope => {
  const subject = scope === "entity" ? "lock.front" : "lock";
  vi.mocked(api.getMesaIssues).mockResolvedValueOnce({ ...empty, suggestions: [{ key: "suggestion", signal: "naked_risky", scope, subject_id: subject, suggested_mode: "prohibited", reason: "Physical boundary", evidence: {} }] }).mockResolvedValue(empty);
  const put = scope === "entity" ? api.putMesaProfile : api.putMesaDomain;
  vi.mocked(put).mockRejectedValue(new ApiError(412, "already_exists", "A profile already exists."));
  render(<MesaView />);
  fireEvent.click(await screen.findByRole("button", { name: "Apply" }));
  await waitFor(() => expect(put).toHaveBeenCalledExactlyOnceWith(subject, expect.any(Object), true));
  expect(await screen.findByText("A profile already exists.")).toBeVisible();
  expect(api.getMesaIssues).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
});
