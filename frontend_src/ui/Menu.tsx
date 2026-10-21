/**
 * A menu button and its menu, drawn inside the panel's shadow root.
 *
 * Keyboard: Enter, Space or Down opens the menu on its first available item;
 * Up, Down, Home and End move between available items; Enter or Space runs
 * one; Escape closes and returns focus to the button; Tab closes and lets
 * focus move on. A pointer press outside closes it.
 */

import { createContext, useContext, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode, type Ref } from "react";
import { createPortal } from "react-dom";

export interface MenuItem {
  id: string;
  label: string;
  disabled?: boolean;
  onSelect: () => void;
}
export type MenuEntry = MenuItem | "separator";

/** The element menus are portalled into: the panel's shadow-root mount, so panel styles apply. */
export const MenuRootContext = createContext<HTMLElement | null>(null);

const GAP = 4;

function mergeRefs<T>(...refs: Array<Ref<T> | undefined>) {
  return (value: T | null) => {
    for (const r of refs) {
      if (typeof r === "function") r(value);
      else if (r) (r as { current: T | null }).current = value;
    }
  };
}

export function MenuButton({ label, title, icon, items, className = "", buttonRef, onButtonKeyDown, pressed = false }: {
  /** Accessible name of the button and of the menu. */
  label: string;
  title?: string;
  icon: ReactNode;
  /** The entries, or a function building them when the menu opens so availability is current. */
  items: MenuEntry[] | (() => MenuEntry[]);
  className?: string;
  buttonRef?: Ref<HTMLButtonElement>;
  /** Handles a key first while the menu is closed; return true to consume it. */
  onButtonKeyDown?: (e: KeyboardEvent<HTMLButtonElement>) => boolean;
  /** Shows the button as active, for a mode started from the menu. */
  pressed?: boolean;
}) {
  const root = useContext(MenuRootContext);
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<MenuEntry[]>([]);
  const [place, setPlace] = useState<{ top?: number; bottom?: number; right: number }>({ right: 0 });
  const button = useRef<HTMLButtonElement | null>(null);
  const list = useRef<HTMLDivElement | null>(null);
  const focusFirst = useRef(false);

  const available = () => [...(list.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])') ?? [])];

  const openMenu = (fromKeyboard: boolean) => {
    const r = button.current!.getBoundingClientRect();
    const below = window.innerHeight - r.bottom;
    // Open upward when the space below is tight and there is more above.
    setPlace(below < 220 && r.top > below
      ? { bottom: window.innerHeight - r.top + GAP, right: Math.max(GAP, window.innerWidth - r.right) }
      : { top: r.bottom + GAP, right: Math.max(GAP, window.innerWidth - r.right) });
    focusFirst.current = fromKeyboard;
    setEntries(typeof items === "function" ? items() : items);
    setOpen(true);
  };
  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  };

  useLayoutEffect(() => {
    if (!open) return;
    (focusFirst.current ? available()[0] : list.current)?.focus();
    const outside = (e: PointerEvent) => {
      const path = e.composedPath();
      if (list.current && !path.includes(list.current) && button.current && !path.includes(button.current)) setOpen(false);
    };
    window.addEventListener("pointerdown", outside, true);
    return () => window.removeEventListener("pointerdown", outside, true);
  }, [open]);

  const onMenuKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const entries = available();
    const at = entries.indexOf(e.target as HTMLElement);
    if (e.key === "ArrowDown") entries[(at + 1) % entries.length]?.focus();
    else if (e.key === "ArrowUp") entries[(at - 1 + entries.length) % entries.length]?.focus();
    else if (e.key === "Home") entries[0]?.focus();
    else if (e.key === "End") entries[entries.length - 1]?.focus();
    else if (e.key === "Escape") close(true);
    else if (e.key === "Tab") { close(false); return; }
    else return;
    e.preventDefault();
    e.stopPropagation();
  };

  const run = (item: MenuItem) => {
    if (item.disabled) return;
    close(true);
    item.onSelect();
  };

  return (
    <>
      <button
        ref={mergeRefs(button, buttonRef)}
        type="button"
        className={`${className}${open || pressed ? " is-active" : ""}`}
        aria-label={label}
        title={title ?? label}
        aria-haspopup="menu"
        aria-expanded={open}
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
      </button>
      {open && createPortal(
        <div ref={list} className="phx-menu" role="menu" aria-label={label} tabIndex={-1}
          style={{ top: place.top, bottom: place.bottom, right: place.right }} onKeyDown={onMenuKeyDown}>
          {entries.map((item, i) => item === "separator"
            ? <div key={`sep-${i}`} className="phx-menu-separator" role="separator" />
            : (
              <div key={item.id} role="menuitem" tabIndex={-1} className="phx-menu-item"
                aria-disabled={item.disabled || undefined}
                onClick={() => run(item)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); run(item); }
                }}>
                {item.label}
              </div>
            ))}
        </div>,
        root ?? document.body,
      )}
    </>
  );
}
