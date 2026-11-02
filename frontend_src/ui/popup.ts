/**
 * A separate browser window that shows part of the panel: Agent Chat and the
 * Guide both pop out into one. This only prepares the window. The caller's
 * React tree renders into the returned mount through a portal, so nothing
 * restarts and the content keeps its state.
 */

import PANEL_CSS from "../phoenix-mcp-panel.css?inline";
import THEMES_CSS from "../themes.css?inline";
import { ensurePhoenixFont } from "./fonts";

// The panel stylesheet is authored for a shadow host. A same-origin popup has
// no shadow host, so translate those selectors to the document root while
// preserving the exact same tokens and component rules.
const POPUP_PANEL_CSS = (PANEL_CSS + THEMES_CSS)
  .replace(/:host\(([^)]*)\)/g, ":root$1")
  .replaceAll(":host", ":root");

export interface PopupRequest {
  /** The window's name, so a second request reuses the same window. */
  name: string;
  /** The id of the element the content renders into. */
  mountId: string;
  /** The size of the window being popped out; the popup opens at least 360 px each way. */
  width: number;
  height: number;
  title: string;
  language: string;
  /** The CSS colour behind the content, such as a surface token. */
  background: string;
  /** Other stylesheets the content needs beyond the panel's own, such as the window controls'. */
  extraCss?: string;
}

export interface OpenPopup {
  window: Window;
  mount: HTMLElement;
}

/**
 * Open the popup centred over this window, with Phoenix's styles and font,
 * and return where to render. Null when the browser blocks it.
 */
export function openPopupWindow(request: PopupRequest): OpenPopup | null {
  const width = Math.max(360, Math.min(request.width, window.screen.availWidth || request.width));
  const height = Math.max(360, Math.min(request.height, window.screen.availHeight || request.height));
  const left = Math.max(0, Math.round(window.screenX + (window.outerWidth - width) / 2));
  const top = Math.max(0, Math.round(window.screenY + (window.outerHeight - height) / 2));
  let popup: Window | null = null;
  try {
    popup = window.open(
      "",
      request.name,
      [
        "popup=yes",
        `width=${width}`,
        `height=${height}`,
        `left=${left}`,
        `top=${top}`,
        "toolbar=no",
        "location=no",
        "menubar=no",
        "status=no",
        "scrollbars=no",
        "resizable=yes",
      ].join(","),
    );
  } catch {
    // Browser policy can reject window.open by returning null or throwing.
  }
  if (!popup) return null;

  const doc = popup.document;
  const base = doc.createElement("base");
  base.href = window.location.href;
  const viewport = doc.createElement("meta");
  viewport.name = "viewport";
  viewport.content = "width=device-width, initial-scale=1";
  const styleEl = doc.createElement("style");
  styleEl.textContent = `${POPUP_PANEL_CSS}\n${request.extraCss ?? ""}\n
      html, body, #${request.mountId} { width: 100%; height: 100%; margin: 0; overflow: hidden; }
      body { background: ${request.background}; }
      #${request.mountId} { position: relative; }
    `;
  doc.head.replaceChildren(base, viewport, styleEl);
  ensurePhoenixFont(doc);
  doc.title = request.title;
  doc.documentElement.lang = request.language;
  const mount = doc.createElement("div");
  mount.id = request.mountId;
  doc.body.replaceChildren(mount);
  popup.focus();
  return { window: popup, mount };
}
