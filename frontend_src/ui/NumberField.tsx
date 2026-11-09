/**
 * A number field with its own minus and plus buttons on either side of the
 * value, in place of the browser's spinner. The field is still a native
 * number input, so typing, the Up and Down keys, its label and its value work
 * as before; the buttons step it by its `step` within `min` and `max` and are
 * left out of the Tab order and the accessibility tree, as the browser's
 * spinner is, since the keys already do the same.
 */

import { useRef, type ComponentPropsWithRef, type PointerEvent as ReactPointerEvent } from "react";
import { BusyInput } from "../components/BusyButton";
import { Icon } from "./Icon";

/** How long a held button waits before repeating, then how often it repeats. */
const REPEAT_DELAY_MS = 400;
const REPEAT_EVERY_MS = 60;

type Props = Omit<ComponentPropsWithRef<"input">, "type"> & {
  /** Keeps the field focusable but unchangeable while a save runs. */
  busy?: boolean;
  /** Classes for the whole control (its border, width and error state). */
  className?: string;
};

function step(input: HTMLInputElement, direction: 1 | -1) {
  const before = input.value;
  const view = input.ownerDocument.defaultView ?? window;
  try {
    if (direction > 0) input.stepUp(); else input.stepDown();
  } catch {
    // A value the browser cannot step from: start at min, or 0. The prototype's setter, so the
    // page's change handler still sees a change.
    Object.getOwnPropertyDescriptor(view.HTMLInputElement.prototype, "value")?.set?.call(input, input.min || "0");
  }
  if (input.value === before) return;
  input.dispatchEvent(new view.Event("input", { bubbles: true }));
  input.dispatchEvent(new view.Event("change", { bubbles: true }));
}

export function NumberField({ busy, className = "input", ref, ...props }: Props) {
  const own = useRef<HTMLInputElement | null>(null);
  const timer = useRef<number | null>(null);
  const off = Boolean(props.disabled || busy || props.readOnly);
  const value = props.value === undefined || props.value === "" ? null : Number(props.value);
  const atMin = value !== null && props.min !== undefined && props.min !== "" && value <= Number(props.min);
  const atMax = value !== null && props.max !== undefined && props.max !== "" && value >= Number(props.max);

  const setRef = (el: HTMLInputElement | null) => {
    own.current = el;
    if (typeof ref === "function") ref(el);
    else if (ref) (ref as React.MutableRefObject<HTMLInputElement | null>).current = el;
  };

  const stop = () => {
    const view = own.current?.ownerDocument.defaultView ?? window;
    if (timer.current !== null) view.clearTimeout(timer.current);
    timer.current = null;
  };
  // Holding a button keeps stepping, as the browser's spinner does.
  const press = (direction: 1 | -1) => (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0 || off) return;
    e.preventDefault();
    const input = own.current;
    if (!input) return;
    input.focus({ preventScroll: true });
    step(input, direction);
    const view = input.ownerDocument.defaultView ?? window;
    const repeat = (delay: number) => { timer.current = view.setTimeout(() => { step(input, direction); repeat(REPEAT_EVERY_MS); }, delay); };
    repeat(REPEAT_DELAY_MS);
    view.addEventListener("pointerup", stop, { once: true });
    view.addEventListener("pointercancel", stop, { once: true });
  };

  const help = (props as Record<string, unknown>)["data-help"] as string | undefined;
  const input = busy === undefined
    ? <input {...props} ref={setRef} type="number" className="phx-number-input" />
    : <BusyInput {...props} ref={setRef} busy={busy} type="number" className="phx-number-input" />;
  // The input comes first so a label around the field names it, not a button; the stylesheet
  // draws minus before it. The buttons are hidden from assistive technology, as the browser's
  // spinner is: the field itself is the spin button, and its Up and Down keys do the same.
  return (
    <span className={`phx-number ${className}`} data-help={help} data-disabled={props.disabled ? "" : undefined}
      aria-disabled={busy || undefined}>
      {input}
      <button type="button" tabIndex={-1} aria-hidden="true" className="phx-number-step phx-number-down"
        disabled={props.disabled || atMin} onPointerDown={press(-1)} onPointerLeave={stop}>
        <Icon name="minimize" size={14} />
      </button>
      <button type="button" tabIndex={-1} aria-hidden="true" className="phx-number-step phx-number-up"
        disabled={props.disabled || atMax} onPointerDown={press(1)} onPointerLeave={stop}>
        <Icon name="add" size={14} />
      </button>
    </span>
  );
}
