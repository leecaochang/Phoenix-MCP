import type { ButtonHTMLAttributes, InputHTMLAttributes, SelectHTMLAttributes } from "react";

/** Keep the focused action mounted and focusable while its request runs. */
export function BusyButton({ busy, disabled, onClick, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { busy: boolean }) {
  return <button {...props} disabled={disabled} aria-disabled={busy || disabled}
    aria-busy={busy || undefined} onClick={(event) => {
      if (busy || disabled) { event.preventDefault(); return; }
      onClick?.(event);
    }} />;
}

export function BusyInput({ busy, disabled, readOnly, onChange, onClick, ...props }: InputHTMLAttributes<HTMLInputElement> & { busy: boolean }) {
  return <input {...props} disabled={disabled} readOnly={busy || readOnly} aria-disabled={busy || disabled}
    onClick={(event) => { if (busy) event.preventDefault(); else onClick?.(event); }}
    onChange={(event) => { if (!busy && !disabled) onChange?.(event); }} />;
}

export function BusySelect({ busy, disabled, onChange, onKeyDown, onMouseDown, ...props }: SelectHTMLAttributes<HTMLSelectElement> & { busy: boolean }) {
  return <select {...props} disabled={disabled} aria-disabled={busy || disabled}
    onKeyDown={(event) => { if (busy && event.key !== "Tab") event.preventDefault(); else onKeyDown?.(event); }}
    onMouseDown={(event) => { if (busy) event.preventDefault(); else onMouseDown?.(event); }}
    onChange={(event) => { if (!busy && !disabled) onChange?.(event); }} />;
}
