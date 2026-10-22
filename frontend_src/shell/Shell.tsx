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
import { t } from "../i18n";
import { ContextMenuHost, MenuRootContext, useContextMenu, type MenuEntry } from "../ui/Menu";
import { Notifications, NotificationsContext, Toasts } from "../ui/notify";
import { WorkspaceController } from "./dock/controller";
import { WorkspaceDock } from "./dock/WorkspaceDock";
import { usePanelData } from "./PanelData";
import { PhoneStack } from "./PhoneStack";
import { DEFAULT_TOOLBAR, readRecord, writeRecord, WORKSPACE_IDS, type Env, type ShellRecord, type ToolbarState, type WorkspaceId } from "./storage";
import { RemoteRecord, shouldAdopt, writerId, type Section, type UserDataConnection } from "./sync";
import { Toolbar, WorkspaceActionsContext, workspaceMenu, type WorkspaceActions } from "./Toolbar";
import { ToolRegistry, ToolRegistryContext } from "./tools";
import { LogSelection, LogSelectionContext } from "./logDetails";
import { ATTENTION_WINDOW, DOCK_COMPONENTS, EmptyWorkspace, WORKSPACES, WorkspaceVisibleContext, renderWindow, windowAvailable, windowTitle } from "./workspaces";
import { AccessKeyDetailProvider } from "../views/AccessKeyDetail";

/** Below this panel width the phone stack is used; the gap between the two bounds stops flicker. */
const PHONE_BELOW = 680;
const DESKTOP_ABOVE = 720;
/** Arrangements are written this long after the last change. */
const SAVE_DELAY_MS = 1000;
const TRANSIENT = new Set([ATTENTION_WINDOW]);

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

/** Every section a record holds, to send a whole record to Home Assistant. */
function allSections(record: ShellRecord): Section[] {
  return [
    "toolbar", "lastWorkspace",
    ...Object.keys(record.workspaces).map((id) => `ws:${id}` as Section),
    ...Object.keys(record.myDefaults ?? {}).map((id) => `my:${id}` as Section),
  ];
}

function useEnvironment(root: React.RefObject<HTMLElement | null>): Env {
  const [env, setEnv] = useState<Env>(() => (window.innerWidth < (PHONE_BELOW + DESKTOP_ABOVE) / 2 ? "mobile" : "desktop"));
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const decide = (width: number) => setEnv((current) => {
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
  const root = useRef<HTMLDivElement | null>(null);
  const [rootEl, setRootEl] = useState<HTMLDivElement | null>(null);
  const env = useEnvironment(root);
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
  // A different user or environment, or an adopted record, starts from fresh controllers.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const controllers = useMemo(() => new Map<WorkspaceId, WorkspaceController>(), [store.generation]);
  const controllerFor = useCallback((id: WorkspaceId): WorkspaceController => {
    let controller = controllers.get(id);
    if (!controller) {
      const def = WORKSPACES[id];
      controller = new WorkspaceController(id, def.windows, def.arrange, windowTitle, { transient: TRANSIENT, folded: def.folded, conditional: def.conditional, heights: def.heights, pinned: def.pinned });
      controllers.set(id, controller);
    }
    return controller;
  }, [controllers]);

  const registry = useMemo(() => new ToolRegistry(), []);
  // Selecting a log record shows it in Log Details, opening or unfolding that window first.
  const logSelection = useMemo(() => new LogSelection(), []);
  logSelection.onSelect = () => {
    if (env === "desktop") controllerFor("logs").ensureOpen("logDetails");
    requestAnimationFrame(() => root.current?.querySelector('[data-phx-window="logDetails"]')?.scrollIntoView?.({ block: "nearest" }));
  };

  // A jump to a permission (from Permission Summary) brings the Permissions tree into view.
  const revealPermissions = useCallback(() => {
    if (env === "desktop") controllerFor("accessKeys").ensureOpen("permissions");
  }, [env, controllerFor]);

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
  const workspaceRef = useRef(d.workspace);
  workspaceRef.current = d.workspace;
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
      } else if (section === "lastWorkspace") {
        if (rec.lastWorkspace !== workspaceRef.current) { rec.lastWorkspace = workspaceRef.current; changed.push(section); }
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

  // The toolbar and the visible workspace are part of the record.
  useEffect(() => {
    if (!same(toolbar, recordRef.current?.toolbar ?? DEFAULT_TOOLBAR)) markChanged(["toolbar"]);
  }, [toolbar, markChanged]);
  const lastSeenWorkspace = useRef(d.workspace);
  useEffect(() => {
    if (lastSeenWorkspace.current === d.workspace) return;
    lastSeenWorkspace.current = d.workspace;
    markChanged(["lastWorkspace", ...WORKSPACE_IDS.filter((id) => controllers.has(id)).map((id) => `ws:${id}` as Section)], true);
  }, [d.workspace, markChanged, controllers]);

  // Toasts and this session's notification history, shared by every workspace.
  const notifications = useMemo(() => new Notifications(), []);
  const notify = useCallback((text: string) => notifications.notify(text), [notifications]);

  // Restore Panel Default Settings asks first, inside the workspace area.
  const [confirmDefaults, setConfirmDefaults] = useState(false);
  const restorePanelDefaults = useCallback(() => {
    setConfirmDefaults(false);
    const rec = recordRef.current;
    setToolbar(DEFAULT_TOOLBAR);
    for (const controller of controllers.values()) controller.restoreOriginal();
    const sections: Section[] = ["toolbar"];
    for (const id of WORKSPACE_IDS) {
      sections.push(`ws:${id}`);
      if (rec && !controllers.has(id)) delete rec.workspaces[id];
    }
    toolbarRef.current = DEFAULT_TOOLBAR;
    markChanged(sections, true);
    if (rec) commit(WORKSPACE_IDS.filter((id) => !controllers.has(id)).map((id) => `ws:${id}` as Section), true);
    notify(t("workspace.panelDefaultsRestored"));
  }, [commit, controllers, markChanged, notify]);

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
      controller.load(saved);
      markChanged([`ws:${id}`], true);
      notify(t("workspace.myDefaultRestored"));
    },
    restoreOriginal: (controller) => {
      controller.restoreOriginal();
      markChanged([`ws:${controller.id as WorkspaceId}`], true);
    },
    restorePanelDefaults: () => setConfirmDefaults(true),
  }), [commit, markChanged, notify]);

  // Window titles follow the panel language.
  const languageSignature = t("shell.tabAccessKeys") + t("workspace.logs");
  useEffect(() => {
    for (const controller of controllers.values()) controller.retitle();
  }, [controllers, languageSignature]);

  // Needs Attention appears in the visible workspace while items need checking (or a
  // notification asked for it). Closing it hides it until more items arrive.
  const attentionCount = d.attention.items.length;
  const [dismissedAt, setDismissedAt] = useState<number | null>(null);
  const showAttention = (attentionCount > 0 || d.attentionRequested) && (dismissedAt === null || attentionCount > dismissedAt);
  useEffect(() => { if (attentionCount === 0) setDismissedAt(null); }, [attentionCount]);
  const dismissAttention = d.dismissAttentionRequest;
  const settings = d.settings;
  useEffect(() => {
    for (const [id, controller] of controllers) {
      // Presets appears only while its setting is on.
      controller.availability = (w) => windowAvailable(w, settings);
      controller.syncAvailable();
      controller.onTransientClose = () => { setDismissedAt(attentionCount); dismissAttention(); };
      if (env === "desktop" && id === d.workspace && showAttention) controller.showTransient(ATTENTION_WINDOW);
      else controller.hideTransient(ATTENTION_WINDOW);
    }
  });

  const s = d.settings;
  const active = d.workspace;
  const dock = env === "mobile" ? "top" : toolbar.dock;
  return (
    <MenuRootContext.Provider value={rootEl}>
    <ContextMenuHost>
    <ToolRegistryContext.Provider value={registry}>
    <NotificationsContext.Provider value={notifications}>
    <LogSelectionContext.Provider value={logSelection}>
    <WorkspaceActionsContext.Provider value={actions}>
    <AccessKeyDetailProvider
      accessKeyId={d.accessKeyView.name === "detail" ? d.accessKeyView.accessKeyId : null}
      onDeselect={d.goBack}
      onRefresh={d.refreshAccessKeys}
      onReveal={revealPermissions}
      presetsEnabled={s?.access_key_presets_enabled ?? false}
      esphomeIntegration={s ? !!s.esphome_integration : null}
      esphomeBuilder={s ? !!s.esphome_builder : null}
    >
      <div ref={(el) => { root.current = el; setRootEl(el); }} className={`phx-shell phx-env-${env}`} data-toolbar={dock}>
        <h1 className="sr-only">{t("shell.srTitle")}</h1>
        <Toolbar controller={env === "desktop" && visited.has(active) ? controllerFor(active) : null} shellRoot={root} env={env}
          state={toolbar} onChange={setToolbar} />
        <WorkspaceArea menu={env === "desktop" ? () => workspaceMenu(controllerFor(active), actions) : null}>
          {confirmDefaults && (
            <div className="banner banner-warning phx-shell-confirm" role="alertdialog" aria-labelledby="phx-defaults-question">
              <p id="phx-defaults-question">{t("workspace.restorePanelDefaultsConfirm")}</p>
              <div className="phx-shell-confirm-actions">
                <button type="button" className="btn btn-outline btn-sm" onClick={() => setConfirmDefaults(false)}>{t("common.cancel")}</button>
                <button type="button" className="btn btn-primary btn-sm" autoFocus onClick={restorePanelDefaults}>{t("workspace.restoreDefaultsAction")}</button>
              </div>
            </div>
          )}
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
            : <PhoneStack workspace={active} showAttention={showAttention} settings={s} />}
        </WorkspaceArea>

        {/* Panel-only Agent Chat window. Not rendered in global-visibility mode (the
            inject module owns the window then), nor while the kill switch is on.
            When global injection is unsupported, __phxAgentChat is absent, so this
            renders as the fallback. */}
        {s && !s.kill_switch && d.agentCliOpen && !(s.agentcli_global && d.globalChatReady) && (
          <ErrorBoundary key={d.agentCliKey}>
            <AgentCliWindow
              key={d.agentCliKey}
              accessKeys={d.accessKeys}
              instances={d.agentCliInstances}
              scrollbackLines={s.agentcli_scrollback_lines}
              initialAccessKeyId={d.accessKeys[0]?.id ?? ""}
              onClose={d.closeAgentCli}
              hass={d.hass}
            />
          </ErrorBoundary>
        )}
        <Toasts center={notifications} />
      </div>
    </AccessKeyDetailProvider>
    </WorkspaceActionsContext.Provider>
    </LogSelectionContext.Provider>
    </NotificationsContext.Provider>
    </ToolRegistryContext.Provider>
    </ContextMenuHost>
    </MenuRootContext.Provider>
  );
}
