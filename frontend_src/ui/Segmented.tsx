/**
 * A segmented control: a recessed track with a raised pill that glides to the
 * chosen segment. Each segment is a toggle button (`aria-pressed`), so a
 * screen reader hears which one is on. Segments with an icon follow the
 * toolbar's display mode: the icon alone in icons-only mode, where the label
 * names the button and shows as its tooltip, and the icon with its label in
 * icons-and-text mode.
 */

import { useContext, useLayoutEffect, useRef, useState } from "react";
import { DisplayModeContext } from "./ActionButton";
import { Icon, type IconName } from "./Icon";

export interface Segment<V extends string> {
  value: V;
  label: string;
  icon?: IconName;
}

export function Segmented<V extends string>({ segments, value, onChange, label, className }: {
  segments: Segment<V>[];
  value: V;
  onChange: (value: V) => void;
  /** The group's accessible name. */
  label: string;
  className?: string;
}) {
  const iconsOnly = useContext(DisplayModeContext) === "icons";
  const track = useRef<HTMLDivElement | null>(null);
  const [pill, setPill] = useState<{ left: number; width: number } | null>(null);
  // The pill moves only after it has been placed once, so it never slides in from the edge.
  const [placed, setPlaced] = useState(false);

  useLayoutEffect(() => {
    const el = track.current;
    if (!el) return;
    const measure = () => {
      const chosen = el.querySelector<HTMLElement>("[aria-pressed='true']");
      setPill(chosen ? { left: chosen.offsetLeft, width: chosen.offsetWidth } : null);
    };
    measure();
    const view = el.ownerDocument.defaultView ?? window;
    const frame = view.requestAnimationFrame(() => setPlaced(true));
    if (typeof ResizeObserver === "undefined") return () => view.cancelAnimationFrame(frame);
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => { view.cancelAnimationFrame(frame); observer.disconnect(); };
  }, [value, iconsOnly, segments.length]);

  return (
    <div className={`phx-segmented${className ? ` ${className}` : ""}`} role="group" aria-label={label}>
      <div ref={track} className="phx-segmented-track">
        <span className={`phx-segmented-pill${placed ? " is-placed" : ""}`} aria-hidden="true"
          style={pill ? { transform: `translateX(${pill.left}px)`, width: pill.width } : { opacity: 0 }} />
        {segments.map((segment) => {
          const iconOnly = iconsOnly && segment.icon;
          return (
            <button key={segment.value} type="button" className="phx-segmented-btn" aria-pressed={segment.value === value}
              aria-label={iconOnly ? segment.label : undefined} title={iconOnly ? segment.label : undefined}
              onClick={() => onChange(segment.value)}>
              {segment.icon && <Icon name={segment.icon} size={15} />}
              {!iconOnly && <span>{segment.label}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
