/** The bundled Inter face is registered once per document, from Phoenix's own path. */
import { afterEach, describe, expect, it } from "vitest";
import { ensurePhoenixFont, PHOENIX_FONT_URL } from "../ui/fonts";

describe("ensurePhoenixFont", () => {
  afterEach(() => document.getElementById("phx-font-face")?.remove());

  it("declares a Phoenix-only family served from the integration", () => {
    ensurePhoenixFont();
    const css = document.getElementById("phx-font-face")?.textContent ?? "";
    expect(css).toContain('font-family: "Phoenix Inter"');
    expect(css).toContain(`url("${PHOENIX_FONT_URL}")`);
    expect(PHOENIX_FONT_URL.startsWith("/local/phoenix-mcp/")).toBe(true);
  });

  it("adds the face only once, however many bundles ask", () => {
    ensurePhoenixFont();
    ensurePhoenixFont(document);
    expect(document.querySelectorAll("#phx-font-face")).toHaveLength(1);
  });

  it("registers separately in another document, such as the chat pop-out", () => {
    const other = document.implementation.createHTMLDocument("popup");
    ensurePhoenixFont(other);
    expect(other.getElementById("phx-font-face")).not.toBeNull();
    expect(document.getElementById("phx-font-face")).toBeNull();
  });
});
