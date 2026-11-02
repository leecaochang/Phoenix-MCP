/**
 * Agent Chat docked in My Workspace. The window only offers a slot element;
 * the Agent Chat window that already exists (the panel's own, or the global
 * one floating over Home Assistant) draws itself into the slot, as it does
 * into its pop-out window, so the conversation is never duplicated or started
 * again. While the slot is not on screen (another workspace, or another page)
 * Chat floats; it returns to the slot when My Workspace is shown again.
 */

import { createContext, useContext, useEffect, useState } from "react";
import { t } from "../i18n";
import { Icon } from "../ui/Icon";
import { usePanelDataOrNull } from "./PanelData";
import { useSettings } from "./settings";
import { WorkspaceVisibleContext } from "./visibility";

/** The window id of Agent Chat in My Workspace. */
export const CHAT_WINDOW = "agentChat";

export interface ChatDock {
  /** Offer `slot` for Chat to draw into, or take it back (only the slot that holds it is released). */
  offer: (slot: HTMLElement) => void;
  release: (slot: HTMLElement) => void;
  /** Open Chat, which draws into the slot while the slot is shown. */
  openChat: () => void;
}

export const ChatDockContext = createContext<ChatDock | null>(null);

/** Agent Chat's window in My Workspace: the slot, with a way to open Chat while it is closed. */
export function ChatDockWindow() {
  const dock = useContext(ChatDockContext);
  const visible = useContext(WorkspaceVisibleContext);
  // With the kill switch on Chat cannot open, so the window says why and leads to the switch, as the toolbar does.
  const killSwitch = !!usePanelDataOrNull()?.settings?.kill_switch;
  const settings = useSettings();
  const [slot, setSlot] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!dock || !slot || !visible) return;
    dock.offer(slot);
    return () => dock.release(slot);
  }, [dock, slot, visible]);
  return (
    <div className="phx-chat-dock">
      <div ref={setSlot} className="phx-chat-dock-slot" />
      <div className="phx-chat-dock-empty">
        {killSwitch ? (
          <>
            <p>{t("shell.killSwitchTitle")}</p>
            <button type="button" className="btn btn-outline btn-sm phx-action" onClick={() => settings?.reveal("killSwitch")}>
              <Icon name="killSwitch" size={16} />
              <span>{t("shell.killSwitchFull")}</span>
            </button>
          </>
        ) : (
          <>
            <p>{t("agentchat.dockedClosed")}</p>
            <button type="button" className="btn btn-outline btn-sm phx-action" onClick={() => dock?.openChat()}>
              <Icon name="agentChat" size={16} />
              <span>{t("shell.agentChat")}</span>
            </button>
          </>
        )}
      </div>
    </div>
  );
}
