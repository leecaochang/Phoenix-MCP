/** Register the bundled Inter face for every Phoenix surface. */

// Served from the integration's own static path, so the panel never contacts a
// third-party font host and keeps working offline. The version in the filename
// replaces cache-busting, which only covers JavaScript chunks.
export const PHOENIX_FONT_URL = "/local/phoenix-mcp/fonts/phoenix-inter-4.1.woff2";

const STYLE_ID = "phx-font-face";

/**
 * Add the "Phoenix Inter" face to a document once.
 *
 * Phoenix renders inside shadow roots, where browsers ignore @font-face, so the
 * face is declared in the owning document's head. The Phoenix-only family name
 * keeps it from changing text on Home Assistant's pages, which may load their
 * own Inter. The element id makes the call idempotent across separately loaded
 * bundles that share one document.
 */
export function ensurePhoenixFont(doc: Document = document): void {
  if (doc.getElementById(STYLE_ID)) return;
  const style = doc.createElement("style");
  style.id = STYLE_ID;
  style.textContent =
    `@font-face { font-family: "Phoenix Inter"; src: url("${PHOENIX_FONT_URL}") format("woff2"); `
    + "font-weight: 100 900; font-style: normal; font-display: swap; }";
  (doc.head ?? doc.documentElement).appendChild(style);
}
