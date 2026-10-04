import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { ToolAnnouncementToggle } from "../views/AccessKeyDetail";
import type { AccessKeyRecord } from "../types";

vi.mock("../api", () => ({ api: { patchAccessKey: vi.fn() } }));

describe("Tool Announcement layout", () => {
  it("puts Inline approval wait help above its select without stacking switches", () => {
    const view = render(
      <ToolAnnouncementToggle
        accessKey={{
          id: "tok-1",
          announce_all_tools: false,
          use_assist_exposure: false,
          pass_through: false,
          confirm_inline_wait_seconds: 0,
        } as AccessKeyRecord}
        onUpdate={() => {}}
      />,
    );
    expect(view.container.querySelector("select")?.closest(".toggle-row"))
      .toHaveClass("toggle-row-stacked-control");
    expect(view.container.querySelector("input[type='checkbox']")?.closest(".toggle-row"))
      .not.toHaveClass("toggle-row-stacked-control");
  });
});
