import { useRef, useState } from "react";
import type { ArchivedAccessKeyRecord } from "../types";
import { api } from "../api";
import { accessKeyStatusLabel, formatDate } from "../utils";
import { t } from "../i18n";
import { ConfigTable, type Column } from "../ui/table/ConfigTable";
import { ActionButton } from "../ui/ActionButton";
import { useClickAway } from "../ui/useClickAway";
import { AccessKeyStatus } from "./AccessKeyStatus";

interface Props {
  accessKeys: ArchivedAccessKeyRecord[];
  onDeleted: (id: string) => void;
}

const NEWEST_ARCHIVED = { column: "archived", dir: "desc" } as const;

/** Revoked and expired keys, on the same configurable table as the key list, each with Delete Permanently. */
export function ArchivedAccessKeyTable({ accessKeys, onDeleted }: Props) {
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // A row can be chosen, so Up and Down step through a long list and keep the row in view (user, 2026-10-31).
  const [chosen, setChosen] = useState<string | null>(null);
  // A click anywhere else cancels a delete waiting for its confirmation.
  const confirming = useRef<HTMLSpanElement | null>(null);
  useClickAway(confirming, () => setConfirmId(null), !!confirmId && !deleting);

  async function deletePermanently(id: string) {
    setDeleting(id);
    setError(null);
    try {
      await api.deleteArchivedAccessKey(id);
      onDeleted(id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.archivedDeleteFailed"));
    } finally {
      setDeleting(null);
      setConfirmId(null);
    }
  }

  if (accessKeys.length === 0) {
    return <p className="archived-empty">{t("accessKeys.archivedEmpty")}</p>;
  }

  const statusOf = (k: ArchivedAccessKeyRecord) => (k.revoked ? "Revoked" : "Expired");
  const columns: Column<ArchivedAccessKeyRecord>[] = [
    // Up to 16 characters of a name show, as in the key list (user, 2026-10-31).
    { id: "name", label: t("accessKeys.colName"), width: 160, maxWidth: 320, maxChars: 16, sortValue: (k) => k.name.toLowerCase(), title: (k) => k.name, cell: (k) => k.name },
    { id: "status", label: t("accessKeys.colStatus"), width: 120, sortValue: statusOf, cell: (k) => <AccessKeyStatus status={statusOf(k)} /> },
    { id: "created", label: t("accessKeys.colCreated"), width: 120, sortValue: (k) => k.created_at ?? "", cell: (k) => formatDate(k.created_at) },
    { id: "archived", label: t("accessKeys.colArchivedOn"), width: 120, sortValue: (k) => k.revoked_at ?? "", cell: (k) => formatDate(k.revoked_at) },
    { id: "lastUsed", label: t("accessKeys.colLastUsed"), width: 120, sortValue: (k) => k.last_used_at ?? "", cell: (k) => formatDate(k.last_used_at) },
    // Delete Permanently turns red to confirm, in place, with Cancel beside it (user, 2026-10-28).
    { id: "actions", label: t("accessKeys.colActions"), width: 200, maxWidth: 320, className: "phx-td-actions",
      cell: (k) => (
        <span className="row-actions" ref={confirmId === k.id ? confirming : undefined}>
          <ActionButton icon="delete" label={confirmId === k.id ? t("accessKeys.confirmDelete") : t("accessKeys.deletePermanently")}
            className={confirmId === k.id ? "btn btn-danger btn-sm" : "btn btn-ghost btn-sm"} busy={deleting === k.id}
            onClick={() => (confirmId === k.id ? void deletePermanently(k.id) : setConfirmId(k.id))} />
          {confirmId === k.id && (
            <ActionButton icon="undo" label={t("accessKeys.cancel")} className="btn btn-outline btn-sm" busy={deleting === k.id} onClick={() => setConfirmId(null)} />
          )}
        </span>
      ) },
  ];

  return (
    <div>
      {error && <div className="banner banner-error mb-8">{error}</div>}
      <ConfigTable
        id="archivedAccessKeys"
        label={t("accessKeys.archivedTitle")}
        defaultSort={NEWEST_ARCHIVED}
        columns={columns}
        rows={accessKeys}
        rowKey={(k) => k.id}
        selectedKey={chosen}
        onSelect={(k) => setChosen(k.id)}
        rowLabel={(k) => `${k.name}, ${accessKeyStatusLabel(statusOf(k))}`}
      />
    </div>
  );
}
