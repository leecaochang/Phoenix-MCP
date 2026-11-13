/**
 * The MESA injector loads on every Home Assistant page. For a non-admin it purges what an
 * admin's chat left and opens nothing; for an admin it harvests cards only on a dashboard.
 */
import { afterEach, expect, it, vi } from "vitest";
import { BTN_CLASS } from "../inject/dom";

const harvest = vi.hoisted(() => ({ calls: 0 }));

vi.mock("../api", () => ({
  setHass: vi.fn(),
  api: {
    listMesaProfiles: async () => ({ profiles: [], has_more: false }),
    listMesaDevices: async () => ({ devices: [] }),
    listMesaAreas: async () => ({ areas: [] }),
    listMesaIntegrations: async () => ({ integrations: [] }),
  },
}));
vi.mock("../utils/card_harvest", () => ({ syncCardCatalog: vi.fn(async () => { harvest.calls += 1; }) }));
vi.mock("../i18n", () => ({
  loadTranslations: async () => true,
  syncTranslations: async () => false,
  resolveLanguage: () => "en",
  t: (key: string) => key,
}));
vi.mock("../inject/QuickAdd", () => ({ QUICK_ADD_TAG: "phx-mesa-quick-add-gate-test" }));

afterEach(() => {
  (window as unknown as { __phxMesaInjectActive?: { dispose: () => void } }).__phxMesaInjectActive?.dispose();
  delete (window as unknown as Record<string, unknown>).__phxMesaInjectCompatibility;
  document.body.innerHTML = "";
  localStorage.clear();
  harvest.calls = 0;
  vi.useRealTimers();
  vi.resetModules();
});

type Ha = HTMLElement & { hass: Record<string, unknown> };

function page(path: string, user: Record<string, unknown>, extra: Record<string, unknown> = {}): Ha {
  vi.useFakeTimers();
  window.history.replaceState({}, "", path);
  const ha = document.createElement("home-assistant") as Ha;
  ha.hass = { user, states: {}, ...extra };
  document.body.appendChild(ha);
  return ha;
}

async function boot(): Promise<void> {
  await import("../inject/index");
  await vi.advanceTimersByTimeAsync(200);
}

it("removes an admin's saved chat when a non-admin's page loads it", async () => {
  localStorage.setItem("phx-agentcli-conversation:an-admin", JSON.stringify({ turns: [{ text: "tool output" }] }));
  page("/config/entities", { id: "member", is_admin: false });
  await boot();
  expect(Object.keys(localStorage).filter((key) => key.startsWith("phx-agentcli"))).toEqual([]);
});

it("opens no editor when the user stopped being an admin after the click", async () => {
  const ha = page("/config/entities", { id: "admin", is_admin: true });
  const table = document.createElement("ha-data-table");
  ha.attachShadow({ mode: "open" }).appendChild(table);
  const root = table.attachShadow({ mode: "open" });
  root.innerHTML = '<div role="row"><div role="cell"><ha-entity-id-icon></ha-entity-id-icon></div></div>';
  const row = root.querySelector<HTMLElement>('[role="row"]')!;
  Object.assign(row, { rowId: "light.desk" });
  await boot();
  const button = row.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`)!;
  expect(button).not.toBeNull();

  ha.hass = { ...ha.hass, user: { id: "admin", is_admin: false } };
  button.click();
  await vi.advanceTimersByTimeAsync(200);
  expect(document.querySelector("phx-mesa-quick-add-gate-test")).toBeNull();
});

it("harvests cards on a dashboard page", async () => {
  page("/lovelace/0", { id: "admin", is_admin: true }, { panels: { lovelace: { component_name: "lovelace" } } });
  await boot();
  expect(harvest.calls).toBe(1);
});

it("does not harvest on a configuration page", async () => {
  page("/config/entities", { id: "admin", is_admin: true }, { panels: { config: { component_name: "config" } } });
  await boot();
  expect(harvest.calls).toBe(0);
});
