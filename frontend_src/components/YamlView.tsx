/** Read-only YAML viewer for configuration snapshots. Prefers HA's native
 * <ha-code-editor> (CodeMirror: monospace, syntax highlighting, scrollbars) and
 * falls back to a styled <pre> when that element is not registered. */
import React, { useEffect, useRef, useState } from "react";
import yaml from "js-yaml";
import { t } from "../i18n";

export function toYaml(value: Record<string, unknown> | null): string {
  if (value == null) return "";
  try {
    return yaml.dump(value, { indent: 2, lineWidth: 100, sortKeys: false, noRefs: true });
  } catch {
    // Fall back to JSON if the structure is somehow not YAML-serialisable.
    return JSON.stringify(value, null, 2);
  }
}

/**
 * `label` names the text for a screen reader, such as Before or After. The text scrolls inside its
 * box, so the box takes keyboard focus to be scrolled with the arrow keys (Safari and the Home
 * Assistant app do not make a scrolling box focusable on their own).
 */
export function YamlView({ value, label }: { value: string; label?: string }) {
  const ref = useRef<HTMLElement | null>(null);
  const [useEditor, setUseEditor] = useState(() => !!customElements.get("ha-code-editor"));

  // HA lazy-loads ha-code-editor, so it may not be registered at mount. Upgrade
  // from the <pre> fallback as soon as whenDefined resolves; if it never does,
  // the styled <pre> stays.
  useEffect(() => {
    if (useEditor || typeof customElements === "undefined") return;
    let cancelled = false;
    customElements
      .whenDefined("ha-code-editor")
      .then(() => { if (!cancelled) setUseEditor(true); })
      .catch(() => { /* never defined: keep the <pre> fallback */ });
    return () => { cancelled = true; };
  }, [useEditor]);

  useEffect(() => {
    if (!useEditor || !ref.current) return;
    // React 18 writes a className prop on a CUSTOM element as a junk
    // attribute named "classname", so a JSX className would leave the element
    // with no class and no .yaml-editor rule (border, the
    // --code-mirror-max-height cap, the theme-variable pins) would match.
    // Set the real attribute imperatively; React never writes "class" itself.
    ref.current.setAttribute("class", "yaml-editor");
    const el = ref.current as unknown as Record<string, unknown>;
    el.mode = "yaml";
    el.readOnly = true;
    el.linewrap = true;
    el.value = value;
  }, [useEditor, value]);

  if (!value) return <pre className="yaml-pre yaml-pre-empty">{t("common.none")}</pre>;

  const name = label ?? t("common.codeBlock");
  if (useEditor) {
    return <div role="group" aria-label={name}><ha-code-editor ref={ref as React.RefObject<HTMLElement>} /></div>;
  }
  return <pre className="yaml-pre" tabIndex={0} role="region" aria-label={name}>{value}</pre>;
}
