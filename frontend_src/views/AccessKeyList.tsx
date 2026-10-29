import { useState, useEffect, type ReactNode } from "react";
import type { AccessKeyRecord, ArchivedAccessKeyRecord } from "../types";
import { AccessKeyCreateForm } from "../components/AccessKeyCreate";
import { ArchivedAccessKeyTable } from "../components/ArchivedAccessKeyTable";
import { api } from "../api";
import { Loading, ErrorMsg } from "../components/common";
import { formatDate, accessKeyStatus, accessKeyStatusLabel } from "../utils";
import { localeDateTime, t } from "../i18n";
import { useToolbarTools, type ToolGroup } from "../shell/tools";
import { InlineTools } from "../shell/windowTools";
import { useWindowVisible } from "../shell/visibility";
import { ConfigTable, type Column } from "../ui/table/ConfigTable";
import { Icon } from "../ui/Icon";
import { AccessKeyStatus } from "../components/AccessKeyStatus";
import { EditableAccessKeyName } from "./AccessKeyDetail";

const MAX_ACTIVE_ACCESS_KEYS_WARNING = 50;
/** How often the list is read again while it is on screen, so Last Used and keys made elsewhere stay current. */
const UPDATE_MS = 30_000;

function relativeTime(iso: string | null): string {
  if (!iso) return t("common.never");
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return t("accessKeys.justNow");
  const m = Math.floor(s / 60);
  if (m < 60) return t("accessKeys.minutesAgo", { m });
  const h = Math.floor(m / 60);
  if (h < 24) return t("accessKeys.hoursAgo", { h });
  return t("accessKeys.daysAgo", { d: Math.floor(h / 24) });
}

/** Scoped in plain words; Pass-Through marked, since it skips the Permissions tree. */
function AccessKeyMode({ passThrough }: { passThrough: boolean }) {
  if (!passThrough) return <>{t("accessKeys.scoped")}</>;
  return (
    <span className="phx-outcome access-key-mode-pass-through" data-help="accessKeys.passThroughModeHelp">
      <Icon name="warning" size={14} />
      {t("accessKeys.passThrough")}
    </span>
  );
}

const BY_NAME = { column: "name", dir: "asc" } as const;

/**
 * The revoked and expired keys, in a window of its own that Show Archived
 * opens below the key list (user, 2026-10-28). It is closed by default and
 * reads the archived keys when it opens.
 */
export function ArchivedAccessKeysWindow() {
  const [archived, setArchived] = useState<ArchivedAccessKeyRecord[] | null>(null);
  useEffect(() => {
    let live = true;
    api.listArchivedAccessKeys().then((keys) => { if (live) setArchived(keys); }).catch(() => { if (live) setArchived([]); });
    return () => { live = false; };
  }, []);
  if (!archived) return <Loading />;
  return (
    <div className="phx-list-view access-keys-archived">
      <ArchivedAccessKeyTable accessKeys={archived} onDeleted={(id) => setArchived((prev) => prev?.filter((k) => k.id !== id) ?? null)} />
    </div>
  );
}

interface Props {
  accessKeys: AccessKeyRecord[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  /** Read the list again without a loading state; there is no Refresh button (user, 2026-10-28). */
  onUpdate?: () => void;
  /** Whether the Archived Access Keys window is open; the workspace keeps it so a reset can close it. */
  showArchived?: boolean;
  onShowArchived?: (show: boolean) => void;
  onOpenDetail: (id: string) => void;
  onLaunchWizard: () => void;
  showCreate: boolean;
  onOpenCreate: () => void;
  onCloseCreate: () => void;
  onOpenSettings: () => void;
  /** The key whose windows are shown below the list. */
  selectedId?: string | null;
  /** The key whose name is being edited in place, from Key Actions. */
  renamingId?: string | null;
  onRenamed?: (updated: AccessKeyRecord) => void;
  onRenameEnd?: () => void;
  /** Tools for the selected key, offered on the toolbar after the list's own. */
  selectedKeyTools?: ToolGroup | null;
  /** Notices about the selected key, shown above the list. */
  notices?: ReactNode;
}

export function AccessKeyListView({
  accessKeys, loading, error, onRefresh, onUpdate, showArchived: keptArchived, onShowArchived, onOpenDetail, onLaunchWizard, showCreate, onOpenCreate, onCloseCreate, onOpenSettings,
  selectedId = null, renamingId = null, onRenamed, onRenameEnd, selectedKeyTools = null, notices = null,
}: Props) {
  const [filter, setFilter] = useState("");
  const [ownArchived, setOwnArchived] = useState(false);
  const showArchived = keptArchived ?? ownArchived;
  const setShowArchived = onShowArchived ?? setOwnArchived;

  const visible = useWindowVisible();
  useEffect(() => {
    if (!visible || !onUpdate) return;
    const timer = window.setInterval(() => { if (!document.hidden) onUpdate(); }, UPDATE_MS);
    return () => clearInterval(timer);
  }, [visible, onUpdate]);

  const filtered = accessKeys.filter((k) => {
    const q = filter.toLowerCase();
    if (!q) return true;
    const status = accessKeyStatus(k);
    return [k.name, status, accessKeyStatusLabel(status)]
      .some((value) => value.toLowerCase().includes(q));
  });

  // The list as a configurable table like the other workspaces' lists (user, 2026-10-28).
  const columns: Column<AccessKeyRecord>[] = [
    { id: "name", label: t("accessKeys.colName"), width: 200, maxWidth: 320, sortValue: (k) => k.name.toLowerCase(), title: (k) => k.name,
      cell: (k) => (k.id === renamingId && onRenamed
        ? <EditableAccessKeyName accessKey={k} onRenamed={onRenamed} onDone={() => onRenameEnd?.()} />
        : <span className="access-key-name">{k.name}</span>) },
    { id: "mode", label: t("accessKeys.colMode"), width: 130, sortValue: (k) => (k.pass_through ? 1 : 0), cell: (k) => <AccessKeyMode passThrough={k.pass_through} /> },
    { id: "status", label: t("accessKeys.colStatus"), width: 120, sortValue: (k) => accessKeyStatusLabel(accessKeyStatus(k)), cell: (k) => <AccessKeyStatus status={accessKeyStatus(k)} /> },
    { id: "created", label: t("accessKeys.colCreated"), width: 120, sortValue: (k) => k.created_at ?? "",
      title: (k) => (k.created_at ? localeDateTime(k.created_at) : undefined), cell: (k) => formatDate(k.created_at) },
    { id: "updated", label: t("accessKeys.colUpdated"), width: 130, sortValue: (k) => k.updated_at ?? "",
      title: (k) => (k.updated_at ? localeDateTime(k.updated_at) : undefined), cell: (k) => relativeTime(k.updated_at) },
    { id: "expires", label: t("accessKeys.colExpires"), width: 120, sortValue: (k) => k.expires_at ?? "9999", cell: (k) => formatDate(k.expires_at) },
    { id: "lastUsed", label: t("accessKeys.colLastUsed"), width: 120, sortValue: (k) => k.last_used_at ?? "",
      title: (k) => (k.last_used_at ? localeDateTime(k.last_used_at) : undefined), cell: (k) => relativeTime(k.last_used_at) },
  ];

  // In the workspace shell the list's buttons and the selected key's actions sit on the toolbar.
  const groups: ToolGroup[] = [
    {
      id: "accessKeys",
      label: t("shell.tabAccessKeys"),
      tools: [
        { id: "setUpAgent", label: t("accessKeys.setUpAgent"), icon: "setupAgent", onSelect: onLaunchWizard },
        { id: "archived", label: showArchived ? t("accessKeys.hideArchived") : t("accessKeys.showArchived"), icon: "archive", pressed: showArchived, onSelect: () => setShowArchived(!showArchived) },
        // Not a toggle: closing the form from here while a key is being created would lose its one-time secret.
        { id: "create", label: t("accessKeys.createAccessKey"), icon: "add", onSelect: onOpenCreate },
      ],
    },
    ...(selectedKeyTools ? [selectedKeyTools] : []),
  ];
  const onToolbar = useToolbarTools(groups);

  function handleCreated(record: AccessKeyRecord) {
    onRefresh();
    onCloseCreate();
    onOpenDetail(record.id);
  }

  // Create Access Key opens its form in place, at the top of the window.
  const createForm = showCreate && (
    <AccessKeyCreateForm
      existingNames={accessKeys.map((k) => k.name)}
      onCreated={handleCreated}
      onClose={onCloseCreate}
      onOpenSettings={onOpenSettings}
    />
  );

  // First run: no access keys and no filter. Show an onboarding hero that launches
  // the wizard instead of the empty table. The create form still opens here so
  // the Create Access Key tool works in the empty state.
  if (!loading && !error && accessKeys.length === 0 && !filter) {
    return (
      <div className="view-root">
        {createForm}
        <div className="card wizard-hero">
          <div className="wizard-hero-title">{t("accessKeys.heroTitle")}</div>
          <p className="wizard-hero-sub">
            {t("accessKeys.heroSub")}
          </p>
          <div className="wizard-hero-actions">
            <button className="btn btn-primary" onClick={onLaunchWizard}>{t("accessKeys.setUpFirstAgent")}</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="phx-list-view access-keys-list">
      {!onToolbar && <InlineTools groups={groups} />}
      {accessKeys.length >= MAX_ACTIVE_ACCESS_KEYS_WARNING && (
        <div className="banner banner-warn">
          {t("accessKeys.maxWarning", { max: MAX_ACTIVE_ACCESS_KEYS_WARNING })}
        </div>
      )}

      {notices}
      {createForm}

      <div className="phx-filters"><div className="filter-row">
        <input type="search" className="input" aria-label={t("accessKeys.filterAria")} placeholder={t("accessKeys.filterPlaceholder")}
          value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div></div>
      {/* Reading the list again keeps the table in place; only a first load shows the loading state. */}
      {error && accessKeys.length > 0 && <ErrorMsg msg={error} />}
      {loading && accessKeys.length === 0 ? <Loading /> : error && accessKeys.length === 0 ? <ErrorMsg msg={error} /> : (
        <ConfigTable
          id="accessKeys"
          label={t("shell.tabAccessKeys")}
          defaultSort={BY_NAME}
          columns={columns}
          rows={filtered}
          rowKey={(k) => k.id}
          selectedKey={selectedId}
          // Choosing an active key hides the archived ones (user, 2026-10-28).
          onSelect={(k) => { setShowArchived(false); onOpenDetail(k.id); }}
          rowLabel={(k) => `${k.name}, ${k.pass_through ? t("accessKeys.passThrough") : t("accessKeys.scoped")}, ${accessKeyStatusLabel(accessKeyStatus(k))}`}
          empty={<p className="audit-empty">{filter ? t("accessKeys.emptyFiltered") : t("accessKeys.empty")}</p>}
        />
      )}

    </div>
  );
}
