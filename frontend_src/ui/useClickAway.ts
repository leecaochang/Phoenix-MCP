/**
 * Cancel on a click elsewhere: calls `onAway` when a pointer goes down outside
 * an element, so an in-place confirmation never holds the workspace until its
 * own Cancel is pressed (user, 2026-10-28). The press still does what it was
 * for. A press on a scrollbar does not count, so a long window can be scrolled,
 * nor does one on `opener`, the control that toggles the element itself.
 *
 * `on: "click"` waits for the click rather than the press: closing something
 * that sits above other controls moves them up, and a click whose press already
 * moved the control away from the pointer lands on something else (user,
 * 2026-10-31, a provider row below an open account could not be chosen).
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
    const down = (e: MouseEvent) => {
      // The path crosses shadow roots, so a press inside the element counts as inside from the document too.
      const path = e.composedPath();
      if (path.includes(el) || (opener?.current && path.includes(opener.current))) return;
      const target = path[0];
      if (target instanceof Element && onScrollbar(target, e)) return;
      latest.current();
    };
    doc.addEventListener(on, down, true);
    return () => doc.removeEventListener(on, down, true);
  }, [ref, active, opener, on]);
}
