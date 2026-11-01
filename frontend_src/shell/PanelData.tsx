/**
 * Panel-wide data and navigation shared by every workspace window: access
 * keys, settings, approvals state, deep links and Agent Chat routing.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { AccessKeyRecord, AgentCliInstance, GlobalSettings } from "../types";
import { focusAgentCliPopup } from "../components/AgentCliWindow";
import { useAttention, type AttentionState } from "../components/AttentionPanel";
import { subscribeApprovalEvents } from "../utils/approval_events";
import {
  agentCliOpenPatch,
  getDurable as getAgentCliDurable,
  patchDurable as patchAgentCliDurable,
} from "../utils/agentcli_state";
import { api } from "../api";
import { syncCardCatalog } from "../utils/card_harvest";
import { registerAgentChatShortcut } from "../utils/agentchat_shortcut";
import { t } from "../i18n";
import type { WorkspaceId } from "./storage";
import { APPROVALS_SCOPES, type ApprovalsScope } from "./approvals";

export type Theme = "light" | "dark" | "auto";

export type AccessKeyView =
  | { name: "list" }
  | { name: "detail"; accessKeyId: string }
  | { name: "wizard" };

/** Which requests the Approvals list shows. */
export type ApprovalsTab = ApprovalsScope;

// Persist the selected access key so a reload selects it again; with none stored,
// the Access Keys windows wait for a selection.
const ACCESS_KEY_DETAIL_STORAGE_KEY = "phx-access-key-detail";
function readStoredView(): AccessKeyView {
  try {
    const id = localStorage.getItem(ACCESS_KEY_DETAIL_STORAGE_KEY)
      ?? localStorage.getItem("phx-token-detail");
    if (id) {
      localStorage.setItem(ACCESS_KEY_DETAIL_STORAGE_KEY, id);
      localStorage.removeItem("phx-token-detail");
    }
    if (id) return { name: "detail", accessKeyId: id };
  } catch {
    // localStorage unavailable: fall back to the list.
  }
  return { name: "list" };
}

// Persist the Approvals sub-tab (Pending vs History) the same way the Access
// keys detail view is remembered, so reloading keeps whichever one was open.
const APPROVALS_TAB_STORAGE_KEY = "phx-approvals-tab";
function readStoredApprovalsTab(): ApprovalsTab {
  try {
    const v = localStorage.getItem(APPROVALS_TAB_STORAGE_KEY);
    if (v && (APPROVALS_SCOPES as readonly string[]).includes(v)) return v as ApprovalsScope;
  } catch {
    // localStorage unavailable: default to Pending.
  }
  return "pending";
}

export function approvalDeepLink(): { approvalId: string | null } | null {
  const pathMatch = window.location.pathname.match(/^\/phoenix-mcp\/approvals(?:\/([^/]+))?\/?$/);
  const hashMatch = window.location.hash.replace(/^#/, "").match(/^approvals(?:\/(.+))?$/);
  const match = pathMatch ?? hashMatch;
  if (!match) return null;
  if (!match[1]) return { approvalId: null };
  try {
    return { approvalId: decodeURIComponent(match[1]) };
  } catch {
    return { approvalId: null };
  }
}

export interface PanelData {
  hass: unknown;
  narrow: boolean;
  theme: Theme;
  onThemeChange: (t: Theme) => void;
  language: string;
  onLanguageChange: (lang: string) => void;

  workspace: WorkspaceId;
  navigate: (workspace: WorkspaceId) => void;

  accessKeys: AccessKeyRecord[];
  loadingAccessKeys: boolean;
  accessKeysError: string | null;
  refreshAccessKeys: () => Promise<void>;
  /** Read the list again without a loading state, keeping what is shown if that fails. */
  updateAccessKeys: () => Promise<void>;
  accessKeyView: AccessKeyView;
  openDetail: (id: string) => void;
  openWizard: () => void;
  goBack: () => void;
  /** Leave the chosen key, as a workspace reset does; the wizard and the list stay as they are. */
  deselectKey: () => void;
  goToSettings: () => void;
  showCreate: boolean;
  setShowCreate: (open: boolean) => void;
  /** Whether the key list shows the archived keys; Reset This Workspace hides them again. */
  showArchived: boolean;
  setShowArchived: (show: boolean) => void;

  settings: GlobalSettings | null;
  setSettings: (settings: GlobalSettings) => void;

  pendingCount: number;
  refreshPendingCount: () => Promise<void>;
  approvalSignal: number;
  claimedApprovals: ReadonlySet<string>;
  deepApprovalId: string | null;
  consumeDeepApprovalLink: () => void;
  approvalsTab: ApprovalsTab;
  setApprovalsTab: (tab: ApprovalsTab) => void;

  attention: AttentionState;
  /** The page was opened at #attention; the Needs Attention window shows even when empty. */
  attentionRequested: boolean;
  dismissAttentionRequest: () => void;

  openAgentCli: (accessKeyId?: string, preservePosition?: boolean) => void;
  /** Mark the panel's own Agent Chat open without starting it again (it is showing in My Workspace). */
  keepAgentCliOpen: () => void;
  closeAgentCli: () => void;
  agentCliOpen: boolean;
  agentCliKey: number;
  /** Bumped to bring an open panel Chat back without remounting it. */
  agentCliSummon: number;
  agentCliInstances: AgentCliInstance[];
  globalChatReady: boolean;
}

const PanelDataContext = createContext<PanelData | null>(null);

/** The panel data, or null outside the provider (a window drawn on its own). */
export function usePanelDataOrNull(): PanelData | null {
  return useContext(PanelDataContext);
}

export function usePanelData(): PanelData {
  const data = useContext(PanelDataContext);
  if (!data) throw new Error("usePanelData outside PanelDataProvider");
  return data;
}

export function PanelDataProvider({ hass, narrow, theme, onThemeChange, language, onLanguageChange, initialWorkspace, children }: {
  hass: unknown;
  narrow: boolean;
  theme: Theme;
  onThemeChange: (t: Theme) => void;
  language: string;
  onLanguageChange: (lang: string) => void;
  initialWorkspace: WorkspaceId;
  children: ReactNode;
}) {
  const [workspace, setWorkspace] = useState<WorkspaceId>(initialWorkspace);
  const [accessKeyView, setView] = useState<AccessKeyView>(readStoredView);
  const [accessKeys, setAccessKeys] = useState<AccessKeyRecord[]>([]);
  const [settings, setSettings] = useState<GlobalSettings | null>(null);
  const [loadingAccessKeys, setLoadingAccessKeys] = useState(true);
  const [accessKeysError, setAccessKeysError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [pendingCount, setPendingCount] = useState<number>(0);
  // Bumped on each HA approval event so the Approvals list can refresh instantly.
  const [approvalSignal, setApprovalSignal] = useState<number>(0);
  // Approvals whose saved action is executing right now. An approve runs its tool
  // inline in the admin's request, so nothing resolves for seconds; without this
  // the list keeps offering Approve and Reject on an approval already being acted
  // on, and the second click can only ever earn a 409.
  const [claimedApprovals, setClaimedApprovals] = useState<ReadonlySet<string>>(() => new Set());
  const [deepApprovalId, setDeepApprovalId] = useState<string | null>(null);
  const [approvalsTab, setApprovalsTab] = useState<ApprovalsTab>(readStoredApprovalsTab);
  const [attentionRequested, setAttentionRequested] = useState(() => window.location.hash === "#attention");
  const [agentCliOpen, setAgentCliOpen] = useState<boolean>(() => getAgentCliDurable().open);
  // Whether the global floating-window bootstrap (inject/agentchat.ts) has
  // installed itself. Held as STATE, not read live off window.__phxAgentChat in
  // render: the bootstrap becomes ready asynchronously (it polls for hass), and
  // a live read is only re-evaluated on unrelated re-renders, which would
  // unmount an open fallback window at arbitrary moments (e.g. a theme toggle).
  // The bootstrap dispatches phx-agentchat-ready when it installs.
  const [globalChatReady, setGlobalChatReady] = useState<boolean>(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    () => !!(window as any).__phxAgentChat?.ready,
  );
  // Bumped each time the window is opened from a button so it remounts and
  // re-reads the (re-centered, un-minimized) durable geometry.
  const [agentCliKey, setAgentCliKey] = useState<number>(0);
  const [agentCliSummon, setAgentCliSummon] = useState<number>(0);
  const [agentCliInstances, setAgentCliInstances] = useState<AgentCliInstance[]>([]);
  // Baseline pendingCount "seen" while actually viewing Pending; null until the
  // first count is known, so a reload with History persisted and count > 0
  // never looks like a rise. Used to detect a genuinely NEW approval arriving
  // while History is open, distinct from "some approval happens to be pending".
  const lastSeenPendingCountRef = useRef<number | null>(null);
  const openAgentCliRef = useRef<(accessKeyId?: string) => void>(() => {});
  const attention = useAttention();

  // Deep-link from a notification: /phoenix-mcp/approvals/{id} opens the
  // Approvals workspace and that specific approval. Legacy fragment links remain
  // readable. Listen on HA's SPA-navigation signals as well as hashchange:
  // when the panel is already open, HA routes without remounting this tree.
  useEffect(() => {
    function handleApprovalDeepLink() {
      const link = approvalDeepLink();
      if (!link) return;
      setWorkspace("approvals");
      setView({ name: "list" });
      if (link.approvalId) {
        setDeepApprovalId(link.approvalId);
        // A notification deep-link is for a pending approval, so show Pending
        // immediately: otherwise a persisted History sub-tab flashes until the
        // fetch resolves (ApprovalsView corrects to History if it is already
        // resolved).
        setApprovalsTab("pending");
      }
    }
    handleApprovalDeepLink();
    window.addEventListener("hashchange", handleApprovalDeepLink);
    window.addEventListener("location-changed", handleApprovalDeepLink);
    window.addEventListener("popstate", handleApprovalDeepLink);
    return () => {
      window.removeEventListener("hashchange", handleApprovalDeepLink);
      window.removeEventListener("location-changed", handleApprovalDeepLink);
      window.removeEventListener("popstate", handleApprovalDeepLink);
    };
  }, []);

  // #attention (from a notification) asks for the Needs Attention window.
  const loadAttention = attention.load;
  useEffect(() => {
    const show = () => {
      if (window.location.hash !== "#attention") return;
      setAttentionRequested(true);
      void loadAttention();
    };
    window.addEventListener("hashchange", show);
    window.addEventListener("location-changed", show);
    return () => {
      window.removeEventListener("hashchange", show);
      window.removeEventListener("location-changed", show);
    };
  }, [loadAttention]);

  const consumeDeepApprovalLink = useCallback(() => {
    setDeepApprovalId(null);
    if (!approvalDeepLink()?.approvalId) return;
    // Clear only after ApprovalsView has fetched and opened the record. Keeping
    // the route until then lets a custom-panel remount consume the same target.
    try {
      window.history.replaceState(
        window.history.state,
        "",
        `/phoenix-mcp${window.location.search}`,
      );
    } catch { /* URL API blocked: leave the deep link */ }
  }, []);

  // Build the dashboard card catalog once per panel visit. Backgrounded and
  // fully self-contained: it never blocks a render, never surfaces an error, and
  // rate-limits itself, because the catalog only changes when the operator
  // installs or removes a plugin. The panel is the reliable place for this since
  // only a browser can see which custom cards exist (utils/card_harvest).
  useEffect(() => {
    void syncCardCatalog();
  }, []);

  // Remember the selected access key; clear it when nothing is selected.
  useEffect(() => {
    try {
      if (accessKeyView.name === "detail") {
        localStorage.setItem(ACCESS_KEY_DETAIL_STORAGE_KEY, accessKeyView.accessKeyId);
      } else {
        localStorage.removeItem(ACCESS_KEY_DETAIL_STORAGE_KEY);
      }
    } catch { /* storage blocked: skip */ }
  }, [accessKeyView]);

  useEffect(() => {
    try { localStorage.setItem(APPROVALS_TAB_STORAGE_KEY, approvalsTab); } catch { /* storage blocked: skip */ }
  }, [approvalsTab]);

  // `quiet` reads the list again behind the one shown: a failure keeps what is shown, for the next update to retry.
  const loadAccessKeys = useCallback(async (quiet: boolean) => {
    if (!quiet) {
      setLoadingAccessKeys(true);
      setAccessKeysError(null);
    }
    try {
      const data = await api.listAccessKeys();
      setAccessKeys(data);
      setAccessKeysError(null);
    } catch (e: unknown) {
      if (!quiet) setAccessKeysError(e instanceof Error ? e.message : t("shell.loadAccessKeysFailed"));
    } finally {
      if (!quiet) setLoadingAccessKeys(false);
    }
  }, []);
  const refreshAccessKeys = useCallback(() => loadAccessKeys(false), [loadAccessKeys]);
  const updateAccessKeys = useCallback(() => loadAccessKeys(true), [loadAccessKeys]);

  // Load configured agentCLI providers, open the floating chat window when a
  // "connect" surface requests it, and refresh the provider list whenever it
  // changes in Settings (so the window updates without a page reload).
  useEffect(() => {
    const refresh = () => api.getAgentCliProviders()
      .then((r) => setAgentCliInstances(r.instances)).catch(() => { /* not configured yet */ });
    refresh();
    // Opening from an access key's connect flow binds the window to that access key;
    // opening from the toolbar restores the last-used access key.
    // Routed through a ref, not the closure: this effect mounts once, when
    // `settings` is still null, so a captured openAgentCli would decide routing
    // (kill switch, global-vs-panel window) before settings loaded and always
    // fall through to the panel-only window.
    const onOpen = (e: Event) => {
      const detail = (e as CustomEvent).detail as { accessKeyId?: string } | undefined;
      openAgentCliRef.current(detail?.accessKeyId);
    };
    // An access key created or revoked elsewhere (wizard, Access key detail) must reach
    // the panel fallback window's own accessKeys prop independent of navigation,
    // matching the floating window.
    const onAccessKeysChanged = () => { void refreshAccessKeys(); };
    window.addEventListener("phx-open-agentcli", onOpen);
    window.addEventListener("phx-agentcli-providers-changed", refresh);
    window.addEventListener("phx-access-keys-changed", onAccessKeysChanged);
    return () => {
      window.removeEventListener("phx-open-agentcli", onOpen);
      window.removeEventListener("phx-agentcli-providers-changed", refresh);
      window.removeEventListener("phx-access-keys-changed", onAccessKeysChanged);
    };
  }, [refreshAccessKeys]);

  // Open (or re-summon) the chat window: re-center it and clear any minimized
  // state, so a window that was dragged off screen or minimized always comes
  // back visible. Blocked while the kill switch is on. When the admin chose the
  // global-visibility mode and the inject module is ready, hand off to that
  // window (floats over all of HA); otherwise render the panel-only window (also
  // the fallback when global injection is unsupported). The key bump forces a
  // remount that re-reads the centered geometry.
  const openAgentCli = useCallback((accessKeyId?: string, preservePosition = false) => {
    if (settings?.kill_switch) return;
    // The panel-local fallback can also be popped out. Keep its live React tree
    // intact and use this direct button gesture to request popup focus.
    if (accessKeyId === undefined && focusAgentCliPopup()) return;
    const d = getAgentCliDurable();
    patchAgentCliDurable(agentCliOpenPatch(
      d,
      accessKeyId,
      preservePosition ? undefined : { w: window.innerWidth, h: window.innerHeight },
    ));
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = (window as any).__phxAgentChat;
    if (settings?.agentcli_global && g?.ready) {
      g.open(accessKeyId);
      return;
    }
    // An open window is brought back, not remounted: a remount would end a running reply and
    // cancel its approvals. Opening it on a given key, as the
    // setup does, starts afresh.
    if (agentCliOpen && accessKeyId === undefined) {
      setAgentCliSummon((v) => v + 1);
      return;
    }
    setAgentCliKey((k) => k + 1);
    setAgentCliOpen(true);
  }, [settings, agentCliOpen]);
  const closeAgentCli = useCallback(() => {
    patchAgentCliDurable({ open: false });
    setAgentCliOpen(false);
  }, []);
  const keepAgentCliOpen = useCallback(() => {
    patchAgentCliDurable({ open: true });
    setAgentCliOpen(true);
  }, []);

  // The panel registers the same shortcut as the global bootstrap so Shift+A
  // also works when the admin chose the panel-only Agent Chat mode. The helper
  // follows HA's profile opt-in and input/selection guards.
  useEffect(() => {
    if (!settings || settings.kill_switch) return;
    return registerAgentChatShortcut(() => hass as { enableShortcuts?: boolean }, () => {
      // The injected listener normally handles global mode first. This branch
      // keeps the toggle correct if the panel listener happened to register first.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const globalChat = (window as any).__phxAgentChat;
      if (settings.agentcli_global && globalChat?.ready && globalChat.toggle) {
        void globalChat.toggle();
      } else if (agentCliOpen) {
        closeAgentCli();
      } else {
        openAgentCli(undefined, true);
      }
    });
  }, [agentCliOpen, closeAgentCli, hass, openAgentCli, settings]);

  // Keep the ref the phx-open-agentcli listener calls pointing at the current
  // openAgentCli, so an open request always routes against loaded settings.
  useEffect(() => { openAgentCliRef.current = openAgentCli; }, [openAgentCli]);

  useEffect(() => {
    const onReady = () => setGlobalChatReady(true);
    window.addEventListener("phx-agentchat-ready", onReady);
    // The bootstrap may have installed between the state initializer and this
    // listener attaching; re-read so the flip is never missed.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    if ((window as any).__phxAgentChat?.ready) setGlobalChatReady(true);
    return () => window.removeEventListener("phx-agentchat-ready", onReady);
  }, []);

  // In global-visibility mode the floating window owns the chat, but on a fresh
  // page load its bootstrap can become ready AFTER this panel already restored
  // its fallback window. Hand the open fallback off to the floating window
  // instead of leaving a window the next re-render would silently unmount.
  useEffect(() => {
    if (globalChatReady && settings?.agentcli_global && agentCliOpen) {
      setAgentCliOpen(false);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const globalChat = (window as any).__phxAgentChat;
      // Navigating into this panel can mount it underneath an Agent Chat that
      // already owns the global host. A visible window needs no action; a fresh
      // page load needs a restore, which preserves the saved desktop geometry.
      // The explicit toolbar button remains the only path that summons/centers.
      if (!globalChat?.isVisible?.()) globalChat?.restore?.();
    }
  }, [globalChatReady, settings?.agentcli_global, agentCliOpen]);

  // The kill switch hides the window everywhere and blocks reopening until it is
  // turned off again. The toggle lives in this panel, so flipping it re-renders
  // here and fires this.
  useEffect(() => {
    if (settings?.kill_switch) {
      setAgentCliOpen(false);
      patchAgentCliDurable({ open: false });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any).__phxAgentChat?.close?.();
    }
  }, [settings?.kill_switch]);

  // While the user is actually looking at Pending, treat the current count as
  // "seen" so a later visit while on History can tell a genuinely new arrival
  // apart from a count that was already nonzero.
  useEffect(() => {
    if (workspace === "approvals" && approvalsTab === "pending") {
      lastSeenPendingCountRef.current = pendingCount;
    }
  }, [workspace, approvalsTab, pendingCount]);

  useEffect(() => {
    void refreshAccessKeys();
    api.getSettings().then(setSettings).catch(() => null);
  }, [refreshAccessKeys]);

  const refreshPendingCount = useCallback(async () => {
    try {
      const resp = await api.listApprovals({ status: "pending", limit: 1 });
      setPendingCount(resp.total);
      // Seed the baseline on the very first successful fetch so a reload with
      // History persisted and an already-nonzero count never looks like a rise.
      if (lastSeenPendingCountRef.current === null) lastSeenPendingCountRef.current = resp.total;
    } catch {
      // Silent failure: badge just won't update. Don't surface in UI.
    }
  }, []);

  useEffect(() => {
    void refreshPendingCount();
    // Poll as a fallback (in case the event subscription below is unavailable or
    // an event is missed); the subscription makes the common case instant.
    const id = setInterval(refreshPendingCount, 5_000);
    return () => clearInterval(id);
  }, [refreshPendingCount]);

  // Instant approval updates: subscribe to the HA event bus so a queued or
  // resolved approval refreshes the badge AND (via approvalSignal) the Approvals
  // list immediately, instead of waiting for the next poll. Falls back silently
  // to polling if the connection has no subscribeEvents.
  useEffect(() => {
    const conn = (hass as {
      connection?: { subscribeEvents?: (cb: () => void, ev: string) => Promise<() => void> };
    } | null)?.connection;
    if (!conn?.subscribeEvents) return;
    let cancelled = false;
    const unsubs: Array<() => void> = [];
    const onApprovalEvent = () => {
      void refreshPendingCount();
      setApprovalSignal((n) => n + 1);
    };
    unsubs.push(subscribeApprovalEvents(hass, {
      onClaimChanged: (approvalId, claimed) => setClaimedApprovals((prev) => {
        if (claimed === prev.has(approvalId)) return prev;
        const next = new Set(prev);
        if (claimed) next.add(approvalId); else next.delete(approvalId);
        return next;
      }),
    }));
    for (const ev of ["phoenix_mcp_approval_requested", "phoenix_mcp_approval_resolved"]) {
      conn.subscribeEvents(onApprovalEvent, ev)
        .then((unsub) => { if (cancelled) unsub(); else unsubs.push(unsub); })
        .catch(() => { /* subscription unavailable: polling covers it */ });
    }
    return () => { cancelled = true; unsubs.forEach((u) => u()); };
  }, [hass, refreshPendingCount]);

  const openDetail = useCallback((id: string) => {
    setView({ name: "detail", accessKeyId: id });
    setWorkspace("accessKeys");
  }, []);

  const openWizard = useCallback(() => {
    setWorkspace("accessKeys");
    setView({ name: "wizard" });
  }, []);

  const goBack = useCallback(() => {
    setView({ name: "list" });
    void refreshAccessKeys();
  }, [refreshAccessKeys]);

  const deselectKey = useCallback(() => {
    setView((v) => (v.name === "detail" ? { name: "list" } : v));
  }, []);

  // Jump to Settings (used by the "Use Agent Chat"/"Setup Agent Chat" options in
  // the create/connect flows). Refresh access keys first so an access key just
  // made in the wizard is listed when the user returns to Access Keys.
  const goToSettings = useCallback(() => {
    void refreshAccessKeys();
    setView({ name: "list" });
    setWorkspace("settings");
  }, [refreshAccessKeys]);

  const navigate = useCallback((next: WorkspaceId) => {
    // A new approval arrived while History was open: jump to Pending so it's
    // not missed. Gated on a genuine rise (not just "something is pending",
    // which is true often enough that it would otherwise defeat the
    // persisted-sub-tab restore on almost every visit).
    if (
      next === "approvals" && approvalsTab !== "pending" && approvalsTab !== "all" &&
      lastSeenPendingCountRef.current !== null && pendingCount > lastSeenPendingCountRef.current
    ) {
      setApprovalsTab("pending");
    }
    setWorkspace(next);
    // Coming back to Access Keys keeps the chosen key (user, 2026-10-28); the list is read again.
    if (next === "accessKeys") void refreshAccessKeys();
  }, [refreshAccessKeys, approvalsTab, pendingCount]);

  // The link's #attention goes too, or a reload would bring back the window just closed.
  const dismissAttentionRequest = useCallback(() => {
    setAttentionRequested(false);
    if (window.location.hash === "#attention") history.replaceState(history.state, "", window.location.pathname + window.location.search);
  }, []);

  const value: PanelData = {
    hass, narrow, theme, onThemeChange, language, onLanguageChange,
    workspace, navigate,
    accessKeys, loadingAccessKeys, accessKeysError, refreshAccessKeys, updateAccessKeys,
    accessKeyView, openDetail, openWizard, goBack, deselectKey, goToSettings, showCreate, setShowCreate, showArchived, setShowArchived,
    settings, setSettings,
    pendingCount, refreshPendingCount, approvalSignal, claimedApprovals, deepApprovalId, consumeDeepApprovalLink,
    approvalsTab, setApprovalsTab,
    attention, attentionRequested, dismissAttentionRequest,
    openAgentCli, keepAgentCliOpen, closeAgentCli, agentCliOpen, agentCliKey, agentCliSummon, agentCliInstances, globalChatReady,
  };
  return <PanelDataContext.Provider value={value}>{children}</PanelDataContext.Provider>;
}
