/** One workspace's Dockview instance, with Phoenix's window rules and title bars. */

import { useEffect, useMemo, useRef, type FunctionComponent, type MouseEvent as ReactMouseEvent } from "react";
import { DockviewReact, type DockviewOptions, type DockviewReadyEvent, type IDockviewPanelProps, type IWatermarkPanelProps } from "dockview-react";
import { dimWhileDragging, edgeResolver, floatingMouseMove, refuseTabDrops } from "./drop";
import { MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH } from "./fold";
import { ControllerContext, TitleTab, WindowControls, WindowGhost, windowMenuEntries } from "./TitleBar";
import { t } from "../../i18n";
import { useContextMenu, type MenuEntry } from "../../ui/Menu";
import type { WorkspaceController, WorkspaceLayout } from "./controller";

/** Thickness of the edge highlight showing where a dragged window will dock. */
const DROP_LINE = 4;

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
  dropOverlayModel: () => ({ size: { type: "pixels", value: DROP_LINE } }),
  dndEdges: { activationSize: { type: "pixels", value: 10 }, size: { type: "pixels", value: DROP_LINE } },
  theme: { name: "phoenix", className: "phx-dock", dndPanelOverlay: "group", dndOverlayMounting: "absolute" },
  // Dockview's announcements and tab menu are English-only; Phoenix supplies its own controls.
  announcements: false,
  getTabContextMenuItems: () => [],
} satisfies Partial<DockviewOptions>;

export function WorkspaceDock({ controller, components, watermark, saved, visible, onLayoutChange, workspaceMenu }: {
  controller: WorkspaceController;
  components: Record<string, FunctionComponent<IDockviewPanelProps>>;
  watermark: FunctionComponent<IWatermarkPanelProps>;
  saved: WorkspaceLayout | undefined;
  visible: boolean;
  /** Called after the arrangement settles; the caller decides when to store it. */
  onLayoutChange: () => void;
  /** Items for a right-click on empty workspace space. */
  workspaceMenu: () => MenuEntry[];
}) {
  const showMenu = useContextMenu();
  const host = useRef<HTMLDivElement>(null);
  const resolver = useMemo(() => edgeResolver(() => {
    const node = host.current?.getRootNode();
    return node instanceof ShadowRoot || node instanceof Document ? node : null;
  }), []);
  const onChange = useRef(onLayoutChange);
  onChange.current = onLayoutChange;
  const disposers = useRef<Array<() => void>>([]);

  const onReady = (event: DockviewReadyEvent) => {
    const api = event.api;
    controller.api = api;
    const constrain = api.onDidAddGroup((group) => {
      group.api.setConstraints({ minimumWidth: MIN_WINDOW_WIDTH, minimumHeight: MIN_WINDOW_HEIGHT });
    });
    const guards = [refuseTabDrops(api), dimWhileDragging(api)];
    controller.load(saved);
    // The toolbar's tools follow the focused window.
    const focus = api.onDidActiveGroupChange((group) => controller.setActiveWindow(group?.activePanel?.id ?? null));
    controller.setActiveWindow(api.activeGroup?.activePanel?.id ?? null);
    const layout = api.onDidLayoutChange(() => onChange.current());
    const folds = controller.folds.subscribe(() => onChange.current());
    const stopMove = host.current ? floatingMouseMove(api, host.current) : () => {};
    disposers.current = [
      () => constrain.dispose(), () => layout.dispose(), () => focus.dispose(), folds, stopMove, ...guards.map((g) => () => g.dispose()),
    ];
  };

  useEffect(() => () => {
    for (const dispose of disposers.current) dispose();
    disposers.current = [];
    controller.api = null;
  }, [controller]);

  // Right-click on a title bar opens that window's menu; on empty workspace space, the workspace
  // menu. Window content keeps the browser's own menu, so copying text still works there.
  const onContextMenu = (e: ReactMouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    if (!showMenu || target.closest("input, textarea, select, [contenteditable='true']")) return;
    if (target.closest(".dv-tabs-and-actions-container")) {
      const group = controller.api?.groups.find((g) => g.element.contains(target));
      if (group) showMenu(e, t("workspace.windowMenu"), windowMenuEntries(controller, group, { full: true }));
      return;
    }
    if (target.closest(".dv-content-container, .dv-render-overlay")) return;
    showMenu(e, t("workspace.options"), workspaceMenu());
  };

  return (
    <div ref={host} className="phx-workspace" hidden={!visible} data-workspace={controller.id} onContextMenu={onContextMenu}>
      <ControllerContext.Provider value={controller}>
        <DockviewReact
          {...DOCK_OPTIONS}
          dropPositionResolver={resolver}
          components={components}
          defaultTabComponent={TitleTab}
          rightHeaderActionsComponent={WindowControls}
          groupDragGhostComponent={WindowGhost}
          watermarkComponent={watermark}
          onReady={onReady}
        />
      </ControllerContext.Provider>
    </div>
  );
}
