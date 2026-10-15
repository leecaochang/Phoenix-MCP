import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import type { EntityTree as EntityTreeData, PermissionTree } from "../types";

const mocks = vi.hoisted(() => ({
  getEntityTree: vi.fn(),
  getEntityHints: vi.fn(),
}));

vi.mock("../api", () => ({
  api: {
    getEntityTree: mocks.getEntityTree,
    getEntityHints: mocks.getEntityHints,
  },
}));

import { EntityTree } from "../components/EntityTree";

const ENTITY_ID = "light.desk";

const TREE: EntityTreeData = {
  light: {
    devices: {},
    deviceless_entities: [ENTITY_ID],
    entity_details: {
      [ENTITY_ID]: {
        entity_id: ENTITY_ID,
        friendly_name: "Desk lamp",
        device_id: null,
        area_id: null,
        area_name: null,
        labels: [],
      },
    },
  },
};

const GRANTED: PermissionTree = { domains: {}, devices: {}, entities: { [ENTITY_ID]: { state: "GREEN", hint: null } } };

describe("entity hint editor caution", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getEntityTree.mockResolvedValue(TREE);
    mocks.getEntityHints.mockResolvedValue({ entity_hints: {} });
  });

  it("tells the administrator hints reach agents and links the caution to the input", async () => {
    render(<EntityTree accessKeyId="access-key-1" permissions={GRANTED} onPermissionsChange={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Expand light" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add hint" }));

    const dialog = await screen.findByRole("dialog");
    const caution = within(dialog).getByText(
      "Sent to agents and their model providers. Never include passwords or codes.",
    );
    const input = within(dialog).getByRole("textbox", { name: `Hint for ${ENTITY_ID}` });
    expect(caution.id).not.toBe("");
    expect(input.getAttribute("aria-describedby")).toBe(caution.id);
    expect(input).toHaveAccessibleDescription(
      "Sent to agents and their model providers. Never include passwords or codes.",
    );
  });
});
