/**
 * The Filter menu narrows the Permissions tree to an area, label or integration,
 * keeping the devices and domains above each match. It is a filter only: it
 * never writes a permission, and it combines with the text filter.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { EntityTree as EntityTreeData, IntegrationPermissionOption, PermissionTree } from "../types";

const mocks = vi.hoisted(() => ({
  getEntityTree: vi.fn(),
  getEntityHints: vi.fn(),
  getPermissionIntegrationOptions: vi.fn(),
  patchDomainPermission: vi.fn(),
  patchDevicePermission: vi.fn(),
  patchEntityPermission: vi.fn(),
  setPermissions: vi.fn(),
}));

// No Home Assistant connection: the tree does not follow the registries here.
vi.mock("../api", () => ({ api: mocks, currentHass: () => null }));

import { EntityTree } from "../components/EntityTree";

const KITCHEN_DEVICE = "dev-kitchen";
const HALL_DEVICE = "dev-hall";
const HALL_PART = "dev-hall-part";

const entity = (entity_id: string, friendly_name: string, device_id: string | null, area: [string, string] | null, labels: string[] = []) => ({
  entity_id, friendly_name, device_id,
  area_id: area?.[0] ?? null, area_name: area?.[1] ?? null,
  labels: labels.map((id) => ({ id, name: id === "main" ? "Main" : id })),
});

const TREE: EntityTreeData = {
  light: {
    devices: {
      [KITCHEN_DEVICE]: { device_id: KITCHEN_DEVICE, name: "Kitchen ceiling", area_id: "kitchen", area_name: "Kitchen", entities: ["light.kitchen_ceiling"] },
      [HALL_DEVICE]: { device_id: HALL_DEVICE, name: "Hall strip", area_id: "hall", area_name: "Hall", entities: ["light.hall_strip"] },
      [HALL_PART]: { device_id: HALL_PART, parent_device_id: HALL_DEVICE, name: "Hall strip segment", area_id: "hall", area_name: "Hall", entities: ["light.hall_segment"] },
    },
    deviceless_entities: ["light.desk"],
    entity_details: {
      "light.kitchen_ceiling": entity("light.kitchen_ceiling", "Kitchen ceiling", KITCHEN_DEVICE, ["kitchen", "Kitchen"], ["main"]),
      "light.hall_strip": entity("light.hall_strip", "Hall strip", HALL_DEVICE, ["hall", "Hall"]),
      "light.hall_segment": entity("light.hall_segment", "Hall strip segment", HALL_PART, ["hall", "Hall"]),
      "light.desk": entity("light.desk", "Desk lamp", null, null, ["main"]),
    },
  },
  sensor: {
    devices: {},
    deviceless_entities: ["sensor.kitchen_temp", "sensor.outdoor"],
    entity_details: {
      "sensor.kitchen_temp": entity("sensor.kitchen_temp", "Kitchen temperature", null, ["kitchen", "Kitchen"]),
      "sensor.outdoor": entity("sensor.outdoor", "Outdoor temperature", null, null),
    },
  },
};

const BRIDGE: IntegrationPermissionOption = {
  entry_id: "entry-bridge", domain: "hue", title: "Strip Bridge",
  device_count: 1, deviceless_entity_count: 1, registry_only_deviceless_count: 0, required_domain_ids: [], shared_device_count: 0,
  device_ids: [HALL_DEVICE], entity_ids: ["sensor.outdoor"],
};

// An entry without a title of its own is named by its integration's domain.
const UNTITLED: IntegrationPermissionOption = { ...BRIDGE, entry_id: "entry-untitled", domain: "hacs", title: "", device_count: 1, deviceless_entity_count: 0, device_ids: [KITCHEN_DEVICE], entity_ids: [] };

const NONE: PermissionTree = { domains: {}, devices: {}, entities: {} };
const ALL = ["light.kitchen_ceiling", "light.hall_strip", "light.hall_segment", "light.desk", "sensor.kitchen_temp", "sensor.outdoor"];

/** The entity ids the tree currently shows. */
const shown = () => [...document.querySelectorAll(".tree-entity-id")].map((el) => el.textContent);

async function renderTree(selectBy = true) {
  render(<EntityTree accessKeyId="access-key-1" permissions={NONE} onPermissionsChange={vi.fn()} selectBy={selectBy} />);
  await screen.findByRole("button", { name: "Expand light" });
}

function choose(grouping: "Reset" | "Area" | "Label" | "Integration") {
  fireEvent.click(screen.getByRole("button", { name: "Filter" }));
  fireEvent.click(screen.getByRole(grouping === "Reset" ? "menuitem" : "menuitemradio", { name: grouping }));
}

function expectNoGrant() {
  for (const call of [mocks.patchDomainPermission, mocks.patchDevicePermission, mocks.patchEntityPermission, mocks.setPermissions]) {
    expect(call).not.toHaveBeenCalled();
  }
}

describe("Filter in the Permissions tree", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getEntityTree.mockResolvedValue(TREE);
    mocks.getEntityHints.mockResolvedValue({ entity_hints: {} });
    mocks.getPermissionIntegrationOptions.mockResolvedValue({ integrations: [BRIDGE, UNTITLED] });
  });

  it("narrows the tree to an area with the devices and domains above it, without granting anything", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));
    expect(screen.getByRole("menuitem", { name: "Reset" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Area" }));

    const area = screen.getByRole("combobox", { name: "Area" });
    expect(area).toHaveFocus();
    expect([...area.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["-- Select area --", "Hall (2 entities)", "Kitchen (2 entities)"]);
    // A grouping with no value chosen yet still shows the whole tree.
    expect(screen.getByRole("button", { name: "Expand light" })).toBeInTheDocument();

    fireEvent.change(area, { target: { value: "kitchen" } });
    await waitFor(() => expect(shown().sort()).toEqual(["light.kitchen_ceiling", "sensor.kitchen_temp"]));
    expect(screen.getByText("Kitchen ceiling", { selector: ".tree-device-name *" })).toBeInTheDocument();
    expect(screen.queryByText("Hall strip", { selector: ".tree-device-name *" })).toBeNull();
    expect(screen.getByRole("button", { name: "Filter" })).toHaveClass("is-active");
    expectNoGrant();
  });

  it("combines with the text filter, and Reset shows the whole tree again", async () => {
    await renderTree();
    choose("Label");
    fireEvent.change(screen.getByRole("combobox", { name: "Label" }), { target: { value: "main" } });
    await waitFor(() => expect(shown().sort()).toEqual(["light.desk", "light.kitchen_ceiling"]));
    // A domain with nothing in the group is left out.
    expect(screen.queryByText("sensor", { selector: ".tree-domain-label" })).toBeNull();

    const text = screen.getByRole("textbox", { name: "Filter domains, devices, and entities" });
    fireEvent.change(text, { target: { value: "desk" } });
    await waitFor(() => expect(shown()).toEqual(["light.desk"]));
    fireEvent.change(text, { target: { value: "outdoor" } });
    expect(await screen.findByRole("status")).toHaveTextContent("No domains, devices, entities, or stored grants match this filter.");
    fireEvent.change(text, { target: { value: "" } });

    choose("Reset");
    expect(screen.queryByRole("combobox", { name: "Label" })).toBeNull();
    // Leaving the filter restores the folded tree it opened.
    expect(await screen.findByRole("button", { name: "Expand light" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Expand light" }));
    fireEvent.click(screen.getByRole("button", { name: "Expand sensor" }));
    expect(screen.getAllByRole("button", { name: /^Expand Hall strip|^Expand Kitchen ceiling/ })).toHaveLength(2);
    expectNoGrant();
  });

  it("an integration brings its devices with their child devices and its deviceless entities, loaded once", async () => {
    await renderTree();
    choose("Integration");
    const integration = await screen.findByRole("combobox", { name: "Integration" });
    await waitFor(() => expect(integration).not.toBeDisabled());
    expect(mocks.getPermissionIntegrationOptions).toHaveBeenCalledWith("access-key-1");
    expect([...integration.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["-- Select integration --", "hacs (1 resource)", "Strip Bridge (hue) (2 resources)"]);

    fireEvent.change(integration, { target: { value: "entry-bridge" } });
    await waitFor(() => expect(shown().sort()).toEqual(["light.hall_segment", "light.hall_strip", "sensor.outdoor"]));

    choose("Area");
    choose("Integration");
    await screen.findByRole("combobox", { name: "Integration" });
    expect(mocks.getPermissionIntegrationOptions).toHaveBeenCalledTimes(1);
    expect(shown()).toHaveLength(0);
    expectNoGrant();
  });

  it("says when integration choices cannot be loaded", async () => {
    mocks.getPermissionIntegrationOptions.mockRejectedValue(new Error("Registry unavailable."));
    await renderTree();
    choose("Integration");
    expect(await screen.findByRole("alert")).toHaveTextContent("Registry unavailable.");
    expect(shown()).toEqual([]);
    expect(screen.getByRole("button", { name: "Expand light" })).toBeInTheDocument();
  });

  it("is offered only where asked for, so the wizard's tree has none", async () => {
    await renderTree(false);
    expect(screen.queryByRole("button", { name: "Filter" })).toBeNull();
    expect(ALL.every((id) => !shown().includes(id))).toBe(true);
  });
});
