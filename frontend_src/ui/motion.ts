/** Motion the panel starts from script, which the stylesheet's reduced-motion rule cannot reach. */

/** How a scroll should move: at once for someone who asked for less motion. */
export function scrollMotion(): ScrollBehavior {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? "auto" : "smooth";
}
