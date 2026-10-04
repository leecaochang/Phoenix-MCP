import { useState, useEffect } from "react";
import type { AccessKeyRecord, PatchAccessKeyBody } from "../types";
import { api } from "../api";
import { t } from "../i18n";

interface Props {
  accessKey: AccessKeyRecord;
  onUpdate: (updated: AccessKeyRecord) => void;
}

export function RateLimitConfig({ accessKey, onUpdate }: Props) {
  const [requests, setRequests] = useState(String(accessKey.rate_limit_requests));
  const [burst, setBurst] = useState(String(accessKey.rate_limit_burst));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRequests(String(accessKey.rate_limit_requests));
    setBurst(String(accessKey.rate_limit_burst));
  }, [accessKey.rate_limit_requests, accessKey.rate_limit_burst]);

  const requestsNum = parseInt(requests, 10);
  const burstDisabled = isNaN(requestsNum) || requestsNum === 0;

  async function save(reqStr: string, burstStr: string) {
    const reqNum = parseInt(reqStr, 10);
    const burstNum = burstDisabled ? 0 : parseInt(burstStr, 10);
    if (isNaN(reqNum) || reqNum < 0 || isNaN(burstNum) || burstNum < 0) {
      setError(t("accessKeys.rateValuesError"));
      return;
    }
    setError(null);
    try {
      const body: PatchAccessKeyBody = {
        rate_limit_requests: reqNum,
        rate_limit_burst: burstNum,
      };
      const updated = await api.patchAccessKey(accessKey.id, body);
      onUpdate(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.rateSaveFailed"));
    }
  }

  return (
    <div>
      {error && <div className="banner banner-error mb-8">{error}</div>}
      <div className="rate-limit-row">
        <div className="field">
          <label htmlFor="rate-limit-requests">{t("accessKeys.rateRequestsLabel")}</label>
          <input
            id="rate-limit-requests"
            className="input"
            type="number"
            min={0}
            value={requests}
            onChange={(e) => setRequests(e.target.value)}
            onBlur={(e) => save(e.target.value, burst)}
          />
        </div>
        <div className="field">
          <label htmlFor="rate-limit-burst">{t("accessKeys.rateBurstLabel")}</label>
          <input
            id="rate-limit-burst"
            className="input"
            type="number"
            min={0}
            value={burstDisabled ? "0" : burst}
            disabled={burstDisabled}
            onChange={(e) => setBurst(e.target.value)}
            onBlur={(e) => save(requests, e.target.value)}
          />
        </div>
      </div>
      {requestsNum === 0 && (
        <p className="rate-limit-disabled-text">
          {t("accessKeys.rateLimitingDisabled")}
        </p>
      )}
    </div>
  );
}
