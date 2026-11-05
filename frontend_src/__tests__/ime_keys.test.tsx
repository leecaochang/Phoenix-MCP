/**
 * A key press that belongs to an input method does nothing else: Enter picks a
 * candidate and Escape cancels the composition, so neither may reject a request,
 * save half-typed text or close what the field sits in.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Modal } from "../components/Modal";
import { TagInput } from "../components/TagInput";
import { InlineConfirm } from "../ui/InlineConfirm";
import { isImeKey } from "../utils/ime";
import { renderApprovals } from "./approvals_harness";
import { api } from "../api";
import type { ApprovalRecord } from "../types";

vi.mock("../api", () => ({
  api: { listApprovals: vi.fn(), getApproval: vi.fn(), rejectApproval: vi.fn(), approveApproval: vi.fn() },
  currentHass: () => ({}),
}));

const COMPOSING = [
  ["isComposing", { isComposing: true }],
  ["key code 229", { keyCode: 229 }],
] as const;

describe("isImeKey", () => {
  it("reads native and React events alike", () => {
    expect(isImeKey({ isComposing: true, keyCode: 13 })).toBe(true);
    expect(isImeKey({ isComposing: false, keyCode: 229 })).toBe(true);
    expect(isImeKey({ isComposing: false, keyCode: 13 })).toBe(false);
    expect(isImeKey({ nativeEvent: { isComposing: true, keyCode: 13 } })).toBe(true);
    expect(isImeKey({ nativeEvent: { isComposing: false, keyCode: 13 } })).toBe(false);
  });
});

describe.each(COMPOSING)("a key press during composition, flagged by %s", (_name, flag) => {
  it("leaves a dialog open, and plain Escape still closes it", () => {
    const onClose = vi.fn();
    render(<Modal titleId="t" onClose={onClose}><input aria-label="field" /></Modal>);
    fireEvent.keyDown(document, { key: "Escape", ...flag });
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("leaves an inline confirmation open, and plain Escape still closes it", () => {
    const onCancel = vi.fn();
    render(<InlineConfirm label="Delete" confirmLabel="Delete" cancelLabel="Cancel" onConfirm={() => {}} onCancel={onCancel} />);
    const box = screen.getByRole("group", { name: "Delete" });
    fireEvent.keyDown(box, { key: "Escape", ...flag });
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("commits no tag, and plain Enter still does", () => {
    const onChange = vi.fn();
    render(<TagInput value={[]} onChange={onChange} canonicalTags={["lighting.task"]} />);
    const combo = screen.getByRole("combobox");
    fireEvent.change(combo, { target: { value: "light" } });
    fireEvent.keyDown(combo, { key: "Enter", ...flag });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.keyDown(combo, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith(["lighting.task"]);
  });

  it("does not reject a request, and plain Enter still does", async () => {
    const record: ApprovalRecord = {
      id: "appr_1", access_key_id: "t1", access_key_name: "my_access_key", tool_name: "create_automation",
      cap_name: "cap_automation_write", args: {}, diff: { kind: "config_diff", summary: "Create automation 'x'", after: "{}" },
      status: "pending", created_at: "2026-01-01T00:00:00Z", expires_at: "2099-01-01T01:00:00Z", resolved_at: null,
      approved_by_user_id: null, rejected_reason: null, result: null, request_id: "r1", client_ip: null,
    } as ApprovalRecord;
    vi.mocked(api.rejectApproval).mockClear();
    vi.mocked(api.listApprovals).mockResolvedValue({ approvals: [record], total: 1 } as never);
    vi.mocked(api.getApproval).mockResolvedValue(record as never);
    vi.mocked(api.rejectApproval).mockResolvedValue({ ...record, status: "rejected" } as never);
    const view = renderApprovals();
    fireEvent.click(await view.findByText("Create automation 'x'"));
    fireEvent.click(await view.findByRole("button", { name: "Reject" }));
    const reason = await view.findByLabelText("Optional reason");
    fireEvent.keyDown(reason, { key: "Enter", ...flag });
    expect(api.rejectApproval).not.toHaveBeenCalled();
    fireEvent.keyDown(reason, { key: "Enter" });
    await waitFor(() => expect(api.rejectApproval).toHaveBeenCalledTimes(1));
    view.unmount();
  });
});

/** Files whose Enter and Escape handlers sit on buttons, rows and menus, where no text is composed. */
const NO_TEXT_FIELD = new Set(["Menu.tsx", "ConfigTable.tsx", "TitleBar.tsx"]);

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((item) => {
    if (item.name === "__tests__") return [];
    const path = join(dir, item.name);
    return item.isDirectory() ? sources(path) : /\.tsx?$/.test(item.name) ? [path] : [];
  });
}

it("guards every file that reads Enter or Escape on a text field", () => {
  const unguarded = sources("frontend_src").filter((path) => {
    const name = path.split("/").pop()!;
    const text = readFileSync(path, "utf8");
    return !NO_TEXT_FIELD.has(name) && /key [!=]== "(Enter|Escape)"/.test(text) && !text.includes("isImeKey(e)");
  });
  expect(unguarded).toEqual([]);
});
