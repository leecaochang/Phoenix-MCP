import { BusyInput } from "./BusyButton";
import type { GlobalSettings } from "../types";
import { t } from "../i18n";

interface Props {
  settings: GlobalSettings;
  onToggle: (key: keyof GlobalSettings, value: boolean) => void;
  saving: boolean;
}

export function NotificationSettings({ settings, onToggle, saving }: Props) {
  return (
    <div>
      <div className="toggle-row">
        <div className="toggle-label">
          <span>{t("settings.notifyApprovalLabel")}</span>
          <small>
            {t("settings.notifyApprovalHelp")}
          </small>
        </div>
        <label className={`toggle-switch${saving ? " disabled" : ""}`}>
          <BusyInput
            type="checkbox"
            aria-label={t("settings.notifyApprovalLabel")}
            checked={settings.notify_on_approval}
            busy={saving}
            onChange={(e) => onToggle("notify_on_approval", e.target.checked)}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
      <div className="toggle-row">
        <div className="toggle-label">
          <span>{t("settings.notifyRateLimitLabel")}</span>
          <small>
            {t("settings.notifyRateLimitHelp")}
          </small>
        </div>
        <label className={`toggle-switch${saving ? " disabled" : ""}`}>
          <BusyInput
            type="checkbox"
            aria-label={t("settings.notifyRateLimitLabel")}
            checked={settings.notify_on_rate_limit}
            busy={saving}
            onChange={(e) => onToggle("notify_on_rate_limit", e.target.checked)}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
    </div>
  );
}
