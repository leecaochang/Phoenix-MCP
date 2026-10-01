/** Contrast checks use shipped tokens and actual alpha-composited surfaces. */
import { it, expect } from "vitest";
import { readFileSync } from "node:fs";

const css = readFileSync("frontend_src/phoenix-mcp-panel.css", "utf8");
function tokens(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/(--phx-[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}
function rgb(value: string): number[] {
  if (value.startsWith("#")) return [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16));
  return value.match(/[\d.]+/g)!.map(Number);
}
function luminance(color: number[]): number {
  return color.slice(0, 3).map((v) => v / 255).map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
}
function contrast(a: number[], b: number[]): number {
  const [low, high] = [luminance(a), luminance(b)].sort((x, y) => x - y);
  return (high + .05) / (low + .05);
}
const light = tokens(css.match(/:host\s*\{([^}]+)/)![1]);
const dark = { ...light, ...tokens(css.match(/:host\(:not\(.phx-theme-light\)\)\s*\{([^}]+)/)![1]) };

it.each([["light", light], ["dark", dark]] as const)("keeps %s status and chat text above 4.5:1", (_theme, values) => {
  for (const surface of ["--phx-bg", "--phx-surface", "--phx-surface-2"]) {
    const background = rgb(values[surface]);
    for (const status of ["success", "error", "warning", "info"]) {
      const tint = rgb(values[`--phx-${status}-bg`]);
      const blended = background.map((v, i) => tint[i] * tint[3] + v * (1 - tint[3]));
      expect(contrast(rgb(values[`--phx-${status}`]), blended), `${surface} ${status}`).toBeGreaterThanOrEqual(4.5);
    }
  }
  for (const text of ["--phx-text-2", "--phx-error"]) {
    expect(contrast(rgb(values[text]), rgb(values["--phx-agentcli-surface"])), text).toBeGreaterThanOrEqual(4.5);
  }
  expect(contrast(rgb(values["--phx-text-2"]), rgb(values["--phx-agentcli-titlebar"]))).toBeGreaterThanOrEqual(4.5);
});

it("defines every Phoenix CSS variable or provides a fallback", () => {
  const defined = new Set([...css.matchAll(/(--phx-[\w-]+)\s*:/g)].map((m) => m[1]));
  const missing = [...css.matchAll(/var\((--phx-[\w-]+)\s*([,)])/g)].filter((m) => m[2] === ")" && !defined.has(m[1]));
  expect(missing.map((m) => m[1])).toEqual([]);
});

it("keeps documentation buttons, signals and focus rings legible in both themes", () => {
  const docs = readFileSync("docs/assets/phoenix-mcp-docs.css", "utf8");
  const read = (block: string) => Object.fromEntries([...block.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
  const base = read(docs.match(/:root\s*\{([^}]+)/)![1]);
  const night = { ...base, ...read(docs.match(/:root\[data-theme="dark"\]\s*\{([^}]+)/)![1]) };
  for (const values of [base, night]) {
    expect(contrast(rgb(values["--button-ink"]), rgb(values["--button-bg"]))).toBeGreaterThanOrEqual(4.5);
    for (const surface of ["--bg", "--surface", "--surface-2"]) {
      const background = rgb(values[surface]);
      expect(contrast(rgb(values["--accent-deep"]), background)).toBeGreaterThanOrEqual(3);
      expect(contrast(rgb(values["--ink-3"]), background)).toBeGreaterThanOrEqual(4.5);
      for (const status of ["success", "error", "warn", "st-yellow", "st-green", "st-red"]) {
        const tint = rgb(values[`--${status}-bg`]);
        const blended = background.map((v, i) => tint[i] * tint[3] + v * (1 - tint[3]));
        expect(contrast(rgb(values[`--${status}`]), blended), `${surface} ${status}`).toBeGreaterThanOrEqual(4.5);
      }
      const tint = rgb(values["--st-grey-bg"]);
      const blended = background.map((v, i) => tint[i] * tint[3] + v * (1 - tint[3]));
      expect(contrast(rgb(values["--ink-2"]), blended)).toBeGreaterThanOrEqual(4.5);
    }
  }
});
