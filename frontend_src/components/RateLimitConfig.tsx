import { useState, useEffect, useRef } from "react";
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

  // Edit revisions per field, and the revision each field had when it was last
  // in step with a saved value. Only the newest save's reply may update a
  // field, and only one nothing was typed into since that save was sent: an
  // earlier reply must not rewind a newer edit, even one that typed the old
  // value back. Saved values arriving from elsewhere wait until no save of
  // this form is in flight.
  const edits = useRef({ requests: 0, burst: 0 });
  const inStep = useRef({ requests: 0, burst: 0 });
  const submitted = useRef(0);
  const inFlight = useRef(0);
  const keyId = useRef(accessKey.id);
  useEffect(() => {
    const otherKey = keyId.current !== accessKey.id;
    keyId.current = accessKey.id;
    if (!otherKey && inFlight.current > 0) return;
    if (otherKey || edits.current.requests === inStep.current.requests) {
      setRequests(String(accessKey.rate_limit_requests));
      inStep.current.requests = edits.current.requests;
    }
    if (otherKey || edits.current.burst === inStep.current.burst) {
      setBurst(String(accessKey.rate_limit_burst));
      inStep.current.burst = edits.current.burst;
    }
  }, [accessKey.id, accessKey.rate_limit_requests, accessKey.rate_limit_burst]);

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
    const submission = ++submitted.current;
    const sent = { ...edits.current };
    inFlight.current += 1;
    let updated: AccessKeyRecord;
    try {
      const body: PatchAccessKeyBody = {
        rate_limit_requests: reqNum,
        rate_limit_burst: burstNum,
      };
      updated = await api.patchAccessKey(accessKey.id, body);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.rateSaveFailed"));
      return;
    } finally {
      inFlight.current -= 1;
    }
    if (submission === submitted.current) {
      if (edits.current.requests === sent.requests) {
        setRequests(String(updated.rate_limit_requests));
        inStep.current.requests = sent.requests;
      }
      if (edits.current.burst === sent.burst) {
        setBurst(String(updated.rate_limit_burst));
        inStep.current.burst = sent.burst;
      }
    }
    onUpdate(updated);
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
            onChange={(e) => { edits.current.requests += 1; setRequests(e.target.value); }}
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
            onChange={(e) => { edits.current.burst += 1; setBurst(e.target.value); }}
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
