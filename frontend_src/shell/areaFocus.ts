/**
 * F6 and Shift+F6 move focus between the panel's areas: the toolbar, each
 * window in the order the workspace shows them, the Guide and Agent Chat
 * (user, 2026-11-01). Tab still moves between the controls inside an area.
 * The convention follows browsers, Windows applications and the ARIA
 * Authoring Practices for moving between an application's panes.
 */

import { useEffect, useRef, type RefObject } from "react";

/** One area F6 can reach: the element that holds it and the element that takes focus first. */
export interface FocusArea {
  key: string;
  holds: (el: Element) => boolean;
  target: HTMLElement;
}

function shown(el: Element | null): el is HTMLElement {
  if (!(el instanceof HTMLElement) || el.closest("[hidden]")) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

/** Top to bottom, then left to right, as the eye reads the workspace. */
function readingOrder(a: Element, b: Element): number {
  const ra = a.getBoundingClientRect();
  const rb = b.getBoundingClientRect();
  return Math.abs(ra.top - rb.top) > 4 ? ra.top - rb.top : ra.left - rb.left;
}

/** The areas F6 cycles through now, in order. */
export function focusAreas(root: ParentNode): FocusArea[] {
  const areas: FocusArea[] = [];
  const toolbar = root.querySelector(".phx-toolbar");
  if (shown(toolbar)) areas.push({ key: "toolbar", holds: (el) => toolbar.contains(el), target: toolbar });

  // A window is marked by its title bar's menu button; its content, when unfolded, takes focus.
  const frame = root.querySelector(".phx-workspace-frame:not([hidden])");
  const menus = [...(frame?.querySelectorAll("[data-phx-window-menu]") ?? [])].filter(shown).sort(readingOrder);
  for (const menu of menus) {
    const id = (menu as HTMLElement).dataset.phxWindowMenu;
    if (!id) continue;
    const header = menu.closest(".dv-groupview") ?? menu.parentElement!;
    const body = frame!.querySelector(`.phx-window-body[data-phx-window="${id}"]`);
    areas.push({
      key: `window:${id}`,
      holds: (el) => header.contains(el) || !!body?.contains(el),
      target: shown(body) ? body : (menu as HTMLElement),
    });
  }
  // Phones stack their windows; each is one area.
  for (const win of root.querySelectorAll(".phx-phone-stack > .phx-phone-window")) {
    if (!shown(win)) continue;
    const body = win.querySelector(".phx-phone-body");
    const id = body?.getAttribute("data-phx-window") ?? win.getAttribute("aria-label") ?? "";
    areas.push({ key: `window:${id}`, holds: (el) => win.contains(el), target: shown(body) ? body : (win as HTMLElement) });
  }

  const guide = root.querySelector("section.phx-guide:not(.is-popped)");
  if (shown(guide)) areas.push({ key: "guide", holds: (el) => guide.contains(el), target: guide });
  const chat = root.querySelector(".agentcli-window:not(.agentcli-docked)");
  if (shown(chat)) areas.push({ key: "chat", holds: (el) => chat.contains(el), target: chat });
  return areas;
}

/** The area after (or before) the one holding `active`; from outside every area, the first (or last). */
export function nextArea(areas: FocusArea[], active: Element | null, back: boolean): FocusArea | null {
  if (!areas.length) return null;
  const at = active ? areas.findIndex((a) => a.holds(active)) : -1;
  if (at < 0) return back ? areas[areas.length - 1] : areas[0];
  return areas[(at + (back ? -1 : 1) + areas.length) % areas.length];
}

/** Move focus into an area: where it last was there, else the area itself. */
export function enterArea(area: FocusArea, remembered: Element | undefined): void {
  if (remembered instanceof HTMLElement && !gone(remembered) && area.holds(remembered)) {
    remembered.focus();
    return;
  }
  const target = area.target;
  if (!target.matches("button, a[href], input, select, textarea, [tabindex]")) target.tabIndex = -1;
  target.focus();
}

/** Whether focus left `el` because it went away: removed, hidden, made inert or disabled. */
function gone(el: Element): boolean {
  return !el.isConnected || !!el.closest("[hidden], [inert]") || el.matches(":disabled") || !shown(el);
}

/**
 * Where focus goes when the focused control goes away (a closed window, a decided approval, a
 * confirmation that closed): the area that held it, else the area now in its place, else the
 * first. Left alone, the browser drops focus to the page and a keyboard user starts over.
 */
export function rescueFocus(root: ParentNode, lost: { area: string | null; index: number }, remembered: Map<string, Element>): void {
  const areas = focusAreas(root);
  if (!areas.length) return;
  const same = lost.area ? areas.find((a) => a.key === lost.area) : undefined;
  const area = same ?? areas[Math.min(Math.max(lost.index, 0), areas.length - 1)];
  enterArea(area, remembered.get(area.key));
}

/** Listen for F6 and Shift+F6 inside the panel's root element, and keep focus in the panel when its control goes away. */
export function useAreaKeys(root: RefObject<HTMLElement | null>): void {
  const remembered = useRef(new Map<string, Element>());
  useEffect(() => {
    const el = root.current;
    if (!el) return undefined;
    const scope = el.getRootNode() as Document | ShadowRoot;
    let last: { el: Element; area: string | null; index: number } | null = null;
    const onFocus = (e: FocusEvent) => {
      const target = e.composedPath()[0];
      if (!(target instanceof Element)) return;
      const areas = focusAreas(scope);
      const index = areas.findIndex((a) => a.holds(target));
      const area = index >= 0 ? areas[index] : null;
      if (area) remembered.current.set(area.key, target);
      last = { el: target, area: area?.key ?? null, index };
    };
    // Focus that leaves on purpose lands somewhere; focus whose control went away lands on the page.
    const check = () => {
      if (!last) return;
      const active = scope.activeElement;
      if (active && active !== document.body) return;
      const page = document.activeElement;
      const lost = last;
      last = null;
      if ((page && page !== document.body) || !gone(lost.el)) return;
      rescueFocus(scope, lost, remembered.current);
    };
    const observer = new MutationObserver(check);
    observer.observe(el, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "disabled", "inert"] });
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "F6" || e.ctrlKey || e.altKey || e.metaKey) return;
      const areas = focusAreas(scope);
      const next = nextArea(areas, scope.activeElement, e.shiftKey);
      if (!next) return;
      // The browser uses F6 for its own address bar; inside the panel it moves between areas instead.
      e.preventDefault();
      e.stopPropagation();
      enterArea(next, remembered.current.get(next.key));
    };
    // Nothing focused yet (just after the page loads): the first F6 enters the panel, not the browser.
    const onPageKey = (e: KeyboardEvent) => {
      if (e.key !== "F6" || e.defaultPrevented || document.activeElement !== document.body || !el.isConnected) return;
      onKey(e);
    };
    el.addEventListener("focusin", onFocus);
    el.addEventListener("keydown", onKey);
    document.addEventListener("keydown", onPageKey);
    return () => {
      observer.disconnect();
      el.removeEventListener("focusin", onFocus);
      el.removeEventListener("keydown", onKey);
      document.removeEventListener("keydown", onPageKey);
    };
  }, [root]);
}
