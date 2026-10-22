/**
 * One workspace on screen: a pinned band along the top, the scrolling grid, a
 * pinned band along the bottom, and a layer of floating windows fixed above
 * them. Each is its own Dockview instance with Phoenix's window rules and
 * title bars. Window content is rendered once here and placed into whichever
 * instance holds the window, so moving a window between them keeps its state.
 */

import { useEffect, useMemo, useRef, useSyncExternalStore, type FunctionComponent, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { DockviewReact, type DockviewApi, type DockviewOptions, type DockviewReadyEvent, type IDockviewPanelProps, type IWatermarkPanelProps } from "dockview-react";
import { dimWhileDragging, edgeResolver, floatingMouseMove, refuseTabDrops } from "./drop";
import { MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH } from "./fold";
import { WorkspaceCanvas, bandEdgeResizing, pushDownResizing, scrollWhileDragging } from "./scroll";
import { ControllerContext, TitleTab, WindowControls, WindowGhost, windowMenuEntries } from "./TitleBar";
import { t } from "../../i18n";
import { useContextMenu, type MenuEntry } from "../../ui/Menu";
import type { HostId, WorkspaceController, WorkspaceLayout } from "./controller";

/** Thickness of the edge highlight showing where a dragged window will dock. */
const DROP_LINE = 4;
/** A pinned band never takes more than this share of the workspace's height, so the grid keeps room between two bands; beyond it the band scrolls. */
const BAND_SHARE = 0.35;

/**
 * Free-edition options only. Paid features (keyboard docking and navigation,
 * layout history, edge groups, pinned tabs, smart guides, the drop compass)
 * are never enabled; a test pins this object.
 */
export const DOCK_OPTIONS = {
  // One window per group: the single tab is the window title, the empty space beside it drags the window.
  singleTabMode: "default",
  floatingGroupDragHandle: "tabbar",
  disableTabsOverflowList: true,
  // Keep every window's DOM, scroll position and drafts while it is hidden, maximized over or moved.
  defaultRenderer: "always",
  // Pointer-driven drag and drop: the same in every browser, with Phoenix's own window outline.
  dndStrategy: "pointer",
  // The workspace sizes Dockview itself, on a canvas that may be taller than the screen.
  disableAutoResizing: true,
  dropOverlayModel: () => ({ size: { type: "pixels", value: DROP_LINE } }),
  dndEdges: { activationSize: { type: "pixels", value: 10 }, size: { type: "pixels", value: DROP_LINE } },
  theme: { name: "phoenix", className: "phx-dock", dndPanelOverlay: "group", dndOverlayMounting: "absolute" },
  // Dockview's announcements and tab menu are English-only; Phoenix supplies its own controls.
  announcements: false,
  getTabContextMenuItems: () => [],
} satisfies Partial<DockviewOptions>;

const HOSTS: readonly HostId[] = ["top", "main", "bottom", "float"];

function NoWatermark() { return null; }

/** Every open window's content, rendered once and drawn into the element its host shows. */
function WindowContents({ controller, render }: { controller: WorkspaceController; render: (id: string) => ReactNode }) {
  useSyncExternalStore(controller.subscribe, controller.getVersion);
  return <>{controller.windowOrder().map((id) => createPortal(render(id), controller.contentElement(id), id))}</>;
}

export function WorkspaceDock({ controller, components, renderWindow, watermark, saved, visible, onLayoutChange, workspaceMenu }: {
  controller: WorkspaceController;
  /** Dockview components: a slot per window id that shows the window's content. */
  components: Record<string, FunctionComponent<IDockviewPanelProps>>;
  /** A window's content, rendered once per workspace. */
  renderWindow: (id: string) => ReactNode;
  watermark: FunctionComponent<IWatermarkPanelProps>;
  saved: WorkspaceLayout | undefined;
  visible: boolean;
  /** Called after the arrangement settles; the caller decides when to store it. */
  onLayoutChange: () => void;
  /** Items for a right-click on empty workspace space. */
  workspaceMenu: () => MenuEntry[];
}) {
  const showMenu = useContextMenu();
  const frame = useRef<HTMLDivElement>(null);
  const elements = useRef<Partial<Record<HostId, HTMLDivElement | null>>>({});
  const apis = useRef<Partial<Record<HostId, DockviewApi>>>({});
  const resolver = useMemo(() => edgeResolver(() => {
    const node = frame.current?.getRootNode();
    return node instanceof ShadowRoot || node instanceof Document ? node : null;
  }), []);
  const onChange = useRef(onLayoutChange);
  onChange.current = onLayoutChange;
  const disposers = useRef<Array<() => void>>([]);

  // Wire the workspace up once all four Dockview instances exist.
  const setUp = () => {
    const { main, top, bottom, float } = apis.current;
    const mainEl = elements.current.main;
    if (!main || !top || !bottom || !float || !mainEl || !frame.current) return;
    const collapsed = (id: string) => controller.folds.collapsedHeight(id) !== undefined;
    const bandMax = () => Math.round((frame.current?.clientHeight ?? 0) * BAND_SHARE);
    // The grid scrolls: windows keep their heights and the canvas grows to hold them.
    const mainCanvas = new WorkspaceCanvas(main, mainEl, collapsed);
    controller.attach("main", main, mainCanvas);
    const bandCanvases: WorkspaceCanvas[] = [];
    for (const band of ["top", "bottom"] as const) {
      const el = elements.current[band];
      const api = apis.current[band]!;
      const canvas = el ? new WorkspaceCanvas(api, el, collapsed, bandMax) : null;
      if (canvas) bandCanvases.push(canvas);
      controller.attach(band, api, canvas);
    }
    controller.attach("float", float);
    const out: Array<() => void> = [];

    // A window dropped somewhere else keeps its height, and so does every other window.
    const settleAfterDrop = () => {
      controller.dragStarted();
      const done = () => {
        for (const type of ["pointerup", "pointercancel", "dragend"]) window.removeEventListener(type, done, true);
        requestAnimationFrame(() => controller.layoutSettled());
      };
      for (const type of ["pointerup", "pointercancel", "dragend"]) window.addEventListener(type, done, true);
    };
    for (const id of HOSTS) {
      const api = apis.current[id]!;
      const subs = [
        api.onDidAddGroup((group) => group.api.setConstraints({ minimumWidth: MIN_WINDOW_WIDTH, minimumHeight: MIN_WINDOW_HEIGHT })),
        // The toolbar's tools follow the focused window.
        api.onDidActiveGroupChange((group) => { if (group?.activePanel) controller.setActiveWindow(group.activePanel.id); }),
        api.onDidLayoutChange(() => onChange.current()),
        api.onDidAddPanel(() => controller.changed()),
        api.onDidRemovePanel(() => controller.changed()),
        api.onWillDragGroup(settleAfterDrop),
        api.onWillDragPanel(settleAfterDrop),
        dimWhileDragging(api),
        refuseTabDrops(api),
      ];
      out.push(...subs.map((s) => () => s.dispose()));
    }
    // Floating windows only move; nothing docks inside the floating layer.
    const noDocking = float.onWillShowOverlay((e) => e.preventDefault());
    out.push(() => noDocking.dispose());
    const floatEl = elements.current.float;
    if (floatEl) out.push(floatingMouseMove(float, floatEl), floatingMouseMove(main, mainEl));

    controller.load(saved);
    controller.setActiveWindow(main.activeGroup?.activePanel?.id ?? null);

    out.push(pushDownResizing(main, mainCanvas, () => onChange.current()));
    for (const band of ["top", "bottom"] as const) {
      const el = elements.current[band];
      const canvas = controller.canvasOf(band);
      if (!el || !canvas) continue;
      out.push(pushDownResizing(apis.current[band]!, canvas, () => onChange.current()));
      const edge = frame.current.querySelector<HTMLElement>(`.phx-band-edge-${band}`);
      if (edge) out.push(bandEdgeResizing(apis.current[band]!, canvas, edge, band, () => onChange.current()));
    }
    out.push(scrollWhileDragging(main, mainEl));
    const maximized = main.onDidMaximizedGroupChange(() => controller.maximizedChanged());
    out.push(() => maximized.dispose());
    if (typeof ResizeObserver !== "undefined") {
      const refitMain = new ResizeObserver(() => mainCanvas.refit());
      refitMain.observe(mainEl);
      const refitBands = new ResizeObserver(() => { for (const c of bandCanvases) c.refit(); });
      refitBands.observe(frame.current);
      out.push(() => refitMain.disconnect(), () => refitBands.disconnect());
    }
    const folds = controller.folds.subscribe(() => onChange.current());
    out.push(folds);
    disposers.current = out;
  };

  const ready = (id: HostId) => (event: DockviewReadyEvent) => {
    apis.current[id] = event.api;
    if (HOSTS.every((h) => apis.current[h])) setUp();
  };
  // Stable handlers, so Dockview never sees a new onReady.
  const handlers = useMemo(() => Object.fromEntries(HOSTS.map((h) => [h, ready(h)])) as Record<HostId, (e: DockviewReadyEvent) => void>, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => {
    for (const dispose of disposers.current) dispose();
    disposers.current = [];
    for (const h of HOSTS) controller.attach(h, null);
  }, [controller]);

  // Right-click on a title bar opens that window's menu; on empty workspace space, the workspace
  // menu. Window content keeps the browser's own menu, so copying text still works there.
  const onContextMenu = (e: ReactMouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    if (!showMenu || target.closest("input, textarea, select, [contenteditable='true']")) return;
    if (target.closest(".dv-tabs-and-actions-container")) {
      const group = controller.groupContaining(target);
      if (group) showMenu(e, t("workspace.windowMenu"), windowMenuEntries(controller, group, { full: true }));
      return;
    }
    if (target.closest(".dv-content-container, .dv-render-overlay, .phx-float-layer")) return;
    showMenu(e, t("workspace.options"), workspaceMenu());
  };

  const dock = (id: HostId, extra: { disableAutoResizing?: boolean; watermarkComponent?: FunctionComponent<IWatermarkPanelProps> } = {}) => (
    <DockviewReact
      {...DOCK_OPTIONS}
      dropPositionResolver={resolver}
      components={components}
      defaultTabComponent={TitleTab}
      rightHeaderActionsComponent={WindowControls}
      groupDragGhostComponent={WindowGhost}
      watermarkComponent={NoWatermark}
      {...extra}
      onReady={handlers[id]}
    />
  );

  return (
    <div ref={frame} className="phx-workspace-frame" hidden={!visible} data-workspace={controller.id} onContextMenu={onContextMenu}>
      <ControllerContext.Provider value={controller}>
        <div ref={(el) => { elements.current.top = el; }} className="phx-band phx-band-top phx-canvas-host phx-band-empty" data-band="top">{dock("top")}</div>
        <div className="phx-band-edge phx-band-edge-top" aria-hidden="true" />
        <div ref={(el) => { elements.current.main = el; }} className="phx-workspace phx-canvas-host" hidden={!visible} data-workspace={controller.id}>
          {dock("main", { watermarkComponent: watermark })}
        </div>
        <div className="phx-band-edge phx-band-edge-bottom" aria-hidden="true" />
        <div ref={(el) => { elements.current.bottom = el; }} className="phx-band phx-band-bottom phx-canvas-host phx-band-empty" data-band="bottom">{dock("bottom")}</div>
        <div ref={(el) => { elements.current.float = el; }} className="phx-float-layer">{dock("float", { disableAutoResizing: false })}</div>
        <WindowContents controller={controller} render={renderWindow} />
      </ControllerContext.Provider>
    </div>
  );
}
