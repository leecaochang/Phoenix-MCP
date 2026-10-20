/** Shared presentational helpers used across panel views and the in-context
 * profile modal. Kept out of index.tsx so reusing them (e.g. ProfileEditor in
 * the injected quick-add modal) does not pull the whole panel app into a chunk.
 */
import { t } from "../i18n";
import { Icon } from "../ui/Icon";

export function Loading() {
  return (
    <div className="loading-wrap" role="status">
      <div className="spinner" aria-hidden="true" />
      <span>{t("common.loading")}</span>
    </div>
  );
}

export function ErrorMsg({ msg }: { msg: string }) {
  return <div className="banner banner-error" role="alert">{msg}</div>;
}

// The documentation is a hosted site, not a copy shipped inside the integration,
// so every help link leaves Home Assistant. Exported because index.tsx links the
// site root from the tab bar and the two must not drift.
export const DOCS_BASE_URL = "https://leecaochang.github.io/Phoenix-MCP/";

// Small "?" badge next to a Settings tab card's label, linking out to the
// matching section of the documentation site.
export function DocsHelpLink({ path, label }: { path: string; label: string }) {
  return (
    <a
      className="docs-help-link"
      href={DOCS_BASE_URL + path}
      target="_blank"
      rel="noopener noreferrer"
      title={t("common.docsTitle")}
      aria-label={t("common.docsAria", { label })}
    >
      ?
    </a>
  );
}

export function RefreshIcon() {
  return <Icon name="refresh" size={15} style={{ display: "block" }} />;
}

// Export = data leaves the profile store, out to a file (arrow up and out of the tray).
export function ExportIcon() {
  return <Icon name="export" size={15} style={{ display: "block" }} />;
}

// Import = a file's contents land in the profile store (arrow down into the tray).
export function ImportIcon() {
  return <Icon name="import" size={15} style={{ display: "block" }} />;
}
