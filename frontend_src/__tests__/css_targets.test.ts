/** Rules jsdom cannot see: pointer target sizes, and what a narrow permission tree keeps. */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

const panel = readFileSync("frontend_src/phoenix-mcp-panel.css", "utf8");
const dock = readFileSync("frontend_src/shell/dock/dock.css", "utf8");

/** The bodies of every @media block with this condition, in one string. */
function media(css: string, condition: string): string {
  const out: string[] = [];
  for (let at = css.indexOf(`@media ${condition}`); at >= 0; at = css.indexOf(`@media ${condition}`, at + 1)) {
    let depth = 0;
    let end = css.indexOf("{", at);
    const start = end;
    do {
      if (css[end] === "{") depth++;
      if (css[end] === "}") depth--;
      end++;
    } while (depth > 0);
    out.push(css.slice(start, end));
  }
  return out.join("\n");
}

/** The declarations of the first rule whose selector list is exactly this. */
function rule(css: string, selector: string): string {
  const at = css.search(new RegExp(`^\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{`, "m"));
  expect(at, selector).toBeGreaterThanOrEqual(0);
  return css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at));
}

describe("the permission tree at phone width", () => {
  const narrow = media(panel, "(max-width: 600px)");

  it("keeps the effective permission, the badges and the hint control", () => {
    expect(narrow).not.toMatch(/\.tree-(badge|effective|hint-btn)[^{]*\{\s*display:\s*none/);
  });

  it("wraps what does not fit, with touch-sized permission and hint controls", () => {
    expect(rule(narrow, ".tree-node")).toContain("flex-wrap: wrap");
    expect(rule(narrow, ".tree-name")).toContain("flex: 1 1 140px");
    expect(rule(narrow, ".perm-btn")).toContain("min-width: 44px");
    expect(narrow).toMatch(/\.tree-hint-btn[^{]*\{ min-height: 44px; \}/);
  });

  it("overrides from after the base rules, which would otherwise win at equal specificity", () => {
    const override = panel.indexOf(".tree-node { flex-wrap: wrap");
    for (const base of [".tree-node { display: flex", ".tree-name {\n  flex: 1", ".perm-btn {\n"]) {
      expect(panel.indexOf(base), base).toBeGreaterThanOrEqual(0);
      expect(panel.indexOf(base), base).toBeLessThan(override);
    }
  });
});

describe("narrow overrides of the access key rows", () => {
  it.each([
    [".cap-row { flex-direction: column", ".cap-row {\n"],
    [".cap-row-modes { width: 100%", ".cap-row-modes { display: flex"],
    [".access-key-detail-sticky { margin: 0 -10px", ".access-key-detail-sticky {\n"],
    [".access-key-card-actions { min-width: auto", ".access-key-card-actions {\n"],
  ])("%s comes after its base rule", (override, base) => {
    expect(panel.indexOf(base), base).toBeGreaterThanOrEqual(0);
    expect(panel.indexOf(override), override).toBeGreaterThan(panel.indexOf(base));
  });
});

describe("pointer targets", () => {
  it("are at least 24 by 24", () => {
    expect(rule(panel, ".tree-expand")).toContain("width: 24px; height: 24px");
    expect(rule(panel, ".tree-spacer")).toContain("width: 24px");
    expect(rule(panel, ".agentcli-copy-btn")).toContain("width: 24px; height: 24px");
    expect(rule(panel, ".tag-chip-x")).toContain("min-width: 24px; min-height: 24px");
  });

  it("grow under a coarse pointer, to 44 in the phone stack", () => {
    expect(rule(media(dock, "(pointer: coarse)"), ".phx-window-control")).toContain("width: 32px; height: 32px");
    expect(rule(media(panel, "(pointer: coarse)"), ".phx-phone-title .phx-window-control, .phx-window-control.phx-guide-phone-close")).toContain("width: 44px; height: 44px");
  });
});
