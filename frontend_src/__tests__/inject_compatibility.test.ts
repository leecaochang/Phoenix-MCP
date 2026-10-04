import { afterEach, describe, expect, it, vi } from "vitest";
import { BTN_CLASS } from "../inject/dom";
import { injectSessionBlocked } from "../inject/compatibility";

vi.mock("../api", () => ({
  setHass: vi.fn(),
  api: {
    listMesaProfiles: async () => ({ profiles: [{ entity_id: "light.profiled" }], has_more: false }),
    listMesaDevices: async () => ({ devices: [] }),
    listMesaAreas: async () => ({ areas: [] }),
    listMesaIntegrations: async () => ({ integrations: [] }),
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

// HA inherits this conditional slot from @material/mwc-list 0.27.0:
// ListItemBase.render() only calls renderMeta() when hasMeta is true.
if (!customElements.get("ha-list-item")) {
  customElements.define("ha-list-item", class extends HTMLElement {
    static observedAttributes = ["hasmeta"];
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
    }
    connectedCallback() { this.render(); }
    attributeChangedCallback() { this.render(); }
    private render() {
      this.shadowRoot!.innerHTML = '<slot></slot>' +
        (this.hasAttribute("hasmeta") ? '<slot name="meta"></slot>' : "");
    }
  });
}

afterEach(() => {
  const host = window as unknown as { __phxMesaInjectActive?: { dispose: () => void } };
  host.__phxMesaInjectActive?.dispose();
  delete (window as unknown as Record<string, unknown>).__phxMesaInjectCompatibility;
  document.body.innerHTML = "";
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.resetModules();
});

function start(path: string, states = {}, admin = true) {
  vi.useFakeTimers();
  window.history.replaceState({}, "", path);
  const ha = document.createElement("home-assistant");
  Object.assign(ha, { hass: { user: { is_admin: admin }, states } });
  document.body.appendChild(ha);
  return ha.attachShadow({ mode: "open" });
}

async function boot() {
  await import("../inject/index");
  await vi.advanceTimersByTimeAsync(200);
}

// Templates taken from home-assistant/frontend tag 20260826.7, pinned by HA
// 2026.9.3: ha-config-entities/helpers/person, ha-automation-picker, ha-data-table.
describe("HA frontend 20260826.7 injection templates", () => {
  function entityTable(markup: string, rowId = "light.example") {
    const sr = start("/config/entities");
    const table = document.createElement("ha-data-table");
    sr.appendChild(table);
    const root = table.attachShadow({ mode: "open" });
    root.innerHTML = `<div role="row"><div role="cell">${markup}</div></div>`;
    Object.assign(root.firstElementChild!, { rowId });
    return { table, root, row: root.firstElementChild as HTMLElement };
  }

  it("immediately disables the session when the existing row error guard catches a failure", async () => {
    const { root, row } = entityTable("<ha-icon></ha-icon>");
    const healthy = document.createElement("div");
    healthy.setAttribute("role", "row");
    Object.assign(healthy, { rowId: "light.healthy" });
    healthy.innerHTML = '<div role="cell"><ha-icon></ha-icon></div>';
    root.prepend(healthy);
    const cell = row.firstElementChild!;
    vi.spyOn(cell, "insertBefore").mockImplementation(() => { throw new DOMException("Changed HA structure", "NotFoundError"); });
    await boot();
    expect(injectSessionBlocked()).toBe(true);
    expect(root.querySelector(`.${BTN_CLASS}`)).toBeNull();
    await vi.advanceTimersByTimeAsync(4000);
    expect(root.querySelector(`.${BTN_CLASS}`)).toBeNull();
  });

  it("disables immediately through the existing outer scan error guard", async () => {
    start("/config/integrations/integration/%");
    await boot();
    expect(injectSessionBlocked()).toBe(true);
  });

  it("keeps the existing retry behavior for missing icons and empty tables", async () => {
    const { root } = entityTable("");
    await boot();
    await vi.advanceTimersByTimeAsync(10000);
    expect(injectSessionBlocked()).toBe(false);
    root.querySelector('[role="cell"]')!.appendChild(document.createElement("ha-icon"));
    await vi.advanceTimersByTimeAsync(2200);
    expect(root.querySelector(`.${BTN_CLASS}`)).not.toBeNull();
    root.replaceChildren();
    await vi.advanceTimersByTimeAsync(10000);
    expect(injectSessionBlocked()).toBe(false);
  });

  it.each([
    ["/config/entities", "light.new", "<ha-entity-id-icon></ha-entity-id-icon>"],
    ["/config/entities", "light.profiled", "<ha-icon></ha-icon>"],
    ["/config/helpers", "input_boolean.guest", "<ha-entity-id-icon></ha-entity-id-icon>"],
    ["/config/helpers", "sensor.unavailable", "<ha-svg-icon></ha-svg-icon>"],
    ["/config/automation/dashboard", "automation.morning", "<div><ha-state-icon></ha-state-icon><ha-svg-icon></ha-svg-icon></div>"],
    ["/config/scene/dashboard", "scene.evening", "<ha-state-icon></ha-state-icon>"],
    ["/config/script/dashboard", "script.evening", "<ha-state-icon></ha-state-icon>"],
  ])("decorates %s row %s and opens its profile", async (path, entityId, markup) => {
    const sr = start(path);
    const table = document.createElement("ha-data-table");
    sr.appendChild(table);
    const root = table.attachShadow({ mode: "open" });
    root.innerHTML = `<div role="row"><div role="cell">${markup}</div></div>`;
    const row = root.querySelector<HTMLElement>('[role="row"]')!;
    Object.assign(row, { rowId: entityId });
    const haClick = vi.fn();
    row.addEventListener("click", haClick);
    await boot();
    const button = row.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`)!;
    expect(button).not.toBeNull();
    expect(button.dataset.phxState).toBe(entityId === "light.profiled" ? "has" : "new");
    button.click();
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector("phx-mesa-quick-add-test-build")?.getAttribute("key")).toBe(entityId);
    expect(haClick).not.toHaveBeenCalled();
    Object.assign(row, { rowId: "light.recycled" });
    await vi.advanceTimersByTimeAsync(2200);
    expect(row.querySelectorAll(`.${BTN_CLASS}`)).toHaveLength(1);
    row.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`)!.click();
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelectorAll("phx-mesa-quick-add-test-build")[1].getAttribute("key")).toBe("light.recycled");
    Object.assign(row, { rowId: "not-an-entity" });
    await vi.advanceTimersByTimeAsync(2200);
    expect(row.querySelector(`.${BTN_CLASS}`)).toBeNull();
  });

  it("maps storage and YAML people by id, including renamed entities, and skips unresolved people", async () => {
    const sr = start("/config/person", {
      "person.renamed": { attributes: { id: "storage-id" } },
      "person.yaml": { attributes: { id: "yaml-id" } },
    });
    const page = document.createElement("ha-config-person");
    sr.appendChild(page);
    const root = page.attachShadow({ mode: "open" });
    for (const id of ["storage-id", "yaml-id", "missing"]) {
      const row = document.createElement("ha-list-item");
      const badge = document.createElement("ha-person-badge");
      Object.assign(badge, { person: { id, name: "Same display name" } });
      row.appendChild(badge);
      if (id === "yaml-id") row.setAttribute("hasmeta", "");
      root.appendChild(row);
    }
    await boot();
    const buttons = root.querySelectorAll<HTMLButtonElement>(`.${BTN_CLASS}`);
    expect(Array.from(buttons, (b) => b.dataset.phxKey)).toEqual(["person.renamed", "person.yaml"]);
    expect(buttons[0].slot).toBe("meta");
    expect(buttons[0].parentElement!.hasAttribute("hasmeta")).toBe(true);
    for (const button of buttons) {
      const slot = button.parentElement!.shadowRoot!.querySelector<HTMLSlotElement>('slot[name="meta"]');
      expect(slot?.assignedElements()).toContain(button);
    }
    buttons[0].click();
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector("phx-mesa-quick-add-test-build")?.getAttribute("key")).toBe("person.renamed");
    await vi.advanceTimersByTimeAsync(2200);
    expect(root.querySelectorAll(`.${BTN_CLASS}`)).toHaveLength(2);
    const controller = (window as unknown as { __phxMesaInjectActive: { dispose: () => void } }).__phxMesaInjectActive;
    controller.dispose();
    expect(root.querySelector(`.${BTN_CLASS}`)).toBeNull();
    const rows = root.querySelectorAll("ha-list-item");
    expect(rows[0].hasAttribute("hasmeta")).toBe(false);
    expect(rows[1].hasAttribute("hasmeta")).toBe(true);
  });

  it("preserves integration detail toolbar injection", async () => {
    start("/config/integrations/integration/hue");
    const page = document.createElement("hass-subpage");
    // jsdom 29 incorrectly returns null for :scope child selectors on hosts
    // inside a shadow root. Keep this toolbar path in light DOM;
    // entity and People fixtures above exercise shadow-root discovery.
    document.body.appendChild(page);
    page.attachShadow({ mode: "open" }).innerHTML = '<div class="main-title"></div>';
    page.innerHTML = '<ha-icon-button slot="toolbar-icon"></ha-icon-button>';
    await boot();
    const button = page.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`)!;
    expect(button.slot).toBe("toolbar-icon");
    button.click();
    await vi.advanceTimersByTimeAsync(0);
    const modal = document.querySelector("phx-mesa-quick-add-test-build")!;
    expect(modal.getAttribute("scope")).toBe("integration");
    expect(modal.getAttribute("key")).toBe("hue");
    const controller = (window as unknown as { __phxMesaInjectActive: { dispose: () => void } }).__phxMesaInjectActive;
    controller.dispose();
    expect(document.querySelector("phx-mesa-quick-add-test-build")).toBeNull();
  });

  it("does not inject for non-admin users", async () => {
    const sr = start("/config/entities", {}, false);
    const table = document.createElement("ha-data-table");
    sr.appendChild(table);
    const root = table.attachShadow({ mode: "open" });
    root.innerHTML = '<div role="row"><div role="cell"><ha-entity-id-icon></ha-entity-id-icon></div></div>';
    Object.assign(root.firstElementChild!, { rowId: "light.example" });
    await boot();
    expect(root.querySelector(`.${BTN_CLASS}`)).toBeNull();
  });
});


it("waits for a late HA identity and removes controls when authority changes", async () => {
  const root = start("/config/entities");
  const ha = document.querySelector("home-assistant") as HTMLElement & { hass: { user?: { id: string; is_admin: boolean } } };
  ha.hass.user = undefined;
  const table = document.createElement("ha-data-table");
  root.appendChild(table);
  const rows = table.attachShadow({ mode: "open" });
  rows.innerHTML = '<div role="row"><div role="cell"><ha-icon></ha-icon></div></div>';
  Object.assign(rows.firstElementChild!, { rowId: "light.example" });
  await boot();
  await vi.advanceTimersByTimeAsync(12000);
  expect(rows.querySelector(`.${BTN_CLASS}`)).toBeNull();
  ha.hass.user = { id: "first-admin", is_admin: true };
  await vi.advanceTimersByTimeAsync(2500);
  expect(rows.querySelector(`.${BTN_CLASS}`)).not.toBeNull();
  ha.hass.user = { id: "regular-user", is_admin: false };
  await vi.advanceTimersByTimeAsync(5500);
  expect(rows.querySelector(`.${BTN_CLASS}`)).toBeNull();
  ha.hass.user = { id: "second-admin", is_admin: true };
  await vi.advanceTimersByTimeAsync(2500);
  expect(rows.querySelector(`.${BTN_CLASS}`)).not.toBeNull();
});
