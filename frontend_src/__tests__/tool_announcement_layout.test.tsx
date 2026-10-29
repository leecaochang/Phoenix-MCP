import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { ToolAnnouncementToggle } from "../views/AccessKeyDetail";
import type { AccessKeyRecord } from "../types";

vi.mock("../api", () => ({ api: { patchAccessKey: vi.fn() } }));

describe("Agent Behavior layout", () => {
  it("puts every setting's label beside its control, with its help kept for the Help window", () => {
    const view = render(
      <ToolAnnouncementToggle
        accessKey={{
          id: "tok-1",
          announce_all_tools: false,
          use_assist_exposure: false,
          pass_through: true,
          confirm_inline_wait_seconds: 0,
        } as AccessKeyRecord}
        onUpdate={() => {}}
      />,
    );
    const rows = [...view.container.querySelectorAll(".toggle-row")];
    expect(rows).toHaveLength(3);
    for (const row of rows) {
      expect(row).not.toHaveClass("toggle-row-stacked-control");
      expect(row.querySelector("small")).toBeNull();
      expect(row.getAttribute("data-help")).toBeTruthy();
    }
    expect(view.container.querySelector("select")?.getAttribute("data-help")).toBe("help.inlineWait");
  });
});
