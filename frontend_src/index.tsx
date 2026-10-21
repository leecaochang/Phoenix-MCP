import { useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  getLanguagePreference,
  isI18nReady,
  loadTranslations,
  resolveLanguage,
  setLanguagePreference,
  syncTranslations,
} from "./i18n";
import { setHass } from "./api";
import DOCKVIEW_CSS from "dockview-react/dist/styles/dockview.css?inline";
import PANEL_CSS from "./phoenix-mcp-panel.css?inline";
import DOCK_CSS from "./shell/dock/dock.css?inline";
import { PanelDataProvider, type Theme } from "./shell/PanelData";
import { Shell } from "./shell/Shell";
import { readRecord } from "./shell/storage";
import { ensurePhoenixFont } from "./ui/fonts";
import { applyThemeClass, resolveThemeClass } from "./ui/theme";

export { HIGH_RISK_DOMAINS } from "./utils";

function PhoenixApp({ hass, narrow, theme, onThemeChange, language, onLanguageChange }: { hass: unknown; narrow: boolean; theme: Theme; onThemeChange: (t: Theme) => void; language: string; onLanguageChange: (lang: string) => void }) {
  // Ordinary entry resumes the workspace last used in this environment.
  const [initialWorkspace] = useState(() => {
    const userId = (hass as { user?: { id?: string } } | null)?.user?.id ?? null;
    return readRecord(userId, window.innerWidth < 700 ? "mobile" : "desktop")?.lastWorkspace ?? "accessKeys";
  });
  return (
    <PanelDataProvider
      hass={hass}
      narrow={narrow}
      theme={theme}
      onThemeChange={onThemeChange}
      language={language}
      onLanguageChange={onLanguageChange}
      initialWorkspace={initialWorkspace}
    >
      <Shell />
    </PanelDataProvider>
  );
}

class PhoenixPanelElement extends HTMLElement {
  private _root: Root | null = null;
  private _hass: unknown = null;
  private _narrow: boolean = false;
  private _prevUserId: string | undefined = undefined;
  private _theme: Theme = "auto";
  private _i18nStarted: boolean = false;
  private _language: string = getLanguagePreference();

  connectedCallback() {
    this.style.touchAction = "pan-y";

    let saved: string | null = null;
    try { saved = localStorage.getItem("phx-theme"); } catch { /* storage blocked: use default */ }
    if (saved === "light" || saved === "dark" || saved === "auto") {
      this._theme = saved;
    }
    this._applyThemeClass();
    ensurePhoenixFont(this.ownerDocument);

    const shadow = this.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    // Dockview's own stylesheet first, so the panel's window styles override it.
    style.textContent = DOCKVIEW_CSS + PANEL_CSS + DOCK_CSS;
    shadow.appendChild(style);

    const mount = document.createElement("div");
    mount.style.height = "100%";
    shadow.appendChild(mount);

    this._root = createRoot(mount);
    this._render();
  }

  disconnectedCallback() {
    this._root?.unmount();
    this._root = null;
  }

  set hass(hass: unknown) {
    this._hass = hass;
    setHass(hass);
    if (!this._i18nStarted) {
      this._i18nStarted = true;
      loadTranslations(hass, resolveLanguage(hass)).then((committed) => {
        if (committed) this._render();
      });
    } else {
      // Under "auto" the language follows the HA profile, which an admin can
      // change without reloading the page. A no-op unless it actually differs.
      void syncTranslations(hass).then((changed) => { if (changed) this._render(); });
    }
    const uid = (hass as Record<string, Record<string, string>> | null)?.user?.id;
    if (uid !== this._prevUserId) {
      this._prevUserId = uid;
      this._render();
    }
    if (this._theme === "auto") this._applyThemeClass();
  }

  set narrow(value: boolean) {
    if (this._narrow !== value) {
      this._narrow = value;
      this._render();
    }
  }

  private _applyThemeClass() {
    const darkMode = (this._hass as { themes?: { darkMode?: boolean } } | null)?.themes?.darkMode;
    applyThemeClass(this, resolveThemeClass(darkMode, this._theme));
  }

  private _setLanguage(pref: string) {
    this._language = pref;
    setLanguagePreference(pref);
    // Repaint immediately so the dropdown reflects the choice, then again once
    // the new catalog lands. Without the first render the select would appear
    // stuck on the old value for the length of a round trip.
    this._render();
    loadTranslations(this._hass, resolveLanguage(this._hass)).then((committed) => {
      if (committed) this._render();
    });
  }

  private _setTheme(t: Theme) {
    this._theme = t;
    try { localStorage.setItem("phx-theme", t); } catch { /* storage blocked: skip persistence */ }
    this._applyThemeClass();
    // The global Agent Chat window lives outside this element (document.body
    // shadow host) and cannot see its class change; tell it to re-resolve.
    window.dispatchEvent(new CustomEvent("phx-theme-changed"));
    this._render();
  }

  private _render() {
    // Strings arrive from the admin API after boot, so hold the first paint
    // until they do rather than flashing raw keys. Same shape as the hass guard.
    if (this._root && this._hass && isI18nReady()) {
      this._root.render(
        <PhoenixApp
          key={this._prevUserId ?? "unidentified"}
          hass={this._hass}
          narrow={this._narrow}
          theme={this._theme}
          onThemeChange={(t) => this._setTheme(t)}
          language={this._language}
          onLanguageChange={(lang) => this._setLanguage(lang)}
        />
      );
    }
  }
}

if (!customElements.get("phoenix-mcp-panel")) {
  customElements.define("phoenix-mcp-panel", PhoenixPanelElement);
}
