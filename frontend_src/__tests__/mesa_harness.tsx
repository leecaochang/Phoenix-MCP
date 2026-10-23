/**
 * Renders the MESA workspace as the shell does: its windows around their
 * shared state. Outside the shell each window draws its tools inside it.
 * `onFindings` receives which conditional windows have something to show.
 */

import { render, screen, within } from "@testing-library/react";
import { vi } from "vitest";
import { api } from "../api";
import { MesaProvider, type MesaFindings } from "../shell/mesa";
import type { MesaIssuesResponse } from "../types";
import {
  AdvisoryWindow, DiagnosticsWindow, MesaImportWindow, MesaProfilesWindow, OrphanedProfilesWindow, ProfileEditorWindow, SuggestedProfilesWindow,
} from "../views/MesaWindows";

/** The api functions the MESA workspace calls; mock `../api` with these. */
export const MESA_API = [
  "listMesaProfiles", "getMesaIssues", "listMesaDomains", "listMesaIntegrations", "listMesaAreas", "listMesaDevices",
  "getEntityTree", "getMesaVocabulary", "getMesaIntegrationOptions", "getMesaDeviceOptions",
  "putMesaDomain", "putMesaProfile", "putMesaDevice", "putMesaArea", "putMesaIntegration",
  "getMesaProfile", "getMesaDomain", "getMesaDevice", "getMesaArea", "getMesaIntegration",
  "deleteMesaProfile", "deleteMesaDomain", "deleteMesaDevice", "deleteMesaArea", "deleteMesaIntegration",
  "dismissMesaSuggestion", "restoreMesaSuggestions", "exportMesaProfiles", "importMesaProfiles", "clearMesaOrphans",
] as const;

export const EMPTY_ISSUES: MesaIssuesResponse = { issues: [], orphans: [], orphan_devices: [], orphan_areas: [], orphan_integrations: [], suggestions: [], dismissed_suggestions: [] };

/** Nothing stored, no findings. */
export function mockEmptyMesa(): void {
  vi.mocked(api.listMesaProfiles).mockResolvedValue({ profiles: [], total_matched: 0, has_more: false, next_cursor: null });
  vi.mocked(api.getMesaIssues).mockResolvedValue(EMPTY_ISSUES);
  vi.mocked(api.listMesaDomains).mockResolvedValue({ domains: [] });
  vi.mocked(api.listMesaIntegrations).mockResolvedValue({ integrations: [] });
  vi.mocked(api.listMesaAreas).mockResolvedValue({ areas: [] });
  vi.mocked(api.listMesaDevices).mockResolvedValue({ devices: [] });
  vi.mocked(api.getEntityTree).mockResolvedValue({});
  vi.mocked(api.getMesaVocabulary).mockResolvedValue({ canonical_tags: [], canonical_roots: [] });
  vi.mocked(api.getMesaIntegrationOptions).mockResolvedValue({ integrations: [] });
  vi.mocked(api.getMesaDeviceOptions).mockResolvedValue({ devices: [] });
}

export function renderMesa({ onFindings }: { onFindings?: (f: MesaFindings | null) => void } = {}) {
  return render(
    <MesaProvider active onFindings={onFindings}>
      <section aria-label="Profiles window"><MesaProfilesWindow /></section>
      <section aria-label="Profile Editor window"><ProfileEditorWindow /></section>
      <section aria-label="Suggested Profiles window"><SuggestedProfilesWindow /></section>
      <section aria-label="Orphaned Profiles window"><OrphanedProfilesWindow /></section>
      <section aria-label="Advisory window"><AdvisoryWindow /></section>
      <section aria-label="Diagnostics window"><DiagnosticsWindow /></section>
      <section aria-label="Import window"><MesaImportWindow /></section>
    </MesaProvider>,
  );
}

const win = (name: string) => within(screen.getByRole("region", { name: `${name} window` }));
export const profilesWindow = () => win("Profiles");
export const editorWindow = () => win("Profile Editor");
export const suggestedWindow = () => win("Suggested Profiles");
export const orphansWindow = () => win("Orphaned Profiles");
export const advisoryWindow = () => win("Advisory");
export const diagnosticsWindow = () => win("Diagnostics");

/** The table rows of a window, in shown order. */
export const rowsIn = (name: string): HTMLElement[] => [...screen.getByRole("region", { name: `${name} window` }).querySelectorAll<HTMLElement>("tbody tr[data-row]")];
