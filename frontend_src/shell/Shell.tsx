/**
 * The workspace shell: toolbar, one window workspace per destination, and the
 * panel-only Agent Chat window. Desktop and phone layouts are chosen from the
 * panel's own width and remembered separately.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AgentCliWindow } from "../components/AgentCliWindow";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { t } from "../i18n";
import { ContextMenuHost, MenuRootContext, useContextMenu, type MenuEntry } from "../ui/Menu";
import { WorkspaceController } from "./dock/controller";
import { WorkspaceDock } from "./dock/WorkspaceDock";
import { usePanelData } from "./PanelData";
import { PhoneStack } from "./PhoneStack";
import { DEFAULT_TOOLBAR, readRecord, writeRecord, WORKSPACE_IDS, type Env, type ShellRecord, type ToolbarState, type WorkspaceId } from "./storage";
import { Toolbar, workspaceMenu } from "./Toolbar";
import { ToolRegistry, ToolRegistryContext } from "./tools";
import { LogSelection, LogSelectionContext } from "./logDetails";
import { ATTENTION_WINDOW, DOCK_COMPONENTS, EmptyWorkspace, WORKSPACES, WorkspaceVisibleContext, windowAvailable, windowTitle } from "./workspaces";
import { AccessKeyDetailProvider } from "../views/AccessKeyDetail";

/** Below this panel width the phone stack is used; the gap between the two bounds stops flicker. */
const PHONE_BELOW = 680;
const DESKTOP_ABOVE = 720;
/** Arrangements are written this long after the last change. */
const SAVE_DELAY_MS = 1000;
const TRANSIENT = new Set([ATTENTION_WINDOW]);

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

  // A record written by a newer build reads as null: arrange from defaults and never write over it.
  const record = useMemo<ShellRecord | null>(() => readRecord(userId, env), [userId, env]);
  // A different user or environment starts from fresh controllers and its own record.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const controllers = useMemo(() => new Map<WorkspaceId, WorkspaceController>(), [userId, env]);
  const controllerFor = useCallback((id: WorkspaceId): WorkspaceController => {
    let controller = controllers.get(id);
    if (!controller) {
      const def = WORKSPACES[id];
      controller = new WorkspaceController(id, def.windows, def.arrange, windowTitle, { transient: TRANSIENT, folded: def.folded, conditional: def.conditional, heights: def.heights });
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

  // Save shortly after the last change, and at once when the page is hidden or the shell goes away.
  const saveTimer = useRef<number | undefined>(undefined);
  const workspaceRef = useRef(d.workspace);
  workspaceRef.current = d.workspace;
  const flush = useCallback(() => {
    window.clearTimeout(saveTimer.current);
    saveTimer.current = undefined;
    if (!record) return;
    for (const [id, controller] of controllers) {
      const layout = controller.layout();
      if (layout) record.workspaces[id] = layout;
    }
    record.lastWorkspace = workspaceRef.current;
    record.toolbar = toolbarRef.current;
    writeRecord(userId, env, record);
  }, [controllers, env, record, userId]);
  const scheduleSave = useCallback(() => {
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(flush, SAVE_DELAY_MS);
  }, [flush]);
  useEffect(() => { scheduleSave(); }, [d.workspace, toolbar, scheduleSave]);
  useEffect(() => {
    const onHide = () => { if (document.visibilityState === "hidden") flush(); };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", flush);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", flush);
      if (saveTimer.current !== undefined) flush();
    };
  }, [flush]);

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
    <LogSelectionContext.Provider value={logSelection}>
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
        <WorkspaceArea menu={env === "desktop" ? () => workspaceMenu(controllerFor(active)) : null}>
          {env === "desktop"
            ? WORKSPACE_IDS.filter((id) => visited.has(id)).map((id) => (
              <WorkspaceVisibleContext.Provider key={`${userId}:${id}`} value={id === active}>
                <WorkspaceDock
                  controller={controllerFor(id)}
                  components={DOCK_COMPONENTS}
                  watermark={EmptyWorkspace}
                  saved={record?.workspaces[id]}
                  visible={id === active}
                  onLayoutChange={scheduleSave}
                  workspaceMenu={() => workspaceMenu(controllerFor(id))}
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
      </div>
    </AccessKeyDetailProvider>
    </LogSelectionContext.Provider>
    </ToolRegistryContext.Provider>
    </ContextMenuHost>
    </MenuRootContext.Provider>
  );
}
