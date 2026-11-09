/**
 * Cancel on a click elsewhere: calls `onAway` when a pointer goes down outside
 * an element, so an in-place confirmation never holds the workspace until its
 * own Cancel is pressed. The press still does what it was
 * for. A press on a scrollbar does not count, so a long window can be scrolled,
 * nor does one on `opener`, the control that toggles the element itself.
 *
 * `on: "click"` waits for the click rather than the press: closing something
 * that sits above other controls moves them up, and a click whose press already
 * moved the control away from the pointer lands on something else (a
 * provider row below an open account could not be chosen).
 *
 * A touch always waits for the click: a finger that goes down to scroll the
 * page also starts with a press, and the box it was scrolling towards closed
 * under it. A scroll ends
 * without a click, so it no longer counts as a click elsewhere.
 *
 * Opening and using a dropdown inside the element never counts: its list is
 * drawn outside the element (ui/SelectPopup.tsx).
 */

import { useEffect, useRef, type RefObject } from "react";

/** Whether the press landed on the element's own scrollbar rather than its content. */
function onScrollbar(el: Element, e: MouseEvent): boolean {
  const r = el.getBoundingClientRect();
  const right = el.clientWidth > 0 && e.clientX >= r.left + el.clientLeft + el.clientWidth && e.clientX <= r.right;
  const bottom = el.clientHeight > 0 && e.clientY >= r.top + el.clientTop + el.clientHeight && e.clientY <= r.bottom;
  return right || bottom;
}

export function useClickAway(ref: RefObject<HTMLElement | null>, onAway: () => void, active = true, opener?: RefObject<HTMLElement | null>,
  on: "pointerdown" | "click" = "pointerdown"): void {
  const latest = useRef(onAway);
  latest.current = onAway;
  useEffect(() => {
    const el = ref.current;
    if (!active || !el) return;
    const doc = el.ownerDocument;
    let touched = false;
    const away = (e: MouseEvent) => {
      // The path crosses shadow roots, so a press inside the element counts as inside from the document too.
      const path = e.composedPath();
      if (path.includes(el) || (opener?.current && path.includes(opener.current))) return;
      // A dropdown's list draws outside the element, over its dropdown: the press that opened it
      // ends on the list, and a choice is made there. Neither is a click elsewhere.
      if (el.querySelector("select[data-phx-open]")) return;
      if (path.some((n) => n instanceof Element && n.classList.contains("phx-select-list"))) return;
      const target = path[0];
      if (target instanceof Element && onScrollbar(target, e)) return;
      latest.current();
    };
    const down = (e: MouseEvent) => {
      touched = (e as PointerEvent).pointerType === "touch";
      if (!touched) away(e);
    };
    const click = (e: MouseEvent) => {
      if (!touched) return;
      touched = false;
      away(e);
    };
    if (on === "click") {
      doc.addEventListener("click", away, true);
      return () => doc.removeEventListener("click", away, true);
    }
    doc.addEventListener("pointerdown", down, true);
    doc.addEventListener("click", click, true);
    return () => {
      doc.removeEventListener("pointerdown", down, true);
      doc.removeEventListener("click", click, true);
    };
  }, [ref, active, opener, on]);
}
