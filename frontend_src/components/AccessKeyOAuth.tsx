// OAuth sign-in for one access key: the switch, the address to paste into an app, the
// sign-in window with its countdown, and the apps signed in. Shared by Key Settings and
// the OAuth side of the connect instructions. The window's end time comes from the
// server, which also decides it; this only shows it. Every copy on the page reloads when
// another one changes the key's sign-in, and polls while a window is open so an approval
// made in another tab shows up.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { t, tn } from "../i18n";
import { tRich } from "../i18n/rich";
import type { AccessKeyOAuthStatus, AccessKeyRecord } from "../types";
import { ActionButton } from "../ui/ActionButton";
import { Icon } from "../ui/Icon";
import { InlineConfirm } from "../ui/InlineConfirm";
import { useClickAway } from "../ui/useClickAway";
import { formatDateTime } from "../utils";
import { BusyInput } from "./BusyButton";
import { CopyCodeBox } from "./ConnectInstructions";

const POLL_MS = 3000;
const CHANGED = "phx-oauth-changed";

/** Tell every OAuth view on the page that a key's sign-in changed (no key: any key). */
export function announceOAuthChange(accessKeyId?: string): void {
  window.dispatchEvent(new CustomEvent(CHANGED, { detail: accessKeyId ?? null }));
}

/** Run `onChange` when any view announces a sign-in change for `accessKeyId` (or for every key, when omitted). */
export function useOAuthChanges(onChange: () => void, accessKeyId?: string): void {
  const latest = useRef(onChange);
  latest.current = onChange;
  useEffect(() => {
    const heard = (e: Event) => {
      const changed = (e as CustomEvent<string | null>).detail;
      if (!accessKeyId || !changed || changed === accessKeyId) latest.current();
    };
    window.addEventListener(CHANGED, heard);
    return () => window.removeEventListener(CHANGED, heard);
  }, [accessKeyId]);
}

/** Minutes and seconds left, as m:ss. */
export function countdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** The current time, ticking each second while `active`. */
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

type Outcome = { kind: "signedIn"; app: string } | null;

export function AccessKeyOAuth({ accessKeyId, version, onUpdate, compact = false, onAddress }: {
  accessKeyId: string;
  /** Changes whenever the key does (its updated time): rotation or revocation ends sign-ins elsewhere. */
  version?: string | null;
  /** Called with the key after the switch changed it. */
  onUpdate?: (record: AccessKeyRecord) => void;
  /** Inside the connect instructions: no sign-in list. */
  compact?: boolean;
  /** Told the HTTPS address apps should use, or null when Home Assistant has none. */
  onAddress?: (url: string | null) => void;
}) {
  const [status, setStatus] = useState<AccessKeyOAuthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmOff, setConfirmOff] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const removeBox = useRef<HTMLDivElement | null>(null);
  useClickAway(removeBox, () => setConfirmRemove(null), confirmRemove !== null && !saving);
  const previous = useRef<AccessKeyOAuthStatus | null>(null);
  const onAddressRef = useRef(onAddress);
  onAddressRef.current = onAddress;

  const until = status?.window_open_until ? Date.parse(status.window_open_until) : null;
  const now = useNow(until !== null);
  const open = until !== null && until > now && !status?.kill_switch;

  const absorb = useCallback((next: AccessKeyOAuthStatus) => {
    const before = previous.current;
    if (before?.window_open_until && !next.window_open_until && !next.kill_switch) {
      const added = next.sign_ins.find((s) => !before.sign_ins.some((b) => b.id === s.id));
      setOutcome(added ? { kind: "signedIn", app: added.client_name || t("oauth.unnamedApp") } : null);
    } else if (next.window_open_until) {
      setOutcome(null);
    }
    previous.current = next;
    setStatus(next);
    onAddressRef.current?.(next.public_url);
  }, []);

  const load = useCallback(async () => {
    try {
      absorb(await api.getAccessKeyOAuth(accessKeyId));
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oauth.loadFailed"));
    }
  }, [accessKeyId, absorb]);

  // Reload when the key changes and when the panel comes back into view: sign-ins end on
  // rotation, revocation or from another tab, and the list must not keep showing them.
  useEffect(() => { void load(); }, [load, version]);
  useOAuthChanges(() => void load(), accessKeyId);
  useEffect(() => {
    const again = () => { if (!document.hidden) void load(); };
    document.addEventListener("visibilitychange", again);
    window.addEventListener("focus", again);
    return () => {
      document.removeEventListener("visibilitychange", again);
      window.removeEventListener("focus", again);
    };
  }, [load]);
  useEffect(() => {
    if (!status?.window_open_until) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [status?.window_open_until, load]);
  // When the countdown reaches zero, ask the server once rather than waiting for a poll.
  useEffect(() => {
    if (until !== null && until <= now) void load();
  }, [until !== null && until <= now]); // eslint-disable-line react-hooks/exhaustive-deps

  async function setEnabled(enabled: boolean) {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.patchAccessKey(accessKeyId, { oauth_enabled: enabled });
      onUpdate?.(updated);
      setConfirmOff(false);
      setOutcome(null);
      announceOAuthChange(accessKeyId);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.updateFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function setWindow(open: boolean) {
    setSaving(true);
    setError(null);
    try {
      const next = open ? await api.openOAuthWindow(accessKeyId) : await api.closeOAuthWindow(accessKeyId);
      // Closing on purpose is not an outcome: leave nothing behind but the button to open it again.
      previous.current = null;
      absorb(next);
      setOutcome(null);
      announceOAuthChange(accessKeyId);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.updateFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function remove(signInId: string) {
    setSaving(true);
    setError(null);
    try {
      await api.removeOAuthSignIn(accessKeyId, signInId);
      announceOAuthChange(accessKeyId);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.updateFailed"));
    } finally {
      setSaving(false);
      setConfirmRemove(null);
    }
  }

  if (!status) return error ? <div role="alert" className="banner banner-error">{error}</div> : null;
  const enabled = status.oauth_enabled;
  const killed = status.kill_switch;
  const count = status.sign_ins.length;
  // Apps reach sign-in only over HTTPS. Without such an address nothing can sign in. With one,
  // sign-ins arrive and are approved at that address, so a panel opened over plain HTTP sends
  // the administrator there instead of offering controls that would lead nowhere.
  const noHttps = status.public_url === null;
  const httpsOrigin = status.public_url ? new URL(status.public_url).origin : "";
  const panelOnHttp = !noHttps && window.location.protocol === "http:";
  const unavailable = noHttps || panelOnHttp;
  // Under the kill switch the switch can still go off, which only takes access away.
  const blocked = unavailable || (killed && !enabled);
  const switchHelp = noHttps ? "oauth.noHttpsHelp" : panelOnHttp ? "oauth.httpPanelHelp"
    : killed && !enabled ? "oauth.stoppedHelp" : "oauth.switchHelp";

  return (
    <div className="phx-oauth" data-guide="window.keySettings#oauth-sign-in">
      {error && <div role="alert" className="banner banner-error mb-8">{error}</div>}
      {noHttps && <div role="alert" className="banner banner-error mb-8" data-help="oauth.noHttpsHelp">{t("oauth.noHttpsAddress")}</div>}
      {panelOnHttp && (
        <div role="alert" className="banner banner-warn mb-8 phx-oauth-http">
          <Icon name="error" size={16} />
          <span>
            {tRich("oauth.httpPanel", {
              link: (chunk) => <a href={`${httpsOrigin}/phoenix-mcp`} target="_top">{chunk}</a>,
            }, { address: `${httpsOrigin}/` })}
          </span>
        </div>
      )}
      <div className="toggle-row" data-help={switchHelp}>
        <div className="toggle-label"><span>{t("oauth.switchLabel")}</span></div>
        <label className="toggle-switch">
          <BusyInput
            type="checkbox"
            aria-label={t("oauth.switchLabel")}
            aria-description={t(switchHelp)}
            data-help={switchHelp}
            checked={enabled}
            busy={saving}
            disabled={blocked}
            onChange={(e) => {
              if (e.target.checked) void setEnabled(true);
              else if (count > 0) setConfirmOff(true);
              else void setEnabled(false);
            }}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
      {confirmOff && (
        <InlineConfirm label={t("oauth.turnOffTitle")} tone="warning" help={t("oauth.turnOffHelp")}
          confirmLabel={t("oauth.turnOffTitle")} cancelLabel={t("accessKeys.cancel")} busy={saving}
          onConfirm={() => void setEnabled(false)} onCancel={() => setConfirmOff(false)}>
          <p>{tn("oauth.endsSignIns", count)}</p>
        </InlineConfirm>
      )}
      {enabled && (
        <>
          {!unavailable && !killed && status.public_url && <CopyCodeBox label={t("oauth.address")} value={status.public_url} />}
          {!unavailable && (
            <div className="phx-oauth-window">
              {killed ? (
                <span role="status">{t("oauth.stopped")}</span>
              ) : open ? (
                <>
                  <span role="status" className="sr-only">{t("oauth.windowOpenSpoken")}</span>
                  <span aria-hidden="true" data-help="oauth.windowHelp">
                    {t("oauth.windowOpen", { time: countdown((until ?? now) - now) })}
                  </span>
                  <ActionButton icon="timerOff" label={t("oauth.stopWindow")} help="oauth.stopWindowHelp" className="btn btn-outline btn-sm"
                    guide="window.keySettings#oauth-allow-new" busy={saving} onClick={() => void setWindow(false)} />
                </>
              ) : (
                <>
                  <span role="status">{outcome ? t("oauth.signedIn", { app: outcome.app }) : ""}</span>
                  <ActionButton icon="timer" label={t("oauth.allowNew")} help="oauth.allowNewHelp" className="btn btn-outline btn-sm"
                    guide="window.keySettings#oauth-allow-new" busy={saving} onClick={() => void setWindow(true)} />
                </>
              )}
            </div>
          )}
          {!compact && (
            <div className="phx-oauth-sign-ins">
              <h4 className="phx-section-title">{t("oauth.signInsTitle")}</h4>
              {count === 0 ? <p className="wizard-hint">{t("oauth.noSignIns")}</p> : (
                <ul className="phx-oauth-list">
                  {status.sign_ins.map((s) => {
                    const app = s.client_name || t("oauth.unnamedApp");
                    const asked = confirmRemove === s.id;
                    return (
                    <li key={s.id}>
                      <div>
                        <strong>{s.client_name || t("oauth.unnamedApp")}</strong>{" "}
                        <span className="wizard-hint">{s.redirect_host}</span>
                        <div className="wizard-hint">
                          {t("oauth.lastUsed", { when: formatDateTime(s.last_used_at ?? s.created_at) })}
                        </div>
                      </div>
                      {/* Remove turns red to confirm, in place, with Cancel beside it; a click elsewhere cancels. */}
                      <div className="row-actions" ref={asked ? removeBox : undefined}>
                        <ActionButton icon="delete" label={asked ? t("common.confirm") : t("oauth.remove")} help="oauth.removeHelp"
                          className={asked ? "btn btn-danger btn-sm" : "btn btn-ghost btn-sm"}
                          name={asked ? t("oauth.removeConfirm", { app }) : t("oauth.removeAria", { app })}
                          busy={saving && asked} onClick={() => (asked ? void remove(s.id) : setConfirmRemove(s.id))} />
                        {asked && (
                          <ActionButton icon="undo" label={t("common.cancel")} className="btn btn-outline btn-sm"
                            busy={saving} onClick={() => setConfirmRemove(null)} />
                        )}
                      </div>
                    </li>
                    );
                  })}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
