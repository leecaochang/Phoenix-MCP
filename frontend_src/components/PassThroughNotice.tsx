/**
 * What a pass-through key may do, shown in place of its Permissions tree, with
 * Switch to Scoped Mode behind an inline confirmation in the calm look and
 * buttons that follow the display mode (user, 2026-10-28).
 */
import React, { useState } from "react";
import type { AccessKeyRecord, PatchAccessKeyBody } from "../types";
import { api } from "../api";
import { t } from "../i18n";
import { tRich } from "../i18n/rich";
import { ActionButton } from "../ui/ActionButton";
import { InlineConfirm } from "../ui/InlineConfirm";

interface Props {
  accessKey: AccessKeyRecord;
  onUpdate: (updated: AccessKeyRecord) => void;
}

export const PassThroughNotice = React.memo(function PassThroughNotice({ accessKey, onUpdate }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function convertToScoped() {
    setSaving(true);
    setError(null);
    try {
      const body: PatchAccessKeyBody = { pass_through: false };
      const updated = await api.patchAccessKey(accessKey.id, body);
      onUpdate(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.ptConvertFailed"));
    } finally {
      setSaving(false);
      setConfirming(false);
    }
  }

  return (
    <div>
      <div className="pass-through-header-banner">
        <p>
          {tRich("accessKeys.ptNoticeIntro", { strong: (c) => <strong>{c}</strong> })}
        </p>
        <p className="mt-8">
          {t("accessKeys.ptNoticeCaps")}
        </p>
        <p className="mt-8">
          {t("accessKeys.ptNoticeUsage")}
        </p>
      </div>

      {error && <div className="banner banner-error">{error}</div>}

      {!confirming ? (
        <ActionButton icon="switchPreset" label={t("accessKeys.switchToScoped")} className="btn btn-outline btn-sm" onClick={() => setConfirming(true)} />
      ) : (
        <InlineConfirm label={t("accessKeys.switchToScoped")} confirmLabel={t("accessKeys.switchToScoped")}
          confirmIcon="switchPreset" cancelLabel={t("accessKeys.cancel")} busy={saving}
          onConfirm={() => void convertToScoped()} onCancel={() => setConfirming(false)}>
          <p>{t("accessKeys.ptConvertBody")}</p>
        </InlineConfirm>
      )}
    </div>
  );
});
