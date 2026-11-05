/**
 * The workspaces, their windows and default arrangements. Today's screens are
 * the windows: each keeps its own behavior, data loading and safety checks.
 */

import { useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type FunctionComponent, type ReactNode } from "react";
import type { AddPanelOptions, DockviewApi, IDockviewPanelProps, IWatermarkPanelProps } from "dockview-react";
import { AttentionList } from "../components/AttentionPanel";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { AccessKeyListView, ArchivedAccessKeysWindow } from "../views/AccessKeyList";
import {
  AccessKeyNotices, CapabilitiesWindow, KeySettingsWindow, PermissionSummaryWindow, PermissionsWindow, PersonaWindow, PresetsWindow,
  useAccessKeyDetail, useKeyActionsTool,
} from "../views/AccessKeyDetail";
import { ApprovalsListWindow, RequestDetailsWindow, ReviewWindow } from "../views/ApprovalsView";
import { AuditView } from "../views/AuditView";
import { ChangesListWindow, ComparisonWindow, ItemHistoryWindow } from "../views/ChangesView";
import { ConversationsView } from "../views/ConversationsView";
import { AdvisoryWindow, MesaProfilesWindow, ProfileEditorWindow, SuggestedProfilesWindow } from "../views/MesaWindows";
import type { MesaFindings } from "./mesa";
import { SETTINGS_WINDOW_TITLE_KEYS } from "./settingsIndex";
import { OnboardingWizard } from "../views/OnboardingWizard";
import { AgentChatSettingsWindow, AiProvidersWindow, ChatRoutingStatisticsWindow, GeneralSettingsWindow, HomeAssistantWindow, LogsSettingsWindow, SafetyWindow, VoiceRoutingStatisticsWindow } from "../views/SettingsWindows";
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
import { AuditFocusContext, LogDetailsContent } from "./logDetails";
import { TableSettingsContext } from "../ui/table/settings";
import { NotificationsWindow } from "./notifications";
import { HELP_WINDOW, HelpWindow } from "./help";
// A cycle (Toolbar reads WORKSPACES), safe because neither module uses the other at load.
import { WorkspaceActionsContext } from "./Toolbar";
import { MIN_WINDOW_HEIGHT, TITLE_BAR_HEIGHT } from "./dock/fold";
import { DisplayModeContext } from "../ui/ActionButton";

export const WINDOW_TITLE_KEYS = {
  accessKeys: "shell.tabAccessKeys",
  archivedAccessKeys: "accessKeys.archivedTitle",
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
  mesaAdvisory: "workspace.advisory",
  auditLog: "shell.tabAudit",
  conversationLog: "conversations.title",
  logDetails: "workspace.logDetails",
  ...SETTINGS_WINDOW_TITLE_KEYS,
  chatRoutingStatistics: "routingStats.chatWindow",
  voiceRoutingStatistics: "routingStats.voiceWindow",
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

/** The selected access key's windows, hidden in place while no key is chosen (user, 2026-10-28). */
export const KEY_WINDOWS: readonly WindowId[] = ["persona", "capabilities", "keySettings", "presets", "permissions", "permissionSummary"];

/** The revoked and expired keys: closed until Show Archived opens it (user, 2026-10-28). */
export const ARCHIVED_WINDOW = "archivedAccessKeys";

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
  /** Docked windows are as tall as their content and never scroll inside; the workspace scrolls instead. */
  contentHeights?: boolean;
  /** Windows as tall as their content although the rest of the workspace is not. */
  contentWindows?: readonly WindowId[];
  /** The most of the visible height a window following its content takes, as a fraction; beyond it the window scrolls inside. */
  contentMax?: Partial<Record<WindowId, number>>;
  /** Windows that always take their whole `contentMax` share, however little they hold. */
  contentFill?: readonly WindowId[];
  /** The width a window opens at when it floats for the first time, within the workspace's own. */
  floatWidths?: Partial<Record<WindowId, number>>;
  /** Windows that open beside a partner in its pinned band (see WorkspaceExtras.pairs). */
  pairs?: Readonly<Record<string, string>>;
}

/**
 * Whether a conditional window is allowed now; null while unknown. Presets
 * follows its setting; Advisory appears only while MESA has a finding, of any
 * kind, that is not dismissed.
 */
export function windowAvailable(id: string, settings: GlobalSettings | null, mesa: MesaFindings | null = null, showArchived = false): boolean | null {
  if (id === "presets") return settings ? !!settings.access_key_presets_enabled : null;
  if (id === ARCHIVED_WINDOW) return showArchived;
  if (id === "mesaAdvisory") return mesa ? mesa.advisory : null;
  return true;
}

const size = (api: DockviewApi, id: string, dims: { width?: number; height?: number }) => api.getPanel(id)?.group.api.setSize(dims);

/**
 * Give a window its share of the workspace's width. A workspace is arranged
 * before it is shown, when the grid can still measure zero, and the share is
 * applied again at the next frame so the columns are not left equal.
 */
const share = (api: DockviewApi, id: string, fraction: number) => {
  const apply = () => { if (api.width > 0) size(api, id, { width: Math.round(api.width * fraction) }); };
  apply();
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(apply);
};

const OWN_WINDOWS: Record<WorkspaceId, WorkspaceDef> = {
  accessKeys: {
    labelKey: "shell.tabAccessKeys",
    helpKey: "help.wsAccessKeys",
    icon: "accessKeys",
    windows: ["accessKeys", "archivedAccessKeys", "persona", "capabilities", "keySettings", "presets", "permissions", "permissionSummary", "attention"],
    // Two equal columns (user, 2026-10-31): the key list over the selected key's
    // Persona, Presets, Capabilities and Key Settings on the left, its Permissions
    // Tree over the Permissions Summary on the right. Every window is as tall as its
    // content and none starts folded: the workspace scrolls.
    arrange: (api, add) => {
      add("accessKeys");
      add("permissions", { referencePanel: "accessKeys", direction: "right" });
      add("persona", { referencePanel: "accessKeys", direction: "below" });
      add("capabilities", { referencePanel: "persona", direction: "below" });
      add("keySettings", { referencePanel: "capabilities", direction: "below" });
      add("permissionSummary", { referencePanel: "permissions", direction: "below" });
      share(api, "accessKeys", 0.5);
    },
    conditional: { presets: { neighbor: "persona", side: "bottom" } },
    // Archived Access Keys floats over the workspace from Show Archived, wide enough for
    // its Actions column, and closes again with it.
    closedAt: { archivedAccessKeys: { neighbor: "accessKeys", side: "bottom", host: "float" } },
    floatWidths: { archivedAccessKeys: 960 },
    toolWindows: [ARCHIVED_WINDOW as WindowId],
    // Heights until the content is measured.
    heights: { accessKeys: 300, archivedAccessKeys: 300, persona: 420, capabilities: 600, keySettings: 420, presets: 320, permissions: 760, permissionSummary: 320 },
    contentHeights: true,
    // The key lists are as tall as their rows up to half the visible height, and scroll inside beyond it.
    contentMax: { accessKeys: 1 / 2, archivedAccessKeys: 1 / 2 },
    // 3: two columns from the top, with Archived Access Keys floating.
    layoutVersion: 3,
  },
  approvals: {
    labelKey: "shell.tabApprovals",
    helpKey: "help.wsApprovals",
    icon: "approvals",
    windows: ["approvals", "review", "requestDetails", "attention"],
    // The list beside Review, with Request Details under it from the start (user, 2026-10-29);
    // Workspace Options reopens it there once closed.
    arrange: (api, add) => {
      add("approvals");
      add("review", { referencePanel: "approvals", direction: "right" });
      add("requestDetails", { referencePanel: "review", direction: "below" });
      share(api, "approvals", 0.34);
    },
    // Until a request is chosen there is nothing to measure, so the two windows start
    // at these heights and Request Details takes the rest of the column.
    heights: { review: 420, requestDetails: 320 },
    // The windows always fit the screen (user, 2026-10-29): Approvals never scrolls, so the
    // list scrolls inside its own window and keeps the chosen request in view.
    fit: true,
    // Review is as tall as the request it shows and Request Details takes the rest of
    // the column, rather than the two splitting it evenly (user, 2026-10-29). Only Review
    // asks for a height, so when the column is too short Request Details gives way first.
    contentWindows: ["review"],
    // 3: Request Details opens with the workspace, under a Review sized to its request.
    layoutVersion: 3,
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
    windows: ["mesaProfiles", "mesaEditor", "mesaSuggestions", "mesaAdvisory", "attention"],
    // Two columns of equal width, each the full height: Profiles with Suggested
    // Profiles under it, and Profile Editor beside them (user, 2026-10-30).
    // Importing is part of Profiles, and the findings windows float over the
    // workspace rather than taking space from either column.
    arrange: (api, add) => {
      add("mesaProfiles");
      add("mesaEditor", { referencePanel: "mesaProfiles", direction: "right" });
      add("mesaSuggestions", { referencePanel: "mesaProfiles", direction: "below" });
      share(api, "mesaProfiles", 0.5);
    },
    conditional: {
      mesaAdvisory: { neighbor: "mesaProfiles", side: "bottom", host: "float" },
    },
    heights: { mesaProfiles: 560, mesaEditor: 560, mesaSuggestions: 260 },
    // The windows always fit the screen; Suggested Profiles takes a third of its column
    // and Profiles the rest (user, 2026-11-05). Advisory floats at the height of its
    // findings, up to the workspace's own (user, 2026-10-30).
    fit: true,
    contentWindows: ["mesaSuggestions", "mesaAdvisory"],
    contentMax: { mesaSuggestions: 1 / 3, mesaAdvisory: 1 },
    contentFill: ["mesaSuggestions"],
    // 4: one floating Advisory window holds every finding; Orphaned Profiles and Diagnostics are gone.
    // 5: Advisory drops the width it saved when floating windows opened without a maximum.
    layoutVersion: 5,
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
    windows: ["settingsProviders", "settingsChat", "settingsHome", "settingsSafety", "settingsLogs", "settingsGeneral", "chatRoutingStatistics", "voiceRoutingStatistics", "attention"],
    // Three equal columns (user, 2026-10-31): AI Providers alone on the left; Home Assistant over
    // Logs and Notifications in the middle; Agent Chat over Safety over General on the right.
    // Every window is as tall as its content and the workspace scrolls (user, 2026-10-31).
    arrange: (api, add) => {
      add("settingsProviders");
      add("settingsHome", { referencePanel: "settingsProviders", direction: "right" });
      add("settingsChat", { referencePanel: "settingsHome", direction: "right" });
      add("settingsLogs", { referencePanel: "settingsHome", direction: "below" });
      add("settingsSafety", { referencePanel: "settingsChat", direction: "below" });
      add("settingsGeneral", { referencePanel: "settingsSafety", direction: "below" });
      share(api, "settingsProviders", 1 / 3);
      share(api, "settingsHome", 1 / 3);
    },
    // Heights until the content is measured.
    heights: { settingsProviders: 600, settingsChat: 600, settingsHome: 680, settingsSafety: 680, settingsLogs: 640, settingsGeneral: 520, chatRoutingStatistics: 360, voiceRoutingStatistics: 360 },
    contentHeights: true,
    // Show Routing Statistics, in Agent Chat and on the Voice Agent tab, floats that surface's
    // counts over the workspace at their own height, and closes them again (user, 2026-10-31).
    closedAt: {
      chatRoutingStatistics: { neighbor: "settingsChat", side: "bottom", host: "float" },
      voiceRoutingStatistics: { neighbor: "settingsHome", side: "bottom", host: "float" },
    },
    // Wide enough that each count and its name share a line.
    floatWidths: { chatRoutingStatistics: 520, voiceRoutingStatistics: 520 },
    contentMax: { chatRoutingStatistics: 1, voiceRoutingStatistics: 1 },
    toolWindows: ["chatRoutingStatistics", "voiceRoutingStatistics"],
    // 4: three columns, nothing folded, replaced the two columns with folded windows along the bottom.
    layoutVersion: 4,
  },
};

/**
 * Windows every workspace offers besides its own; desktop only for now. Help
 * starts pinned along the bottom and follows the Help menu's Quick Help; Notifications
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

/**
 * My Workspace's settings for the windows the user added. Its default
 * arrangement holds only Help: the added windows then take their places in
 * the order added (see `nextPlace`). It fits its windows to the screen until
 * the user lets it scroll.
 */
export const MY_EXTRAS = { heights: MY_HEIGHTS, conditional: MY_CONDITIONAL, collect: (id: string) => canAddToMy(id) || id === CHAT_WINDOW, scrollable: true };

const noSubscribe = () => () => {};
const noVersion = () => 0;

const px = (value: string): number => {
  const n = parseFloat(value);
  return Number.isFinite(n) ? n : 0;
};

/**
 * The height an element's content needs, ignoring any height forced on it.
 *
 * A box that is as tall as its own content is measured directly. A box that
 * was stretched to fill its window, or that scrolls, reports back the height
 * it was given rather than the height it wants, so its children are measured
 * instead: a window sized from that number would chase itself up and down for
 * ever (user, 2026-10-30, a list that never settled).
 */
function neededHeight(el: HTMLElement): number {
  const style = getComputedStyle(el);
  const scrolls = style.overflowY === "auto" || style.overflowY === "scroll";
  const column = style.display.endsWith("flex") && style.flexDirection.startsWith("column");
  if (!scrolls && !column) return el.offsetHeight;
  let inner = 0;
  let placed = 0;
  for (const child of Array.from(el.children) as HTMLElement[]) {
    // Absent, absolutely placed, or the heading only screen readers hear: no room of its own.
    if (!child.offsetHeight && !child.offsetWidth) continue;
    const box = getComputedStyle(child);
    if (box.position === "absolute" || box.position === "fixed") continue;
    inner += neededHeight(child) + px(box.marginTop) + px(box.marginBottom);
    placed++;
  }
  // A flex column's gaps take room too: Review asked for 24 pixels too few (user, 2026-10-31).
  const gaps = column && placed > 1 ? px(style.rowGap) * (placed - 1) : 0;
  return px(style.paddingTop) + px(style.paddingBottom) + px(style.borderTopWidth) + px(style.borderBottomWidth) + inner + gaps;
}

/** The height a window's content needs, frame included. */
function contentHeight(el: HTMLElement): number {
  // The heading screen readers announce the window by takes no room.
  const content = (Array.from(el.children) as HTMLElement[]).filter((child) => !child.classList.contains("sr-only"));
  // A window with nothing to show yet keeps the height the arrangement gave it,
  // rather than shrinking to the line that says so.
  if (content.every((child) => child.classList.contains("phx-window-empty"))) return 0;
  const frame = getComputedStyle(el);
  let height = px(frame.paddingTop) + px(frame.paddingBottom);
  for (const child of content) {
    const box = getComputedStyle(child);
    height += neededHeight(child) + px(box.marginTop) + px(box.marginBottom);
  }
  return height;
}

/**
 * A window's content frame: one error boundary per window, hidden but mounted
 * while folded. A window set to Show Icons and Text shows its buttons'
 * labels whatever the toolbar's display mode.
 */
export function WindowBody({ id, children }: { id: string; children: ReactNode }) {
  const controller = useContext(ControllerContext);
  const workspaceVisible = useContext(WorkspaceVisibleContext);
  const mode = useContext(DisplayModeContext);
  useSyncExternalStore(controller?.folds.subscribe ?? noSubscribe, controller?.folds.getVersion ?? noVersion);
  useSyncExternalStore(controller?.display.subscribe ?? noSubscribe, controller?.display.getVersion ?? noVersion);
  const folded = controller?.folds.isFolded(id) ?? false;
  const labels = controller?.display.hasLabels(id) ?? false;
  const place = useMemo(() => (controller ? { workspace: controller.id, window: id } : null), [controller, id]);
  // While this window follows its content, report the height that content needs.
  const body = useRef<HTMLDivElement | null>(null);
  const followsContent = !!controller && controller.followsContent(id);
  // Reset This Workspace puts every disclosure in the window back to how it opens by default:
  // closed, or open where it says `data-default-open` (user, 2026-10-31). A disclosure that
  // keeps its own state hears the change through its toggle event. Agent Chat's conversation is not
  // part of the arrangement, so its messages keep theirs.
  useEffect(() => controller?.onReset(() => {
    if (id === CHAT_WINDOW) return;
    for (const d of Array.from(body.current?.querySelectorAll("details") ?? [])) {
      const open = d.hasAttribute("data-default-open");
      if (d.open !== open) d.open = open;
    }
  }), [controller, id]);
  useLayoutEffect(() => {
    const el = body.current;
    if (!followsContent || !controller || !el || typeof ResizeObserver === "undefined") return undefined;
    // The title bar and the window's frame come on top of the content.
    // A folded window, or one in a hidden workspace, measures nothing.
    const observer = new ResizeObserver(() => {
      for (const child of Array.from(el.children)) observer.observe(child);
      const needed = el.offsetHeight ? contentHeight(el) : 0;
      if (needed > 0) controller.contentResized(id, TITLE_BAR_HEIGHT + needed + 2);
    });
    observer.observe(el);
    for (const child of Array.from(el.children)) observer.observe(child);
    return () => observer.disconnect();
  }, [followsContent, controller, id]);
  return (
    <WindowPlaceContext.Provider value={place}>
    <WindowVisibleContext.Provider value={workspaceVisible && !folded}>
    <TableSettingsContext.Provider value={controller?.tables ?? null}>
    <DisplayModeContext.Provider value={labels ? "icon-text" : mode}>
      {/* Dockview already makes each window a region named by its title; a second one inside it
          listed every window twice and read its name three times. */}
      <div ref={body} className="phx-window-body" data-phx-window={id} data-labels={labels ? "" : undefined} hidden={folded}>
        <h2 className="sr-only">{windowTitle(id)}</h2>
        <ErrorBoundary>{children}</ErrorBoundary>
      </div>
    </DisplayModeContext.Provider>
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
  // An edit to the selected key (Enable Pass-Through, rename, rotate) shows in the list at once,
  // not at its next quiet update (user, 2026-10-28).
  const edited = detail && !detail.stale ? detail.accessKey?.updated_at : undefined;
  const updateList = d.updateAccessKeys;
  useEffect(() => { if (edited) void updateList(); }, [edited, updateList]);
  // A double-click on a name chooses the key and renames it once its record is here (user, 2026-10-31).
  const [renameNext, setRenameNext] = useState<string | null>(null);
  useEffect(() => {
    if (renameNext && detail && !detail.stale && detail.accessKey && detail.accessKeyId === renameNext) {
      detail.setRenaming(true);
      setRenameNext(null);
    }
  }, [renameNext, detail]);
  // Setting up an agent needs room: the key list window fills the workspace until the wizard ends.
  useEffect(() => {
    const panel = controller?.api?.getPanel("accessKeys");
    if (!wizard || !controller || !panel || panel.group.api.location.type !== "grid") return;
    controller.maximize(panel.group);
    return () => { if (panel.api.isMaximized()) controller.restoreMaximized(panel.group); };
  }, [wizard, controller]);
  return (
    <ErrorBoundary key={view.name}>
      {wizard ? <OnboardingWizard onCancel={d.goBack} onFinish={d.openDetail} /> : (
        <AccessKeyListView
          accessKeys={d.accessKeys}
          loading={d.loadingAccessKeys}
          error={d.accessKeysError}
          onRefresh={d.refreshAccessKeys}
          onUpdate={d.updateAccessKeys}
          showArchived={d.showArchived}
          onShowArchived={d.setShowArchived}
          onOpenDetail={d.openDetail}
          onLaunchWizard={d.openWizard}
          showCreate={d.showCreate}
          onOpenCreate={() => d.setShowCreate(true)}
          onCloseCreate={() => d.setShowCreate(false)}
          onOpenSettings={d.goToSettings}
          selectedId={view.name === "detail" ? view.accessKeyId : null}
          renamingId={detail?.renaming && !detail.stale ? detail.accessKeyId : null}
          onRenamed={(updated) => { detail?.applyUpdate(updated); d.refreshAccessKeys(); }}
          onRenameEnd={() => detail?.setRenaming(false)}
          onStartRename={setRenameNext}
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
  const focus = useContext(AuditFocusContext);
  return (
    <div className="phx-attention phx-attention-window">
      <AttentionList state={usePanelData().attention} onShowRequest={focus ? (id) => focus.ask(id) : undefined} />
    </div>
  );
}

export const WINDOW_CONTENT: Record<WindowId, FunctionComponent> = {
  accessKeys: AccessKeysContent,
  archivedAccessKeys: ArchivedAccessKeysWindow,
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
  mesaAdvisory: AdvisoryWindow,
  auditLog: AuditLogContent,
  conversationLog: ConversationLogContent,
  settingsProviders: AiProvidersWindow,
  settingsChat: AgentChatSettingsWindow,
  settingsHome: HomeAssistantWindow,
  settingsSafety: SafetyWindow,
  settingsLogs: LogsSettingsWindow,
  settingsGeneral: GeneralSettingsWindow,
  chatRoutingStatistics: ChatRoutingStatisticsWindow,
  voiceRoutingStatistics: VoiceRoutingStatisticsWindow,
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
  const actions = useContext(WorkspaceActionsContext);
  useSyncExternalStore(controller?.subscribe ?? (() => () => {}), controller?.getVersion ?? (() => 0));
  if (!controller) return null;
  const mine = controller.id === "my";
  const toolWindows: readonly string[] = WORKSPACES[controller.id as WorkspaceId]?.toolWindows ?? [];
  // The workspace's own windows, as Workspace Options offers them: Quick Help follows the Help menu,
  // and Notifications has its own item there.
  const closed = controller.closedWindows().filter((id) => !toolWindows.includes(id) && !SHARED_WINDOWS.includes(id as WindowId));
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
          <button type="button" className="btn btn-outline btn-sm" onClick={() => (actions ? actions.restoreOriginal(controller) : controller.restoreOriginal())}>
            {t("workspace.restoreOriginal")}
          </button>
        )}
      </div>
    </div>
  );
}
