import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { EntityTree as EntityTreeData, PermissionTree } from "../types";

const mocks = vi.hoisted(() => ({
  getEntityTree: vi.fn(),
  getEntityHints: vi.fn(),
  patchDevicePermission: vi.fn(),
}));

vi.mock("../api", () => ({
  api: {
    getEntityTree: mocks.getEntityTree,
    getEntityHints: mocks.getEntityHints,
    patchDevicePermission: mocks.patchDevicePermission,
  },
}));

import { EntityTree, matchesTreeSearch } from "../components/EntityTree";
import { t } from "../i18n";

const DEVICE_ID = "93d485bc30d4dc83aaf5efed8ea23fd3";
const GHOST_DEVICE_ID = "deadbeefdeadbeefdeadbeefdeadbeef";

const TREE: EntityTreeData = {
  binary_sensor: {
    devices: {
      [DEVICE_ID]: {
        device_id: DEVICE_ID,
        name: "Phoenix Removal Smoke 1.1.1.1",
        area_id: null,
        area_name: null,
        entities: ["binary_sensor.phoenix_removal_smoke_1_1_1_1"],
      },
    },
    deviceless_entities: [],
    entity_details: {
      "binary_sensor.phoenix_removal_smoke_1_1_1_1": {
        entity_id: "binary_sensor.phoenix_removal_smoke_1_1_1_1",
        friendly_name: "Phoenix Removal Smoke status",
        device_id: DEVICE_ID,
        area_id: null,
        area_name: null,
        labels: [],
      },
    },
  },
};

const EMPTY_PERMISSIONS: PermissionTree = { domains: {}, devices: {}, entities: {} };

function renderTree(permissions: PermissionTree = EMPTY_PERMISSIONS) {
  const onPermissionsChange = vi.fn();
  render(
    <EntityTree
      accessKeyId="access-key-1"
      permissions={permissions}
      onPermissionsChange={onPermissionsChange}
    />,
  );
  return { onPermissionsChange };
}

describe("permission tree search and device cleanup", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getEntityTree.mockResolvedValue(TREE);
    mocks.getEntityHints.mockResolvedValue({ entity_hints: {} });
    mocks.patchDevicePermission.mockResolvedValue(EMPTY_PERMISSIONS);
  });

  it("matches normalized punctuation variants without losing literal matching", () => {
    expect(matchesTreeSearch("1.1.1.1", "binary_sensor.host_1_1_1_1")).toBe(true);
    expect(matchesTreeSearch("REMOVAL SMOKE", "Phoenix Removal Smoke")).toBe(true);
    expect(matchesTreeSearch("garage", DEVICE_ID, "Phoenix Removal Smoke")).toBe(false);
  });

  it("finds a device by raw registry ID through its domain ancestor", async () => {
    renderTree();
    const filter = await screen.findByRole("textbox", { name: "Filter domains, devices, and entities" });

    fireEvent.change(filter, { target: { value: DEVICE_ID } });

    expect(await screen.findByText("Phoenix Removal Smoke 1.1.1.1")).toBeInTheDocument();
    expect(screen.getByText(DEVICE_ID).tagName).toBe("CODE");
    expect(screen.queryByRole("button", { name: /copy device id/i })).not.toBeInTheDocument();
  });

  it("finds an underscore-derived entity ID when the operator types the dotted address", async () => {
    renderTree();
    const filter = await screen.findByRole("textbox", { name: "Filter domains, devices, and entities" });

    fireEvent.change(filter, { target: { value: "1.1.1.1" } });

    expect(await screen.findByText("binary_sensor.phoenix_removal_smoke_1_1_1_1")).toBeInTheDocument();
  });

  it("restores a collapsed domain after clearing a search", async () => {
    renderTree();
    const filter = await screen.findByRole("textbox", { name: "Filter domains, devices, and entities" });
    expect(screen.getByRole("button", { name: "Expand binary_sensor" })).toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "1.1.1.1" } });
    expect(await screen.findByRole("button", { name: "Collapse binary_sensor" })).toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "" } });
    expect(await screen.findByRole("button", { name: "Expand binary_sensor" })).toBeInTheDocument();
  });

  it("restores independent domain and device choices after clearing a search", async () => {
    renderTree();
    const filter = await screen.findByRole("textbox", { name: "Filter domains, devices, and entities" });
    fireEvent.click(screen.getByRole("button", { name: "Expand binary_sensor" }));
    expect(await screen.findByRole("button", { name: "Expand Phoenix Removal Smoke 1.1.1.1" })).toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "1.1.1.1" } });
    expect(await screen.findByRole("button", { name: "Collapse Phoenix Removal Smoke 1.1.1.1" })).toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "" } });
    expect(await screen.findByRole("button", { name: "Collapse binary_sensor" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Expand Phoenix Removal Smoke 1.1.1.1" })).toBeInTheDocument();
  });

  it("surfaces an unmatched explicit device grant and lets the operator clear it", async () => {
    const permissions: PermissionTree = {
      domains: {},
      devices: { [GHOST_DEVICE_ID]: { state: "GREEN", hint: null } },
      entities: {},
    };
    const { onPermissionsChange } = renderTree(permissions);

    expect(await screen.findByText("Unmatched Device Permissions")).toBeInTheDocument();
    expect(screen.getByText(GHOST_DEVICE_ID)).toBeInTheDocument();
    const selector = screen.getByRole("group", { name: `Permission for device ${GHOST_DEVICE_ID}` });
    const clearButton = within(selector).getByRole("button", {
      name: "N: No explicit grant (inherits from parent)",
    });
    clearButton.releasePointerCapture = vi.fn();
    fireEvent.pointerDown(clearButton, { pointerId: 1, isPrimary: true });

    await waitFor(() => expect(mocks.patchDevicePermission).toHaveBeenCalledWith(
      "access-key-1", GHOST_DEVICE_ID, { state: "GREY" },
    ));
    expect(onPermissionsChange).toHaveBeenCalledWith(EMPTY_PERMISSIONS);
  });

  it("shows a useful empty state instead of a blank tree", async () => {
    renderTree();
    const filter = await screen.findByRole("textbox", { name: "Filter domains, devices, and entities" });

    fireEvent.change(filter, { target: { value: "does-not-exist" } });

    expect(screen.getByRole("status")).toHaveTextContent(
      "No domains, devices, entities, or stored grants match this filter.",
    );
  });

  it("shows empty grantable domains without nonfunctional expanders", async () => {
    mocks.getEntityTree.mockResolvedValue({
      tag: { devices: {}, deviceless_entities: [], entity_details: {} },
      zone: { devices: {}, deviceless_entities: [], entity_details: {} },
    });

    renderTree();

    expect(await screen.findByText("tag")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Permission for domain tag" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Expand tag" })).not.toBeInTheDocument();
    expect(await screen.findByText("zone")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Permission for domain zone" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Expand zone" })).not.toBeInTheDocument();
  });
  it("applies a RED main parent to its child device and the child's entities", async () => {
    const PARENT_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const CHILD_ID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    mocks.getEntityTree.mockResolvedValue({
      sensor: {
        devices: {
          [PARENT_ID]: {
            device_id: PARENT_ID,
            parent_device_id: null,
            name: "Strip",
            area_id: null,
            area_name: null,
            entities: ["sensor.strip_power"],
          },
        },
        deviceless_entities: [],
        entity_details: {
          "sensor.strip_power": {
            entity_id: "sensor.strip_power",
            friendly_name: "Strip power",
            device_id: PARENT_ID,
            area_id: null,
            area_name: null,
            labels: [],
          },
        },
      },
      switch: {
        devices: {
          [CHILD_ID]: {
            device_id: CHILD_ID,
            parent_device_id: PARENT_ID,
            name: "Outlet",
            area_id: null,
            area_name: null,
            entities: ["switch.outlet"],
          },
        },
        deviceless_entities: [],
        entity_details: {
          "switch.outlet": {
            entity_id: "switch.outlet",
            friendly_name: "Outlet",
            device_id: CHILD_ID,
            area_id: null,
            area_name: null,
            labels: [],
          },
        },
      },
    } satisfies EntityTreeData);

    const { container } = render(
      <EntityTree
        accessKeyId="access-key-1"
        permissions={{
          domains: { switch: { state: "GREEN", hint: null } },
          devices: { [PARENT_ID]: { state: "RED", hint: null } },
          entities: {},
        }}
        onPermissionsChange={vi.fn()}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Expand switch" }));
    fireEvent.click(await screen.findByRole("button", { name: "Expand Outlet" }));
    await screen.findByText("switch.outlet");

    const switchDomain = screen.getByRole("button", { name: "Collapse switch" }).closest(".tree-domain-group");
    const effective = [...switchDomain!.querySelectorAll(".tree-effective")].map((node) => node.textContent);
    const denied = `(${t("perms.stateDeny")})`;
    expect(effective).toEqual([`(${t("perms.stateWrite")})`, denied, denied]);
    expect(container).not.toHaveTextContent("Unmatched Device Permissions");
  });
  it("nests child devices under an entity-less main device that the operator can set", async () => {
    const PARENT_ID = "cccccccccccccccccccccccccccccccc";
    const CHILD_ID = "dddddddddddddddddddddddddddddddd";
    mocks.getEntityTree.mockResolvedValue({
      switch: {
        devices: {
          [PARENT_ID]: {
            device_id: PARENT_ID,
            parent_device_id: null,
            name: "Strip",
            area_id: null,
            area_name: null,
            entities: [],
          },
          [CHILD_ID]: {
            device_id: CHILD_ID,
            parent_device_id: PARENT_ID,
            name: "Outlet",
            area_id: null,
            area_name: null,
            entities: ["switch.outlet"],
          },
        },
        deviceless_entities: [],
        entity_details: {
          "switch.outlet": {
            entity_id: "switch.outlet",
            friendly_name: "Outlet",
            device_id: CHILD_ID,
            area_id: null,
            area_name: null,
            labels: [],
          },
        },
      },
    } satisfies EntityTreeData);
    const permissions: PermissionTree = {
      domains: {},
      devices: { [PARENT_ID]: { state: "GREEN", hint: null } },
      entities: {},
    };
    mocks.patchDevicePermission.mockResolvedValue(permissions);

    const { container } = render(
      <EntityTree accessKeyId="access-key-1" permissions={permissions} onPermissionsChange={vi.fn()} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Expand switch" }));
    expect(screen.queryByRole("button", { name: "Expand Outlet" })).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Expand Strip" }));
    fireEvent.click(await screen.findByRole("button", { name: "Expand Outlet" }));
    expect(await screen.findByText("switch.outlet")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("Unmatched Device Permissions");

    const write = `(${t("perms.stateWrite")})`;
    const effective = [...container.querySelectorAll(".tree-effective")].map((node) => node.textContent);
    expect(effective).toEqual([`(${t("perms.stateNoAccess")})`, write, write, write]);

    const selector = screen.getByRole("group", { name: "Permission for device Strip" });
    const red = within(selector).getByRole("button", { name: /^D:/ });
    red.releasePointerCapture = vi.fn();
    fireEvent.pointerDown(red, { pointerId: 1, isPrimary: true });
    await waitFor(() => expect(mocks.patchDevicePermission).toHaveBeenCalledWith(
      "access-key-1", PARENT_ID, { state: "RED" },
    ));
  });

  it("keeps an entity-less parent visible when the search matches only its part", async () => {
    mocks.getEntityTree.mockResolvedValue({
      switch: {
        devices: {
          parent: { device_id: "parent", parent_device_id: null, name: "Strip", area_id: null, area_name: null, entities: [] },
          child: { device_id: "child", parent_device_id: "parent", name: "Outlet", area_id: null, area_name: null, entities: ["switch.outlet"] },
        },
        deviceless_entities: [],
        entity_details: {
          "switch.outlet": { entity_id: "switch.outlet", friendly_name: "Outlet three", device_id: "child", area_id: null, area_name: null, labels: [] },
        },
      },
    } satisfies EntityTreeData);
    renderTree();
    const filter = await screen.findByRole("textbox", { name: "Filter domains, devices, and entities" });

    fireEvent.change(filter, { target: { value: "outlet three" } });

    expect(await screen.findByText("Strip")).toBeInTheDocument();
    expect(await screen.findByText("switch.outlet")).toBeInTheDocument();
  });
});
