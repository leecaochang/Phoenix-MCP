import { BusyButton } from "./BusyButton";
import React, { useState } from "react";
import type { AccessKeyRecord, PatchAccessKeyBody } from "../types";
import { api } from "../api";
import { t } from "../i18n";
import { tRich } from "../i18n/rich";

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
          {tRich("accessKeys.ptNoticeIntro", { strong: (c) => <strong className="text-warning">{c}</strong> })}
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
        <button
          className="btn btn-outline"
          onClick={() => setConfirming(true)}
        >
          {t("accessKeys.switchToScoped")}
        </button>
      ) : (
        <div className="card pass-through-convert-card">
          <p className="pass-through-convert-body">
            {t("accessKeys.ptConvertBody")}
          </p>
          <div className="pass-through-actions">
            <BusyButton
              className="btn btn-primary"
              onClick={convertToScoped}
              busy={saving}
            >
              {saving ? t("accessKeys.switching") : t("accessKeys.switchToScoped")}
            </BusyButton>
            <button className="btn btn-text" onClick={() => setConfirming(false)}>
              {t("accessKeys.cancel")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
});
