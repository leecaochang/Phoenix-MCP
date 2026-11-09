/**
 * Phoenix's own list for every dropdown in a root: the page keeps a native
 * `<select>` (its value, label, form and Help behavior stay the browser's),
 * and a mouse press or an opening key shows this themed list in place of the
 * browser's. Touch keeps the system picker, which suits a phone better.
 *
 * The list opens with the current choice over the dropdown, so it stays under
 * the pointer, and moves up when the window's bottom edge is near. Keyboard:
 * Up, Down, Home, End, Page Up and Page Down move; typing jumps to a matching
 * choice; Enter or Space chooses; Escape closes and Tab closes and moves on.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal, flushSync } from "react-dom";
import { Icon } from "./Icon";

/** Space kept between the list and the edges of the window it opens in. */
const EDGE = 8;
/** The list's padding plus its border, above the first choice. */
const CHROME = 5;
/** How long the close animation runs before the list is removed. */
const CLOSE_MS = 120;
/** How far the pointer moves after opening before a release chooses. */
const DRAG_SLOP = 4;

interface Choice { value: string; label: string; disabled: boolean }

interface OpenState {
  select: HTMLSelectElement;
  choices: Choice[];
  /** Index of the choice in effect when the list opened, or -1. */
  chosen: number;
  compact: boolean;
  /** Where the pointer pressed to open, for a press-drag-release choice. */
  pressAt: { x: number; y: number } | null;
  /** Counts openings, so each one starts a fresh list. */
  serial: number;
}

// Every root a host serves. Agent Chat can sit inside the shell's root; each dropdown belongs to
// the nearest one, so the two hosts never both answer it.
const hostRoots = new WeakSet<Element>();

function servedBy(select: HTMLSelectElement, root: HTMLElement): boolean {
  for (let el: Element | null = select; el; el = el.parentElement) if (hostRoots.has(el)) return el === root;
  return false;
}

function choicesOf(select: HTMLSelectElement): Choice[] {
  return [...select.options].map((option) => ({
    value: option.value,
    label: option.label || option.text,
    disabled: option.disabled || (option.parentElement instanceof HTMLOptGroupElement && option.parentElement.disabled),
  }));
}

/** A dropdown the list takes over: one of the panel's own, usable now. */
function eligible(target: EventTarget | null): HTMLSelectElement | null {
  const el = target as Element | null;
  if (!el || el.tagName !== "SELECT") return null;
  const select = el as HTMLSelectElement;
  if (!select.classList.contains("input") || select.multiple || select.size > 1) return null;
  return select;
}

function unavailable(select: HTMLSelectElement): boolean {
  return select.disabled || select.getAttribute("aria-disabled") === "true";
}

/** The dropdown's accessible name, for the list that stands in for it. */
function nameOf(select: HTMLSelectElement): string {
  const own = select.getAttribute("aria-label");
  if (own) return own;
  const by = select.getAttribute("aria-labelledby");
  if (by) {
    const root = select.getRootNode() as Document | ShadowRoot;
    const text = by.split(/\s+/).map((id) => root.getElementById?.(id)?.textContent?.trim() ?? "").filter(Boolean).join(" ");
    if (text) return text;
  }
  return select.labels?.[0]?.textContent?.trim() ?? "";
}

/** Choose `value` as if the browser's own list had: the page's change handlers run as usual. */
function commit(select: HTMLSelectElement, value: string) {
  if (select.value === value) return;
  const view = select.ownerDocument.defaultView ?? window;
  const setter = Object.getOwnPropertyDescriptor(view.HTMLSelectElement.prototype, "value")?.set;
  if (setter) setter.call(select, value); else select.value = value;
  select.dispatchEvent(new view.Event("input", { bubbles: true }));
  select.dispatchEvent(new view.Event("change", { bubbles: true }));
}

function nextAvailable(choices: Choice[], from: number, step: 1 | -1): number {
  for (let i = from + step; i >= 0 && i < choices.length; i += step) if (!choices[i].disabled) return i;
  return from;
}

type ListKeys = (e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "preventDefault" | "stopPropagation">) => void;

function SelectList({ state, closing, onClose, onChoose, keysRef }: {
  state: OpenState;
  closing: boolean;
  onClose: (refocus: boolean) => void;
  onChoose: (value: string) => void;
  /** Lets the host pass on a key that reached the dropdown before the list took focus. */
  keysRef: React.MutableRefObject<ListKeys | null>;
}) {
  const { select, choices, chosen, compact } = state;
  const listRef = useRef<HTMLDivElement | null>(null);
  const scrollRef = useRef<HTMLUListElement | null>(null);
  const [active, setActive] = useState(chosen >= 0 && !choices[chosen]?.disabled ? chosen : nextAvailable(choices, -1, 1));
  const [box, setBox] = useState<{ top: number; left: number; minWidth: number; origin: number } | null>(null);
  const [shown, setShown] = useState(false);
  const [chevron, setChevron] = useState<{ top: number; left: number } | null>(null);
  // How far the dropdown has moved since the list opened, when the page scrolls under it.
  const [shift, setShift] = useState({ x: 0, y: 0 });
  const openedAt = useRef<{ top: number; left: number } | null>(null);
  const typed = useRef({ text: "", at: 0 });
  const armed = useRef(false);
  const id = useRef(`phx-select-${Math.random().toString(36).slice(2, 9)}`).current;
  keysRef.current = (e) => { scrollRef.current?.focus({ preventScroll: true }); onKeyDown(e); };
  useEffect(() => () => { keysRef.current = null; }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Place the list so the current choice sits over the dropdown, inside the window.
  useLayoutEffect(() => {
    const list = listRef.current;
    const scroller = scrollRef.current;
    if (!list || !scroller) return;
    const view = select.ownerDocument.defaultView ?? window;
    const anchor = select.getBoundingClientRect();
    const styles = view.getComputedStyle(select);
    const padStart = Number.parseFloat(styles.paddingLeft) || 0;
    const rows = scroller.querySelectorAll<HTMLElement>("[role='option']");
    const row = rows[Math.max(0, chosen)] ?? rows[0];
    const rowPad = row ? Number.parseFloat(view.getComputedStyle(row).paddingLeft) || 0 : 0;
    const height = list.offsetHeight;
    const rowTop = row ? row.offsetTop : 0;
    const rowHeight = row ? row.offsetHeight : 0;
    // The list is as wide as its longest name needs, worked out from the text itself: browsers size
    // a scrolling list before its scrollbar takes room, and each measures a cut-off name differently.
    const labels = [...scroller.querySelectorAll<HTMLElement>(".phx-select-label")];
    const textWidth = (label: HTMLElement) => {
      const range = select.ownerDocument.createRange();
      range.selectNodeContents(label);
      // A test page draws nothing to measure; the label's own scroll width stands in.
      return typeof range.getBoundingClientRect === "function" ? range.getBoundingClientRect().width : label.scrollWidth;
    };
    // The list opens slightly scaled down; text measured on screen is undone by that scale.
    const scale = labels[0] && labels[0].offsetWidth ? labels[0].getBoundingClientRect().width / labels[0].offsetWidth || 1 : 1;
    const widest = Math.max(0, ...labels.map(textWidth)) / scale;
    const rowExtra = row && labels[0] ? rows[0].offsetWidth - labels[0].offsetWidth : 0;
    const scrollbar = scroller.offsetWidth - scroller.clientWidth;
    const chrome = list.offsetWidth - scroller.offsetWidth;
    const width = Math.min(Math.max(list.offsetWidth, Math.ceil(widest + rowExtra + scrollbar + chrome) + 1), view.innerWidth - 2 * EDGE);
    // The choice's text starts where the dropdown's does.
    const left = Math.max(EDGE, Math.min(anchor.left + padStart - CHROME - rowPad, view.innerWidth - width - EDGE));
    const ideal = anchor.top + anchor.height / 2 - CHROME - rowTop - rowHeight / 2;
    const top = Math.max(EDGE, Math.min(ideal, view.innerHeight - height - EDGE));
    // Scrolled so the choice is as near the pointer as the edges allow.
    scroller.scrollTop = Math.max(0, top - ideal);
    const shownRowTop = top + CHROME + rowTop - scroller.scrollTop;
    setBox({ top, left, minWidth: Math.max(width, anchor.width + Math.max(0, anchor.left - left) + 6), origin: shownRowTop - top + rowHeight / 2 });
    // The dropdown's own arrow (12 px, 10 px in from its edge) hides while open; this one turns in its place.
    setChevron({ top: anchor.top + anchor.height / 2, left: anchor.right - 16 });
    openedAt.current = { top: anchor.top, left: anchor.left };
    (scroller as HTMLElement).focus({ preventScroll: true });
    const frame = view.requestAnimationFrame(() => setShown(true));
    return () => view.cancelAnimationFrame(frame);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // The list moves with its dropdown when something scrolls (a form scrolling itself into view
  // as it opens), and closes once the dropdown has scrolled out of sight.
  useEffect(() => {
    const view = select.ownerDocument.defaultView ?? window;
    const follow = (e: Event) => {
      if (e.composedPath().some((n) => n === listRef.current)) return;
      const start = openedAt.current;
      if (!start) return;
      const now = select.getBoundingClientRect();
      if (now.bottom < 0 || now.top > view.innerHeight || !select.isConnected) { onClose(false); return; }
      setShift({ x: now.left - start.left, y: now.top - start.top });
    };
    view.addEventListener("scroll", follow, true);
    return () => view.removeEventListener("scroll", follow, true);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the active choice in view as the keys move it.
  useEffect(() => {
    const scroller = scrollRef.current;
    const row = scroller?.querySelectorAll<HTMLElement>("[role='option']")[active];
    if (!scroller || !row || !box) return;
    if (row.offsetTop < scroller.scrollTop) scroller.scrollTop = row.offsetTop;
    else if (row.offsetTop + row.offsetHeight > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = row.offsetTop + row.offsetHeight - scroller.clientHeight;
  }, [active, box]);

  const pageStep = () => {
    const scroller = scrollRef.current;
    const row = scroller?.querySelector<HTMLElement>("[role='option']");
    return scroller && row ? Math.max(1, Math.floor(scroller.clientHeight / row.offsetHeight) - 1) : 8;
  };

  const onKeyDown: ListKeys = (e) => {
    const last = choices.length - 1;
    let next: number | null = null;
    if (e.key === "ArrowDown") next = nextAvailable(choices, active, 1);
    else if (e.key === "ArrowUp") next = nextAvailable(choices, active, -1);
    else if (e.key === "Home") next = nextAvailable(choices, -1, 1);
    else if (e.key === "End") next = nextAvailable(choices, last + 1, -1);
    else if (e.key === "PageDown") { let i = active; for (let n = pageStep(); n > 0; n--) i = nextAvailable(choices, i, 1); next = i; }
    else if (e.key === "PageUp") { let i = active; for (let n = pageStep(); n > 0; n--) i = nextAvailable(choices, i, -1); next = i; }
    else if (e.key === "Enter" || e.key === " ") {
      if (e.key === " " && typed.current.text && performance.now() - typed.current.at < 700) { /* a space inside a typed name */ }
      else { e.preventDefault(); e.stopPropagation(); if (choices[active] && !choices[active].disabled) onChoose(choices[active].value); else onClose(true); return; }
    } else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(true); return; }
    else if (e.key === "Tab") { onClose(true); return; }
    if (next !== null) { e.preventDefault(); e.stopPropagation(); setActive(next); return; }
    // Typing jumps to the next choice that starts with what was typed.
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const now = performance.now();
      const text = (now - typed.current.at < 700 ? typed.current.text : "") + e.key.toLocaleLowerCase();
      typed.current = { text, at: now };
      const start = text.length === 1 ? active + 1 : active;
      for (let k = 0; k < choices.length; k++) {
        const i = (start + k) % choices.length;
        if (!choices[i].disabled && choices[i].label.toLocaleLowerCase().startsWith(text)) { setActive(i); break; }
      }
      e.preventDefault();
      e.stopPropagation();
    }
  };

  // A press on the dropdown, dragged onto a choice and released, chooses it, as the browser's list does.
  useEffect(() => {
    const press = state.pressAt;
    if (!press) return;
    const view = select.ownerDocument.defaultView ?? window;
    const move = (e: PointerEvent) => {
      if (Math.abs(e.clientX - press.x) > DRAG_SLOP || Math.abs(e.clientY - press.y) > DRAG_SLOP) armed.current = true;
    };
    const up = (e: PointerEvent) => {
      view.removeEventListener("pointermove", move, true);
      if (!armed.current) return;
      const row = (e.composedPath().find((n) => n instanceof view.HTMLElement && n.getAttribute("role") === "option") as HTMLElement | undefined);
      const index = row ? Number(row.dataset.index) : -1;
      if (index >= 0 && !choices[index]?.disabled) onChoose(choices[index].value);
    };
    view.addEventListener("pointermove", move, true);
    view.addEventListener("pointerup", up, { capture: true, once: true });
    return () => { view.removeEventListener("pointermove", move, true); view.removeEventListener("pointerup", up, true); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const rowHeightGuess = compact ? 26 : 30;
  const activeRow = scrollRef.current?.querySelectorAll<HTMLElement>("[role='option']")[active];
  const open = shown && !closing;
  return (
    <>
      <div ref={listRef} className={`phx-select-list${compact ? " is-compact" : ""}${open ? " is-open" : ""}`}
        style={box ? { top: box.top + shift.y, left: box.left + shift.x, width: box.minWidth, transformOrigin: `50% ${box.origin}px` } : { top: -9999, left: -9999 }}
        onContextMenu={(e) => e.preventDefault()}>
        <ul ref={scrollRef} id={id} role="listbox" tabIndex={-1} aria-label={nameOf(select)}
          aria-activedescendant={active >= 0 ? `${id}-${active}` : undefined} onKeyDown={(e: ReactKeyboardEvent<HTMLUListElement>) => onKeyDown(e)}>
          {/* One highlight glides to the choice under the pointer or keys. */}
          <span className="phx-select-highlight" aria-hidden="true"
            style={{ transform: `translateY(${activeRow?.offsetTop ?? active * rowHeightGuess}px)`, height: activeRow?.offsetHeight ?? rowHeightGuess, opacity: active >= 0 && !choices[active]?.disabled ? 1 : 0 }} />
          {choices.map((choice, i) => (
            <li key={`${i}:${choice.value}`} id={`${id}-${i}`} role="option" data-index={i}
              aria-selected={i === chosen} aria-disabled={choice.disabled || undefined}
              className={`phx-select-option${i === chosen ? " is-chosen" : ""}`}
              onPointerMove={() => { if (!choice.disabled && active !== i) setActive(i); }}
              onClick={() => { if (!choice.disabled) onChoose(choice.value); }}>
              <span className="phx-select-label">{choice.label}</span>
              <span className="phx-select-check" aria-hidden="true"><Icon name="check" size={14} /></span>
            </li>
          ))}
        </ul>
      </div>
      {chevron && (
        <svg className={`phx-select-chevron${open ? " is-open" : ""}`} viewBox="0 0 12 12" aria-hidden="true" focusable="false"
          style={{ top: chevron.top + shift.y, left: chevron.left + shift.x }}>
          <path d="M3 4.75 6 7.75 9 4.75" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
    </>
  );
}

/**
 * Serve every Phoenix dropdown inside `root` with the themed list. Mount once
 * per root that portals its own popups (the shell, and Agent Chat, which can
 * move into a window of its own).
 */
export function SelectPopupHost({ root }: { root: HTMLElement | null }) {
  const [state, setState] = useState<OpenState | null>(null);
  const [closing, setClosing] = useState(false);
  const closeTimer = useRef<number | null>(null);
  const stateRef = useRef<OpenState | null>(null);
  const closingRef = useRef(false);
  const keysRef = useRef<ListKeys | null>(null);
  const serial = useRef(0);
  stateRef.current = state;
  closingRef.current = closing;

  const finish = useCallback((refocus: boolean) => {
    const current = stateRef.current;
    if (!current || closingRef.current) return;
    closingRef.current = true;
    current.select.removeAttribute("data-phx-open");
    current.select.setAttribute("aria-expanded", "false");
    current.select.removeAttribute("aria-controls");
    if (refocus) current.select.focus({ preventScroll: true });
    setClosing(true);
    const view = current.select.ownerDocument.defaultView ?? window;
    if (closeTimer.current) view.clearTimeout(closeTimer.current);
    closeTimer.current = view.setTimeout(() => { setState(null); setClosing(false); closeTimer.current = null; }, CLOSE_MS);
  }, []);

  const open = useCallback((select: HTMLSelectElement, pressAt: OpenState["pressAt"]) => {
    if (unavailable(select)) return;
    const choices = choicesOf(select);
    if (!choices.length) return;
    if (closeTimer.current) { (select.ownerDocument.defaultView ?? window).clearTimeout(closeTimer.current); closeTimer.current = null; }
    flushSync(() => setClosing(false));
    closingRef.current = false;
    serial.current += 1;
    select.focus({ preventScroll: true });
    select.setAttribute("data-phx-open", "");
    select.setAttribute("aria-expanded", "true");
    // Drawn at once, so the list holds focus before the next key arrives.
    flushSync(() => setState({
      select, choices, chosen: select.selectedIndex, pressAt, serial: serial.current,
      compact: select.classList.contains("agentcli-select"),
    }));
  }, []);

  const choose = useCallback((value: string) => {
    const current = stateRef.current;
    if (!current || closingRef.current) return;
    finish(true);
    commit(current.select, value);
  }, [finish]);

  // Mouse presses and opening keys on a dropdown in this root open the list instead of the browser's.
  useEffect(() => {
    if (!root) return;
    hostRoots.add(root);
    const mine = (e: Event) => {
      const select = eligible(e.composedPath()[0] ?? e.target);
      return select && servedBy(select, root) ? select : null;
    };
    const onPointerDown = (e: PointerEvent) => {
      if (e.button !== 0 || e.pointerType === "touch") return;
      const select = mine(e);
      if (!select) return;
      e.preventDefault();
      if (stateRef.current?.select === select && !closingRef.current) { finish(true); return; }
      open(select, { x: e.clientX, y: e.clientY });
    };
    // The browser opens its own list on mousedown, which a cancelled pointerdown does not stop everywhere.
    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      const select = mine(e);
      if (select && !unavailable(select) && !(e as MouseEvent & { sourceCapabilities?: { firesTouchEvents?: boolean } }).sourceCapabilities?.firesTouchEvents) e.preventDefault();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      const select = mine(e);
      if (!select || unavailable(select)) return;
      // Its list is open but has not taken focus yet: the key is meant for the list.
      if (stateRef.current?.select === select && !closingRef.current && keysRef.current) { keysRef.current(e); return; }
      const opens = e.key === " " || e.key === "Enter" || e.key === "F4"
        || ((e.key === "ArrowDown" || e.key === "ArrowUp") && (e.altKey || /Mac|iPhone|iPad/.test(navigator.platform)));
      if (!opens) return;
      e.preventDefault();
      e.stopPropagation();
      open(select, null);
    };
    // Capture, so a window's own key handling (arrows that move between rows) cannot take the keys first.
    root.addEventListener("pointerdown", onPointerDown, true);
    root.addEventListener("mousedown", onMouseDown, true);
    root.addEventListener("keydown", onKeyDown, true);
    return () => {
      hostRoots.delete(root);
      root.removeEventListener("pointerdown", onPointerDown, true);
      root.removeEventListener("mousedown", onMouseDown, true);
      root.removeEventListener("keydown", onKeyDown, true);
    };
  }, [root, open, finish]);

  // A press elsewhere, a resize or leaving the window closes it; a scroll moves the list with its dropdown.
  useEffect(() => {
    if (!state || closing) return;
    const view = state.select.ownerDocument.defaultView ?? window;
    const inList = (e: Event) => e.composedPath().some((n) => n instanceof view.Element && n.classList.contains("phx-select-list"));
    const onPress = (e: PointerEvent) => {
      if (inList(e) || e.composedPath()[0] === state.select) return;
      finish(false);
    };
    const onLeave = () => finish(false);
    view.addEventListener("pointerdown", onPress, true);
    view.addEventListener("resize", onLeave);
    view.addEventListener("blur", onLeave);
    return () => {
      view.removeEventListener("pointerdown", onPress, true);
      view.removeEventListener("resize", onLeave);
      view.removeEventListener("blur", onLeave);
    };
  }, [state, closing, finish]);

  // A dropdown removed while open takes its list with it.
  useEffect(() => {
    if (!state || closing) return;
    const view = state.select.ownerDocument.defaultView ?? window;
    const timer = view.setInterval(() => { if (!state.select.isConnected) finish(false); }, 250);
    return () => view.clearInterval(timer);
  }, [state, closing, finish]);

  if (!state || !root) return null;
  return createPortal(
    <SelectList key={state.serial} state={state} closing={closing} onClose={finish} onChoose={choose} keysRef={keysRef} />,
    root,
  );
}
