/**
 * Small contracts that have no window of their own: script-driven scrolling
 * honors reduced motion, a status region is in the page before its text, and
 * Polish and Russian counts sit where no plural form is needed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusRegion } from "../components/common";
import { scrollMotion } from "../ui/motion";
import pl from "../../custom_components/phoenix_mcp/catalogs/pl.json";
import ru from "../../custom_components/phoenix_mcp/catalogs/ru.json";

afterEach(() => { vi.unstubAllGlobals(); });

describe("scrollMotion", () => {
  it.each([[true, "auto"], [false, "smooth"]])("with reduced motion %s moves %s", (reduce, behavior) => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: reduce && query === "(prefers-reduced-motion: reduce)" }));
    expect(scrollMotion()).toBe(behavior);
  });

  it("is smooth where the browser cannot say", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(scrollMotion()).toBe("smooth");
  });
});

it("keeps one status region mounted as its notice comes and goes", () => {
  const view = render(<StatusRegion>{false}</StatusRegion>);
  const region = screen.getByRole("status");
  expect(region).toBeEmptyDOMElement();
  view.rerender(<StatusRegion><p>Saved</p></StatusRegion>);
  expect(screen.getByRole("status")).toBe(region);
  expect(region.textContent).toBe("Saved");
});

describe("count-bearing strings with no plural forms", () => {
  it.each([
    ["pl approveSelected", pl.panel.approvals.approveSelected],
    ["pl batchRemaining", pl.panel.approvals.batchRemaining],
    ["pl approvalsTabAria", pl.panel.shell.approvalsTabAria],
    ["pl too_many_tools", pl.panel.agentchat.notice.too_many_tools],
    ["ru too_many_tools", ru.panel.agentchat.notice.too_many_tools],
  ])("%s states the count as a label's value", (_name, text) => {
    expect(text).toMatch(/: \{count\}/);
  });
});
