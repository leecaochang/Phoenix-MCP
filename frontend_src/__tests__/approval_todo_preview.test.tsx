import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ApprovalChange } from "../components/ApprovalChange";
import type { ApprovalRecord } from "../types";

/**
 * The review of a to-do removal. The approver's only look at an irreversible
 * deletion, so the preview is the backend's real payload: objects in an array.
 */

const TODO_ITEMS = [
  { list: "todo.shopping", uid: "eggs", summary: "Eggs", status: "completed" },
  { list: "todo.shopping", uid: "milk", summary: "Milk", status: "needs_action" },
];

function record(preview: Record<string, unknown>): ApprovalRecord {
  return {
    id: "a", access_key_id: "k", access_key_name: "key", tool_name: "set_todo_item", cap_name: "mesa", args: {},
    diff: { kind: "service_preview", preview }, status: "pending", created_at: "", expires_at: "",
    resolved_at: null, approved_by_user_id: null, closure_reason: null, result: null, request_id: "r", client_ip: null,
  };
}

const removal = (extra: Record<string, unknown> = {}) => record({
  domain: "todo", service: "remove_item", service_data: { item: ["eggs", "milk"] },
  warning: "Removing to-do items is permanent. Phoenix keeps no copy, so they cannot be restored.",
  todo_items: TODO_ITEMS, ...extra,
});

describe("to-do removal review", () => {
  it("names each item with its status and never prints an object", () => {
    const { container } = render(<ApprovalChange record={removal()} />);
    expect(screen.getByText("Eggs")).toBeTruthy();
    expect(screen.getByText("Milk")).toBeTruthy();
    expect(screen.getByText("Completed")).toBeTruthy();
    expect(screen.getByText("Not completed")).toBeTruthy();
    expect(container.textContent).not.toContain("[object Object]");
  });

  it("says the removal is permanent, in the catalog's words and once", () => {
    const { container } = render(<ApprovalChange record={removal()} />);
    expect(container.textContent).toContain("Removing to-do items is permanent. Phoenix keeps no copy, so they can't be restored.");
    expect(container.textContent).not.toContain("cannot be restored");
    expect(screen.queryByText("todo_items")).toBeNull();
  });

  it("warns even when no item could be read for the review", () => {
    const { container } = render(<ApprovalChange record={removal({ todo_items: undefined })} />);
    expect(container.textContent).toContain("Removing to-do items is permanent.");
  });

  it("names the list only when the items come from more than one", () => {
    const one = render(<ApprovalChange record={removal()} />);
    expect(one.container.textContent).not.toContain("todo.shopping");
    one.unmount();
    const two = render(<ApprovalChange record={removal({ todo_items: [...TODO_ITEMS, { list: "todo.chores", uid: "dust", summary: "Dust", status: "needs_action" }] })} />);
    expect(two.container.textContent).toContain("todo.chores");
  });

  it("shows an older record without a status by its summary alone", () => {
    render(<ApprovalChange record={removal({ todo_items: [{ list: "todo.shopping", uid: "eggs", summary: "Eggs" }] })} />);
    expect(screen.getByText("Eggs")).toBeTruthy();
  });
});

describe("service preview values", () => {
  it("flattens objects inside an array instead of printing [object Object]", () => {
    const { container } = render(<ApprovalChange record={record({
      domain: "light", service: "turn_on", rows: [{ entity_id: "light.a", brightness: 10 }, "plain", 3],
    })} />);
    expect(container.textContent).not.toContain("[object Object]");
    expect(container.textContent).toContain("entity_id: light.a");
    expect(container.textContent).toContain("plain");
  });

  it("keeps a warning on a service that is not a to-do removal", () => {
    render(<ApprovalChange record={record({ domain: "todo", service: "update_item", warning: "Careful" })} />);
    expect(screen.getByText("Careful")).toBeTruthy();
  });
});
