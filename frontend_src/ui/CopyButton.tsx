/**
 * The one copy control in the panel: a small icon button that copies its text,
 * shows a check for a second, then the copy icon again. Its accessible name
 * and tooltip say what it copies; a screen reader hears "Copied to clipboard",
 * and a failure (a browser that allows no clipboard, as on plain HTTP) is said
 * on screen, since nothing else would show it.
 */

import { useEffect, useRef, useState } from "react";
import { t } from "../i18n";
import { copyToClipboard } from "../utils";
import { Icon } from "./Icon";

/** How long the check stays before the copy icon returns. */
export const COPIED_MS = 1000;
/** How long a failure stays said, long enough to read. */
const FAILED_MS = 4000;

export function CopyButton({ text, label = t("common.copy"), help, className }: {
  /** The text copied, or a function that gives it at the moment of copying. */
  text: string | (() => string);
  /** What the button copies, as its accessible name and tooltip ("Copy text"). */
  label?: string;
  /** Its Help catalog key. */
  help?: string;
  /** Extra classes, for where the button sits. */
  className?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = async () => {
    window.clearTimeout(timer.current);
    let next: "copied" | "failed";
    try {
      await copyToClipboard(typeof text === "function" ? text() : text);
      next = "copied";
    } catch {
      next = "failed";
    }
    setState(next);
    timer.current = window.setTimeout(() => setState("idle"), next === "copied" ? COPIED_MS : FAILED_MS);
  };

  const copied = state === "copied";
  return (
    <>
      <button type="button" className={`phx-copy${copied ? " is-copied" : ""}${className ? ` ${className}` : ""}`}
        aria-label={label} title={copied ? t("common.copied") : label} data-help={help}
        // A copy button inside a clickable row (a chat message) copies without also choosing the row.
        onClick={(event) => { event.stopPropagation(); void copy(); }}>
        <Icon name={copied ? "check" : "copy"} size={14} />
      </button>
      <span role="status" className={state === "failed" ? "phx-copy-status phx-copy-failed" : "phx-copy-status sr-only"}>
        {copied ? t("common.copiedToClipboard") : state === "failed" ? t("conversations.copyFailed") : ""}
      </span>
    </>
  );
}
