// The approval an app's OAuth sign-in waits for. Phoenix's authorize endpoint sends the
// browser to /phoenix-mcp/oauth/<request id>, behind Home Assistant's own login, and this
// dialog shows who is asking and where it returns to. Approving binds the sign-in to one
// access key whose sign-in window is open; the browser then goes back to the app. The
// endpoint also lands here with "closed", "invalid" or "busy" when it parked nothing.
import { useCallback, useEffect, useId, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import type { OAuthSignInRequest } from "../types";
import { ActionButton, DisplayModeContext } from "../ui/ActionButton";
import { announceOAuthChange, countdown, useNow, useOAuthChanges } from "./AccessKeyOAuth";
import { Modal } from "./Modal";

const PAGES = new Set(["closed", "invalid", "busy"]);

/** The request ID or landing page named by the current address, or null. */
export function oauthRequestLink(): string | null {
  const match = window.location.pathname.match(/^\/phoenix-mcp\/oauth\/([^/]+)\/?$/);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return "invalid";
  }
}

function leaveLink() {
  try {
    window.history.replaceState(window.history.state, "", "/phoenix-mcp");
  } catch { /* URL API blocked: leave the address */ }
}

/** Mounted once in the shell: opens the dialog whenever the address names a request. */
export function OAuthRequestHost() {
  const [link, setLink] = useState<string | null>(oauthRequestLink);
  useEffect(() => {
    const follow = () => setLink(oauthRequestLink());
    window.addEventListener("location-changed", follow);
    window.addEventListener("popstate", follow);
    return () => {
      window.removeEventListener("location-changed", follow);
      window.removeEventListener("popstate", follow);
    };
  }, []);
  const close = useCallback(() => { leaveLink(); setLink(null); }, []);
  if (!link) return null;
  return <OAuthRequestDialog key={link} link={link} onClose={close} />;
}

type Phase =
  | { kind: "loading" }
  | { kind: "message"; text: string }
  | { kind: "request"; request: OAuthSignInRequest }
  | { kind: "leaving"; text: string };

function pageText(link: string): string {
  if (link === "closed") return t("oauth.closedPage");
  if (link === "busy") return t("oauth.busyPage");
  return t("oauth.invalidPage");
}

export function OAuthRequestDialog({ link, onClose }: { link: string; onClose: () => void }) {
  const titleId = useId();
  const framed = (() => { try { return window.top !== window.self; } catch { return true; } })();
  const [phase, setPhase] = useState<Phase>(() =>
    framed ? { kind: "message", text: t("oauth.framed") }
      : PAGES.has(link) ? { kind: "message", text: pageText(link) } : { kind: "loading" });
  const [chosen, setChosen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRequest = useCallback(() => api.getOAuthRequest(link).then((fetched) => {
    // The key whose sign-in was opened last comes first: usually the one just set up.
    const request = { ...fetched, keys: [...fetched.keys].sort((a, b) => Date.parse(b.window_open_until) - Date.parse(a.window_open_until)) };
    const usable = request.keys.filter((k) => !k.at_cap);
    setChosen((current) => (current && request.keys.some((k) => k.id === current && !k.at_cap) ? current
      : usable.length === 1 && request.keys.length === 1 ? usable[0].id : null));
    setPhase((p) => (p.kind === "leaving" ? p : { kind: "request", request }));
  }), [link]);

  useEffect(() => {
    if (phase.kind !== "loading") return;
    let live = true;
    fetchRequest().catch((e: unknown) => {
      if (!live) return;
      setPhase({ kind: "message", text: (e as { status?: number } | null)?.status === 404
        ? t("oauth.requestExpired") : e instanceof Error ? e.message : t("oauth.loadFailed") });
    });
    return () => { live = false; };
  }, [fetchRequest, phase.kind]);
  // A window opened or stopped in Key Settings while this is open changes which keys can take the app.
  useOAuthChanges(() => { if (phase.kind === "request") void fetchRequest().catch(() => {}); });

  const request = phase.kind === "request" ? phase.request : null;
  // The time left is the sign-in window's, the same countdown Key Settings shows, cut short only
  // if the request itself runs out first. No key open, or none chosen yet: the latest window.
  const windowEnds = (request?.keys ?? [])
    .filter((k) => (chosen ? k.id === chosen : true) && k.window_open_until)
    .map((k) => Date.parse(k.window_open_until));
  const expiresAt = request
    ? Math.min(Date.parse(request.expires_at), windowEnds.length ? Math.max(...windowEnds) : Infinity)
    : null;
  const now = useNow(expiresAt !== null);
  const expired = expiresAt !== null && expiresAt <= now;

  async function approve() {
    if (!request || !chosen) return;
    setBusy(true);
    setError(null);
    try {
      const { redirect_to } = await api.approveOAuthRequest(request.id, chosen);
      announceOAuthChange(chosen);
      setPhase({ kind: "leaving", text: t("oauth.approvedLeaving") });
      leaveLink();
      window.location.assign(redirect_to);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.updateFailed"));
      setBusy(false);
    }
  }

  async function deny() {
    if (!request) return onClose();
    setBusy(true);
    try {
      const { redirect_to } = await api.denyOAuthRequest(request.id);
      if (redirect_to) {
        setPhase({ kind: "leaving", text: t("oauth.deniedLeaving") });
        leaveLink();
        window.location.assign(redirect_to);
        return;
      }
    } catch { /* already gone: nothing to refuse */ }
    onClose();
  }

  const appName = request?.client_name || t("oauth.unnamedApp");
  return (
    // Only its buttons close it: a request dismissed by a stray click or key cannot be brought back.
    <Modal titleId={titleId} onClose={busy ? undefined : () => void deny()} dismiss="buttons">
      <div className="phx-oauth-request" data-guide="topic.connecting#oauth-approve">
        <h2 id={titleId}>{t("oauth.requestTitle")}</h2>
        {phase.kind === "loading" && <p role="status">{t("oauth.requestLoading")}</p>}
        {(phase.kind === "message" || phase.kind === "leaving") && <p role="status">{phase.text}</p>}
        {request && (
          <>
            <p>{t("oauth.requestIntro")}</p>
            <dl className="phx-oauth-facts">
              <dt>{t("oauth.appName")}</dt>
              <dd>{request.client_name ? t("oauth.claimedName", { name: appName }) : appName}</dd>
              <dt>{t("oauth.appHost")}</dt>
              <dd><code>{request.client_host}</code></dd>
              <dt>{t("oauth.returnHost")}</dt>
              <dd><code>{request.redirect_host}</code></dd>
            </dl>
            {request.loopback_only && <p className="banner banner-warn">{t("oauth.loopbackWarning")}</p>}
            {!request.verified ? (
              <p role="alert" className="banner banner-error">
                {t("oauth.notVerified")} {request.problem ? t(`oauth.problem.${request.problem}`) : ""}
              </p>
            ) : request.keys.length === 0 ? (
              <p role="alert" className="banner banner-warn">{t("oauth.noEligibleKeys")}</p>
            ) : (
              <fieldset className="phx-oauth-keys">
                <legend>{t("oauth.chooseKey")}</legend>
                {request.keys.map((k) => (
                  <label key={k.id} className="phx-oauth-key" data-help="oauth.chooseKeyHelp">
                    <input type="radio" name={`${titleId}-key`} value={k.id} checked={chosen === k.id}
                      disabled={k.at_cap || busy} onChange={() => setChosen(k.id)} />
                    <span>{k.at_cap ? t("oauth.keyAtCap", { name: k.name }) : k.name}</span>
                    {k.pass_through && <span className="wizard-hint"> {t("accessKeys.passThrough")}</span>}
                  </label>
                ))}
              </fieldset>
            )}
            <p className="wizard-hint" aria-hidden="true">
              {expired ? t("oauth.requestExpired") : t("oauth.requestExpires", { time: countdown((expiresAt ?? now) - now) })}
            </p>
            {expired && <span role="status" className="sr-only">{t("oauth.requestExpired")}</span>}
            {error && <p role="alert" className="banner banner-error">{error}</p>}
          </>
        )}
        {/* A consent decision is read, not recognised: the buttons keep their words in every display mode. */}
        <DisplayModeContext.Provider value="icon-text">
        <div className="modal-actions">
          {request && request.verified && request.keys.length > 0 && !expired && (
            <ActionButton icon="check" label={t("oauth.approve")} help="oauth.approveHelp" className="btn btn-primary btn-sm"
              busy={busy} disabled={!chosen} onClick={() => void approve()} />
          )}
          {request && phase.kind === "request" ? (
            <ActionButton icon="reject" label={request.verified ? t("oauth.deny") : t("oauth.close")}
              help={request.verified ? "oauth.denyHelp" : undefined} className="btn btn-outline btn-sm"
              busy={busy} onClick={() => void deny()} />
          ) : phase.kind !== "leaving" && (
            <ActionButton icon="close" label={t("oauth.close")} className="btn btn-outline btn-sm" onClick={onClose} />
          )}
        </div>
        </DisplayModeContext.Provider>
      </div>
    </Modal>
  );
}
