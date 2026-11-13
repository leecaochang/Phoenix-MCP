/** On a 320 px viewport the floating Agent Chat window stays inside the view's margins. */
import { expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { clampPosToViewport, snapResizeRect, windowMinWidth } from "../components/AgentCliWindow";

const VH = 640;

it("lets the window shrink below its usual minimum only as far as the view requires", () => {
  expect(windowMinWidth(1280)).toBe(320);
  expect(windowMinWidth(336)).toBe(320);
  expect(windowMinWidth(320)).toBe(304);
});

it("never resizes past the right margin of a 320 px view", () => {
  for (const dir of ["e", "w", "se", "sw"]) {
    const r = snapResizeRect(dir, { x: 8, y: 40, w: 304, h: 400 }, dir.includes("w") ? 40 : -40, 0, 320, VH);
    expect(r.x, dir).toBeGreaterThanOrEqual(8);
    expect(r.x + r.w, dir).toBeLessThanOrEqual(312);
  }
});

it("places a window that fits the narrow view wholly inside it", () => {
  const p = clampPosToViewport({ x: 200, y: 40 }, { w: windowMinWidth(320), h: 400 }, 320, VH);
  expect(p.x).toBe(8);
  expect(p.x + windowMinWidth(320)).toBe(312);
});

it("caps the stylesheet's minimum width at the view inside its margins", () => {
  const css = readFileSync("frontend_src/phoenix-mcp-panel.css", "utf8");
  const at = css.indexOf(".agentcli-window {");
  const rule = css.slice(at, css.indexOf("}", at));
  expect(rule).toContain("min-width: min(320px, calc(100vw - 16px));");
});
