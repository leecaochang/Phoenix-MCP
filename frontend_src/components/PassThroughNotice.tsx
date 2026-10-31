/**
 * Switch to Scoped Mode, shown in place of a pass-through key's Permissions
 * tree. The panel shows at once, without a first press to open it, and what
 * pass-through allows and what switching does read in the Help window
 * (user, 2026-10-31).
 */
import React, { useState } from "react";
import type { AccessKeyRecord, PatchAccessKeyBody } from "../types";
import { api } from "../api";
import { t } from "../i18n";
import { tPlain } from "../i18n/rich";
import { ActionButton } from "../ui/ActionButton";

interface Props {
  accessKey: AccessKeyRecord;
  onUpdate: (updated: AccessKeyRecord) => void;
}

export const PassThroughNotice = React.memo(function PassThroughNotice({ accessKey, onUpdate }: Props) {
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
    }
  }

  const help = [tPlain("accessKeys.ptNoticeIntro"), t("accessKeys.ptNoticeCaps"), t("accessKeys.ptNoticeUsage"), t("accessKeys.ptConvertBody")]
    .join(t("common.sentenceSeparator"));
  return (
    <div className="phx-inline-confirm is-warning" role="group" aria-label={t("accessKeys.switchToScoped")} data-help-text={help} data-help-controls="">
      <div className="phx-inline-confirm-title">{t("accessKeys.switchToScoped")}</div>
      {error && <div className="banner banner-error" role="alert">{error}</div>}
      <div className="phx-inline-confirm-actions">
        <ActionButton icon="switchPreset" label={t("accessKeys.switchToScoped")} className="btn btn-outline btn-sm" busy={saving}
          helpText={help} onClick={() => void convertToScoped()} />
      </div>
    </div>
  );
});
