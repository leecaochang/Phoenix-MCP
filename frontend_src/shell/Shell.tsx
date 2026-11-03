/**
 * The workspace shell: toolbar, one window workspace per destination, and the
 * panel-only Agent Chat window. Desktop and phone layouts are chosen from the
 * panel's own width and remembered separately: in this browser at once, and in
 * Home Assistant's per-user storage shortly after (see `sync.ts`), along with
 * each workspace's My Default.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AgentCliWindow } from "../components/AgentCliWindow";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { resolveLanguage, t } from "../i18n";
import { ContextMenuHost, MenuRootContext, useContextMenu, type MenuEntry } from "../ui/Menu";
import { Notifications, NotificationsContext, Toasts } from "../ui/notify";
import { DisplayModeContext } from "../ui/ActionButton";
import { WorkspaceController, layoutMembers } from "./dock/controller";
import { useAreaKeys } from "./areaFocus";
import { WorkspaceDock } from "./dock/WorkspaceDock";
import { usePanelData } from "./PanelData";
import { PhoneStack, closedPhoneWindows, phoneDefaults } from "./PhoneStack";
import { withMember, type PhoneLayout } from "./phoneLayout";
import { DEFAULT_HELP, DEFAULT_TOOLBAR, helpOpen, readRecord, withHelpIn, writeRecord, WORKSPACE_IDS, type Env, type GuideState, GUIDE_CLOSED, type HelpPrefs, type ShellRecord, type ToolbarState, type WelcomeStep, type WorkspaceId } from "./storage";
import { WelcomeContext, type Welcome } from "./guideWelcome";
import { RemoteRecord, shouldAdopt, writerId, type Section, type UserDataConnection } from "./sync";
import { Toolbar, WorkspaceActionsContext, workspaceMenu, type WorkspaceActions } from "./Toolbar";
import { ToolRegistry, ToolRegistryContext } from "./tools";
import { AuditFocus, AuditFocusContext, LogSelection, LogSelectionContext } from "./logDetails";
import { ApprovalsProvider } from "./approvals";
import { ChangesProvider } from "./changes";
import { HELP_WINDOW, HelpContext, HelpTracker, helpFor } from "./help";
import { attachToggleNames } from "../ui/toggleNames";
import { GuideTargetTracker } from "./guideTarget";
import { GuideTargetContext, GuideWindow, PhoneGuide, WizardGuideContext, useWizardGuide } from "./guide";
import { Reconnecting } from "./Reconnecting";
import { MesaProvider, type MesaFindings } from "./mesa";
import { SettingsProvider, settingsGuideAnchor } from "./settings";
import type { SettingsWindowId } from "./settingsIndex";
import {
  ARCHIVED_WINDOW, ATTENTION_WINDOW, DOCK_COMPONENTS, EmptyWorkspace, KEY_WINDOWS, MY_EXTRAS, SHARED_PAIRS, SOURCE_OF, WORKSPACES, WorkspaceVisibleContext, canAddToMy, myWorkspaceWindows,
  renderWindow, windowAvailable, windowTitle, type WindowId,
} from "./workspaces";
import { CHAT_WINDOW, ChatDockContext, type ChatDock } from "./chatDock";
import { WindowMenuExtrasContext, type WindowMenuExtras } from "./dock/TitleBar";
import { visibleWindowEl } from "./visibility";
import { TITLE_BAR_HEIGHT } from "./dock/fold";
import { AccessKeyDetailProvider } from "../views/AccessKeyDetail";

/** Below this panel width the phone stack is used; the gap between the two bounds stops flicker. */
const PHONE_BELOW = 680;
const DESKTOP_ABOVE = 720;
/** The most of the visible height Log Details takes for an audit entry and for a conversation. */
const AUDIT_SHARE = 0.6;
const CONVERSATION_SHARE = 0.4;
/** How long Log Details keeps following its content's size after a record is chosen. */
const SIZE_FOLLOW_MS = 2000;
/** Arrangements are written this long after the last change. */
const SAVE_DELAY_MS = 1000;
const TRANSIENT = new Set([ATTENTION_WINDOW]);

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

/** Every section a record holds, to send a whole record to Home Assistant. */
function allSections(record: ShellRecord): Section[] {
  return [
    "toolbar", "help", "guide", "lastWorkspace", "myWindows",
    ...Object.keys(record.workspaces).map((id) => `ws:${id}` as Section),
    ...Object.keys(record.myDefaults ?? {}).map((id) => `my:${id}` as Section),
    ...Object.keys(record.phone ?? {}).map((id) => `phone:${id}` as Section),
    ...Object.keys(record.phoneDefaults ?? {}).map((id) => `phoneDefault:${id}` as Section),
  ];
}

/** A screen whose shorter side is below this, with touch only, is a phone. */
const PHONE_SHORT_SIDE = 600;

/**
 * A phone keeps the phone stack in both orientations, so turning it never swaps
 * the phone arrangement for the desktop one (user, 2026-11-02). A phone is a
 * touch-only screen with a phone-sized shorter side; a tablet is not, and a
 * narrowed tablet window gets the stack by width like any narrow window.
 */
export function isPhone(win: Window = window): boolean {
  const touchOnly = typeof win.matchMedia === "function" && win.matchMedia("(pointer: coarse) and (hover: none)").matches;
  const shortSide = Math.min(win.screen?.width ?? Infinity, win.screen?.height ?? Infinity);
  return touchOnly && shortSide > 0 && shortSide < PHONE_SHORT_SIDE;
}

function useEnvironment(root: React.RefObject<HTMLElement | null>): Env {
  const [env, setEnv] = useState<Env>(() => (isPhone() || window.innerWidth < (PHONE_BELOW + DESKTOP_ABOVE) / 2 ? "mobile" : "desktop"));
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const decide = (width: number) => setEnv((current) => {
      if (isPhone()) return "mobile";
      if (width <= 0) return current;
      if (width < PHONE_BELOW) return "mobile";
      if (width > DESKTOP_ABOVE) return "desktop";
      return current;
    });
    decide(el.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => decide(entries[0]?.contentRect.width ?? 0));
    observer.observe(el);
    return () => observer.disconnect();
  }, [root]);
  return env;
}

/** What Welcome's Point step counts as a button, and how long the pointer must rest on one. */
const WELCOME_BUTTONS = 'button, [role="button"], [role="menuitem"], [role="tab"], summary';
const WELCOME_POINT_MS = 600;

/** The workspace area; a right-click on its margin, outside any window, opens the workspace menu. */
function WorkspaceArea({ menu, children }: { menu: (() => MenuEntry[]) | null; children: React.ReactNode }) {
  const show = useContextMenu();
  return (
    <main className="phx-workspaces" onContextMenu={(e) => {
      if (menu && e.target === e.currentTarget) show?.(e, t("workspace.options"), menu());
    }}>
      {children}
    </main>
  );
}

export function Shell() {
  const d = usePanelData();
  const setShowArchived = d.setShowArchived;
  const deselectKey = d.deselectKey;
  const root = useRef<HTMLDivElement | null>(null);
  const [rootEl, setRootEl] = useState<HTMLDivElement | null>(null);
  // Stable, so React never detaches the root between renders: an inline callback left root.current
  // null while children's layout effects ran, and the toolbar could not keep itself inside the panel.
  const attachRoot = useCallback((el: HTMLDivElement | null) => { root.current = el; setRootEl(el); }, []);
  const env = useEnvironment(root);
  useAreaKeys(root);
  const userId = (d.hass as { user?: { id?: string } } | null)?.user?.id ?? null;

  // The record: this browser's copy paints first; Home Assistant's copy replaces it at
  // start-up when it is newer. A record written by a newer build reads as null: arrange
  // from defaults and never write over it.
  const recordKey = `${userId ?? ""}:${env}`;
  const [store, setStore] = useState(() => ({ key: recordKey, record: readRecord(userId, env), generation: 0 }));
  if (store.key !== recordKey) setStore({ key: recordKey, record: readRecord(userId, env), generation: store.generation + 1 });
  const record = store.record;
  const recordRef = useRef(record);
  recordRef.current = record;
  // The Quick Help setting and the workspaces where Quick Help was reopened or closed against it.
  const [help, setHelp] = useState<HelpPrefs>(() => record?.help ?? DEFAULT_HELP);
  useEffect(() => { setHelp(record?.help ?? DEFAULT_HELP); }, [record]);
  const helpRef = useRef(help);
  helpRef.current = help;
  // The Guide, one window over every workspace: open or not, where it was put, folded or not.
  const [guide, setGuide] = useState<GuideState>(() => record?.guide ?? GUIDE_CLOSED);
  useEffect(() => { setGuide(record?.guide ?? GUIDE_CLOSED); }, [record]);
  const guideRef = useRef(guide);
  guideRef.current = guide;
  // The start workspace was read before the panel's width was known, so possibly from the
  // other environment's record; and a Home Assistant record adopted later has its own. Each
  // record that arrives decides it until the user moves to another workspace.
  const startWorkspace = useRef<WorkspaceId | null>(d.workspace);
  useEffect(() => {
    const last = record?.lastWorkspace;
    if (startWorkspace.current === null || d.workspace !== startWorkspace.current) {
      startWorkspace.current = null;
      return;
    }
    if (last && last !== d.workspace && WORKSPACE_IDS.includes(last)) {
      startWorkspace.current = last;
      d.navigate(last);
    }
  }, [record]); // eslint-disable-line react-hooks/exhaustive-deps
  // The windows added to My Workspace, in the order added.
  const [myWindows, setMyWindows] = useState<string[]>(() => record?.myWindows ?? []);
  useEffect(() => { setMyWindows(record?.myWindows ?? []); }, [record]);
  const myWindowsRef = useRef(myWindows);
  myWindowsRef.current = myWindows;
  // Phones: each workspace's stack as the user arranged it, and the window maximized over it (never saved).
  const [phone, setPhone] = useState<Partial<Record<WorkspaceId, PhoneLayout>>>(() => record?.phone ?? {});
  useEffect(() => { setPhone(record?.phone ?? {}); }, [record]);
  const phoneRef = useRef(phone);
  phoneRef.current = phone;
  const [phoneMax, setPhoneMax] = useState<Partial<Record<WorkspaceId, string | null>>>({});
  /** Close Agent Chat, whichever window shows it; set once the chat state below is known. */
  const closeChatRef = useRef<() => void>(() => {});
  /** Take a window out of My Workspace; set once My Workspace's actions below are known. */
  const removeFromMyRef = useRef<(id: string) => void>(() => {});

  // The height the user gave Log Details for each kind of record, per workspace, and the kind it shows now:
  // an audit entry and a conversation each come back at their own size.
  const detailsHeights = useRef(new Map<string, number>());
  const detailsKind = useRef(new Map<string, string>());

  // A different user or environment, or an adopted record, starts from fresh controllers.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const controllers = useMemo(() => new Map<WorkspaceId, WorkspaceController>(), [store.generation]);
  const controllerFor = useCallback((id: WorkspaceId): WorkspaceController => {
    let controller = controllers.get(id);
    if (!controller) {
      const def = WORKSPACES[id];
      const extras = { transient: TRANSIENT, folded: def.folded, conditional: def.conditional, heights: def.heights, pinned: def.pinned, closedAt: def.closedAt, layoutVersion: def.layoutVersion, fit: def.fit, contentHeights: def.contentHeights, contentWindows: def.contentWindows, contentMax: { [ATTENTION_WINDOW]: 1 / 3, ...def.contentMax }, floatWidths: def.floatWidths, pairs: { ...SHARED_PAIRS, ...def.pairs } };
      // My Workspace holds the windows the user added, laid out in the order added.
      controller = id === "my"
        ? new WorkspaceController(id, myWorkspaceWindows(myWindowsRef.current), def.arrange, windowTitle,
          { ...extras, ...MY_EXTRAS, heights: { ...def.heights, ...MY_EXTRAS.heights } })
        : new WorkspaceController(id, def.windows, def.arrange, windowTitle, extras);
      controller.want(HELP_WINDOW, helpOpen(helpRef.current, id));
      // Closing or reopening Quick Help in one workspace overrides the Help menu's choice there only.
      // Closing Agent Chat's window in My Workspace closes Chat. Closing an added window there takes it out of
      // My Workspace (user, 2026-10-27): it comes back only by adding it again from its own workspace.
      controller.onUserSized = (windowId) => {
        const kind = detailsKind.current.get(id);
        // Only a height under or over a log is a choice; beside the logs Log Details just fills its column.
        const group = windowId === "logDetails" && kind && controller!.isStacked(windowId) ? controller!.groupOf(windowId) : null;
        if (group) detailsHeights.current.set(`${id}:${kind}`, Math.round(group.height));
      };
      controller.onWindowToggle = (windowId, open) => {
        if (windowId === HELP_WINDOW) setHelp((prev) => withHelpIn(prev, id, open));
        if (windowId === CHAT_WINDOW && !open) closeChatRef.current();
        // Closing the archived keys from their title bar turns Show Archived off, so the tool agrees with the window.
        if (windowId === ARCHIVED_WINDOW && !open) setShowArchived(false);
        if (id === "my" && !open && myWindowsRef.current.includes(windowId)) removeFromMyRef.current(windowId);
      };
      controllers.set(id, controller);
    }
    return controller;
  }, [controllers, setShowArchived]);

  // While no key is chosen Access Keys shows only its list, maximized (user, 2026-10-31); the key's windows
  // keep their places for the next one, and choosing a key returns the list to its own size.
  const keyChosen = d.accessKeyView.name === "detail";
  useEffect(() => {
    const keys = controllerFor("accessKeys");
    keys.setHiddenWindows(keyChosen ? [] : KEY_WINDOWS);
    keys.holdMaximized(keyChosen ? null : "accessKeys");
  }, [keyChosen, controllerFor]);

  const registry = useMemo(() => new ToolRegistry(), []);
  // Open (or unfold) a window and bring it into view: in My Workspace when it is shown there and holds it,
  // otherwise in the workspace it belongs to.
  const workspaceRef = useRef(d.workspace);
  workspaceRef.current = d.workspace;
  // On a phone, opening a window reopens and unfolds it in the stack, and a window maximized over it steps back.
  const phoneRevealRef = useRef<(ws: WorkspaceId, windowId: string) => void>(() => {});
  const revealIn = useCallback((source: WorkspaceId, windowId: string): WorkspaceId => {
    const here = workspaceRef.current === "my" && myWindowsRef.current.includes(windowId) ? "my" : source;
    if (env === "desktop") controllerFor(here).ensureOpen(windowId);
    else phoneRevealRef.current(here, windowId);
    requestAnimationFrame(() => visibleWindowEl(root.current, windowId)?.scrollIntoView?.({ block: "nearest" }));
    return here;
  }, [env, controllerFor]);
  // Selecting a log record shows it in Log Details, opening or unfolding that window first,
  // sized to the record (user, 2026-10-26): an audit entry gets the few rows it needs, a
  // conversation at most CONVERSATION_SHARE of the height. A window the user resized or moved
  // only ever gets smaller. Conversations load their detail after opening, so the size
  // follows the content for a moment.
  // Needs Attention asking for one request: show the Logs workspace with the Audit Log on it.
  const auditFocus = useMemo(() => new AuditFocus(), []);
  auditFocus.onFocus = () => { d.navigate("logs"); revealIn("logs", "auditLog"); };
  const logSelection = useMemo(() => new LogSelection(), []);
  const stopSizing = useRef<() => void>(() => {});
  const sizeLogDetails = useCallback((here: WorkspaceId) => {
    stopSizing.current();
    if (env !== "desktop") return;
    const controller = controllerFor(here);
    const kind = logSelection.get()?.kind;
    if (kind) detailsKind.current.set(here, kind);
    let frame = 0;
    const measure = () => {
      frame = 0;
      const body = visibleWindowEl(root.current, "logDetails");
      const content = body?.querySelector<HTMLElement>(".phx-log-details");
      // A record still loading is one short line: keep the size until it arrives, rather than shrink and grow back.
      if (!body || body.hidden || !content || content.querySelector("[data-loading]")) return;
      // Beside the logs, with no window above or below it, Log Details keeps the height of its column.
      if (!controller.isStacked("logDetails")) return;
      const natural = TITLE_BAR_HEIGHT + content.offsetHeight + (parseFloat(getComputedStyle(body).paddingBottom) || 0) + 2;
      const visible = controller.visibleHeight();
      const cap = visible > 0 ? Math.round(visible * (kind === "conversation" ? CONVERSATION_SHARE : AUDIT_SHARE)) : natural;
      // The cap is for the size Phoenix chooses. Once the user sized Log Details for this kind of record,
      // it returns to that size, shrinking only to fit shorter content.
      const chosen = kind ? detailsHeights.current.get(`${here}:${kind}`) : undefined;
      controller.sizeToContent("logDetails", Math.min(natural, chosen ?? cap), true);
    };
    const later = () => { if (!frame) frame = requestAnimationFrame(measure); };
    let observer: ResizeObserver | null = null;
    const start = requestAnimationFrame(() => requestAnimationFrame(() => {
      measure();
      const content = visibleWindowEl(root.current, "logDetails")?.querySelector(".phx-log-details");
      if (content && typeof ResizeObserver !== "undefined") {
        observer = new ResizeObserver(later);
        observer.observe(content);
      }
    }));
    const timer = window.setTimeout(() => stopSizing.current(), SIZE_FOLLOW_MS);
    stopSizing.current = () => {
      cancelAnimationFrame(start);
      cancelAnimationFrame(frame);
      window.clearTimeout(timer);
      observer?.disconnect();
      stopSizing.current = () => {};
    };
  }, [env, controllerFor, logSelection]);
  useEffect(() => () => stopSizing.current(), []);
  logSelection.onSelect = () => sizeLogDetails(revealIn("logs", "logDetails"));
  logSelection.onStep = () => sizeLogDetails(workspaceRef.current === "my" && myWindowsRef.current.includes("logDetails") ? "my" : "logs");

  // A request opened from a link brings Review into view.
  const revealReview = useCallback(() => revealIn("approvals", "review"), [revealIn]);

  // A jump to a permission (from Permission Summary) brings the Permissions tree into view.
  const revealPermissions = useCallback(() => {
    const here = workspaceRef.current === "my" && myWindowsRef.current.includes("permissions") ? "my" : "accessKeys";
    if (env === "desktop") controllerFor(here).ensureOpen("permissions");
    else phoneRevealRef.current(here, "permissions");
  }, [env, controllerFor]);

  // Find Setting and the kill-switch warning open a Settings window, unfolding it first.
  const revealSettings = useCallback((id: SettingsWindowId) => {
    if (env === "desktop") controllerFor("settings").ensureOpen(id);
    else phoneRevealRef.current("settings", id);
  }, [env, controllerFor]);
  const navigate = d.navigate;
  const showSettings = useCallback(() => navigate("settings"), [navigate]);

  // Toolbar placement, display mode and collapsed state, remembered with the arrangement.
  const [toolbar, setToolbar] = useState<ToolbarState>(() => record?.toolbar ?? DEFAULT_TOOLBAR);
  useEffect(() => { setToolbar(record?.toolbar ?? DEFAULT_TOOLBAR); }, [record]);
  const toolbarRef = useRef(toolbar);
  toolbarRef.current = toolbar;

  // Workspaces are created on first visit and kept, so their windows keep drafts and scroll.
  const [visited, setVisited] = useState<ReadonlySet<WorkspaceId>>(() => new Set([d.workspace]));
  useEffect(() => {
    setVisited((prev) => (prev.has(d.workspace) ? prev : new Set([...prev, d.workspace])));
  }, [d.workspace]);
  useEffect(() => { setVisited(new Set([d.workspace])); }, [controllers]); // eslint-disable-line react-hooks/exhaustive-deps

  // Save what changed: in this browser shortly after the last change, then in Home
  // Assistant. Only sections that really changed are written, so another computer's
  // changes to other sections survive.
  const dirty = useRef(new Set<Section>());
  const remote = useRef<RemoteRecord | null>(null);
  const saveTimer = useRef<number | undefined>(undefined);
  /** Store sections already updated in the record, here and in Home Assistant. */
  const commit = useCallback((sections: Section[], now = false) => {
    const rec = recordRef.current;
    if (!rec || !sections.length) return;
    rec.pending = !!remote.current?.writable;
    writeRecord(userId, env, rec);
    remote.current?.schedule(sections, now);
  }, [env, userId]);
  const flush = useCallback((now = false) => {
    window.clearTimeout(saveTimer.current);
    saveTimer.current = undefined;
    const rec = recordRef.current;
    if (!rec || !dirty.current.size) return;
    const changed: Section[] = [];
    for (const section of dirty.current) {
      if (section === "toolbar") {
        if (!same(rec.toolbar, toolbarRef.current)) { rec.toolbar = toolbarRef.current; changed.push(section); }
      } else if (section === "help") {
        if (!same(rec.help, helpRef.current)) { rec.help = helpRef.current; changed.push(section); }
      } else if (section === "guide") {
        if (!same(rec.guide ?? GUIDE_CLOSED, guideRef.current)) { rec.guide = guideRef.current; changed.push(section); }
      } else if (section === "lastWorkspace") {
        if (rec.lastWorkspace !== workspaceRef.current) { rec.lastWorkspace = workspaceRef.current; changed.push(section); }
      } else if (section === "myWindows") {
        if (!same(rec.myWindows ?? [], myWindowsRef.current)) { rec.myWindows = myWindowsRef.current; changed.push(section); }
      } else if (section.startsWith("phone:")) {
        const id = section.slice(6) as WorkspaceId;
        const layout = phoneRef.current[id];
        if (!same(layout, rec.phone?.[id])) {
          const next = { ...rec.phone };
          if (layout) next[id] = layout; else delete next[id];
          rec.phone = next;
          changed.push(section);
        }
      } else if (section.startsWith("ws:")) {
        const id = section.slice(3) as WorkspaceId;
        const controller = controllers.get(id);
        const layout = controller?.layout();
        if (layout && !same(layout, rec.workspaces[id])) { rec.workspaces[id] = layout; changed.push(section); }
      }
    }
    dirty.current.clear();
    commit(changed, now);
  }, [commit, controllers]);
  const markChanged = useCallback((sections: Section[], now = false) => {
    for (const section of sections) dirty.current.add(section);
    if (now) {
      flush(true);
      return;
    }
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => flush(), SAVE_DELAY_MS);
  }, [flush]);
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState !== "hidden") return;
      flush(true);
      void remote.current?.flush();
    };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", onHide);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", onHide);
      if (saveTimer.current !== undefined) flush(true);
    };
  }, [flush]);

  // Home Assistant's copy: adopt it at start-up when it is newer, otherwise send ours.
  useEffect(() => {
    const conn = (d.hass as { connection?: Partial<UserDataConnection> } | null)?.connection;
    if (!userId || !recordRef.current || typeof conn?.subscribeMessage !== "function" || typeof conn.sendMessagePromise !== "function") return;
    const startPending = !!recordRef.current.pending;
    const sync = new RemoteRecord(conn as UserDataConnection, env, writerId(), () => recordRef.current, (stored) => {
      const rec = recordRef.current;
      if (!rec) return;
      for (const key of Object.keys(rec)) delete (rec as unknown as Record<string, unknown>)[key];
      Object.assign(rec, stored, { pending: dirty.current.size > 0 || sync.hasPending() });
      writeRecord(userId, env, rec);
    });
    remote.current = sync;
    void sync.start((stored) => {
      const local = recordRef.current;
      if (!local) return;
      if (stored && shouldAdopt(stored, { ...local, pending: startPending })) {
        dirty.current.clear();
        const adopted = { ...stored, pending: false };
        writeRecord(userId, env, adopted);
        setStore((st) => ({ ...st, record: adopted, generation: st.generation + 1 }));
        return;
      }
      if (!stored || startPending || (local.rev ?? 0) > (stored.rev ?? 0)) sync.schedule(allSections(local));
    });
    return () => {
      void sync.flush();
      sync.stop();
      if (remote.current === sync) remote.current = null;
    };
  }, [userId, env]); // eslint-disable-line react-hooks/exhaustive-deps

  // The toolbar, Help and the visible workspace are part of the record.
  useEffect(() => {
    if (!same(toolbar, recordRef.current?.toolbar ?? DEFAULT_TOOLBAR)) markChanged(["toolbar"]);
  }, [toolbar, markChanged]);
  useEffect(() => {
    for (const [id, controller] of controllers) controller.want(HELP_WINDOW, helpOpen(help, id));
    if (!same(help, recordRef.current?.help ?? DEFAULT_HELP)) markChanged(["help"]);
  }, [help, controllers, markChanged]);
  useEffect(() => {
    if (!same(guide, recordRef.current?.guide ?? GUIDE_CLOSED)) markChanged(["guide"]);
  }, [guide, markChanged]);
  useEffect(() => {
    controllers.get("my")?.setWindows(myWorkspaceWindows(myWindows));
    if (!same(myWindows, recordRef.current?.myWindows ?? [])) markChanged(["myWindows"]);
  }, [myWindows, controllers, markChanged]);
  /** Change a workspace's phone arrangement, starting from its default when the user has not arranged it yet. */
  const updatePhone = useCallback((ws: WorkspaceId, next: PhoneLayout | null) => {
    setPhone((prev) => {
      const out = { ...prev };
      if (next) out[ws] = next; else delete out[ws];
      phoneRef.current = out;
      return out;
    });
    markChanged([`phone:${ws}`]);
  }, [markChanged]);
  const phoneLayoutOf = (ws: WorkspaceId): PhoneLayout => phoneRef.current[ws] ?? phoneDefaults(ws);
  phoneRevealRef.current = (ws, windowId) => {
    const layout = phoneLayoutOf(ws);
    if (layout.closed?.includes(windowId) || layout.folded?.includes(windowId)) {
      updatePhone(ws, { ...layout, closed: withMember(layout.closed, windowId, false), folded: withMember(layout.folded, windowId, false) });
    }
    setPhoneMax((prev) => (prev[ws] && prev[ws] !== windowId ? { ...prev, [ws]: null } : prev));
  };
  const updateGuide = useCallback((next: Partial<GuideState>) => setGuide((prev) => ({ ...prev, ...next })), []);

  // The setup wizard runs in Access Keys and shows the Guide there while it runs; see useWizardGuide.
  const wizard = useWizardGuide();
  const wizardStep = wizard.step;
  const wizardRef = useRef(wizardStep);
  wizardRef.current = wizardStep;
  const lastSeenWorkspace = useRef(d.workspace);
  useEffect(() => {
    if (lastSeenWorkspace.current === d.workspace) return;
    lastSeenWorkspace.current = d.workspace;
    markChanged(["lastWorkspace", ...WORKSPACE_IDS.filter((id) => controllers.has(id)).map((id) => `ws:${id}` as Section)], true);
  }, [d.workspace, markChanged, controllers]);

  // Toasts and this session's notification history, shared by every workspace.
  const notifications = useMemo(() => new Notifications(), []);
  // Help follows the pointer and focus anywhere in the shell.
  const helpTracker = useMemo(() => new HelpTracker(), []);
  useEffect(() => (rootEl ? helpTracker.attach(rootEl) : undefined), [rootEl, helpTracker]);

  // Welcome to the Guide: whether it was seen (saved), and the tips tried in this session (not saved: a demo, not a task).
  const [welcomeTried, setWelcomeTried] = useState<WelcomeStep[]>([]);
  const markWelcome = useCallback((step: WelcomeStep) => setWelcomeTried((prev) => (prev.includes(step) ? prev : [...prev, step])), []);
  const markWelcomeSeen = useCallback(() => setGuide((prev) => (prev.welcome?.seen ? prev : { ...prev, welcome: { seen: true } })), []);
  const [welcomeShowing, setWelcomeShowing] = useState(false);
  const welcome = useMemo<Welcome>(() => ({
    state: guide.welcome,
    phone: env === "mobile",
    tried: welcomeTried,
    mark: markWelcome,
    markSeen: markWelcomeSeen,
    showing: setWelcomeShowing,
    theme: d.theme,
    setTheme: d.onThemeChange,
  }), [guide.welcome, env, welcomeTried, markWelcome, markWelcomeSeen, d.theme, d.onThemeChange]);
  // Point is done when, with Welcome on screen, the reader moves the pointer onto a button and rests
  // it there while Quick Help explains it (user, 2026-11-02): a pointer already resting where the Help
  // menu closed, passing over, keyboard focus or Welcome's own controls do not count.
  const pointDone = welcomeTried.includes("point");
  useEffect(() => {
    if (!rootEl || env !== "desktop" || !welcomeShowing || pointDone) return undefined;
    let target: Element | null = null;
    let timer: number | undefined;
    const over = (e: PointerEvent) => {
      const el = e.target instanceof Element ? e.target.closest(WELCOME_BUTTONS) : null;
      if (el === target) return;
      window.clearTimeout(timer);
      target = el && !el.closest(".phx-welcome") ? el : null;
      if (!target) return;
      const resting = target;
      timer = window.setTimeout(() => {
        if (target === resting && helpOpen(helpRef.current, workspaceRef.current) && helpFor(resting)) markWelcome("point");
      }, WELCOME_POINT_MS);
    };
    const out = (e: PointerEvent) => {
      if (target && !(e.relatedTarget instanceof Node && target.contains(e.relatedTarget))) { window.clearTimeout(timer); target = null; }
    };
    rootEl.addEventListener("pointerover", over);
    rootEl.addEventListener("pointerout", out);
    return () => { window.clearTimeout(timer); rootEl.removeEventListener("pointerover", over); rootEl.removeEventListener("pointerout", out); };
  }, [rootEl, env, welcomeShowing, pointDone, markWelcome]);
  useEffect(() => (rootEl ? attachToggleNames(rootEl) : undefined), [rootEl]);
  // The toolbar keeps clear of the phone's status bar by only as much of the safe area as Home Assistant has
  // not already left above the panel: padding the whole inset again pushed the panel a second inset down.
  useLayoutEffect(() => {
    if (!rootEl) return undefined;
    const probe = document.createElement("div");
    probe.style.cssText = "position:absolute;visibility:hidden;pointer-events:none;padding-top:var(--safe-area-inset-top, env(safe-area-inset-top, 0px))";
    rootEl.appendChild(probe);
    const fit = () => {
      const inset = parseFloat(getComputedStyle(probe).paddingTop) || 0;
      const top = rootEl.getBoundingClientRect().top + window.scrollY;
      rootEl.style.setProperty("--phx-safe-top", `${Math.max(0, Math.round(inset - top))}px`);
    };
    fit();
    window.addEventListener("resize", fit);
    return () => { window.removeEventListener("resize", fit); probe.remove(); };
  }, [rootEl]);
  // The Guide follows the control the reader selects; Settings maps its controls through the Find Setting index.
  const guideTargets = useMemo(() => new GuideTargetTracker(), []);
  useEffect(() => guideTargets.addResolver(settingsGuideAnchor), [guideTargets]);
  useEffect(() => (rootEl ? guideTargets.attach(rootEl) : undefined), [rootEl, guideTargets]);
  const notify = useCallback((text: string) => notifications.notify(text), [notifications]);

  // My Workspace: Add to My Workspace in every window's menu, Remove from My Workspace there.
  const addToMy = useCallback((windowId: string) => {
    if (myWindowsRef.current.includes(windowId)) return;
    setMyWindows((prev) => (prev.includes(windowId) ? prev : [...prev, windowId]));
    notify(t("workspace.addedToMy", { window: windowTitle(windowId) }));
  }, [notify]);
  const removeFromMy = useCallback((windowId: string) => {
    if (!myWindowsRef.current.includes(windowId)) return;
    setMyWindows((prev) => prev.filter((w) => w !== windowId));
    if (windowId !== CHAT_WINDOW) notify(t("workspace.removedFromMy", { window: windowTitle(windowId) }));
  }, [notify]);
  removeFromMyRef.current = removeFromMy;
  // Empty My Workspace at once, so a reset that follows arranges no window.
  const clearMy = useCallback(() => {
    myWindowsRef.current = [];
    setMyWindows([]);
    controllers.get("my")?.setWindows(myWorkspaceWindows([]));
  }, [controllers]);
  // Every window's menu: Add to My Workspace (Float Chat for a docked Chat) and Show Icons and Text;
  // in My Workspace, Vertical Scrolling. Desktop only: the phone stack builds its own window menus.
  const menuExtras = useCallback<WindowMenuExtras>((controller, windowId) => {
    if (env !== "desktop") return [];
    const items: MenuEntry[] = [];
    if (windowId === CHAT_WINDOW) {
      if (controller.id === "my") items.push({ id: "float-chat", label: t("agentchat.floatChat"), help: t("help.menuFloatChat"), onSelect: () => removeFromMy(CHAT_WINDOW) });
    } else {
      if (controller.id !== "my" && canAddToMy(windowId)) {
        items.push({ id: "add-my", label: t("workspace.addToMy"), help: t("help.menuAddToMy"), disabled: myWindowsRef.current.includes(windowId), onSelect: () => addToMy(windowId) });
      }
      const labels = controller.display.hasLabels(windowId);
      items.push({ id: "labels", label: t("workspace.alwaysLabels"), help: t("help.menuAlwaysLabels"), checked: labels, toggle: true, onSelect: () => controller.display.setLabels(windowId, !labels) });
    }
    if (controller.scrollable) {
      const scrolls = controller.display.scrolls;
      items.push({ id: "scrolling", label: t("workspace.verticalScrolling"), help: t("help.menuVerticalScrolling"), checked: scrolls, toggle: true, onSelect: () => controller.setScrolls(!scrolls) });
    }
    return items;
  }, [env, addToMy, removeFromMy]);

  // The Guide opened at the bottom of the phone stack is brought into view, so opening it
  // visibly does something (user, 2026-11-02); one already open when the panel opens is not.
  const [revealGuide, setRevealGuide] = useState(false);
  useEffect(() => {
    if (!revealGuide) return;
    setRevealGuide(false);
    root.current?.querySelector(".phx-guide-phone")?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [revealGuide]);

  // The Help menu: Quick Help (every workspace; not on phones, user 2026-11-02) and the Guide (every workspace).
  // The Guide is the manual, so the website is no longer offered (user, 2026-11-01).
  const documentationMenu = useCallback((): MenuEntry[] => {
    const items: MenuEntry[] = [];
    if (env === "desktop") {
      // Checked while Help is shown in this workspace; choosing it shows or hides Help everywhere.
      const shown = helpOpen(helpRef.current, workspaceRef.current);
      items.push({ id: "help-bar", label: t("help.title"), help: t("help.menuQuickHelp"), checked: shown, onSelect: () => setHelp({ show: !shown, workspaces: {} }) });
    }
    const here = workspaceRef.current;
    // The wizard keeps the Guide shown in Access Keys until it ends.
    const pinned = here === "accessKeys" && !!wizardRef.current;
    const guideOpen = pinned || guideRef.current.open;
    items.push({ id: "guide", label: t("guide.title"), help: t("help.menuGuide"), checked: guideOpen, disabled: pinned, onSelect: () => {
      updateGuide({ open: !guideOpen });
      if (!guideOpen && env === "mobile") setRevealGuide(true);
    } });
    return items;
  }, [env, updateGuide]);

  // Reset All Workspaces applies at once (user, 2026-10-26): it changes presentation only, and says so in a toast.
  const restorePanelDefaults = useCallback(() => {
    const rec = recordRef.current;
    setToolbar(DEFAULT_TOOLBAR);
    setHelp(DEFAULT_HELP);
    helpRef.current = DEFAULT_HELP;
    setGuide(GUIDE_CLOSED);
    setWelcomeTried([]);
    guideRef.current = GUIDE_CLOSED;
    // My Workspace goes back to empty, as Reset This Workspace leaves it.
    clearMy();
    setPhone({});
    phoneRef.current = {};
    setPhoneMax({});
    // Needs Attention comes back everywhere while items are waiting, as Reset This Workspace brings it back there.
    for (const id of WORKSPACE_IDS) attentionRef.current.showAttentionIn(id);
    setShowArchived(false);
    deselectKey();
    for (const controller of controllers.values()) {
      controller.want(HELP_WINDOW, true);
      controller.restoreOriginal();
    }
    const sections: Section[] = ["toolbar", "help", "guide", "myWindows"];
    for (const id of WORKSPACE_IDS) {
      sections.push(`ws:${id}`, `phone:${id}`);
      if (rec && !controllers.has(id)) delete rec.workspaces[id];
    }
    toolbarRef.current = DEFAULT_TOOLBAR;
    markChanged(sections, true);
    if (rec) commit(WORKSPACE_IDS.filter((id) => !controllers.has(id)).map((id) => `ws:${id}` as Section), true);
    notify(t("workspace.panelDefaultsRestored"));
  }, [clearMy, commit, controllers, deselectKey, markChanged, notify, setShowArchived]);

  const actions = useMemo<WorkspaceActions>(() => ({
    saveMyDefault: (controller) => {
      const rec = recordRef.current;
      const layout = controller.layout();
      if (!rec || !layout) return;
      const id = controller.id as WorkspaceId;
      rec.myDefaults = { ...rec.myDefaults, [id]: layout };
      commit([`my:${id}`], true);
      notify(t("workspace.myDefaultSaved", { workspace: t(WORKSPACES[id].labelKey) }));
    },
    hasMyDefault: (controller) => !!recordRef.current?.myDefaults?.[controller.id as WorkspaceId],
    restoreMyDefault: (controller) => {
      const id = controller.id as WorkspaceId;
      const saved = recordRef.current?.myDefaults?.[id];
      if (!saved) return;
      // My Workspace's windows are its own list, which a reset empties: take them back
      // from the saved arrangement before laying it out.
      if (id === "my") {
        const members = layoutMembers(saved).filter((w) => canAddToMy(w) || w === CHAT_WINDOW);
        myWindowsRef.current = members;
        setMyWindows(members);
        controller.setWindows(myWorkspaceWindows(members));
      }
      controller.load(saved);
      markChanged([`ws:${id}`], true);
      notify(t("workspace.myDefaultRestored"));
    },
    restoreOriginal: (controller) => {
      // My Workspace goes back to its original, empty state (user, 2026-10-27).
      const mine = controller.id === "my";
      if (mine) clearMy();
      // The key list's Show Archived is part of how the workspace looks (user, 2026-10-28), and a
      // reset leaves no key chosen, so the list is maximized again (user, 2026-10-31).
      if (controller.id === "accessKeys") {
        setShowArchived(false);
        deselectKey();
      }
      // A reset brings Needs Attention back while items are still waiting (user, 2026-10-29).
      attentionRef.current.showAttentionIn(controller.id as WorkspaceId);
      controller.restoreOriginal();
      markChanged([`ws:${controller.id as WorkspaceId}`, ...(mine ? ["myWindows" as const] : [])], true);
    },
    restorePanelDefaults,
    // Archived Access Keys follows Show Archived, so it opens by turning that on; the others open in their place.
    openToolWindow: (controller, id) => {
      if (id === ARCHIVED_WINDOW) setShowArchived(true);
      else controller.reopen(id);
    },
    attentionHidden: (controller) => attentionRef.current.waiting && !attentionRef.current.attentionShownIn(controller.id as WorkspaceId),
    showAttention: (controller) => attentionRef.current.showAttentionIn(controller.id as WorkspaceId),
  }), [clearMy, commit, deselectKey, markChanged, notify, restorePanelDefaults, setShowArchived]);

  // Phones: Workspace Options with the same choices as on a desktop, over the stack's arrangement.
  const phoneActions = useMemo(() => ({
    saveMyDefault: (ws: WorkspaceId) => {
      const rec = recordRef.current;
      if (!rec) return;
      const saved: PhoneLayout = { ...phoneLayoutOf(ws), ...(ws === "my" ? { windows: myWindowsRef.current } : {}) };
      rec.phoneDefaults = { ...rec.phoneDefaults, [ws]: saved };
      commit([`phoneDefault:${ws}`], true);
      notify(t("workspace.myDefaultSaved", { workspace: t(WORKSPACES[ws].labelKey) }));
    },
    restoreMyDefault: (ws: WorkspaceId) => {
      const saved = recordRef.current?.phoneDefaults?.[ws];
      if (!saved) return;
      const { windows, ...layout } = saved;
      if (ws === "my") {
        const members = (windows ?? []).filter(canAddToMy);
        myWindowsRef.current = members;
        setMyWindows(members);
      }
      setPhoneMax((prev) => ({ ...prev, [ws]: null }));
      updatePhone(ws, layout);
      notify(t("workspace.myDefaultRestored"));
    },
    restoreOriginal: (ws: WorkspaceId) => {
      if (ws === "my") clearMy();
      if (ws === "accessKeys") {
        setShowArchived(false);
        deselectKey();
      }
      attentionRef.current.showAttentionIn(ws);
      setPhoneMax((prev) => ({ ...prev, [ws]: null }));
      updatePhone(ws, null);
      if (ws === "my") markChanged(["myWindows"]);
    },
  }), [clearMy, commit, deselectKey, markChanged, notify, setShowArchived, updatePhone]); // eslint-disable-line react-hooks/exhaustive-deps
  const phoneOptions = useCallback((): MenuEntry[] => {
    const ws = workspaceRef.current;
    const layout = phoneLayoutOf(ws);
    const items: MenuEntry[] = closedPhoneWindows(ws, phoneRef.current[ws], myWindowsRef.current).map((id) => ({
      id: `reopen-${id}`,
      label: t("workspace.reopen", { window: windowTitle(id) }),
      help: t("help.menuReopen", { window: windowTitle(id) }),
      onSelect: () => updatePhone(ws, { ...layout, closed: withMember(layout.closed, id, false) }),
    }));
    if (attentionRef.current.waiting && !attentionRef.current.attentionShownIn(ws)) {
      items.push({ id: "show-attention", label: t("workspace.showAttention"), help: t("help.menuShowAttention"), onSelect: () => attentionRef.current.showAttentionIn(ws) });
    }
    if (items.length) items.push("separator");
    items.push(
      { id: "save-default", label: t("workspace.saveMyDefault"), help: t("help.menuSaveDefault"), onSelect: () => phoneActions.saveMyDefault(ws) },
      { id: "restore-default", label: t("workspace.restoreMyDefault"), help: t("help.menuRestoreDefault"), disabled: !recordRef.current?.phoneDefaults?.[ws], onSelect: () => phoneActions.restoreMyDefault(ws) },
      { id: "restore", label: t("workspace.restoreOriginal"), hint: t(ws === "my" ? "workspace.resetMyHint" : "workspace.resetWorkspaceHint"), onSelect: () => phoneActions.restoreOriginal(ws) },
      "separator",
      { id: "panel-defaults", label: t("workspace.restorePanelDefaults"), hint: t("workspace.resetAllHint"), onSelect: restorePanelDefaults },
    );
    return items;
  }, [phoneActions, restorePanelDefaults, updatePhone]); // eslint-disable-line react-hooks/exhaustive-deps

  // Window titles follow the panel language.
  const languageSignature = t("shell.tabAccessKeys") + t("workspace.logs");
  useEffect(() => {
    for (const controller of controllers.values()) controller.retitle();
    helpTracker.refresh();
  }, [controllers, helpTracker, languageSignature]);

  // Needs Attention appears in the visible workspace while items need checking (or a
  // notification asked for it). Closing it hides it in THAT workspace only, until an
  // item it did not show arrives, and Workspace Options offers it back there (user, 2026-10-29).
  // Items, not a count: a review and a new item together keep the count.
  const attentionKeys = d.attention.items.map((item) => `${item.kind}:${item.id}`);
  const attentionCount = attentionKeys.length;
  const attentionSignature = attentionKeys.join("|");
  const [dismissedAt, setDismissedAt] = useState<Partial<Record<WorkspaceId, readonly string[]>>>({});
  const waiting = attentionCount > 0 || d.attentionRequested;
  const attentionShownIn = useCallback((id: WorkspaceId) => {
    const seen = dismissedAt[id];
    return waiting && (seen === undefined || attentionSignature.split("|").some((key) => key && !seen.includes(key)));
  }, [waiting, attentionSignature, dismissedAt]);
  useEffect(() => { if (attentionCount === 0) setDismissedAt({}); }, [attentionCount]);
  const dismissAttention = d.dismissAttentionRequest;
  const showAttentionIn = useCallback((id: WorkspaceId) => {
    setDismissedAt((prev) => { const next = { ...prev }; delete next[id]; return next; });
  }, []);
  const attentionRef = useRef({ attentionShownIn, showAttentionIn, waiting });
  attentionRef.current = { attentionShownIn, showAttentionIn, waiting };
  // Orphaned Profiles and Advisory show only while MESA has undismissed findings for them.
  const [mesaFindings, setMesaFindings] = useState<MesaFindings | null>(null);
  const settings = d.settings;
  const showArchived = d.showArchived;
  useEffect(() => {
    for (const [id, controller] of controllers) {
      // Presets appears only while its setting is on.
      controller.availability = (w) => windowAvailable(w, settings, mesaFindings, showArchived);
      controller.syncAvailable();
      controller.onTransientClose = () => { setDismissedAt((prev) => ({ ...prev, [id]: attentionKeys })); dismissAttention(); };
      if (env === "desktop" && id === d.workspace && attentionShownIn(id)) controller.showTransient(ATTENTION_WINDOW);
      else controller.hideTransient(ATTENTION_WINDOW);
    }
    // Show Archived opens and closes the archived keys' own window (user, 2026-10-28). Asked for only
    // when the two disagree: `want` lays the workspace out again, which every render must not do.
    const keys = controllers.get("accessKeys");
    if (keys && !!keys.hostOf(ARCHIVED_WINDOW) !== showArchived) keys.want(ARCHIVED_WINDOW, showArchived);
    // A copy added to My Workspace follows Show Archived too, rather than lingering or never appearing.
    const my = controllers.get("my");
    if (my && myWindows.includes(ARCHIVED_WINDOW) && !!my.hostOf(ARCHIVED_WINDOW) !== showArchived) my.want(ARCHIVED_WINDOW, showArchived);
  });

  const s = d.settings;
  const active = d.workspace;
  const language = resolveLanguage(d.hass);

  // Windows in My Workspace keep their workspace's state loading while My Workspace is shown.
  const myFrom = useMemo(() => new Set(myWindows.map((w) => SOURCE_OF[w as WindowId]).filter(Boolean)), [myWindows]);
  const shown = (ws: WorkspaceId) => active === ws || (active === "my" && myFrom.has(ws));

  // Agent Chat docked in My Workspace: Chat draws into the slot while it is on screen and floats otherwise.
  const [chatSlotEl, setChatSlotEl] = useState<HTMLElement | null>(null);
  const chatDocked = env === "desktop" && myWindows.includes(CHAT_WINDOW);
  const chatSlot = chatDocked && s && !s.kill_switch ? chatSlotEl : null;
  const globalChat = !!(s?.agentcli_global && d.globalChatReady);
  const chatBridge = () => (window as unknown as { __phxAgentChat?: { setDock?: (dock: unknown) => void; open?: () => void; restore?: () => void; close?: () => void } }).__phxAgentChat;
  const dockToggle = useCallback(() => {
    if (myWindowsRef.current.includes(CHAT_WINDOW)) removeFromMy(CHAT_WINDOW);
    else addToMy(CHAT_WINDOW);
  }, [addToMy, removeFromMy]);
  const closeAgentCli = d.closeAgentCli;
  closeChatRef.current = () => { if (globalChat) chatBridge()?.close?.(); else closeAgentCli(); };
  // A slot never opens Chat by itself: Chat docks from its own menu, so it is open then, and a
  // Chat closed elsewhere stays closed when My Workspace shows again (user, 2026-11-01). The
  // docked window offers Agent Chat to open it.
  const { agentCliOpen, keepAgentCliOpen, openAgentCli } = d;
  // The global Chat follows the slot through its bridge; without the panel it floats.
  useEffect(() => {
    if (!globalChat) return;
    chatBridge()?.setDock?.(env === "desktop" ? { slot: chatSlot, docked: chatDocked, toggle: dockToggle } : null);
  }, [globalChat, env, chatSlot, chatDocked, dockToggle]);
  useEffect(() => () => { chatBridge()?.setDock?.(null); }, []);
  // The toolbar's Agent Chat: in My Workspace, its docked window; elsewhere, Chat floats.
  const openChat = useCallback(() => {
    if (chatDocked && workspaceRef.current === "my") {
      controllerFor("my").ensureOpen(CHAT_WINDOW);
      if (globalChat) chatBridge()?.restore?.();
      else if (!agentCliOpen) keepAgentCliOpen();
      requestAnimationFrame(() => requestAnimationFrame(() => visibleWindowEl(root.current, CHAT_WINDOW)?.querySelector<HTMLElement>("textarea")?.focus()));
      return;
    }
    openAgentCli();
  }, [chatDocked, globalChat, agentCliOpen, keepAgentCliOpen, openAgentCli, controllerFor]);
  const chatDock = useMemo<ChatDock>(() => ({
    offer: (slot) => setChatSlotEl(slot),
    release: (slot) => setChatSlotEl((current) => (current === slot ? null : current)),
    openChat,
  }), [openChat]);
  const dock = env === "mobile" ? "top" : toolbar.dock;
  // The wizard's Guide: shown in Access Keys while the wizard runs, and on a desktop the wizard makes room for it.
  const wizardGuideShown = active === "accessKeys" && !!wizardStep;
  // The Guide met the reader beside the setup wizard, so it does not open on Welcome afterwards (user, 2026-11-03).
  useEffect(() => { if (wizardGuideShown) markWelcomeSeen(); }, [wizardGuideShown, markWelcomeSeen]);
  const guideShown = wizardGuideShown || guide.open;
  const guideBeside = env === "desktop" && wizardGuideShown;
  const wizardGuide = useMemo(() => ({ report: wizard.report, guideBeside }), [wizard.report, guideBeside]);
  return (
    <MenuRootContext.Provider value={rootEl}>
    <ContextMenuHost>
    <ToolRegistryContext.Provider value={registry}>
    <NotificationsContext.Provider value={notifications}>
    <DisplayModeContext.Provider value={env === "mobile" ? "icons" : toolbar.mode}>
    <HelpContext.Provider value={helpTracker}>
    <GuideTargetContext.Provider value={guideTargets}>
    <WelcomeContext.Provider value={welcome}>
    <AuditFocusContext.Provider value={auditFocus}>
    <LogSelectionContext.Provider value={logSelection}>
    <ApprovalsProvider
      scope={d.approvalsTab}
      onScopeChange={d.setApprovalsTab}
      onCountChange={d.refreshPendingCount}
      refreshSignal={d.approvalSignal}
      claimedApprovals={d.claimedApprovals}
      openApprovalId={d.deepApprovalId}
      onConsumedDeepLink={d.consumeDeepApprovalLink}
      active={shown("approvals")}
      onReveal={revealReview}
    >
    <ChangesProvider hass={d.hass} active={shown("changes")}>
    <MesaProvider active={shown("mesa")} onFindings={setMesaFindings}>
    <SettingsProvider settings={d.settings} onSettingsChange={d.setSettings} theme={d.theme} onThemeChange={d.onThemeChange}
      language={d.language} onLanguageChange={d.onLanguageChange} active={shown("settings")} root={rootEl}
      revealWindow={revealSettings} onNavigate={showSettings}>
    <WorkspaceActionsContext.Provider value={actions}>
    <WindowMenuExtrasContext.Provider value={menuExtras}>
    <ChatDockContext.Provider value={chatDock}>
    <WizardGuideContext.Provider value={wizardGuide}>
    <AccessKeyDetailProvider
      accessKeyId={d.accessKeyView.name === "detail" ? d.accessKeyView.accessKeyId : null}
      onDeselect={d.goBack}
      onRefresh={d.refreshAccessKeys}
      onReveal={revealPermissions}
      presetsEnabled={s?.access_key_presets_enabled ?? false}
      esphomeIntegration={s ? !!s.esphome_integration : null}
      esphomeBuilder={s ? !!s.esphome_builder : null}
    >
      <div ref={attachRoot} className={`phx-shell phx-env-${env}`} data-toolbar={dock}>
        <h1 className="sr-only">{t("shell.srTitle")}</h1>
        <Toolbar controller={env === "desktop" && visited.has(active) ? controllerFor(active) : null} shellRoot={root} env={env}
          state={toolbar} onChange={setToolbar} documentation={documentationMenu} onAgentChat={openChat}
          options={env === "mobile" ? phoneOptions : undefined} />
        <WorkspaceArea menu={env === "desktop" ? () => workspaceMenu(controllerFor(active), actions) : null}>
          {env === "desktop"
            ? WORKSPACE_IDS.filter((id) => visited.has(id)).map((id) => (
              <WorkspaceVisibleContext.Provider key={`${userId}:${store.generation}:${id}`} value={id === active}>
                <WorkspaceDock
                  controller={controllerFor(id)}
                  components={DOCK_COMPONENTS}
                  renderWindow={renderWindow}
                  watermark={EmptyWorkspace}
                  saved={record?.workspaces[id]}
                  visible={id === active}
                  onLayoutChange={() => markChanged([`ws:${id}`])}
                  workspaceMenu={() => workspaceMenu(controllerFor(id), actions)}
                />
              </WorkspaceVisibleContext.Provider>
            ))
            : <PhoneStack workspace={active} showAttention={attentionShownIn(active)}
              layout={phone[active]} onLayout={(next) => updatePhone(active, next)}
              maximized={phoneMax[active] ?? null} onMaximize={(id) => setPhoneMax((prev) => ({ ...prev, [active]: id }))}
              onCloseArchived={() => setShowArchived(false)}
              onCloseAttention={() => { setDismissedAt((prev) => ({ ...prev, [active]: attentionKeys })); dismissAttention(); }}
              onAddToMy={addToMy} onRemoveFromMy={removeFromMy} settings={s} mesa={mesaFindings} showArchived={d.showArchived} myWindows={myWindows}
              guide={guideShown ? <PhoneGuide workspace={active} language={language} onClose={wizardGuideShown ? undefined : () => updateGuide({ open: false })}
                topicId={wizardGuideShown ? `wizard.${wizardStep.step}` : null} /> : null} />}
        </WorkspaceArea>

        {/* Panel-only Agent Chat window. Not rendered in global-visibility mode (the
            inject module owns the window then), nor while the kill switch is on.
            When global injection is unsupported, __phxAgentChat is absent, so this
            renders as the fallback. */}
        {s && !s.kill_switch && d.agentCliOpen && !globalChat && (
          <ErrorBoundary key={d.agentCliKey}>
            <AgentCliWindow
              key={d.agentCliKey}
              accessKeys={d.accessKeys}
              instances={d.agentCliInstances}
              scrollbackLines={s.agentcli_scrollback_lines}
              initialAccessKeyId={d.accessKeys[0]?.id ?? ""}
              onClose={d.closeAgentCli}
              hass={d.hass}
              summonVersion={d.agentCliSummon}
              dockMount={chatSlot}
              dock={env === "desktop" ? { docked: chatDocked, toggle: dockToggle } : null}
            />
          </ErrorBoundary>
        )}
        {env === "desktop" && guideShown && (
          <GuideWindow workspace={active} controller={visited.has(active) ? controllerFor(active) : null} language={language}
            rect={guide.rect} onRect={(rect) => updateGuide({ rect })} onClose={() => updateGuide({ open: false })}
            folded={!!guide.folded} onFold={(folded) => updateGuide({ folded })}
            topicId={wizardGuideShown ? `wizard.${wizardStep.step}` : null}
            follow={wizardGuideShown ? wizardStep : null} opensItself={TRANSIENT} />
        )}
        <Toasts center={notifications} />
        {/* Home Assistant away: one notice over the panel, cleared when it answers again. */}
        <Reconnecting root={rootEl} />
      </div>
    </AccessKeyDetailProvider>
    </WizardGuideContext.Provider>
    </ChatDockContext.Provider>
    </WindowMenuExtrasContext.Provider>
    </WorkspaceActionsContext.Provider>
    </SettingsProvider>
    </MesaProvider>
    </ChangesProvider>
    </ApprovalsProvider>
    </LogSelectionContext.Provider>
    </AuditFocusContext.Provider>
    </WelcomeContext.Provider>
    </GuideTargetContext.Provider>
    </HelpContext.Provider>
    </DisplayModeContext.Provider>
    </NotificationsContext.Provider>
    </ToolRegistryContext.Provider>
    </ContextMenuHost>
    </MenuRootContext.Provider>
  );
}
