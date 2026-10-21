/** The main toolbar: workspaces, Agent Chat or the kill-switch warning, documentation, workspace options. */

import { useEffect, useRef, useSyncExternalStore, type RefObject } from "react";
import { DOCS_BASE_URL } from "../components/common";
import { localeNumber, t } from "../i18n";
import { Icon } from "../ui/Icon";
import { MenuButton, type MenuEntry } from "../ui/Menu";
import type { WorkspaceController } from "./dock/controller";
import { usePanelData } from "./PanelData";
import { WORKSPACE_IDS, type Env } from "./storage";
import { WORKSPACES, windowTitle } from "./workspaces";

function WorkspaceOptions({ controller }: { controller: WorkspaceController }) {
  useSyncExternalStore(controller.subscribe, controller.getVersion);
  const items: MenuEntry[] = [
    ...controller.closedWindows().map((id) => ({
      id: `reopen-${id}`,
      label: t("workspace.reopen", { window: windowTitle(id) }),
      onSelect: () => controller.reopen(id),
    })),
  ];
  if (items.length) items.push("separator");
  items.push({ id: "restore", label: t("workspace.restoreOriginal"), onSelect: () => controller.restoreOriginal() });
  return (
    <MenuButton
      label={t("workspace.options")}
      icon={<Icon name="workspaceOptions" size={16} />}
      items={items}
      className="phx-toolbar-button phx-toolbar-icon-only"
    />
  );
}

export function Toolbar({ controller, shellRoot, env }: {
  /** The visible workspace's controller; absent in the phone stack. */
  controller: WorkspaceController | null;
  shellRoot: RefObject<HTMLElement | null>;
  env: Env;
}) {
  const d = usePanelData();
  const menuRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (menuRef.current) {
      (menuRef.current as unknown as Record<string, unknown>).hass = d.hass;
      (menuRef.current as unknown as Record<string, unknown>).narrow = d.narrow;
    }
  }, [d.hass, d.narrow]);

  // The kill-switch warning opens Settings at the kill switch.
  const openKillSwitch = () => {
    d.navigate("settings");
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const card = shellRoot.current?.querySelector<HTMLElement>("#phx-kill-switch-card");
      card?.scrollIntoView?.({ block: "start" });
      card?.querySelector<HTMLElement>("input, button")?.focus();
    }));
  };

  return (
    <nav className="phx-toolbar" aria-label={t("shell.sectionsAria")}>
      {d.narrow && <ha-menu-button ref={menuRef as RefObject<HTMLElement>} />}
      <div className="phx-toolbar-group">
        {WORKSPACE_IDS.map((id) => {
          const def = WORKSPACES[id];
          const active = d.workspace === id;
          const count = id === "approvals" ? d.pendingCount : 0;
          return (
            <button
              key={id}
              type="button"
              className={`phx-toolbar-button${active ? " is-current" : ""}`}
              aria-current={active ? "page" : undefined}
              aria-label={count > 0 ? t("shell.approvalsTabAria", { count }) : undefined}
              onClick={() => d.navigate(id)}
            >
              <Icon name={def.icon} size={16} />
              <span className="phx-toolbar-label">{t(def.labelKey)}</span>
              {count > 0 && <span className="phx-toolbar-badge" aria-hidden="true">{localeNumber(count)}</span>}
            </button>
          );
        })}
      </div>
      <span className="phx-toolbar-spacer" />
      <div className="phx-toolbar-group">
        {d.settings?.kill_switch ? (
          <button type="button" className="phx-toolbar-button phx-toolbar-warning" title={t("shell.killSwitchTitle")} onClick={openKillSwitch}>
            <Icon name="killSwitch" size={16} />
            <span className="phx-toolbar-label">{t(env === "mobile" ? "shell.killSwitchShort" : "shell.killSwitchFull")}</span>
          </button>
        ) : d.accessKeys.length > 0 ? (
          <button type="button" className="phx-toolbar-button" onClick={() => d.openAgentCli()}>
            <Icon name="agentChat" size={16} />
            <span className="phx-toolbar-label">{t("shell.agentChat")}</span>
          </button>
        ) : null}
        <a className="phx-toolbar-button" href={DOCS_BASE_URL} target="_blank" rel="noopener noreferrer">
          <Icon name="documentation" size={16} />
          <span className="phx-toolbar-label">{t("shell.documentation")}</span>
          <Icon name="externalLink" size={11} className="phx-toolbar-external" />
        </a>
        {controller && <WorkspaceOptions controller={controller} />}
      </div>
    </nav>
  );
}
