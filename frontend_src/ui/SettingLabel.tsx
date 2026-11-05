/**
 * A settings row's name, with its explanation moved out of the window and into
 * Help. The row spreads `helpRow(text)`, so pointing at or
 * focusing anything in it, its switch or field included, shows the explanation
 * in the Help window; screen readers still read it beside the name.
 */

import type { ReactNode } from "react";

/** Attributes that make a row's explanation its Help, for the row and the controls in it. */
export function helpRow(text: string | null | undefined): { "data-help-text"?: string; "data-help-controls"?: "" } {
  return text ? { "data-help-text": text, "data-help-controls": "" } : {};
}

/** The row's name as shown, and its explanation for screen readers only (`helpId` for an `aria-describedby`). */
export function SettingLabel({ help, helpId, children }: { help?: string | null; helpId?: string; children: ReactNode }) {
  return (
    <div className="toggle-label">
      {children}
      {help && <span className="sr-only" id={helpId}>{help}</span>}
    </div>
  );
}
