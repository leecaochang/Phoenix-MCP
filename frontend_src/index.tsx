import React, { useState, useEffect, useCallback, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AccessKeyRecord, GlobalSettings, AgentCliInstance } from "./types";
import { AgentCliWindow, focusAgentCliPopup } from "./components/AgentCliWindow";
import { subscribeApprovalEvents } from "./utils/approval_events";
import {
  agentCliOpenPatch,
  getDurable as getAgentCliDurable,
  patchDurable as patchAgentCliDurable,
} from "./utils/agentcli_state";
import { AttentionPanel } from "./components/AttentionPanel";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { AccessKeyListView } from "./views/AccessKeyList";
import { AccessKeyDetailView } from "./views/AccessKeyDetail";
import { ConversationsView } from "./views/ConversationsView";
import { AuditView } from "./views/AuditView";
import { SettingsView } from "./views/SettingsView";
import { ApprovalsView } from "./views/ApprovalsView";
import { MesaView } from "./views/MesaView";
import { ChangesView } from "./views/ChangesView";
import { OnboardingWizard } from "./views/OnboardingWizard";
import { api, setHass } from "./api";
import { syncCardCatalog } from "./utils/card_harvest";
import { registerAgentChatShortcut } from "./utils/agentchat_shortcut";
import {
  getLanguagePreference,
  isI18nReady,
  loadTranslations,
  resolveLanguage,
  setLanguagePreference,
  syncTranslations,
  localeNumber,
  t,
} from "./i18n";
import { Loading, ErrorMsg, RefreshIcon, DOCS_BASE_URL } from "./components/common";
import PANEL_CSS from "./phoenix-mcp-panel.css?inline";

type Tab = "accessKeys" | "approvals" | "changes" | "mesa" | "audit" | "conversations" | "settings";
type Theme = "light" | "dark" | "auto";

export { HIGH_RISK_DOMAINS } from "./utils";

// Re-exported for the many views that import these from "../index". The
// definitions live in components/common so reusing them outside the panel does
// not pull the whole app into a bundle.
export { Loading, ErrorMsg, RefreshIcon };

type View =
  | { name: "list" }
  | { name: "detail"; accessKeyId: string }
  | { name: "wizard" };

const TAB_LABEL_KEYS: Record<Tab, string> = { accessKeys: "shell.tabAccessKeys", approvals: "shell.tabApprovals", changes: "shell.tabChanges", mesa: "shell.tabMesa", audit: "shell.tabAudit", conversations: "conversations.title", settings: "shell.tabSettings" };

// Persist the active tab so it survives a reload or navigating away and back.
const TAB_STORAGE_KEY = "phx-tab";
function readStoredTab(): Tab {
  try {
    const stored = localStorage.getItem(TAB_STORAGE_KEY);
    const v = stored === "tokens" ? "accessKeys" : stored;
    if (v && v in TAB_LABEL_KEYS) return v as Tab;
  } catch {
    // localStorage unavailable (e.g. blocked): fall back to the default.
  }
  return "accessKeys";
}

// Persist the access key being edited so returning to the Access keys tab reopens its
// detail; with none stored (or any other tab), reopen at the Access keys list.
const ACCESS_KEY_DETAIL_STORAGE_KEY = "phx-access-key-detail";
function readStoredView(tab: Tab): View {
  if (tab !== "accessKeys") return { name: "list" };
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

// Persist the Approvals sub-tab (Pending vs History) the same way the Access keys
// detail view is remembered, so reloading or navigating away and back keeps
// whichever one was open.
type ApprovalsTab = "pending" | "history";
const APPROVALS_TAB_STORAGE_KEY = "phx-approvals-tab";
function readStoredApprovalsTab(): ApprovalsTab {
  try {
    const v = localStorage.getItem(APPROVALS_TAB_STORAGE_KEY);
    if (v === "pending" || v === "history") return v;
  } catch {
    // localStorage unavailable: fall back to the default.
  }
  return "pending";
}

function approvalDeepLink(): { approvalId: string | null } | null {
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

function PhoenixApp({ hass, narrow, theme, onThemeChange, language, onLanguageChange }: { hass: unknown; narrow: boolean; theme: Theme; onThemeChange: (t: Theme) => void; language: string; onLanguageChange: (lang: string) => void }) {
  const [tab, setTab] = useState<Tab>(readStoredTab);
  const [view, setView] = useState<View>(() => readStoredView(readStoredTab()));
  const [accessKeys, setAccessKeys] = useState<AccessKeyRecord[]>([]);
  const [settings, setSettings] = useState<GlobalSettings | null>(null);
  const [loadingAccessKeys, setLoadingAccessKeys] = useState(true);
  const [accessKeysError, setAccessKeysError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
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
  const [agentCliInstances, setAgentCliInstances] = useState<AgentCliInstance[]>([]);
  const menuRef = useRef<HTMLElement | null>(null);
  // Baseline pendingCount "seen" while actually viewing Pending; null until the
  // first count is known, so a reload with History persisted and count > 0
  // never looks like a rise. Used to detect a genuinely NEW approval arriving
  // while History is open, distinct from "some approval happens to be pending".
  const lastSeenPendingCountRef = useRef<number | null>(null);
  const openAgentCliRef = useRef<(accessKeyId?: string) => void>(() => {});

  // Deep-link from a notification: /phoenix-mcp/approvals/{id} opens the
  // Approvals tab and that specific approval. Legacy fragment links remain
  // readable. Listen on HA's SPA-navigation signals as well as hashchange:
  // when the panel is already open, HA routes without remounting this tree.
  useEffect(() => {
    function handleApprovalDeepLink() {
      const link = approvalDeepLink();
      if (!link) return;
      setTab("approvals");
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

  useEffect(() => {
    try { localStorage.setItem(TAB_STORAGE_KEY, tab); } catch { /* storage blocked: skip */ }
  }, [tab]);

  // Remember the open access key detail (only on the Access keys tab); clear it otherwise
  // so returning lands on the list rather than a stale detail.
  useEffect(() => {
    try {
      if (tab === "accessKeys" && view.name === "detail") {
        localStorage.setItem(ACCESS_KEY_DETAIL_STORAGE_KEY, view.accessKeyId);
      } else {
        localStorage.removeItem(ACCESS_KEY_DETAIL_STORAGE_KEY);
      }
    } catch { /* storage blocked: skip */ }
  }, [tab, view]);

  useEffect(() => {
    try { localStorage.setItem(APPROVALS_TAB_STORAGE_KEY, approvalsTab); } catch { /* storage blocked: skip */ }
  }, [approvalsTab]);

  // Load configured agentCLI providers, open the floating chat window when a
  // "connect" surface requests it, and refresh the provider list whenever it
  // changes in Settings (so the window updates without a page reload).
  useEffect(() => {
    const refresh = () => api.getAgentCliProviders()
      .then((r) => setAgentCliInstances(r.instances)).catch(() => { /* not configured yet */ });
    refresh();
    // Opening from an access key's connect flow binds the window to that access key;
    // opening from the header restores the last-used access key.
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
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
    setAgentCliKey((k) => k + 1);
    setAgentCliOpen(true);
  }, [settings]);
  const closeAgentCli = useCallback(() => {
    patchAgentCliDurable({ open: false });
    setAgentCliOpen(false);
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
      // The explicit header button remains the only path that summons/centers.
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
  // "seen" so a later click while on History can tell a genuinely new arrival
  // apart from a count that was already nonzero.
  useEffect(() => {
    if (tab === "approvals" && approvalsTab === "pending") {
      lastSeenPendingCountRef.current = pendingCount;
    }
  }, [tab, approvalsTab, pendingCount]);

  useEffect(() => {
    if (menuRef.current) {
      (menuRef.current as unknown as Record<string, unknown>).hass = hass;
      (menuRef.current as unknown as Record<string, unknown>).narrow = narrow;
    }
  }, [hass, narrow]);

  const refreshAccessKeys = useCallback(async () => {
    setLoadingAccessKeys(true);
    setAccessKeysError(null);
    try {
      const data = await api.listAccessKeys();
      setAccessKeys(data);
    } catch (e: unknown) {
      setAccessKeysError(e instanceof Error ? e.message : t("shell.loadAccessKeysFailed"));
    } finally {
      setLoadingAccessKeys(false);
    }
  }, []);

  useEffect(() => {
    refreshAccessKeys();
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
    refreshPendingCount();
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
      refreshPendingCount();
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
    setTab("accessKeys");
  }, []);

  const openWizard = useCallback(() => {
    setTab("accessKeys");
    setView({ name: "wizard" });
  }, []);

  const goBack = useCallback(() => {
    setView({ name: "list" });
    refreshAccessKeys();
  }, [refreshAccessKeys]);

  // Jump to the Settings tab (used by the "Use Agent Chat"/"Setup Agent Chat"
  // options in the create/connect flows). Refresh access keys first so an access key just
  // made in the wizard is listed when the user returns to the Access keys tab.
  const goToSettings = useCallback(() => {
    refreshAccessKeys();
    setView({ name: "list" });
    setTab("settings");
  }, [refreshAccessKeys]);

  const onTabClick = useCallback((t: Tab) => {
    // A new approval arrived while History was open: jump to Pending so it's
    // not missed. Gated on a genuine rise (not just "something is pending",
    // which is true often enough that it would otherwise defeat the
    // persisted-sub-tab restore below on almost every visit).
    if (
      t === "approvals" && approvalsTab === "history" &&
      lastSeenPendingCountRef.current !== null && pendingCount > lastSeenPendingCountRef.current
    ) {
      setApprovalsTab("pending");
    }
    setTab(t);
    setView({ name: "list" });
    if (t === "accessKeys") refreshAccessKeys();
  }, [refreshAccessKeys, approvalsTab, pendingCount]);

  const TABS: Tab[] = ["accessKeys", "approvals", "changes", "mesa", "audit", "conversations", "settings"];

  function handleTabKeyDown(e: React.KeyboardEvent) {
    const idx = TABS.indexOf(tab);
    if (e.key === "ArrowRight" || e.key === "ArrowLeft" || e.key === "Home" || e.key === "End") {
      e.preventDefault();
      const tablist = e.currentTarget;
      const next = e.key === "Home"
        ? TABS[0]
        : e.key === "End"
          ? TABS[TABS.length - 1]
          : e.key === "ArrowRight"
            ? TABS[(idx + 1) % TABS.length]
            : TABS[(idx - 1 + TABS.length) % TABS.length];
      onTabClick(next);
      window.requestAnimationFrame(() => {
        const target = tablist.querySelector<HTMLButtonElement>(`#phx-tab-${next}`);
        target?.focus();
      });
    }
  }

  return (
    <div className="phx-shell">
      <h1 className="sr-only">{t("shell.srTitle")}</h1>
      <div className="phx-topbar">
        {narrow && (
          <header className="phx-header">
            <ha-menu-button ref={menuRef as React.RefObject<HTMLElement>} />
            <span className="phx-header-title">Phoenix MCP</span>
          </header>
        )}

        <nav className="phx-tabs" aria-label={t("shell.sectionsAria")}>
        {/* Row split is visual only (CSS order, mobile media query); DOM order
            stays the desktop reading order (tabs, doclink, header actions) so
            keyboard/AT navigation is unaffected. Same divergence pattern as
            .two-col's mobile reorder. */}
        <div role="tablist" aria-label={t("shell.sectionsAria")} onKeyDown={handleTabKeyDown} style={{ display: "contents" }}>
          {TABS.map((tabKey, i) => (
            <React.Fragment key={tabKey}>
              {/* No aria-controls: only the active panel is mounted (per-tab
                  lazy data fetch), so inactive tabs would point at missing ids. */}
              <button
                role="tab"
                id={`phx-tab-${tabKey}`}
                aria-selected={tab === tabKey}
                tabIndex={tab === tabKey ? 0 : -1}
                className={`phx-tab${tab === tabKey ? " active" : ""}${i < 3 ? " phx-tab-row1" : " phx-tab-row2"}`}
                onClick={() => onTabClick(tabKey)}
                aria-label={tabKey === "approvals" && pendingCount > 0
                  ? t("shell.approvalsTabAria", { count: pendingCount })
                  : undefined}
              >
                {t(TAB_LABEL_KEYS[tabKey])}
                {tabKey === "approvals" && pendingCount > 0 && (
                  <span className="phx-tab-badge" aria-hidden="true">{localeNumber(pendingCount)}</span>
                )}
              </button>
            </React.Fragment>
          ))}
        </div>

        {/* Forces the row-1/row-2 split at a fixed point (tabs row2 wraps
            below) instead of wherever content happens to overflow; inert
            (zero size, no wrap) outside the mobile layout. */}
        <div className="phx-tabs-break" aria-hidden="true" />

        <a
          className="phx-tab phx-tab-doclink"
          href={DOCS_BASE_URL}
          target="_blank"
          rel="noopener noreferrer"
        >
          {t("shell.documentation")}
          <svg className="phx-tab-extlink" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
            <polyline points="15 3 21 3 21 9" />
            <line x1="10" y1="14" x2="21" y2="3" />
          </svg>
        </a>

        <div className="phx-tab-spacer" />

        <div className="phx-header-actions">
          {settings?.kill_switch ? (
            <span className="phx-killswitch-banner" role="status" title={t("shell.killSwitchTitle")}>
              <span className="phx-killswitch-text-full">{t("shell.killSwitchFull")}</span>
              <span className="phx-killswitch-text-short">{t("shell.killSwitchShort")}</span>
            </span>
          ) : accessKeys.length > 0 ? (
            <button className="btn btn-outline btn-sm btn-header-action" onClick={() => openAgentCli()}>
              {t("shell.agentChat")}
            </button>
          ) : null}
        </div>
        </nav>
      </div>

      <main
        className="phx-content"
        id={`phx-tabpanel-${tab}`}
        role="tabpanel"
        aria-labelledby={`phx-tab-${tab}`}
      >
        <ErrorBoundary><AttentionPanel /></ErrorBoundary>
        <h2 className="sr-only">{t(TAB_LABEL_KEYS[tab])}</h2>
        <ErrorBoundary key={`${tab}:${view.name}:${view.name === "detail" ? view.accessKeyId : ""}`} >
        {tab === "accessKeys" && view.name === "list" && (
          <AccessKeyListView
            accessKeys={accessKeys}
            loading={loadingAccessKeys}
            error={accessKeysError}
            onRefresh={refreshAccessKeys}
            onOpenDetail={openDetail}
            onLaunchWizard={openWizard}
            showCreate={showCreate}
            onOpenCreate={() => setShowCreate(true)}
            onCloseCreate={() => setShowCreate(false)}
            onOpenSettings={goToSettings}
          />
        )}
        {tab === "accessKeys" && view.name === "wizard" && (
          <OnboardingWizard onCancel={goBack} onFinish={openDetail} />
        )}
        {tab === "accessKeys" && view.name === "detail" && (
          <AccessKeyDetailView
            accessKeyId={view.accessKeyId}
            onBack={goBack}
            onRefresh={refreshAccessKeys}
            presetsEnabled={settings?.access_key_presets_enabled ?? false}
            esphome={
              settings
                ? {
                    integration: !!settings.esphome_integration,
                    builder: !!settings.esphome_builder,
                  }
                : null
            }
          />
        )}
        {tab === "approvals" && (
          <ApprovalsView
            tab={approvalsTab}
            onTabChange={setApprovalsTab}
            onCountChange={refreshPendingCount}
            refreshSignal={approvalSignal}
            claimedApprovals={claimedApprovals}
            openApprovalId={deepApprovalId}
            onConsumedDeepLink={consumeDeepApprovalLink}
          />
        )}
        {tab === "mesa" && <MesaView />}
        {tab === "audit" && <AuditView accessKeys={accessKeys} />}
        {tab === "conversations" && <ConversationsView />}
        {tab === "changes" && <ChangesView hass={hass} />}
        {tab === "settings" && (
          <SettingsView
            settings={settings}
            onSettingsChange={setSettings}
            theme={theme}
            onThemeChange={onThemeChange}
            language={language}
            onLanguageChange={onLanguageChange}
          />
        )}
        </ErrorBoundary>
      </main>

      {/* Panel-only window. Not rendered in global-visibility mode (the inject
          module owns the window then), nor while the kill switch is on. When
          global injection is unsupported, __phxAgentChat is absent, so this
          renders as the fallback. */}
      {settings && !settings.kill_switch && agentCliOpen
        && !(settings.agentcli_global && globalChatReady) && (
        <ErrorBoundary key={agentCliKey}><AgentCliWindow
          key={agentCliKey}
          accessKeys={accessKeys}
          instances={agentCliInstances}
          scrollbackLines={settings.agentcli_scrollback_lines}
          initialAccessKeyId={accessKeys[0]?.id ?? ""}
          onClose={closeAgentCli}
          hass={hass}
        /></ErrorBoundary>
      )}
    </div>
  );
}

class PhoenixPanelElement extends HTMLElement {
  private _root: Root | null = null;
  private _hass: unknown = null;
  private _narrow: boolean = false;
  private _prevUserId: string | undefined = undefined;
  private _theme: Theme = "auto";
  private _i18nStarted: boolean = false;
  private _language: string = getLanguagePreference();

  connectedCallback() {
    this.style.touchAction = "pan-y";

    let saved: string | null = null;
    try { saved = localStorage.getItem("phx-theme"); } catch { /* storage blocked: use default */ }
    if (saved === "light" || saved === "dark" || saved === "auto") {
      this._theme = saved;
    }
    this._applyThemeClass();

    const shadow = this.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = PANEL_CSS;
    shadow.appendChild(style);

    const mount = document.createElement("div");
    mount.style.height = "100%";
    shadow.appendChild(mount);

    this._root = createRoot(mount);
    this._render();
  }

  disconnectedCallback() {
    this._root?.unmount();
    this._root = null;
  }

  set hass(hass: unknown) {
    this._hass = hass;
    setHass(hass);
    if (!this._i18nStarted) {
      this._i18nStarted = true;
      loadTranslations(hass, resolveLanguage(hass)).then((committed) => {
        if (committed) this._render();
      });
    } else {
      // Under "auto" the language follows the HA profile, which an admin can
      // change without reloading the page. A no-op unless it actually differs.
      void syncTranslations(hass).then((changed) => { if (changed) this._render(); });
    }
    const uid = (hass as Record<string, Record<string, string>> | null)?.user?.id;
    if (uid !== this._prevUserId) {
      this._prevUserId = uid;
      this._render();
    }
    if (this._theme === "auto") this._applyThemeClass();
  }

  set narrow(value: boolean) {
    if (this._narrow !== value) {
      this._narrow = value;
      this._render();
    }
  }

  private _applyThemeClass() {
    this.classList.remove("phx-theme-light", "phx-theme-dark");
    if (this._theme === "light") {
      this.classList.add("phx-theme-light");
    } else if (this._theme === "dark") {
      this.classList.add("phx-theme-dark");
    } else {
      // Auto: follow HA's dark mode preference when available
      const hassThemes = (this._hass as { themes?: { darkMode?: boolean } } | null)?.themes;
      if (hassThemes?.darkMode === true) {
        this.classList.add("phx-theme-dark");
      } else if (hassThemes?.darkMode === false) {
        this.classList.add("phx-theme-light");
      }
      // If darkMode is undefined, no class - CSS prefers-color-scheme handles it
    }
  }

  private _setLanguage(pref: string) {
    this._language = pref;
    setLanguagePreference(pref);
    // Repaint immediately so the dropdown reflects the choice, then again once
    // the new catalog lands. Without the first render the select would appear
    // stuck on the old value for the length of a round trip.
    this._render();
    loadTranslations(this._hass, resolveLanguage(this._hass)).then((committed) => {
      if (committed) this._render();
    });
  }

  private _setTheme(t: Theme) {
    this._theme = t;
    try { localStorage.setItem("phx-theme", t); } catch { /* storage blocked: skip persistence */ }
    this._applyThemeClass();
    // The global Agent Chat window lives outside this element (document.body
    // shadow host) and cannot see its class change; tell it to re-resolve.
    window.dispatchEvent(new CustomEvent("phx-theme-changed"));
    this._render();
  }

  private _render() {
    // Strings arrive from the admin API after boot, so hold the first paint
    // until they do rather than flashing raw keys. Same shape as the hass guard.
    if (this._root && this._hass && isI18nReady()) {
      this._root.render(
        <PhoenixApp
          key={this._prevUserId ?? "unidentified"}
          hass={this._hass}
          narrow={this._narrow}
          theme={this._theme}
          onThemeChange={(t) => this._setTheme(t)}
          language={this._language}
          onLanguageChange={(lang) => this._setLanguage(lang)}
        />
      );
    }
  }
}

if (!customElements.get("phoenix-mcp-panel")) {
  customElements.define("phoenix-mcp-panel", PhoenixPanelElement);
}
