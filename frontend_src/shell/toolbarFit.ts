/**
 * The toolbar's fit rule. With icons and text along the top or bottom, or
 * floating, the main row stays on one line: when it does not fit, its
 * controls give up their text in a fixed order (Workspace Options, then
 * Help, Agent Chat, and last the workspaces), keeping their names
 * as tooltips and accessible names. The toolbar options never carry text.
 *
 * A toolbar set to one row puts the tools after the main
 * row's workspaces, with every label shown; while that does not fit, it goes
 * back to two rows and the rule above, and returns to one row once there is room.
 */

/** How many steps the main row can drop text in; each control's class `phx-fit-<step>` says when it does. */
export const FIT_STEPS = 4;

/** The class that makes a control give up its text from the given step on. */
export const fitClass = (step: number): string => `phx-fit-${step}`;

/**
 * Whether every visible item in the row sits on its first line. Items on a
 * second line start below the bottom of the shortest item on the first.
 */
export function rowFits(row: HTMLElement): boolean {
  const boxes = [...row.children].map((item) => item.getBoundingClientRect()).filter((r) => r.width > 0 || r.height > 0);
  if (boxes.length < 2) return true;
  const firstLineBottom = Math.min(...boxes.map((r) => r.bottom));
  return boxes.every((r) => r.top < firstLineBottom);
}

/** Applies each step from full text upwards and keeps the first at which the row fits, or the last step. */
export function chooseFit(apply: (step: number) => void, fits: () => boolean, steps = FIT_STEPS): number {
  for (let step = 0; step < steps; step++) {
    apply(step);
    if (fits()) return step;
  }
  apply(steps);
  return steps;
}

/** Whether every button of a one-row toolbar sits on one line, with nothing running past its end. */
export function singleRowFits(body: HTMLElement): boolean {
  const boxes = [...body.querySelectorAll(".phx-toolbar-button")].map((b) => b.getBoundingClientRect()).filter((r) => r.width > 0 || r.height > 0);
  if (body.scrollWidth > body.clientWidth + 1) return false;
  if (boxes.length < 2) return true;
  const firstLineBottom = Math.min(...boxes.map((r) => r.bottom));
  return boxes.every((r) => r.top < firstLineBottom);
}
