import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

// A class defined twice in one stylesheet does not fail: the later block simply
// wins, on whatever properties it happens to set, for every element wearing that
// name. Nothing errors: a new component reusing a class the chat window's
// titlebar already defines just gives its buttons a stray border and a fixed box.
//
// A shared prefix is not a namespace. This fails on any class whose top-level
// rule block appears more than once, so a collision is a red test rather than
// something noticed in a screenshot.

const CSS = fs.readFileSync(
  path.join(__dirname, "..", "phoenix-mcp-panel.css"), "utf8");
const DOCK_CSS = fs.readFileSync(
  path.join(__dirname, "..", "shell", "dock", "dock.css"), "utf8");

/** Strip @media / @supports bodies: a class restated inside one is a responsive
 *  override, which is the normal way to write them and not a collision. */
function stripAtRuleBlocks(css: string): string {
  let out = "";
  for (let i = 0; i < css.length; i++) {
    if (css[i] !== "@") { out += css[i]; continue; }
    const open = css.indexOf("{", i);
    if (open === -1) break;
    let depth = 0, j = open;
    for (; j < css.length; j++) {
      if (css[j] === "{") depth++;
      else if (css[j] === "}" && --depth === 0) break;
    }
    i = j;
  }
  return out;
}

/** Class names that OPEN a top-level rule block, one entry per block. */
function definedClasses(css: string): string[] {
  const withoutComments = stripAtRuleBlocks(css.replace(/\/\*[\s\S]*?\*\//g, ""));
  const out: string[] = [];
  // Only selectors that are a bare class (optionally with pseudo/state/compound
  // parts) count as "defining" it; a descendant selector is scoping, not a
  // redefinition, and legitimately appears many times.
  for (const m of withoutComments.matchAll(/(^|\})\s*([^{}@]+)\{/g)) {
    // Only a rule whose ENTIRE selector is one bare class counts as defining it.
    // A comma-separated group (".a, .b { font-size }") is shared styling that
    // routinely sits beside a class's own rule, and counting it would report
    // every such pair as a collision.
    const bare = /^\.([a-zA-Z0-9_-]+)$/.exec(m[2].trim());
    if (bare) out.push(bare[1]);
  }
  return out;
}

describe("panel stylesheet", () => {
  it("anchors modal backdrops to the viewport", () => {
    expect(CSS).toMatch(/\.modal-backdrop\s*\{[^}]*position:\s*fixed;/s);
  });

  it("keeps short tab content painted through the viewport", () => {
    expect(CSS).toMatch(/:host\s*\{[^}]*min-height:\s*100vh;[^}]*min-height:\s*100dvh;/s);
    expect(CSS).toMatch(/\.phx-shell\s*\{[^}]*min-height:\s*100vh;[^}]*min-height:\s*100dvh;/s);
  });

  it("stops the complete mobile top bar below HA's safe area without padding", () => {
    expect(CSS).toMatch(/\.phx-topbar\s*\{[^}]*position:\s*sticky;[^}]*top:\s*0;[^}]*z-index:\s*var\(--phx-z-tabbar\);/s);
    expect(CSS).toMatch(/@media\s*\(max-width:\s*700px\)[\s\S]*?\.phx-topbar\s*\{[^}]*--phx-topbar-safe-top:\s*var\(--safe-area-inset-top,\s*env\(safe-area-inset-top,\s*0px\)\);[^}]*top:\s*var\(--phx-topbar-safe-top\);/s);
    expect(CSS).toMatch(/\.phx-topbar::before\s*\{[^}]*position:\s*absolute;[^}]*bottom:\s*100%;[^}]*height:\s*var\(--phx-topbar-safe-top\);[^}]*background:\s*var\(--phx-surface\);/s);
    expect(CSS).not.toMatch(/\.phx-topbar\s*\{[^}]*padding-top:/s);
    expect(CSS).not.toMatch(/\.phx-tabs\s*\{[^}]*position:\s*sticky;/s);
  });

  it("keeps the workspace toolbar clear of the companion app's status bar", () => {
    expect(CSS).toMatch(/\.phx-toolbar\s*\{[^}]*--phx-toolbar-safe-top:\s*var\(--safe-area-inset-top,\s*env\(safe-area-inset-top,\s*0px\)\);/s);
    expect(CSS).toMatch(/\.phx-toolbar\[data-dock="top"\]\s*\{[^}]*margin-top:\s*calc\(8px \+ var\(--phx-toolbar-safe-top\)\)/s);
    expect(CSS).toMatch(/\.phx-env-mobile \.phx-toolbar\s*\{[^}]*margin:\s*calc\(4px \+ var\(--phx-toolbar-safe-top\)\)/s);
  });

  it("contains chat transcript overscroll inside the floating window", () => {
    expect(CSS).toMatch(/\.agentcli-body\s*\{[^}]*overscroll-behavior-y:\s*contain;[^}]*touch-action:\s*pan-y;/s);
    expect(CSS).toMatch(/\.agentcli-thinking-body\s*\{[^}]*overscroll-behavior:\s*contain;/s);
    expect(CSS).toMatch(/\.agentcli-md pre\s*\{[^}]*overscroll-behavior:\s*contain;/s);
  });

  it("scales all transcript prose while keeping metadata and controls fixed", () => {
    expect(CSS).toMatch(/\.agentcli-body\s*\{[^}]*font-size:\s*var\(--phx-agentcli-text-size,\s*13px\);/s);
    for (const selector of [
      "agentcli-working",
      "agentcli-thinking",
      "agentcli-tool",
      "agentcli-tool-result",
      "agentcli-tool-image-unavailable",
      "agentcli-progress",
      "agentcli-notice",
      "agentcli-unsafe",
    ]) {
      expect(CSS).toMatch(new RegExp(`\\.${selector}\\s*\\{[^}]*font-size:\\s*0\\.92em;`, "s"));
    }
    expect(CSS).toMatch(/\.agentcli-ts\s*\{[^}]*font-size:\s*10px;/s);
    expect(CSS).toMatch(/\.btn-sm\s*\{[^}]*font-size:\s*12px;/s);
  });

  it("stacks select-backed setting help above its control", () => {
    expect(CSS).toMatch(/\.toggle-row-stacked-control\s*\{[^}]*flex-direction:\s*column;[^}]*align-items:\s*stretch;/s);
    expect(CSS).toMatch(/@media\s*\(min-width:\s*901px\)[\s\S]*?\.toggle-row-stacked-control\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+minmax\(220px,\s*320px\);/s);
    expect(CSS).toMatch(/@media\s*\(max-width:\s*900px\)[\s\S]*?\.toggle-row-stacked-control\s*>\s*\.input\s*\{[^}]*width:\s*100%;/s);
  });

  it("contains modal boundary overscroll", () => {
    expect(CSS).toMatch(/\.modal-backdrop\s*\{[^}]*overscroll-behavior:\s*none;/s);
    expect(CSS).toMatch(/\.modal\s*\{[^}]*overscroll-behavior:\s*contain;/s);
  });

  it("keeps localized theme choices on one line", () => {
    expect(CSS).toMatch(/\.theme-toggle\s*\{[^}]*flex-shrink:\s*0;/s);
    expect(CSS).toMatch(/\.theme-toggle-btn\s*\{[^}]*white-space:\s*nowrap;/s);
  });

  it("bounds Agent Chat provider model selects without constraining compact screens", () => {
    expect(CSS).toMatch(/\.agentcli-settings-model-edit\s*>\s*select,\s*\.agentcli-settings-model-row\s+select\s*\{[^}]*width:\s*320px;[^}]*max-width:\s*100%;[^}]*min-width:\s*0;/s);
    expect(CSS).toMatch(/@media\s*\(max-width:\s*900px\)[\s\S]*?\.agentcli-settings-model-edit\s*>\s*select,\s*\.agentcli-settings-model-row\s+select\s*\{[^}]*width:\s*100%;/s);
  });

  it("keeps long localized change text inside the Changes windows", () => {
    // Each line under an item's name stays in its cell instead of widening the column.
    expect(CSS).toMatch(/\.change-item\s*\{[^}]*min-width:\s*0;/s);
    expect(CSS).toMatch(/\.change-item\s*>\s*span\s*\{[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap;/s);
    // The restore confirmation wraps in a narrow Comparison.
    expect(CSS).toMatch(/\.phx-restore-confirm\s*\{[^}]*flex-wrap:\s*wrap;/s);
    expect(CSS).toMatch(/\.phx-restore-confirm-actions\s*\{[^}]*flex-wrap:\s*wrap;/s);
  });

  it("keeps long localized approval text inside the Approvals windows", () => {
    // The list's search gives way to the Show menu instead of pushing it out of the window.
    expect(CSS).toMatch(/\.approvals-filter-row\s*\{[^}]*flex-wrap:\s*wrap;/s);
    expect(CSS).toMatch(/\.approvals-filter-row\s*>\s*input\[type="search"\]\s*\{[^}]*min-width:\s*0;/s);
    // A long request title wraps in Review and in Request Details.
    expect(CSS).toMatch(/\.phx-review-title\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
    expect(CSS).toMatch(/\.phx-request-title\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
  });

  it("wraps the rejection reason row in a narrow Review", () => {
    expect(CSS).toMatch(/\.phx-review\s+\.approval-reject-row\.phx-review-reason\s*\{[^}]*flex-wrap:\s*wrap;/s);
    expect(CSS).toMatch(/\.phx-review-reason\s+\.approval-reason-input\s*\{[^}]*min-width:\s*0;/s);
  });

  it("keeps localized MESA mode badges visible", () => {
    expect(CSS).toMatch(/\.mesa-group\s+\.data-table\s+td:nth-child\(2\)\s*\{[^}]*width:\s*260px;/s);
    expect(CSS).toMatch(/\.mesa-row-modes\s*\{[^}]*white-space:\s*normal;/s);
  });

  it("never defines the same class in two separate blocks", () => {
    const seen = new Map<string, number>();
    // The panel and window stylesheets share one shadow root, so they are checked together.
    for (const cls of definedClasses(CSS + "\n" + DOCK_CSS)) seen.set(cls, (seen.get(cls) ?? 0) + 1);
    const duplicated = [...seen.entries()].filter(([, n]) => n > 1).map(([c]) => c).sort();
    expect(duplicated).toEqual([]);
  });

  it("the detector actually finds a duplicate", () => {
    // Asserting an absence above, so prove the walk is not blind.
    const dupes = definedClasses(".a { color: red } .b { color: blue } .a { color: green }");
    expect(dupes.filter((c) => c === "a")).toHaveLength(2);
  });

  it("a responsive override inside @media is not a redefinition", () => {
    const found = definedClasses(".a { x: 1 } @media (max-width: 700px) { .a { x: 2 } }");
    expect(found).toEqual(["a"]);
  });

  it("a descendant or compound selector is not a redefinition", () => {
    const found = definedClasses(".a .b { x: 1 } .a.c { y: 2 } .a { z: 3 }");
    expect(found).toEqual(["a"]);
  });

  it("a shared comma group beside a class's own rule is not a redefinition", () => {
    const found = definedClasses(".a, .b { font-size: 1px } .b { margin: 0 }");
    expect(found).toEqual(["b"]);
  });
});
