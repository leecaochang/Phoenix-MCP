/**
 * The workspaces, their windows and default arrangements. Today's screens are
 * the windows: each keeps its own behavior, data loading and safety checks.
 */

import { createContext, useContext, useEffect, useMemo, useSyncExternalStore, type FunctionComponent, type ReactNode } from "react";
import type { AddPanelOptions, DockviewApi, IDockviewPanelProps, IWatermarkPanelProps } from "dockview-react";
import { AttentionList } from "../components/AttentionPanel";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { AccessKeyListView } from "../views/AccessKeyList";
import {
  AccessKeyNotices, CapabilitiesWindow, KeySettingsWindow, PermissionSummaryWindow, PermissionsWindow, PersonaWindow, PresetsWindow,
  useAccessKeyDetail, useKeyActionsTool,
} from "../views/AccessKeyDetail";
import { ApprovalsView } from "../views/ApprovalsView";
import { AuditView } from "../views/AuditView";
import { ChangesView } from "../views/ChangesView";
import { ConversationsView } from "../views/ConversationsView";
import { MesaView } from "../views/MesaView";
import { OnboardingWizard } from "../views/OnboardingWizard";
import { SettingsView } from "../views/SettingsView";
import { t } from "../i18n";
import type { IconName } from "../ui/Icon";
import { ControllerContext } from "./dock/TitleBar";
import { usePanelData } from "./PanelData";
import type { GlobalSettings } from "../types";
import type { Slot } from "./dock/geometry";
import type { WorkspaceId } from "./storage";
import { WindowVisibleContext } from "./visibility";
import { WindowPlaceContext } from "./tools";
import { LogDetailsContent } from "./logDetails";

export const WINDOW_TITLE_KEYS = {
  accessKeys: "shell.tabAccessKeys",
  persona: "accessKeys.cardPersona",
  capabilities: "accessKeys.cardCapabilities",
  permissions: "accessKeys.permissionsTree",
  permissionSummary: "accessKeys.permissionSummary",
  keySettings: "workspace.keySettings",
  presets: "accessKeys.cardPresets",
  approvals: "shell.tabApprovals",
  changes: "shell.tabChanges",
  mesa: "shell.tabMesa",
  auditLog: "shell.tabAudit",
  conversationLog: "conversations.title",
  logDetails: "workspace.logDetails",
  settings: "shell.tabSettings",
  attention: "attention.title",
} as const;
export type WindowId = keyof typeof WINDOW_TITLE_KEYS;

export const windowTitle = (id: string): string =>
  id in WINDOW_TITLE_KEYS ? t(WINDOW_TITLE_KEYS[id as WindowId]) : id;

/** Shown by the shell when items need checking; never saved in a layout. */
export const ATTENTION_WINDOW = "attention";

type Add = (id: string, position?: AddPanelOptions["position"]) => void;

export interface WorkspaceDef {
  labelKey: string;
  icon: IconName;
  windows: WindowId[];
  arrange: (api: DockviewApi, add: Add) => void;
  /** Windows that start folded into their title bars. */
  folded?: WindowId[];
  /** Windows shown only while a setting allows, with where each appears when it does. */
  conditional?: Partial<Record<WindowId, Slot>>;
}

/** Whether a conditional window is allowed by the current settings; null while they load. */
export function windowAvailable(id: string, settings: GlobalSettings | null): boolean | null {
  if (id !== "presets") return true;
  return settings ? !!settings.access_key_presets_enabled : null;
}

const size = (api: DockviewApi, id: string, dims: { width?: number; height?: number }) => api.getPanel(id)?.group.api.setSize(dims);

export const WORKSPACES: Record<WorkspaceId, WorkspaceDef> = {
  accessKeys: {
    labelKey: "shell.tabAccessKeys",
    icon: "accessKeys",
    windows: ["accessKeys", "persona", "capabilities", "keySettings", "presets", "permissions", "permissionSummary", "attention"],
    // The key list across the top; the selected key's windows below it, editing on the
    // left and the Permissions tree on the right. Specialized and informational windows
    // start folded, as accepted for Access Keys.
    arrange: (api, add) => {
      add("accessKeys");
      add("persona", { direction: "below" });
      add("permissions", { referencePanel: "persona", direction: "right" });
      add("capabilities", { referencePanel: "persona", direction: "below" });
      add("keySettings", { referencePanel: "capabilities", direction: "below" });
      add("permissionSummary", { referencePanel: "permissions", direction: "below" });
      const listHeight = Math.round(Math.min(Math.max(api.height * 0.38, 200), 360));
      size(api, "accessKeys", { height: listHeight });
      size(api, "persona", { width: Math.round(api.width * 0.45) });
      size(api, "persona", { height: Math.round((api.height - listHeight) * 0.45) });
    },
    folded: ["keySettings", "presets", "permissionSummary"],
    conditional: { presets: { neighbor: "keySettings", side: "bottom" } },
  },
  approvals: { labelKey: "shell.tabApprovals", icon: "approvals", windows: ["approvals", "attention"], arrange: (_, add) => add("approvals") },
  changes: { labelKey: "shell.tabChanges", icon: "changes", windows: ["changes", "attention"], arrange: (_, add) => add("changes") },
  mesa: { labelKey: "shell.tabMesa", icon: "mesa", windows: ["mesa", "attention"], arrange: (_, add) => add("mesa") },
  logs: {
    labelKey: "workspace.logs",
    icon: "logs",
    windows: ["auditLog", "conversationLog", "logDetails", "attention"],
    // The two logs side by side with equal space, Log Details across the bottom, as accepted for Logs.
    arrange: (api, add) => {
      add("auditLog");
      add("conversationLog", { referencePanel: "auditLog", direction: "right" });
      add("logDetails", { direction: "below" });
      api.getPanel("logDetails")?.group.api.setSize({ height: Math.round(api.height * 0.4) });
    },
  },
  settings: { labelKey: "shell.tabSettings", icon: "settings", windows: ["settings", "attention"], arrange: (_, add) => add("settings") },
};

/** Whether this workspace is on screen; windows in a hidden workspace stay mounted but pause. */
export const WorkspaceVisibleContext = createContext(true);

/** A window's content frame: one error boundary per window, hidden but mounted while folded. */
export function WindowBody({ id, children }: { id: string; children: ReactNode }) {
  const controller = useContext(ControllerContext);
  const workspaceVisible = useContext(WorkspaceVisibleContext);
  useSyncExternalStore(controller?.folds.subscribe ?? (() => () => {}), controller?.folds.getVersion ?? (() => 0));
  const folded = controller?.folds.isFolded(id) ?? false;
  const place = useMemo(() => (controller ? { workspace: controller.id, window: id } : null), [controller, id]);
  return (
    <WindowPlaceContext.Provider value={place}>
    <WindowVisibleContext.Provider value={workspaceVisible && !folded}>
      <div className="phx-window-body" data-phx-window={id} hidden={folded}>
        <h2 className="sr-only">{windowTitle(id)}</h2>
        <ErrorBoundary>{children}</ErrorBoundary>
      </div>
    </WindowVisibleContext.Provider>
    </WindowPlaceContext.Provider>
  );
}

function AccessKeysContent() {
  const d = usePanelData();
  const view = d.accessKeyView;
  const detail = useAccessKeyDetail();
  const keyActions = useKeyActionsTool();
  const controller = useContext(ControllerContext);
  const wizard = view.name === "wizard";
  // Setting up an agent needs room: the key list window fills the workspace until the wizard ends.
  useEffect(() => {
    const panel = controller?.api?.getPanel("accessKeys");
    if (!wizard || !panel || panel.group.api.location.type !== "grid") return;
    panel.api.maximize();
    return () => { if (panel.api.isMaximized()) panel.api.exitMaximized(); };
  }, [wizard, controller]);
  return (
    <ErrorBoundary key={view.name}>
      {wizard ? <OnboardingWizard onCancel={d.goBack} onFinish={d.openDetail} /> : (
        <AccessKeyListView
          accessKeys={d.accessKeys}
          loading={d.loadingAccessKeys}
          error={d.accessKeysError}
          onRefresh={d.refreshAccessKeys}
          onOpenDetail={d.openDetail}
          onLaunchWizard={d.openWizard}
          showCreate={d.showCreate}
          onOpenCreate={() => d.setShowCreate(true)}
          onCloseCreate={() => d.setShowCreate(false)}
          onOpenSettings={d.goToSettings}
          selectedId={view.name === "detail" ? view.accessKeyId : null}
          renamingId={detail?.renaming ? detail.accessKeyId : null}
          onRenamed={(updated) => { detail?.applyUpdate(updated); d.refreshAccessKeys(); }}
          onRenameEnd={() => detail?.setRenaming(false)}
          selectedKeyTools={keyActions}
          notices={<AccessKeyNotices />}
        />
      )}
    </ErrorBoundary>
  );
}

function ApprovalsContent() {
  const d = usePanelData();
  return (
    <ApprovalsView
      tab={d.approvalsTab}
      onTabChange={d.setApprovalsTab}
      onCountChange={d.refreshPendingCount}
      refreshSignal={d.approvalSignal}
      claimedApprovals={d.claimedApprovals}
      openApprovalId={d.deepApprovalId}
      onConsumedDeepLink={d.consumeDeepApprovalLink}
    />
  );
}

function ChangesContent() { return <ChangesView hass={usePanelData().hass} />; }
function MesaContent() { return <MesaView />; }
function AuditLogContent() { return <AuditView accessKeys={usePanelData().accessKeys} />; }
function ConversationLogContent() { return <ConversationsView accessKeys={usePanelData().accessKeys} />; }

function SettingsContent() {
  const d = usePanelData();
  return (
    <SettingsView
      settings={d.settings}
      onSettingsChange={d.setSettings}
      theme={d.theme}
      onThemeChange={d.onThemeChange}
      language={d.language}
      onLanguageChange={d.onLanguageChange}
    />
  );
}

function LogDetailsWindowContent() { return <LogDetailsContent />; }

function AttentionContent() {
  return <div className="phx-attention phx-attention-window"><AttentionList state={usePanelData().attention} /></div>;
}

export const WINDOW_CONTENT: Record<WindowId, FunctionComponent> = {
  accessKeys: AccessKeysContent,
  persona: PersonaWindow,
  capabilities: CapabilitiesWindow,
  permissions: PermissionsWindow,
  permissionSummary: PermissionSummaryWindow,
  keySettings: KeySettingsWindow,
  presets: PresetsWindow,
  approvals: ApprovalsContent,
  changes: ChangesContent,
  mesa: MesaContent,
  auditLog: AuditLogContent,
  conversationLog: ConversationLogContent,
  settings: SettingsContent,
  logDetails: LogDetailsWindowContent,
  attention: AttentionContent,
};

/** Dockview panel components, one per window id; stable for the life of the bundle. */
export const DOCK_COMPONENTS: Record<string, FunctionComponent<IDockviewPanelProps>> = Object.fromEntries(
  (Object.keys(WINDOW_CONTENT) as WindowId[]).map((id) => {
    const Content = WINDOW_CONTENT[id];
    const Window = () => <WindowBody id={id}><Content /></WindowBody>;
    Window.displayName = `Window(${id})`;
    return [id, Window];
  }),
);

/** Shown when every window in a workspace is closed: reopen one, or restore the arrangement. */
export function EmptyWorkspace(_props: IWatermarkPanelProps) {
  const controller = useContext(ControllerContext);
  useSyncExternalStore(controller?.subscribe ?? (() => () => {}), controller?.getVersion ?? (() => 0));
  if (!controller) return null;
  return (
    <div className="phx-workspace-empty">
      <p>{t("workspace.empty")}</p>
      <div className="phx-workspace-empty-actions">
        {controller.closedWindows().map((id) => (
          <button key={id} type="button" className="btn btn-outline btn-sm" onClick={() => controller.reopen(id)}>
            {t("workspace.reopen", { window: windowTitle(id) })}
          </button>
        ))}
        <button type="button" className="btn btn-outline btn-sm" onClick={() => controller.restoreOriginal()}>
          {t("workspace.restoreOriginal")}
        </button>
      </div>
    </div>
  );
}
