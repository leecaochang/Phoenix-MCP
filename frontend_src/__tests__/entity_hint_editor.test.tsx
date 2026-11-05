/**
 * The entity hint editor opens under its entity's row instead of in a dialog.
 * It keeps the existing scopes (this access key or all access keys) and their
 * storage, warns that hints reach agents, and adds Remove for a saved hint.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { EntityTree as EntityTreeData, PermissionTree } from "../types";

const mocks = vi.hoisted(() => ({
  getEntityTree: vi.fn(),
  getEntityHints: vi.fn(),
  setEntityHint: vi.fn(),
  patchEntityPermission: vi.fn(),
}));

vi.mock("../api", () => ({
  // No Home Assistant connection: the tree does not follow the registries here.
  currentHass: () => null,
  api: {
    getEntityTree: mocks.getEntityTree,
    getEntityHints: mocks.getEntityHints,
    setEntityHint: mocks.setEntityHint,
    patchEntityPermission: mocks.patchEntityPermission,
  },
}));

import { EntityTree } from "../components/EntityTree";

const DESK = "light.desk";
const HALL = "light.hall";
const detail = (entity_id: string, friendly_name: string) => ({
  entity_id, friendly_name, device_id: null, area_id: null, area_name: null, labels: [],
});

const TREE: EntityTreeData = {
  light: {
    devices: {},
    deviceless_entities: [DESK, HALL],
    entity_details: { [DESK]: detail(DESK, "Desk lamp"), [HALL]: detail(HALL, "Hall light") },
  },
};

const GRANTED: PermissionTree = {
  domains: {}, devices: {},
  entities: { [DESK]: { state: "GREEN", hint: null }, [HALL]: { state: "YELLOW", hint: "Hall only at night" } },
};

async function renderTree(onPermissionsChange = vi.fn()) {
  render(<EntityTree accessKeyId="access-key-1" permissions={GRANTED} onPermissionsChange={onPermissionsChange} />);
  fireEvent.click(await screen.findByRole("button", { name: "Expand light" }));
  return { onPermissionsChange };
}

const hintButtons = () => screen.getAllByRole("button", { name: /^(Add|Edit) Hint$/ });

describe("entity hint editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getEntityTree.mockResolvedValue(TREE);
    mocks.getEntityHints.mockResolvedValue({ entity_hints: {} });
  });

  it("opens under the row without a dialog, with the agent caution in Help and read out with the input", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Add Hint" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    const editor = screen.getByRole("group", { name: "Entity Hint" });
    const input = within(editor).getByRole("textbox", { name: `Hint for ${DESK}` });
    expect(input).toHaveFocus();
    // The caution moved to Help.
    expect(within(editor).queryByText("Sent to agents and their model providers. Never include passwords or codes.")).toBeNull();
    expect(input).toHaveAttribute("data-help", "perms.hintCaution");
    expect(input).toHaveAttribute("aria-description", "Sent to agents and their model providers. Never include passwords or codes.");
    // The rest of the tree stays usable while it is open.
    expect(screen.getByRole("button", { name: "Edit Hint" })).not.toHaveAttribute("aria-hidden");
  });

  it("marks a populated hint and shows its text, this key's own before the global one", async () => {
    mocks.getEntityHints.mockResolvedValue({ entity_hints: { [HALL]: "Global hall hint", [DESK]: "Global desk hint" } });
    await renderTree();
    await waitFor(() => expect(hintButtons().every((b) => b.classList.contains("has-hint"))).toBe(true));
    const [desk, hall] = hintButtons();
    expect(desk).toHaveAttribute("title", "Edit Hint: Global desk hint");
    expect(hall).toHaveAttribute("title", "Edit Hint: Hall only at night");
  });

  it("an empty hint is not marked", async () => {
    await renderTree();
    const desk = screen.getByRole("button", { name: "Add Hint" });
    expect(desk).not.toHaveClass("has-hint");
    expect(desk).toHaveAttribute("title", "Add Hint");
  });

  it("saves for all access keys by default, then closes and returns focus to its button", async () => {
    mocks.setEntityHint.mockResolvedValue({ entity_hints: { [DESK]: "Reading light" } });
    await renderTree();
    const button = screen.getByRole("button", { name: "Add Hint" });
    fireEvent.click(button);
    const editor = screen.getByRole("group", { name: "Entity Hint" });
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "  Reading light  " } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.setEntityHint).toHaveBeenCalledWith(DESK, "Reading light"));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Entity Hint" })).toBeNull());
    expect(button).toHaveFocus();
    expect(button).toHaveClass("has-hint");
  });

  it("Remove clears only the chosen scope's saved hint, and is offered only when there is one", async () => {
    const onPermissionsChange = vi.fn();
    const cleared: PermissionTree = { ...GRANTED, entities: { ...GRANTED.entities, [HALL]: { state: "YELLOW", hint: null } } };
    mocks.patchEntityPermission.mockResolvedValue(cleared);
    await renderTree(onPermissionsChange);
    fireEvent.click(screen.getByRole("button", { name: "Edit Hint" }));
    const editor = screen.getByRole("group", { name: "Entity Hint" });
    // All access keys has no saved hint for the hall light.
    expect(within(editor).queryByRole("button", { name: "Remove Hint" })).toBeNull();
    fireEvent.click(within(editor).getByRole("checkbox", { name: "Apply hint to all access keys" }));
    // This key's own hint loads into the box when switching scope.
    expect(within(editor).getByRole("textbox")).toHaveValue("Hall only at night");
    fireEvent.click(within(editor).getByRole("button", { name: "Remove Hint" }));
    await waitFor(() => expect(mocks.patchEntityPermission).toHaveBeenCalledWith("access-key-1", HALL, { state: "YELLOW", hint: null }));
    expect(mocks.setEntityHint).not.toHaveBeenCalled();
    await waitFor(() => expect(onPermissionsChange).toHaveBeenCalledWith(cleared));
  });

  it("switching scope keeps text being written, Escape closes without saving, and one editor is open at a time", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Add Hint" }));
    const editor = screen.getByRole("group", { name: "Entity Hint" });
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Draft" } });
    fireEvent.click(within(editor).getByRole("checkbox", { name: "Apply hint to all access keys" }));
    expect(within(editor).getByRole("textbox")).toHaveValue("Draft");

    fireEvent.click(screen.getByRole("button", { name: "Edit Hint" }));
    const editors = screen.getAllByRole("group", { name: "Entity Hint" });
    expect(editors).toHaveLength(1);
    expect(within(editors[0]).getByText(HALL)).toBeInTheDocument();

    fireEvent.keyDown(within(editors[0]).getByRole("textbox"), { key: "Escape" });
    expect(screen.queryByRole("group", { name: "Entity Hint" })).toBeNull();
    expect(screen.getByRole("button", { name: "Edit Hint" })).toHaveFocus();
    expect(mocks.setEntityHint).not.toHaveBeenCalled();
    expect(mocks.patchEntityPermission).not.toHaveBeenCalled();
  });

  it("closes unsaved on a click anywhere else, while its own Hint button still opens and closes it", async () => {
    await renderTree();
    const outside = document.createElement("input");
    document.body.appendChild(outside);
    try {
      fireEvent.click(screen.getByRole("button", { name: "Add Hint" }));
      const editor = screen.getByRole("group", { name: "Entity Hint" });
      fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Draft" } });
      fireEvent.pointerDown(within(editor).getByRole("textbox"));
      expect(screen.getByRole("group", { name: "Entity Hint" })).toBeInTheDocument();
      fireEvent.pointerDown(outside);
      expect(screen.queryByRole("group", { name: "Entity Hint" })).toBeNull();
      expect(mocks.setEntityHint).not.toHaveBeenCalled();
      expect(mocks.patchEntityPermission).not.toHaveBeenCalled();

      // The button that opened it toggles it, rather than closing and reopening it.
      const button = screen.getByRole("button", { name: "Add Hint" });
      fireEvent.click(button);
      expect(screen.getByRole("group", { name: "Entity Hint" })).toBeInTheDocument();
      fireEvent.pointerDown(button);
      fireEvent.click(button);
      expect(screen.queryByRole("group", { name: "Entity Hint" })).toBeNull();
    } finally {
      outside.remove();
    }
  });
});
