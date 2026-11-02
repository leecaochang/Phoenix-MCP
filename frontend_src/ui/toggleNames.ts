/**
 * A switch's name flips it, as a label flips its checkbox. Toggle rows draw the
 * name (`.toggle-label`) and the switch as siblings, so clicking the name did
 * nothing while clicking the switch did. Only the name's own text counts: the
 * row's empty space and the description under the name stay inert.
 */

/** The name of a toggle row: its first line of text, not a screen-reader copy. */
const NAME = ".toggle-row > .toggle-label > span:not(.sr-only)";

/** Flip a row's switch when its name is clicked inside `root`; returns a function that stops. */
export function attachToggleNames(root: HTMLElement): () => void {
  const click = (e: MouseEvent) => {
    if (e.button !== 0 || e.defaultPrevented || !(e.target instanceof Element)) return;
    const name = e.target.closest(NAME);
    // A real label, link or control inside the name acts for itself.
    if (!name || e.target.closest("a, button, input, select, textarea, label")) return;
    const row = name.parentElement?.parentElement;
    const switches = row?.querySelectorAll<HTMLInputElement>('.toggle-switch input[type="checkbox"]');
    // A disabled switch ignores the click; a busy one refuses it itself.
    if (switches?.length === 1) switches[0].click();
  };
  root.addEventListener("click", click);
  return () => root.removeEventListener("click", click);
}
