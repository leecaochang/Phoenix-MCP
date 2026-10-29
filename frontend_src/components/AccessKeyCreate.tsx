/**
 * Creating an access key: the form shown in place at the top of the Access Keys
 * window, then a dialog with the new key's one-time secret and the ways to use
 * it. The secret stays in a dialog so it cannot be scrolled past or lost by
 * moving a window.
 */

import React, { useEffect, useRef, useState } from "react";
import type { AccessKeyRecord, CreateAccessKeyBody } from "../types";
import { api } from "../api";
import { copyToClipboard } from "../utils";
import { t } from "../i18n";
import { tRich } from "../i18n/rich";
import { Modal } from "./Modal";
import { ConnectInstructions, CopyCodeBox } from "./ConnectInstructions";
import { ActionButton } from "../ui/ActionButton";
import { Icon, type IconName } from "../ui/Icon";

const NAME_REGEX = /^[A-Za-z0-9_\-]{3,32}$/;

interface Props {
  existingNames: string[];
  onCreated: (accessKey: AccessKeyRecord, rawAccessKey: string) => void;
  onClose: () => void;
  onOpenSettings: () => void;
}

type TtlUnit = "minutes" | "hours" | "days" | "weeks" | "none";

function slugify(name: string) {
  return name.toLowerCase().replace(/-/g, "_");
}

function addMinutes(m: number): string {
  const d = new Date(Date.now() + m * 60000);
  return d.toISOString();
}


export function CopyButton({ text, label = t("accessKeys.copyToClipboard") }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    await copyToClipboard(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <>
      <ActionButton icon={copied ? "check" : "copy"} label={copied ? t("accessKeys.copied") : label} className="btn btn-outline btn-sm" onClick={() => void copy()} />
      {/* Announce the visual Copy -> Copied! swap to assistive tech. */}
      <span className="sr-only" role="status">{copied ? t("accessKeys.copiedToClipboard") : ""}</span>
    </>
  );
}

// The raw-access-key reveal block (amber warning + monospace access key). Shared by the
// post-create modal and the onboarding wizard so both look identical. The
// warning text is overridable because the wizard shows the access key again on its
// Connect step, so the default "will not be shown again" copy is inaccurate there.
export function RawAccessKeyDisplay({ rawAccessKey, note }: { rawAccessKey: string; note?: React.ReactNode }) {
  return (
    <>
      <div className="amber-block">
        {note ?? <p>{tRich("accessKeys.rawAccessKeyNote", { strong: (c) => <strong>{c}</strong> })}</p>}
      </div>
      <CopyCodeBox value={rawAccessKey} />
    </>
  );
}

interface PostCreateProps {
  rawAccessKey: string;
  accessKeyName: string;
  onOpenSettings: () => void;   // "Setup Agent Chat" -> closes this modal, opens Settings
  onDone: () => void;           // finish -> refresh + open the new access key's detail page
}

// After an access key is created, offer three ways forward instead of dumping the raw
// access key immediately: connect an external agent, set up in-panel Agent Chat, or
// just reveal the access key to copy. It is drawn over the whole workspace, not inside
// the Access Keys window, and in the UI2 style: plain rows with icons, no coloured
// accents (user, 2026-10-28).
function PostCreateModal({ rawAccessKey, accessKeyName, onOpenSettings, onDone }: PostCreateProps) {
  const [view, setView] = useState<"choice" | "connect" | "access-key">("choice");

  // On view switches the clicked control unmounts; refocus the first control
  // of the new view so keyboard focus never falls out of the dialog.
  const viewsRef = useRef<HTMLDivElement>(null);
  const prevViewRef = useRef(view);
  useEffect(() => {
    if (prevViewRef.current === view) return;
    prevViewRef.current = view;
    viewsRef.current
      ?.querySelector<HTMLElement>("button, [href], input, select, textarea")
      ?.focus();
  }, [view]);

  const choices: { id: string; icon: IconName; title: string; sub: string; onClick: () => void }[] = [
    { id: "connect", icon: "popOut", title: t("accessKeys.postCreateConnectTitle"), sub: t("accessKeys.postCreateConnectSub"), onClick: () => setView("connect") },
    { id: "chat", icon: "agentChat", title: t("accessKeys.postCreateChatTitle"), sub: t("accessKeys.postCreateChatSub"), onClick: onOpenSettings },
    { id: "show", icon: "accessKeys", title: t("accessKeys.postCreateShowTitle"), sub: t("accessKeys.postCreateShowSub"), onClick: () => setView("access-key") },
  ];
  const back = <ActionButton icon="back" label={t("accessKeys.back")} className="btn btn-ghost btn-sm" onClick={() => setView("choice")} />;
  const done = <ActionButton icon="check" label={t("accessKeys.done")} className="btn btn-primary btn-sm" onClick={onDone} />;

  return (
    <Modal titleId="created-access-key-title" onClose={onDone} overWorkspace>
      <h3 className="modal-title" id="created-access-key-title">{t("accessKeys.accessKeyCreatedTitle", { name: accessKeyName })}</h3>
      <div ref={viewsRef} style={{ display: "contents" }}>

      {view === "choice" && (
        <>
          <p className="modal-body-text">{t("accessKeys.postCreateQuestion")}</p>
          <div className="post-create-choices">
            {choices.map((c) => (
              <button key={c.id} type="button" className="post-create-choice" onClick={c.onClick} aria-label={c.title} aria-description={c.sub}>
                <Icon name={c.icon} size={18} className="post-create-choice-icon" />
                <span className="post-create-choice-text">
                  <span className="post-create-choice-title">{c.title}</span>
                  <span className="post-create-choice-sub">{c.sub}</span>
                </span>
              </button>
            ))}
          </div>
          <div className="modal-actions">
            <ActionButton icon="close" label={t("common.close")} className="btn btn-outline btn-sm" onClick={onDone} />
          </div>
        </>
      )}

      {view === "connect" && (
        <>
          <ConnectInstructions accessKey={rawAccessKey} accessKeyName={accessKeyName} />
          <div className="modal-actions">{back}{done}</div>
        </>
      )}

      {view === "access-key" && (
        <>
          <RawAccessKeyDisplay rawAccessKey={rawAccessKey} />
          <p className="modal-body-text">{t("accessKeys.postCreateReplaceNote")}</p>
          <div className="modal-actions">{back}{done}</div>
        </>
      )}
      </div>
    </Modal>
  );
}

export function AccessKeyCreateForm({ existingNames, onCreated, onClose, onOpenSettings }: Props) {
  const [name, setName] = useState("");
  const [ttlUnit, setTtlUnit] = useState<TtlUnit>("none");
  const [ttlValue, setTtlValue] = useState("24");
  const [passThrough, setPassThrough] = useState(false);
  const [ptConfirmed, setPtConfirmed] = useState(false);
  const [rateLimitRequests, setRateLimitRequests] = useState("60");
  const [rateLimitBurst, setRateLimitBurst] = useState("10");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdAccessKey, setCreatedAccessKey] = useState<{ record: AccessKeyRecord; raw: string } | null>(null);

  const nameError = (() => {
    if (!name) return null;
    if (!NAME_REGEX.test(name)) return t("accessKeys.nameFormatError");
    const slug = slugify(name);
    if (existingNames.some((n) => slugify(n) === slug)) return t("accessKeys.nameDuplicateError");
    return null;
  })();

  const reqNum = parseInt(rateLimitRequests, 10);
  const burstDisabled = isNaN(reqNum) || reqNum === 0;

  const canSubmit =
    name.length >= 3 &&
    !nameError &&
    (!passThrough || ptConfirmed);

  async function submit() {
    setSaving(true);
    setError(null);
    try {
      let expiresAt: string | undefined;
      if (ttlUnit !== "none") {
        const n = parseInt(ttlValue, 10);
        const minutes =
          ttlUnit === "minutes" ? n :
          ttlUnit === "hours" ? n * 60 :
          ttlUnit === "days" ? n * 60 * 24 :
          n * 60 * 24 * 7;
        expiresAt = addMinutes(minutes);
      }
      const burstNum = burstDisabled ? 0 : parseInt(rateLimitBurst, 10);
      const body: CreateAccessKeyBody = {
        name,
        expires_at: expiresAt,
        pass_through: passThrough,
        confirm_pass_through: passThrough ? true : undefined,
        rate_limit_requests: parseInt(rateLimitRequests, 10) || 0,
        rate_limit_burst: burstNum,
      };
      const resp = await api.createAccessKey(body);
      const { access_key: rawAccessKey, ...record } = resp;
      setCreatedAccessKey({ record, raw: rawAccessKey });
      // The caller's onRefresh covers the panel shell, but the floating chat
      // window is a separate bundle that only learns about new access keys here.
      window.dispatchEvent(new CustomEvent("phx-access-keys-changed"));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.createFailed"));
    } finally {
      setSaving(false);
    }
  }

  if (createdAccessKey) {
    return (
      <PostCreateModal
        rawAccessKey={createdAccessKey.raw}
        accessKeyName={createdAccessKey.record.name}
        onOpenSettings={() => { onClose(); onOpenSettings(); }}
        onDone={() => {
          onCreated(createdAccessKey.record, createdAccessKey.raw);
          onClose();
        }}
      />
    );
  }

  return (
    <section className="phx-inline-form" aria-labelledby="create-access-key-title"
      onKeyDown={(e) => {
        if (e.key !== "Escape" || saving) return;
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }}>
      <h3 className="phx-section-title" id="create-access-key-title">{t("accessKeys.createAccessKey")}</h3>

      {/* One row of fields that wraps on narrow windows (user, 2026-10-28). */}
      <div className="access-key-create-fields">
        <div className="field access-key-create-name">
          <label htmlFor="access-key-name-input">{t("accessKeys.nameLabel")}</label>
          <input
            id="access-key-name-input"
            className={`input${nameError ? " error" : ""}`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("accessKeys.namePlaceholder")}
            maxLength={32}
            autoFocus
            aria-invalid={nameError ? true : undefined}
            aria-describedby={nameError ? "access-key-name-error" : undefined}
          />
          {nameError && <span className="field-error" id="access-key-name-error" role="alert">{nameError}</span>}
        </div>

        <div className="field">
          <label htmlFor="access-key-expiry-unit">{t("accessKeys.expiryLabel")}</label>
          <div className="access-key-create-expiry-row">
            <select
              id="access-key-expiry-unit"
              className="input input-auto"
              value={ttlUnit}
              onChange={(e) => setTtlUnit(e.target.value as TtlUnit)}
            >
              <option value="none">{t("accessKeys.expiryNone")}</option>
              <option value="minutes">{t("accessKeys.expiryMinutes")}</option>
              <option value="hours">{t("accessKeys.expiryHours")}</option>
              <option value="days">{t("accessKeys.expiryDays")}</option>
              <option value="weeks">{t("accessKeys.expiryWeeks")}</option>
            </select>
            {ttlUnit !== "none" && (
              <input
                aria-label={t("accessKeys.expiryAmountAria")}
                className="input access-key-create-expiry-value"
                type="number"
                min={1}
                value={ttlValue}
                onChange={(e) => setTtlValue(e.target.value)}
              />
            )}
          </div>
        </div>

        {!passThrough && (
          <>
            <div className="field access-key-create-number">
              <label htmlFor="create-rate-requests">{t("accessKeys.rateRequestsLabel")}</label>
              <input
                id="create-rate-requests"
                className="input"
                type="number"
                min={0}
                value={rateLimitRequests}
                onChange={(e) => setRateLimitRequests(e.target.value)}
              />
            </div>
            <div className="field access-key-create-number">
              <label htmlFor="create-rate-burst">{t("accessKeys.rateBurstLabel")}</label>
              <input
                id="create-rate-burst"
                className="input"
                type="number"
                min={0}
                value={burstDisabled ? "0" : rateLimitBurst}
                disabled={burstDisabled}
                onChange={(e) => setRateLimitBurst(e.target.value)}
              />
            </div>
          </>
        )}
      </div>

      {/* What pass-through does is in Help (user, 2026-10-28). */}
      <div className="toggle-row access-key-create-pt" data-help="accessKeys.passThroughModeHelp">
        <label className="toggle-switch">
          <input
            type="checkbox"
            aria-label={t("accessKeys.passThroughMode")}
            aria-description={t("accessKeys.passThroughModeHelp")}
            checked={passThrough}
            onChange={(e) => { setPassThrough(e.target.checked); setPtConfirmed(false); }}
          />
          <span className="toggle-switch-track" />
        </label>
        <div className="toggle-label"><span>{t("accessKeys.passThroughMode")}</span></div>
      </div>

      {passThrough && (
        <div className="amber-block">
          <p>
            {tRich("accessKeys.createPtWarning", { strong: (c) => <strong>{c}</strong> })}
          </p>
          <div className="toggle-row mt-10">
            <div className="toggle-label"><span>{t("accessKeys.createPtUnderstand")}</span></div>
            <label className="toggle-switch">
              <input
                type="checkbox"
                aria-label={t("accessKeys.createPtUnderstand")}
                checked={ptConfirmed}
                onChange={(e) => setPtConfirmed(e.target.checked)}
              />
              <span className="toggle-switch-track" />
            </label>
          </div>
        </div>
      )}

      {error && <div className="banner banner-error mt-12">{error}</div>}

      <div className="phx-inline-form-actions">
        <ActionButton icon="add" label={t("accessKeys.create")} busy={saving} disabled={!canSubmit}
          onClick={() => { void submit(); }} className="btn btn-primary btn-sm" />
        <ActionButton icon="undo" label={t("accessKeys.cancel")} busy={saving} onClick={onClose} className="btn btn-outline btn-sm" />
      </div>
    </section>
  );
}
