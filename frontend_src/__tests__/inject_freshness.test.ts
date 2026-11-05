/**
 * The in-context buttons read which targets have a profile once per page load.
 * A profile made since then (in the panel or another tab) must open in edit
 * mode, not in create mode whose Save fails because the profile exists, and a
 * profile deleted since then must open in create mode.
 */
import { afterEach, expect, it, vi } from "vitest";
import { BTN_CLASS } from "../inject/dom";

const stored = vi.hoisted(() => ({ entities: [] as string[], integrations: [] as string[] }));

vi.mock("../api", () => ({
  setHass: vi.fn(),
  api: {
    listMesaProfiles: async () => ({ profiles: stored.entities.map((entity_id) => ({ entity_id })), has_more: false }),
    listMesaDevices: async () => ({ devices: [] }),
    listMesaAreas: async () => ({ areas: [] }),
    listMesaIntegrations: async () => ({ integrations: stored.integrations.map((integration) => ({ integration })) }),
  },
}));
vi.mock("../utils/card_harvest", () => ({ syncCardCatalog: vi.fn() }));
vi.mock("../i18n", () => ({
  loadTranslations: async () => true,
  syncTranslations: async () => false,
  resolveLanguage: () => "en",
  t: (key: string) => key,
}));
vi.mock("../inject/QuickAdd", () => ({ QUICK_ADD_TAG: "phx-mesa-quick-add-test-build" }));

afterEach(() => {
  const host = window as unknown as { __phxMesaInjectActive?: { dispose: () => void } };
  host.__phxMesaInjectActive?.dispose();
  delete (window as unknown as Record<string, unknown>).__phxMesaInjectCompatibility;
  document.body.innerHTML = "";
  stored.entities = [];
  stored.integrations = [];
  vi.useRealTimers();
  vi.resetModules();
});

function start(path: string) {
  vi.useFakeTimers();
  window.history.replaceState({}, "", path);
  const ha = document.createElement("home-assistant");
  Object.assign(ha, { hass: { user: { id: "admin", is_admin: true }, states: {} } });
  document.body.appendChild(ha);
  return ha.attachShadow({ mode: "open" });
}

async function boot() {
  await import("../inject/index");
  await vi.advanceTimersByTimeAsync(200);
}

function entityRow(entityId: string) {
  const sr = start("/config/entities");
  const table = document.createElement("ha-data-table");
  sr.appendChild(table);
  const root = table.attachShadow({ mode: "open" });
  root.innerHTML = '<div role="row"><div role="cell"><ha-entity-id-icon></ha-entity-id-icon></div></div>';
  const row = root.querySelector<HTMLElement>('[role="row"]')!;
  Object.assign(row, { rowId: entityId });
  return row;
}

const modal = () => document.querySelector("phx-mesa-quick-add-test-build");

it("opens a profile made elsewhere in edit mode and shows it as profiled", async () => {
  const row = entityRow("light.made_elsewhere");
  await boot();
  const button = row.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`)!;
  expect(button.dataset.phxState).toBe("new");

  stored.entities = ["light.made_elsewhere"];
  button.click();
  await vi.advanceTimersByTimeAsync(200);
  expect(modal()?.getAttribute("has-profile")).toBe("1");
  expect(row.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`)!.dataset.phxState).toBe("has");
});

it("opens a profile deleted elsewhere in create mode", async () => {
  stored.entities = ["light.deleted_elsewhere"];
  const row = entityRow("light.deleted_elsewhere");
  await boot();
  expect(row.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`)!.dataset.phxState).toBe("has");

  stored.entities = [];
  row.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`)!.click();
  await vi.advanceTimersByTimeAsync(200);
  expect(modal()).not.toBeNull();
  expect(modal()!.hasAttribute("has-profile")).toBe(false);
  expect(row.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`)!.dataset.phxState).toBe("new");
});

it("reads a detail page's own scope again before opening", async () => {
  start("/config/integrations/integration/hue");
  const page = document.createElement("hass-subpage");
  document.body.appendChild(page);
  page.attachShadow({ mode: "open" }).innerHTML = '<div class="main-title"></div>';
  page.innerHTML = '<ha-icon-button slot="toolbar-icon"></ha-icon-button>';
  await boot();
  stored.integrations = ["hue"];
  page.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`)!.click();
  await vi.advanceTimersByTimeAsync(200);
  expect(modal()?.getAttribute("scope")).toBe("integration");
  expect(modal()?.getAttribute("has-profile")).toBe("1");
});

it("replaces the button when Home Assistant reuses a detail page for another target", async () => {
  start("/config/integrations/integration/hue");
  const page = document.createElement("hass-subpage");
  document.body.appendChild(page);
  page.attachShadow({ mode: "open" }).innerHTML = '<div class="main-title"></div>';
  page.innerHTML = '<ha-icon-button slot="toolbar-icon"></ha-icon-button>';
  await boot();
  expect(page.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`)!.dataset.phxKey).toBe("hue");

  // The page stays in the DOM with Phoenix's button first in the slot; only the URL moves.
  window.history.pushState({}, "", "/config/integrations/integration/mqtt");
  window.dispatchEvent(new Event("location-changed"));
  await vi.advanceTimersByTimeAsync(200);

  const buttons = page.querySelectorAll<HTMLButtonElement>(`.${BTN_CLASS}`);
  expect([...buttons].map((button) => button.dataset.phxKey)).toEqual(["mqtt"]);
  expect((window as unknown as { __phxMesaInjectCompatibility?: { blocked: boolean } }).__phxMesaInjectCompatibility?.blocked).toBe(false);
});
