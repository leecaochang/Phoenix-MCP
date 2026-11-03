/**
 * Home Assistant is away: one notice over the panel, and only the panel, instead of a raw
 * fetch failure in every window (user, 2026-10-29). It says what is happening,
 * keeps asking Home Assistant whether it is back, and clears itself when a
 * request arrives again. Restarting Home Assistant is the usual reason, so the
 * wait is ordinary rather than an error.
 */

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import { useHomeAssistantAway } from "../utils/connection";
import { t } from "../i18n";

export function Reconnecting({ root }: { root: HTMLElement | null }) {
  const away = useHomeAssistantAway(() => api.getSettings());
  const notice = useRef<HTMLDivElement | null>(null);
  // While it shows, the panel under it is out of reach of the keyboard as well as the pointer, and
  // the notice takes focus so a screen reader reads it; focus goes back when Home Assistant answers.
  useEffect(() => {
    const box = notice.current;
    if (!away || !root || !box) return undefined;
    const scope = root.getRootNode() as Document | ShadowRoot;
    const before = scope.activeElement instanceof HTMLElement ? scope.activeElement : null;
    const others = [...root.children].filter((el) => el !== box && !el.hasAttribute("inert"));
    for (const el of others) el.setAttribute("inert", "");
    box.focus();
    return () => {
      for (const el of others) el.removeAttribute("inert");
      if (before?.isConnected) before.focus();
    };
  }, [away, root]);
  if (!away || !root) return null;
  return createPortal(
    <div ref={notice} className="phx-offline" role="alertdialog" aria-modal="true" tabIndex={-1}
      aria-labelledby="phx-offline-title" aria-describedby="phx-offline-body">
      <div className="phx-offline-box">
        <span className="phx-offline-spinner" aria-hidden="true" />
        <h2 className="phx-offline-title" id="phx-offline-title">{t("shell.offlineTitle")}</h2>
        <p className="phx-offline-body" id="phx-offline-body">{t("shell.offlineBody")}</p>
      </div>
    </div>,
    root,
  );
}
