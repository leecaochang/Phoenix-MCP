/**
 * The workspaces, their windows and default arrangements. Today's screens are
 * the windows: each keeps its own behavior, data loading and safety checks.
 */

import { createContext, useContext, useMemo, useSyncExternalStore, type FunctionComponent, type ReactNode } from "react";
import type { AddPanelOptions, DockviewApi, IDockviewPanelProps, IWatermarkPanelProps } from "dockview-react";
import { AttentionList } from "../components/AttentionPanel";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { AccessKeyListView } from "../views/AccessKeyList";
import { AccessKeyDetailView } from "../views/AccessKeyDetail";
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
import type { WorkspaceId } from "./storage";
import { WindowVisibleContext } from "./visibility";
import { WindowPlaceContext } from "./tools";
import { LogDetailsContent } from "./logDetails";

export const WINDOW_TITLE_KEYS = {
  accessKeys: "shell.tabAccessKeys",
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
}

export const WORKSPACES: Record<WorkspaceId, WorkspaceDef> = {
  accessKeys: { labelKey: "shell.tabAccessKeys", icon: "accessKeys", windows: ["accessKeys", "attention"], arrange: (_, add) => add("accessKeys") },
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
  return (
    <ErrorBoundary key={`${view.name}:${view.name === "detail" ? view.accessKeyId : ""}`}>
      {view.name === "list" && (
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
        />
      )}
      {view.name === "wizard" && <OnboardingWizard onCancel={d.goBack} onFinish={d.openDetail} />}
      {view.name === "detail" && (
        <AccessKeyDetailView
          accessKeyId={view.accessKeyId}
          onBack={d.goBack}
          onRefresh={d.refreshAccessKeys}
          presetsEnabled={d.settings?.access_key_presets_enabled ?? false}
          esphome={d.settings ? { integration: !!d.settings.esphome_integration, builder: !!d.settings.esphome_builder } : null}
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
