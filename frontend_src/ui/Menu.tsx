/**
 * Menus drawn inside the panel's shadow root: a menu button, and context menus
 * opened by right-click (or the keyboard's context-menu key) on panel chrome.
 *
 * Keyboard: Enter, Space or Down opens a menu button on its first available
 * item; Up, Down, Home and End move between available items; Enter or Space
 * runs one; Escape closes and returns focus to where it was; Tab closes and
 * lets focus move on. A pointer press outside closes it.
 */

import {
  createContext, useCallback, useContext, useLayoutEffect, useRef, useState,
  type KeyboardEvent, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type ReactNode, type Ref,
} from "react";
import { createPortal } from "react-dom";
import { Icon } from "./Icon";

export interface MenuItem {
  id: string;
  label: string;
  disabled?: boolean;
  /** Shows a check mark, for a choice that is currently in effect. */
  checked?: boolean;
  onSelect: () => void;
}
export type MenuEntry = MenuItem | "separator";

/** The element menus are portalled into: the panel's shadow-root mount, so panel styles apply. */
export const MenuRootContext = createContext<HTMLElement | null>(null);

const GAP = 4;
const EDGE = 8;

type Place = { top: number; left: number };

function MenuList({ label, entries, place, focusFirst, onClose, listRef }: {
  label: string;
  entries: MenuEntry[];
  place: Place;
  focusFirst: boolean;
  /** Close; `refocus` returns focus to where the menu was opened from. */
  onClose: (refocus: boolean) => void;
  listRef: React.MutableRefObject<HTMLDivElement | null>;
}) {
  const root = useContext(MenuRootContext);
  const [pos, setPos] = useState(place);
  const available = () => [...(listRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])') ?? [])];

  // Keep the menu on screen once its size is known.
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const left = Math.max(EDGE, Math.min(place.left, window.innerWidth - r.width - EDGE));
    const top = place.top + r.height > window.innerHeight - EDGE ? Math.max(EDGE, place.top - r.height - 2 * GAP) : place.top;
    if (left !== pos.left || top !== pos.top) setPos({ top, left });
    (focusFirst ? available()[0] : el)?.focus();
  }, [place]); // eslint-disable-line react-hooks/exhaustive-deps

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const items = available();
    const at = items.indexOf(e.target as HTMLElement);
    if (e.key === "ArrowDown") items[(at + 1) % items.length]?.focus();
    else if (e.key === "ArrowUp") items[(at - 1 + items.length) % items.length]?.focus();
    else if (e.key === "Home") items[0]?.focus();
    else if (e.key === "End") items[items.length - 1]?.focus();
    else if (e.key === "Escape") onClose(true);
    else if (e.key === "Tab") { onClose(false); return; }
    else return;
    e.preventDefault();
    e.stopPropagation();
  };
  const run = (item: MenuItem) => {
    if (item.disabled) return;
    onClose(true);
    item.onSelect();
  };

  return createPortal(
    <div ref={listRef} className="phx-menu" role="menu" aria-label={label} tabIndex={-1}
      style={{ top: pos.top, left: pos.left }} onKeyDown={onKeyDown} onContextMenu={(e) => e.preventDefault()}>
      {entries.map((item, i) => item === "separator"
        ? <div key={`sep-${i}`} className="phx-menu-separator" role="separator" />
        : (
          <div key={item.id} role={item.checked === undefined ? "menuitem" : "menuitemradio"} tabIndex={-1}
            className="phx-menu-item"
            aria-checked={item.checked}
            aria-disabled={item.disabled || undefined}
            onClick={() => run(item)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); run(item); } }}>
            {item.checked !== undefined && <span className="phx-menu-check" aria-hidden="true">{item.checked && <Icon name="check" size={14} />}</span>}
            {item.label}
          </div>
        ))}
    </div>,
    root ?? document.body,
  );
}

/** Close on a pointer press outside the menu (and outside `alsoInside`, such as its button). */
function useOutsidePress(open: boolean, list: React.RefObject<HTMLElement | null>, onOutside: () => void, alsoInside?: React.RefObject<HTMLElement | null>) {
  useLayoutEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      const path = e.composedPath();
      if (list.current && path.includes(list.current)) return;
      if (alsoInside?.current && path.includes(alsoInside.current)) return;
      onOutside();
    };
    window.addEventListener("pointerdown", outside, true);
    return () => window.removeEventListener("pointerdown", outside, true);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
}

function mergeRefs<T>(...refs: Array<Ref<T> | undefined>) {
  return (value: T | null) => {
    for (const r of refs) {
      if (typeof r === "function") r(value);
      else if (r) (r as { current: T | null }).current = value;
    }
  };
}

export function MenuButton({ label, title, icon, children, items, className = "", buttonRef, onButtonKeyDown, onPointerDown, pressed = false }: {
  /** Accessible name of the button and of the menu. */
  label: string;
  title?: string;
  icon: ReactNode;
  /** Visible text after the icon, when the button shows one. */
  children?: ReactNode;
  /** The entries, or a function building them when the menu opens so availability is current. */
  items: MenuEntry[] | (() => MenuEntry[]);
  className?: string;
  buttonRef?: Ref<HTMLButtonElement>;
  /** Handles a key first while the menu is closed; return true to consume it. */
  onButtonKeyDown?: (e: KeyboardEvent<HTMLButtonElement>) => boolean;
  /** For a button that is also a drag handle. */
  onPointerDown?: (e: ReactPointerEvent<HTMLButtonElement>) => void;
  /** Shows the button as active, for a mode started from the menu. */
  pressed?: boolean;
}) {
  const [open, setOpen] = useState<{ entries: MenuEntry[]; place: Place; focusFirst: boolean } | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const list = useRef<HTMLDivElement | null>(null);

  const openMenu = (fromKeyboard: boolean) => {
    const r = button.current!.getBoundingClientRect();
    setOpen({
      entries: typeof items === "function" ? items() : items,
      place: { top: r.bottom + GAP, left: r.right - 200 },
      focusFirst: fromKeyboard,
    });
  };
  const close = (refocus: boolean) => {
    setOpen(null);
    if (refocus) button.current?.focus();
  };
  useOutsidePress(!!open, list, () => setOpen(null), button);

  return (
    <>
      <button
        ref={mergeRefs(button, buttonRef)}
        type="button"
        className={`${className}${open || pressed ? " is-active" : ""}`}
        aria-label={label}
        title={title ?? label}
        aria-haspopup="menu"
        aria-expanded={!!open}
        onPointerDown={onPointerDown}
        onClick={() => (open ? close(false) : openMenu(false))}
        onKeyDown={(e) => {
          if (onButtonKeyDown?.(e)) return;
          if (!open && (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            openMenu(true);
          }
        }}
      >
        {icon}
        {children}
      </button>
      {open && <MenuList label={label} entries={open.entries} place={open.place} focusFirst={open.focusFirst} onClose={close} listRef={list} />}
    </>
  );
}

type ShowContextMenu = (event: ReactMouseEvent | MouseEvent, label: string, entries: MenuEntry[]) => void;
const ContextMenuContext = createContext<ShowContextMenu | null>(null);

/**
 * Hosts the panel's context menus. Panel chrome (toolbar, title bars, empty
 * workspace space) opens its own menu; window content keeps the browser's menu
 * so copying and other text actions still work there.
 */
export function ContextMenuHost({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<{ label: string; entries: MenuEntry[]; place: Place; focusFirst: boolean } | null>(null);
  const list = useRef<HTMLDivElement | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const show = useCallback<ShowContextMenu>((event, label, entries) => {
    if (!entries.length) return;
    event.preventDefault();
    event.stopPropagation();
    const target = event.target as HTMLElement;
    // The keyboard's context-menu key reports no pointer position; open at the focused control.
    const fromKeyboard = event.clientX === 0 && event.clientY === 0;
    const r = target.getBoundingClientRect();
    returnFocus.current = (target.closest("button, [tabindex]") as HTMLElement | null) ?? null;
    setOpen({ label, entries, place: fromKeyboard ? { top: r.bottom + GAP, left: r.left } : { top: event.clientY, left: event.clientX }, focusFirst: fromKeyboard });
  }, []);
  const close = (refocus: boolean) => {
    setOpen(null);
    if (refocus) returnFocus.current?.focus();
  };
  useOutsidePress(!!open, list, () => setOpen(null));
  return (
    <ContextMenuContext.Provider value={show}>
      {children}
      {open && <MenuList label={open.label} entries={open.entries} place={open.place} focusFirst={open.focusFirst} onClose={close} listRef={list} />}
    </ContextMenuContext.Provider>
  );
}

/** Opens a context menu at the pointer; null outside a ContextMenuHost. */
export function useContextMenu(): ShowContextMenu | null {
  return useContext(ContextMenuContext);
}
