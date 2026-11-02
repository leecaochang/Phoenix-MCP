/**
 * Home Assistant is away: one notice over the panel, and only the panel, instead of a raw
 * fetch failure in every window (user, 2026-10-29). It says what is happening,
 * keeps asking Home Assistant whether it is back, and clears itself when a
 * request arrives again. Restarting Home Assistant is the usual reason, so the
 * wait is ordinary rather than an error.
 */

import { createPortal } from "react-dom";
import { api } from "../api";
import { useHomeAssistantAway } from "../utils/connection";
import { t } from "../i18n";

export function Reconnecting({ root }: { root: HTMLElement | null }) {
  const away = useHomeAssistantAway(() => api.getSettings());
  if (!away || !root) return null;
  return createPortal(
    <div className="phx-offline" role="alertdialog" aria-labelledby="phx-offline-title" aria-describedby="phx-offline-body">
      <div className="phx-offline-box">
        <span className="phx-offline-spinner" aria-hidden="true" />
        <h2 className="phx-offline-title" id="phx-offline-title">{t("shell.offlineTitle")}</h2>
        <p className="phx-offline-body" id="phx-offline-body">{t("shell.offlineBody")}</p>
      </div>
    </div>,
    root,
  );
}
