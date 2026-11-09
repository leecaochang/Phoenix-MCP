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
    for (const status of ["success", "error", "warning", "info", "read"]) {
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

const forcedDark = { ...light, ...tokens(css.match(/:host\(.phx-theme-dark\)\s*\{([^}]+)/)![1]) };
it.each([["light", light], ["auto dark", dark], ["forced dark", forcedDark]] as const)("keeps %s destructive controls readable", (_theme, values) => {
  const declarations = (selector: string) => {
    const block = css.slice(css.indexOf(selector + " {"));
    return Object.fromEntries([...block.slice(0, block.indexOf("}")).matchAll(/(?:^|[;{])\s*([\w-]+):\s*([^;]+)/g)].map(m => [m[1], m[2].trim()]));
  };
  const color = (value: string) => rgb(value.startsWith("var(") ? values[value.match(/--phx-[\w-]+/)![0]] : value);
  for (const selector of [".btn-danger", ".phx-toolbar-badge", ".agentcli-cancel-btn", ".agentcli-popout-error"]) {
    const rule = declarations(selector);
    expect(rule.background, selector).toBeTruthy();
    expect(contrast(color(rule.color), color(rule.background)), selector).toBeGreaterThanOrEqual(4.5);
  }
});

it.each([["light", light], ["auto dark", dark], ["forced dark", forcedDark]] as const)("keeps %s neutral chrome legible and operable", (_theme, values) => {
  // Text and the read signal hold 4.5:1 on every surface a panel or window can
  // paint; the ink focus ring and field borders are non-text UI and hold 3:1.
  const surfaces = ["--phx-bg", "--phx-surface", "--phx-surface-2", "--phx-raised", "--phx-hover", "--phx-accent-lt", "--phx-selected"];
  for (const surface of surfaces) {
    for (const text of ["--phx-text", "--phx-text-2"]) {
      expect(contrast(rgb(values[text]), rgb(values[surface])), `${text} on ${surface}`).toBeGreaterThanOrEqual(4.5);
    }
  }
  for (const surface of ["--phx-bg", "--phx-surface", "--phx-surface-2", "--phx-raised"]) {
    expect(contrast(rgb(values["--phx-read"]), rgb(values[surface])), `read on ${surface}`).toBeGreaterThanOrEqual(4.5);
    expect(contrast(rgb(values["--phx-focus"]), rgb(values[surface])), `focus on ${surface}`).toBeGreaterThanOrEqual(3);
  }
  for (const surface of ["--phx-surface", "--phx-raised"]) {
    expect(contrast(rgb(values["--phx-field-border"]), rgb(values[surface])), `field border on ${surface}`).toBeGreaterThanOrEqual(3);
  }
  expect(contrast(rgb(values["--phx-accent-fg"]), rgb(values["--phx-accent"])), "primary button").toBeGreaterThanOrEqual(4.5);
});

// Color themes re-value the same tokens on the host; each one must stay as legible as the default.
const themes = readFileSync("frontend_src/themes.css", "utf8");
const block = (selector: string): Record<string, string> => {
  const at = themes.indexOf(selector + " {");
  return at < 0 ? {} : tokens(themes.slice(at, themes.indexOf("}", at)));
};
const palettes = [...themes.matchAll(/:host\(\[data-phx-palette="(\w+)"\]\) \{/g)].map((m) => m[1]);

/** A theme's tokens as the page resolves them, following var() references. */
function themeValues(name: string | null, mode: "light" | "dark"): (token: string) => string {
  const base = { ...(mode === "light" ? light : forcedDark), ...block(":host"), ...(mode === "dark" ? block(":host(.phx-theme-dark)") : {}) };
  const palette = name ? (mode === "light" ? block(`:host([data-phx-palette="${name}"])`) : block(`:host([data-phx-palette="${name}"].phx-theme-dark)`)) : {};
  const values: Record<string, string> = { ...base, ...palette };
  const resolve = (token: string): string => {
    const value = values[token];
    const reference = value?.match(/^var\((--phx-[\w-]+)\)$/);
    return reference ? resolve(reference[1]) : value;
  };
  return resolve;
}
const everyTheme = [null, ...palettes].flatMap((name) => [[name, "light"], [name, "dark"]]) as [string | null, "light" | "dark"][];

it.each(everyTheme)("shows keyboard focus on the toolbar in theme %s, %s", (name, mode) => {
  const value = themeValues(name, mode);
  expect(contrast(rgb(value("--phx-toolbar-focus")), rgb(value("--phx-toolbar-bg")))).toBeGreaterThanOrEqual(3);
});

it("draws the toolbar's focus ring in the toolbar's own token", () => {
  const at = themes.indexOf(".phx-toolbar-button:focus-visible");
  expect(at).toBeGreaterThan(0);
  expect(themes.slice(at, themes.indexOf("}", at))).toContain("outline-color: var(--phx-toolbar-focus)");
  expect(themes.slice(0, at)).toMatch(/\.phx-toolbar:focus-visible,\s*\.phx-toolbar-handle:focus-visible,\s*$/);
});

it.each(everyTheme)("keeps the kill switch warning legible in theme %s, %s", (name, mode) => {
  const value = themeValues(name, mode);
  const toolbar = rgb(value("--phx-toolbar-bg"));
  const fill = value("--phx-toolbar-danger-bg");
  const tint = fill === "transparent" ? [0, 0, 0, 0] : rgb(fill);
  const blended = toolbar.map((v, i) => (i < 3 ? tint[i] * tint[3] + v * (1 - tint[3]) : v));
  expect(contrast(rgb(value("--phx-toolbar-danger")), blended)).toBeGreaterThanOrEqual(4.5);
});

it("restyles the warning button after the plain toolbar button, which would otherwise win", () => {
  const plain = themes.indexOf(".phx-toolbar-button { background");
  const warning = themes.indexOf(".phx-toolbar-button.phx-toolbar-warning,");
  expect(plain).toBeGreaterThan(0);
  expect(warning).toBeGreaterThan(plain);
  const rule = themes.slice(warning, themes.indexOf("}", warning));
  expect(rule).toContain("color: var(--phx-toolbar-danger)");
  expect(rule).toContain("border-color: var(--phx-toolbar-danger)");
  expect(rule).toContain(".phx-toolbar-warning:hover");
});

it("ships the color themes Settings offers", () => {
  expect(palettes).toEqual(["wayfinding", "framed", "mist", "office", "aubergine", "autumn"]);
});

it.each(palettes)("keeps the %s theme's light and dark blocks complete and in step", (name) => {
  const lightBlock = block(`:host([data-phx-palette="${name}"])`);
  const autoDark = block(`  :host([data-phx-palette="${name}"]:not(.phx-theme-light))`);
  const forced = block(`:host([data-phx-palette="${name}"].phx-theme-dark)`);
  // A token the light block sets but a dark block leaves out would outrank the panel's own dark value.
  expect(Object.keys(forced).sort()).toEqual(Object.keys(lightBlock).sort());
  expect(autoDark).toEqual(forced);
});

it.each(palettes.flatMap((name) => [[name, "light"], [name, "dark"]]))("keeps the %s theme legible in %s", (name, mode) => {
  const hex = (v: Record<string, string>) => Object.fromEntries(Object.entries(v).filter(([, x]) => x.startsWith("#")));
  const values = mode === "light"
    ? { ...light, ...hex(block(`:host([data-phx-palette="${name}"])`)) }
    : { ...forcedDark, ...hex(block(`:host([data-phx-palette="${name}"].phx-theme-dark)`)) };
  for (const surface of ["--phx-bg", "--phx-surface", "--phx-surface-2", "--phx-raised", "--phx-hover", "--phx-selected", "--phx-accent-lt"]) {
    for (const text of ["--phx-text", "--phx-text-2"]) {
      expect(contrast(rgb(values[text]), rgb(values[surface])), `${text} on ${surface}`).toBeGreaterThanOrEqual(4.5);
    }
  }
  for (const surface of ["--phx-bg", "--phx-surface", "--phx-surface-2", "--phx-raised"]) {
    for (const signal of ["--phx-success", "--phx-error", "--phx-warning", "--phx-read"]) {
      expect(contrast(rgb(values[signal]), rgb(values[surface])), `${signal} on ${surface}`).toBeGreaterThanOrEqual(4.5);
    }
    expect(contrast(rgb(values["--phx-focus"]), rgb(values[surface])), `focus on ${surface}`).toBeGreaterThanOrEqual(3);
  }
  for (const surface of ["--phx-surface", "--phx-raised"]) {
    expect(contrast(rgb(values["--phx-field-border"]), rgb(values[surface])), `field border on ${surface}`).toBeGreaterThanOrEqual(3);
  }
  expect(contrast(rgb(values["--phx-accent-fg"]), rgb(values["--phx-accent"])), "primary button").toBeGreaterThanOrEqual(4.5);
  if (values["--phx-guide-surface"]) {
    expect(contrast(rgb(values["--phx-text-2"]), rgb(values["--phx-guide-surface"])), "Guide text").toBeGreaterThanOrEqual(4.5);
  }
  if (values["--phx-toolbar-current-bg"]?.startsWith("#")) {
    expect(contrast(rgb(values["--phx-toolbar-current-fg"]), rgb(values["--phx-toolbar-current-bg"])), "current workspace").toBeGreaterThanOrEqual(4.5);
  }
  if (values["--phx-toolbar-bg"]?.startsWith("#") && values["--phx-toolbar-btn-fg"]?.startsWith("#")) {
    expect(contrast(rgb(values["--phx-toolbar-btn-fg"]), rgb(values["--phx-toolbar-bg"])), "toolbar icons").toBeGreaterThanOrEqual(4.5);
  }
});
