/**
 * The workspaces, their windows and default arrangements. Today's screens are
 * the windows: each keeps its own behavior, data loading and safety checks.
 */

import { useContext, useEffect, useLayoutEffect, useMemo, useRef, useSyncExternalStore, type FunctionComponent, type ReactNode } from "react";
import type { AddPanelOptions, DockviewApi, IDockviewPanelProps, IWatermarkPanelProps } from "dockview-react";
import { AttentionList } from "../components/AttentionPanel";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { AccessKeyListView } from "../views/AccessKeyList";
import {
  AccessKeyNotices, CapabilitiesWindow, KeySettingsWindow, PermissionSummaryWindow, PermissionsWindow, PersonaWindow, PresetsWindow,
  useAccessKeyDetail, useKeyActionsTool,
} from "../views/AccessKeyDetail";
import { ApprovalsListWindow, RequestDetailsWindow, ReviewWindow } from "../views/ApprovalsView";
import { AuditView } from "../views/AuditView";
import { ChangesListWindow, ComparisonWindow, ItemHistoryWindow } from "../views/ChangesView";
import { ConversationsView } from "../views/ConversationsView";
import { AdvisoryWindow, DiagnosticsWindow, MesaImportWindow, MesaProfilesWindow, OrphanedProfilesWindow, ProfileEditorWindow, SuggestedProfilesWindow } from "../views/MesaWindows";
import type { MesaFindings } from "./mesa";
import { SETTINGS_WINDOW_TITLE_KEYS } from "./settingsIndex";
import { OnboardingWizard } from "../views/OnboardingWizard";
import { AgentChatSettingsWindow, AiProvidersWindow, GeneralSettingsWindow, HomeAssistantWindow, LogsSettingsWindow, SafetyWindow } from "../views/SettingsWindows";
import { t } from "../i18n";
import type { IconName } from "../ui/Icon";
import { ControllerContext } from "./dock/TitleBar";
import { usePanelData } from "./PanelData";
import type { GlobalSettings } from "../types";
import type { Slot } from "./dock/geometry";
import type { WorkspaceId } from "./storage";
import { WindowVisibleContext, WorkspaceVisibleContext } from "./visibility";
import { CHAT_WINDOW, ChatDockWindow } from "./chatDock";
import { WindowPlaceContext } from "./tools";
import { LogDetailsContent } from "./logDetails";
import { TableSettingsContext } from "../ui/table/settings";
import { NotificationsWindow } from "./notifications";
import { HELP_WINDOW, HelpWindow } from "./help";
import { MIN_WINDOW_HEIGHT } from "./dock/fold";

export const WINDOW_TITLE_KEYS = {
  accessKeys: "shell.tabAccessKeys",
  persona: "accessKeys.cardPersona",
  capabilities: "accessKeys.cardCapabilities",
  permissions: "accessKeys.permissionsTree",
  permissionSummary: "accessKeys.permissionSummary",
  keySettings: "workspace.keySettings",
  presets: "accessKeys.cardPresets",
  approvals: "shell.tabApprovals",
  review: "workspace.review",
  requestDetails: "workspace.requestDetails",
  changes: "shell.tabChanges",
  comparison: "workspace.comparison",
  itemHistory: "workspace.itemHistory",
  mesaProfiles: "workspace.mesaProfiles",
  mesaEditor: "workspace.profileEditor",
  mesaSuggestions: "mesa.suggestTitle",
  mesaOrphans: "workspace.orphanedProfiles",
  mesaAdvisory: "workspace.advisory",
  mesaImport: "mesa.importTitle",
  mesaDiagnostics: "workspace.diagnostics",
  auditLog: "shell.tabAudit",
  conversationLog: "conversations.title",
  logDetails: "workspace.logDetails",
  ...SETTINGS_WINDOW_TITLE_KEYS,
  agentChat: "agentchat.title",
  attention: "attention.title",
  notifications: "notifications.title",
  help: "help.title",
} as const;
export type WindowId = keyof typeof WINDOW_TITLE_KEYS;

export const windowTitle = (id: string): string =>
  id in WINDOW_TITLE_KEYS ? t(WINDOW_TITLE_KEYS[id as WindowId]) : id;

/** Shown by the shell when items need checking; never saved in a layout. */
export const ATTENTION_WINDOW = "attention";

type Add = (id: string, position?: AddPanelOptions["position"]) => void;

export interface WorkspaceDef {
  labelKey: string;
  /** What the workspace is for, shown in Help when its toolbar button is pointed at. */
  helpKey: string;
  icon: IconName;
  windows: WindowId[];
  arrange: (api: DockviewApi, add: Add) => void;
  /** Windows that start folded into their title bars. */
  folded?: WindowId[];
  /** Windows shown only while a setting allows, with where each appears when it does. */
  conditional?: Partial<Record<WindowId, Slot>>;
  /** Comfortable starting heights; the workspace scrolls when the windows need more than the screen. */
  heights?: Partial<Record<WindowId, number>>;
  /** Windows the default arrangement pins along the top or bottom of the workspace. */
  pinned?: Partial<Record<"top" | "bottom", WindowId[]>>;
  /** Windows the default arrangement leaves closed, with where each opens. */
  closedAt?: Partial<Record<WindowId, Slot>>;
  /** Raised when the default arrangement changes so much that saved ones should not load. */
  layoutVersion?: number;
  /** Windows a toolbar tool opens and closes here; Workspace Options does not offer to reopen them. */
  toolWindows?: readonly WindowId[];
  /** The windows always share the visible height; the workspace never scrolls. */
  fit?: boolean;
  /** Windows that open beside a partner in its pinned band (see WorkspaceExtras.pairs). */
  pairs?: Readonly<Record<string, string>>;
}

/**
 * Whether a conditional window is allowed now; null while unknown. Presets
 * follows its setting; Orphaned Profiles and Advisory appear only while MESA
 * has findings for them that are not dismissed.
 */
export function windowAvailable(id: string, settings: GlobalSettings | null, mesa: MesaFindings | null = null): boolean | null {
  if (id === "presets") return settings ? !!settings.access_key_presets_enabled : null;
  if (id === "mesaOrphans") return mesa ? mesa.orphans : null;
  if (id === "mesaAdvisory") return mesa ? mesa.advisory : null;
  return true;
}

const size = (api: DockviewApi, id: string, dims: { width?: number; height?: number }) => api.getPanel(id)?.group.api.setSize(dims);

const OWN_WINDOWS: Record<WorkspaceId, WorkspaceDef> = {
  accessKeys: {
    labelKey: "shell.tabAccessKeys",
    helpKey: "help.wsAccessKeys",
    icon: "accessKeys",
    windows: ["accessKeys", "persona", "capabilities", "keySettings", "presets", "permissions", "permissionSummary", "attention"],
    // The key list across the top; the selected key's windows below it, editing on the
    // left and the Permissions tree on the right, at the heights below. Specialized and
    // informational windows start folded, as accepted for Access Keys.
    arrange: (api, add) => {
      add("accessKeys");
      add("persona", { direction: "below" });
      add("permissions", { referencePanel: "persona", direction: "right" });
      add("capabilities", { referencePanel: "persona", direction: "below" });
      add("keySettings", { referencePanel: "capabilities", direction: "below" });
      add("permissionSummary", { referencePanel: "permissions", direction: "below" });
      size(api, "persona", { width: Math.round(api.width * 0.45) });
    },
    folded: ["keySettings", "presets", "permissionSummary"],
    conditional: { presets: { neighbor: "keySettings", side: "bottom" } },
    // Comfortable heights; the workspace scrolls when the screen is shorter than they need.
    heights: { accessKeys: 300, persona: 420, capabilities: 600, keySettings: 420, presets: 320, permissions: 760, permissionSummary: 320 },
  },
  approvals: {
    labelKey: "shell.tabApprovals",
    helpKey: "help.wsApprovals",
    icon: "approvals",
    windows: ["approvals", "review", "requestDetails", "attention"],
    // The list beside a larger Review, as accepted for Approvals. Request Details starts
    // closed and opens below Review from its toolbar tool.
    arrange: (api, add) => {
      add("approvals");
      add("review", { referencePanel: "approvals", direction: "right" });
      size(api, "approvals", { width: Math.round(api.width * 0.34) });
    },
    closedAt: { requestDetails: { neighbor: "review", side: "bottom" } },
    heights: { requestDetails: 320 },
    // 2: the three windows replaced the single Approvals window.
    layoutVersion: 2,
  },
  changes: {
    labelKey: "shell.tabChanges",
    helpKey: "help.wsChanges",
    icon: "changes",
    windows: ["changes", "comparison", "itemHistory", "attention"],
    // The list and Comparison in two equal columns (user, 2026-10-27). Item History starts closed and opens
    // below the list by itself when a chosen change's item has other changes, unless the user closed it.
    // The windows always fit the screen: Changes never scrolls.
    arrange: (_, add) => {
      add("changes");
      add("comparison", { referencePanel: "changes", direction: "right" });
    },
    closedAt: { itemHistory: { neighbor: "changes", side: "bottom" } },
    heights: { itemHistory: 280 },
    fit: true,
    // 2: the three windows replaced the single Changes window.
    layoutVersion: 2,
  },
  mesa: {
    labelKey: "shell.tabMesa",
    helpKey: "help.wsMesa",
    icon: "mesa",
    windows: ["mesaProfiles", "mesaEditor", "mesaSuggestions", "mesaOrphans", "mesaAdvisory", "mesaImport", "mesaDiagnostics", "attention"],
    // Finding and editing a profile get the primary space: Profiles beside Profile
    // Editor, Suggested Profiles across the bottom, as accepted for MESA. Orphaned
    // Profiles and Advisory appear below it only while they have findings; Import
    // and Diagnostics open from the MESA Actions menu.
    arrange: (api, add) => {
      add("mesaProfiles");
      add("mesaEditor", { referencePanel: "mesaProfiles", direction: "right" });
      add("mesaSuggestions", { direction: "below" });
      size(api, "mesaProfiles", { width: Math.round(api.width * 0.45) });
    },
    conditional: {
      mesaOrphans: { neighbor: "mesaSuggestions", side: "bottom" },
      mesaAdvisory: { neighbor: "mesaSuggestions", side: "bottom" },
    },
    closedAt: {
      mesaImport: { neighbor: "mesaEditor", side: "bottom" },
      mesaDiagnostics: { neighbor: "mesaSuggestions", side: "bottom" },
    },
    heights: { mesaProfiles: 560, mesaEditor: 560, mesaSuggestions: 320, mesaOrphans: 240, mesaAdvisory: 260, mesaImport: 360, mesaDiagnostics: 360 },
    // 2: the MESA windows replaced the single MESA window.
    layoutVersion: 2,
  },
  logs: {
    labelKey: "workspace.logs",
    helpKey: "help.wsLogs",
    icon: "logs",
    // Log Settings opens the Logs and Notifications settings floating over the logs.
    windows: ["auditLog", "conversationLog", "logDetails", "settingsLogs", "attention"],
    // Two equal columns (user, 2026-10-27): the Audit Log over the Conversation Log on the left, Log Details on
    // the right; Help pinned below them across the full width, and Notifications beside it splitting at the
    // same border. They always fit the screen (user, 2026-10-26): Logs never scrolls. Beside the logs Log Details
    // keeps its column's height; arranged under a log instead, it sizes itself to the record chosen.
    arrange: (_, add) => {
      add("auditLog");
      add("logDetails", { referencePanel: "auditLog", direction: "right" });
      add("conversationLog", { referencePanel: "auditLog", direction: "below" });
    },
    heights: { auditLog: 500, conversationLog: 500 },
    closedAt: { settingsLogs: { neighbor: "", side: "bottom", host: "float" } },
    toolWindows: ["settingsLogs"],
    // Notifications opens to the right of Help, at its height (user, 2026-10-26).
    fit: true,
  },
  my: {
    labelKey: "workspace.myWorkspace",
    helpKey: "help.wsMy",
    icon: "star",
    // The windows the user added; the shell sets them (see `myWorkspaceWindows`).
    windows: ["attention"],
    // The shell lays out the added windows in the order they were added.
    arrange: () => {},
  },
  settings: {
    labelKey: "shell.tabSettings",
    helpKey: "help.wsSettings",
    icon: "settings",
    windows: ["settingsProviders", "settingsChat", "settingsHome", "settingsSafety", "settingsLogs", "settingsGeneral", "attention"],
    // AI Providers over Home Assistant on the left, Agent Chat over Safety on the right;
    // Logs and Notifications and General along the bottom, folded, as accepted for Settings.
    arrange: (api, add) => {
      add("settingsProviders");
      add("settingsChat", { referencePanel: "settingsProviders", direction: "right" });
      add("settingsHome", { referencePanel: "settingsProviders", direction: "below" });
      add("settingsSafety", { referencePanel: "settingsChat", direction: "below" });
      add("settingsLogs", { direction: "below" });
      add("settingsGeneral", { referencePanel: "settingsLogs", direction: "below" });
      size(api, "settingsProviders", { width: Math.round(api.width * 0.55) });
    },
    folded: ["settingsLogs", "settingsGeneral"],
    heights: { settingsProviders: 600, settingsChat: 600, settingsHome: 680, settingsSafety: 680, settingsLogs: 640, settingsGeneral: 520 },
    // 2: the Settings windows replaced the single Settings window.
    layoutVersion: 2,
  },
};

/**
 * Windows every workspace offers besides its own; desktop only for now. Help
 * starts pinned along the bottom and follows Show Help Bar; Notifications
 * starts closed until reopened from Workspace Options.
 */
export const SHARED_WINDOWS: readonly WindowId[] = [HELP_WINDOW, "notifications"];
/** In every workspace Notifications opens to the right of Help, sharing its row. */
export const SHARED_PAIRS: Readonly<Record<string, string>> = { notifications: "help" };
const SHARED_HEIGHTS: Partial<Record<WindowId, number>> = { help: MIN_WINDOW_HEIGHT, notifications: 240 };

export const WORKSPACES: Record<WorkspaceId, WorkspaceDef> = Object.fromEntries(
  Object.entries(OWN_WINDOWS).map(([id, def]) => [id, {
    ...def,
    windows: [...def.windows, ...SHARED_WINDOWS],
    arrange: (api: DockviewApi, add: Add) => {
      def.arrange(api, add);
      add(HELP_WINDOW, { direction: "below" });
    },
    heights: { ...SHARED_HEIGHTS, ...def.heights },
    pinned: { ...def.pinned, bottom: [...(def.pinned?.bottom ?? []), HELP_WINDOW] },
  }]),
) as Record<WorkspaceId, WorkspaceDef>;

export { WorkspaceVisibleContext };

/**
 * The workspace each window belongs to; My Workspace shows a second copy of
 * it, sharing its selection through the workspace's shell-level state. Agent
 * Chat belongs to no workspace: it docks only in My Workspace.
 */
export const SOURCE_OF: Partial<Record<WindowId, WorkspaceId>> = Object.fromEntries(
  (Object.entries(OWN_WINDOWS) as [WorkspaceId, WorkspaceDef][])
    .filter(([ws]) => ws !== "my")
    .flatMap(([ws, def]) => def.windows.filter((w) => w !== ATTENTION_WINDOW).map((w) => [w, ws])),
);

/** Whether a window can be added to My Workspace from its window menu. */
export const canAddToMy = (id: string): boolean => id in SOURCE_OF;

/** Comfortable heights for windows opened in My Workspace: each one's own, else enough for a list. */
const MY_HEIGHTS: Partial<Record<WindowId, number>> = {
  ...Object.fromEntries(Object.keys(SOURCE_OF).map((id) => [id, 420])),
  ...Object.assign({}, ...Object.values(OWN_WINDOWS).map((def) => def.heights ?? {})),
  [CHAT_WINDOW]: 560,
};

/** Conditional windows in My Workspace open along the bottom once they are allowed. */
const MY_CONDITIONAL: Partial<Record<WindowId, Slot>> = Object.fromEntries(
  Object.values(OWN_WINDOWS).flatMap((def) => Object.keys(def.conditional ?? {})).map((id) => [id, { neighbor: "", side: "bottom" as const }]),
);

/** My Workspace's controller settings for the windows the user added, in the order added. */
export function myWorkspaceWindows(members: readonly string[]): string[] {
  return [...members.filter((id) => canAddToMy(id) || id === CHAT_WINDOW), ...WORKSPACES.my.windows];
}

/** My Workspace's default arrangement: its windows one below another in the order they were added, then Help. */
export function arrangeMy(members: () => readonly string[]): WorkspaceDef["arrange"] {
  return (api, add) => {
    members().forEach((id, i) => add(id, i ? { direction: "below" } : undefined));
    WORKSPACES.my.arrange(api, add);
  };
}

export const MY_EXTRAS = { heights: MY_HEIGHTS, conditional: MY_CONDITIONAL, collect: (id: string) => canAddToMy(id) || id === CHAT_WINDOW };

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
    <TableSettingsContext.Provider value={controller?.tables ?? null}>
      <div className="phx-window-body" data-phx-window={id} hidden={folded}>
        <h2 className="sr-only">{windowTitle(id)}</h2>
        <ErrorBoundary>{children}</ErrorBoundary>
      </div>
    </TableSettingsContext.Provider>
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
    if (!wizard || !controller || !panel || panel.group.api.location.type !== "grid") return;
    controller.maximize(panel.group);
    return () => { if (panel.api.isMaximized()) controller.restoreMaximized(panel.group); };
  }, [wizard, controller]);
  return (
    <ErrorBoundary key={view.name}>
      {wizard ? <OnboardingWizard onCancel={d.goBack} onFinish={d.openDetail} firstRun={!d.loadingAccessKeys && d.accessKeys.length === 0} /> : (
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

function AuditLogContent() { return <AuditView accessKeys={usePanelData().accessKeys} />; }
function ConversationLogContent() { return <ConversationsView accessKeys={usePanelData().accessKeys} />; }

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
  approvals: ApprovalsListWindow,
  review: ReviewWindow,
  requestDetails: RequestDetailsWindow,
  changes: ChangesListWindow,
  comparison: ComparisonWindow,
  itemHistory: ItemHistoryWindow,
  mesaProfiles: MesaProfilesWindow,
  mesaEditor: ProfileEditorWindow,
  mesaSuggestions: SuggestedProfilesWindow,
  mesaOrphans: OrphanedProfilesWindow,
  mesaAdvisory: AdvisoryWindow,
  mesaImport: MesaImportWindow,
  mesaDiagnostics: DiagnosticsWindow,
  auditLog: AuditLogContent,
  conversationLog: ConversationLogContent,
  settingsProviders: AiProvidersWindow,
  settingsChat: AgentChatSettingsWindow,
  settingsHome: HomeAssistantWindow,
  settingsSafety: SafetyWindow,
  settingsLogs: LogsSettingsWindow,
  settingsGeneral: GeneralSettingsWindow,
  logDetails: LogDetailsWindowContent,
  agentChat: ChatDockWindow,
  attention: AttentionContent,
  notifications: NotificationsWindow,
  help: HelpWindow,
};

/** A window's content, rendered once per workspace and moved between hosts with the window. */
export function renderWindow(id: string): ReactNode {
  const Content = WINDOW_CONTENT[id as WindowId];
  return Content ? <WindowBody id={id}><Content /></WindowBody> : null;
}

/** Where Dockview shows a window: the window's content element is placed inside it. */
function WindowSlot(props: IDockviewPanelProps) {
  const controller = useContext(ControllerContext);
  const ref = useRef<HTMLDivElement | null>(null);
  useLayoutEffect(() => {
    const slot = ref.current;
    if (!controller || !slot) return;
    const content = controller.contentElement(props.api.id);
    slot.appendChild(content);
    return () => { if (content.parentElement === slot) slot.removeChild(content); };
  }, [controller, props.api.id]);
  return <div ref={ref} className="phx-window-slot" />;
}

/** Dockview panel components, one per window id; stable for the life of the bundle. */
export const DOCK_COMPONENTS: Record<string, FunctionComponent<IDockviewPanelProps>> = Object.fromEntries(
  (Object.keys(WINDOW_CONTENT) as WindowId[]).map((id) => [id, WindowSlot]),
);

/** Shown when every window in a workspace is closed: reopen one, or restore the arrangement. */
export function EmptyWorkspace(_props: IWatermarkPanelProps) {
  const controller = useContext(ControllerContext);
  useSyncExternalStore(controller?.subscribe ?? (() => () => {}), controller?.getVersion ?? (() => 0));
  if (!controller) return null;
  const mine = controller.id === "my";
  const toolWindows: readonly string[] = WORKSPACES[controller.id as WorkspaceId]?.toolWindows ?? [];
  const closed = controller.closedWindows().filter((id) => !toolWindows.includes(id));
  return (
    <div className="phx-workspace-empty">
      {/* My Workspace without any added window explains how to add one. */}
      <p>{t(mine && !closed.some((id) => canAddToMy(id) || id === CHAT_WINDOW) ? "workspace.myEmpty" : "workspace.empty")}</p>
      <div className="phx-workspace-empty-actions">
        {closed.map((id) => (
          <button key={id} type="button" className="btn btn-outline btn-sm" onClick={() => controller.reopen(id)}>
            {t("workspace.reopen", { window: windowTitle(id) })}
          </button>
        ))}
        {!mine && (
          <button type="button" className="btn btn-outline btn-sm" onClick={() => controller.restoreOriginal()}>
            {t("workspace.restoreOriginal")}
          </button>
        )}
      </div>
    </div>
  );
}
