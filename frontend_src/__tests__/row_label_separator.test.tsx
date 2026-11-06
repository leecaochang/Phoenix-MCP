/**
 * A table row's accessible name joins its cells with the language's own list
 * separator, which is not a comma everywhere.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { ArchivedAccessKeyTable } from "../components/ArchivedAccessKeyTable";
import { primeTranslations } from "../i18n";
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";
import ja from "../../custom_components/phoenix_mcp/catalogs/ja.json";

const ROOT = "frontend_src";

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    if (entry.name === "__tests__") return [];
    const path = join(dir, entry.name);
    return entry.isDirectory() ? sources(path) : /\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

afterEach(() => { cleanup(); primeTranslations(en.panel); });

describe("row names", () => {
  it("never spell a separator by hand", () => {
    const spelled = sources(ROOT).flatMap((path) => readFileSync(path, "utf8").split("\n")
      .filter((line) => /rowLabel=\{.*`[^`]*(, |\. )\$\{/.test(line) || /rowLabel=\{.*`[^`]*\}(, |\. )/.test(line))
      .map((line) => `${path}: ${line.trim()}`));
    expect(spelled).toEqual([]);
  });

  it("use the displayed language's separator", () => {
    primeTranslations(ja.panel, "ja");
    render(<ArchivedAccessKeyTable accessKeys={[{ id: "k1", name: "one", revoked: true, created_at: "2026-01-01T00:00:00Z" }] as never} onDeleted={() => {}} />);
    const row = screen.getAllByRole("row").find((candidate) => candidate.getAttribute("aria-label")?.startsWith("one"));
    expect(row?.getAttribute("aria-label")).toContain(`one${ja.panel.common.listSeparator}`);
  });
});
