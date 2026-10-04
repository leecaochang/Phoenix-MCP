import { BusyInput, BusySelect } from "../components/BusyButton";
import { BusyButton } from "../components/BusyButton";
import React, { useState, useEffect, useCallback, useRef } from "react";
import type { AccessKeyRecord, PatchAccessKeyBody } from "../types";
import PHOENIX_ICON from "../../custom_components/phoenix_mcp/brand/icon.png";
import { api } from "../api";
import { Loading, ErrorMsg } from "../index";
import { formatDateTime, accessKeyStatus, accessKeyStatusLabel } from "../utils";
import type { LinkScope } from "../components/MesaProfileLink";
import { Modal } from "../components/Modal";
import { RawAccessKeyDisplay } from "../components/AccessKeyCreateModal";
import { ConnectInstructions } from "../components/ConnectInstructions";
import { CapabilityMatrix, CAP_NAMES } from "../components/CapabilityMatrix";
import type { EsphomeAvailability } from "../components/CapabilityMatrix";
import { PersonaPicker } from "../components/PersonaPicker";
import { PERSONAS } from "../personas";
import { RateLimitConfig } from "../components/RateLimitConfig";
import { CollapsibleCard } from "../components/CollapsibleCard";
import { PassThroughNotice } from "../components/PassThroughNotice";
import { EntityTree } from "../components/EntityTree";
import { PermissionSummary } from "../components/PermissionSummary";
import { PermissionSimulator } from "../components/PermissionSimulator";
import { SelectByPicker } from "../components/SelectByPicker";
import { DocsHelpLink } from "../components/common";
import { ProfileEditor } from "./MesaView";
import { localeDateTime, t } from "../i18n";
import { tRich } from "../i18n/rich";

// Whether a token change alters which tools the MCP client is shown (tools/list),
// which is the only kind of change that requires a connected agent to reconnect.
// A capability only changes the announced set when it crosses the deny boundary: a
// cap-tied tool is announced whenever its cap is not "deny", so switching between
// allow and confirm (per-request gating, enforced live) does NOT need a reconnect.
// pass_through and announce_all_tools change the announced set wholesale. Persona
// changes surface here as capability changes.
function toolGatingChanged(a: AccessKeyRecord, b: AccessKeyRecord): boolean {
  const capCrossedDeny = CAP_NAMES.some((c) => (a[c] === "deny") !== (b[c] === "deny"));
  return capCrossedDeny || a.pass_through !== b.pass_through || a.announce_all_tools !== b.announce_all_tools;
}

// The destructive actions on this page, each behind its own confirm modal.
// Exactly one can be in flight at a time; see the `action` state below.
type AccessKeyAction = "revoke" | "rotate" | "passThrough" | "clearPerms";

interface Props {
  accessKeyId: string;
  onBack: () => void;
  onRefresh?: () => void;
  // settings.access_key_presets_enabled, threaded from the panel shell; the Presets
  // card renders only while the feature toggle is on.
  presetsEnabled?: boolean;
  // Which ESPHome surfaces exist on this system, threaded from the panel shell.
  // Null while settings are still loading; the ESPHome controls simply show no
  // note until it arrives.
  esphome?: EsphomeAvailability | null;
}


interface ConfirmModalProps {
  title: string;
  body: React.ReactNode;
  checkLabel: string;
  confirmLabel: string;
  confirmClass: string;
  loading: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

function ConfirmModal({ title, body, checkLabel, confirmLabel, confirmClass, loading, onConfirm, onClose }: ConfirmModalProps) {
  const [checked, setChecked] = useState(false);
  const titleId = `confirm-modal-${title.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <Modal titleId={titleId} onClose={loading ? undefined : onClose}>
      <h3 className="modal-title" id={titleId}>{title}</h3>
      {body}
      <div className="toggle-row mt-12" style={{ borderTop: "1px solid var(--phx-border)", paddingTop: 12 }}>
        <div className="toggle-label"><span>{checkLabel}</span></div>
        <label className="toggle-switch">
          <input
            type="checkbox"
            aria-label={checkLabel}
            checked={checked}
            onChange={(e) => setChecked(e.target.checked)}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
      <div className="modal-actions">
        <BusyButton className={`btn ${confirmClass}`} onClick={onConfirm} busy={loading} disabled={!checked}>
          {loading ? t("accessKeys.pleaseWait") : confirmLabel}
        </BusyButton>
        <BusyButton className="btn btn-text" onClick={onClose} busy={loading}>{t("accessKeys.cancel")}</BusyButton>
      </div>
    </Modal>
  );
}

function RotatedAccessKeyModal({ rawAccessKey, accessKeyName, onClose }: { rawAccessKey: string; accessKeyName: string; onClose: () => void }) {
  const [closeEnabled, setCloseEnabled] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    timerRef.current = setTimeout(() => setCloseEnabled(true), 3000);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, []);

  return (
    <Modal titleId="rotated-access-key-title" onClose={closeEnabled ? onClose : undefined}>
      <h3 className="modal-title" id="rotated-access-key-title">{t("accessKeys.accessKeyRotatedTitle", { name: accessKeyName })}</h3>
      <RawAccessKeyDisplay
        rawAccessKey={rawAccessKey}
        note={<p>{tRich("accessKeys.rotatedNote", { strong: (c) => <strong>{c}</strong> })}</p>}
      />
      <div className="banner banner-info">
        {t("accessKeys.rotatedBanner")}
      </div>
      <details className="connect-details">
        <summary>{t("accessKeys.helpConnect")}</summary>
        <ConnectInstructions accessKey={rawAccessKey} accessKeyName={accessKeyName} />
      </details>
      <div className="modal-actions">
        <button
          className="btn btn-text"
          onClick={onClose}
          disabled={!closeEnabled}
          title={closeEnabled ? undefined : t("accessKeys.waitBeforeClose")}
        >
          {closeEnabled ? t("common.close") : t("accessKeys.closeCountdown")}
        </button>
      </div>
    </Modal>
  );
}

export function ToolAnnouncementToggle({ accessKey, onUpdate }: { accessKey: AccessKeyRecord; onUpdate: (t: AccessKeyRecord) => void }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function patch(body: PatchAccessKeyBody) {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.patchAccessKey(accessKey.id, body);
      onUpdate(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.updateFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      {error && <div className="banner banner-error mb-8">{error}</div>}
      <div className="toggle-row">
        <div className="toggle-label">
          <span>{t("accessKeys.announceAllLabel")}</span>
          <small>{t("accessKeys.announceAllHelp")}</small>
        </div>
        <label className="toggle-switch">
          <BusyInput
            type="checkbox"
            aria-label={t("accessKeys.announceAllLabel")}
            checked={!!accessKey.announce_all_tools}
            busy={saving}
            onChange={(e) => patch({ announce_all_tools: e.target.checked })}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
      <div className="toggle-row">
        <div className="toggle-label">
          <span>{t("accessKeys.assistExposureLabel")}</span>
          <small>{t("accessKeys.assistExposureHelp")}</small>
        </div>
        <label className="toggle-switch">
          <BusyInput
            type="checkbox"
            aria-label={t("accessKeys.assistExposureLabel")}
            checked={!!accessKey.use_assist_exposure}
            busy={saving} disabled={!accessKey.pass_through}
            onChange={(e) => patch({ use_assist_exposure: e.target.checked })}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
      <div className="toggle-row toggle-row-stacked-control">
        <div className="toggle-label">
          <span>{t("accessKeys.inlineWaitLabel")}</span>
          <small>
            {t("accessKeys.inlineWaitHelp")}
            {(accessKey.confirm_inline_wait_seconds ?? 0) === 0 && (
              <> <em> {t("accessKeys.inlineWaitDisabled")}</em></>
            )}
          </small>
        </div>
        <BusySelect
          className="input input-auto"
          aria-label={t("accessKeys.inlineWaitAria")}
          value={accessKey.confirm_inline_wait_seconds ?? 0}
          busy={saving}
          onChange={(e) => patch({ confirm_inline_wait_seconds: Number(e.target.value) })}
        >
          <option value={0}>{t("accessKeys.inlineWaitOff")}</option>
          {inlineWaitOptions(accessKey.confirm_inline_wait_seconds ?? 0).map((s) => (
            <option key={s} value={s}>{t("accessKeys.secondsOption", { s })}</option>
          ))}
        </BusySelect>
      </div>
    </>
  );
}

// Duration choices for the inline-wait select: fixed steps across the allowed
// 30-180 range (const.py MIN/MAX_CONFIRM_INLINE_WAIT_SECONDS), plus the token's
// own value if the API set an off-step one (so it still displays correctly). 0
// (off) is rendered separately as the first option and is the DEFAULT (const.py
// DEFAULT_CONFIRM_INLINE_WAIT_SECONDS): a confirm-gated call returns pending
// immediately so approvals stage in the queue, and an agent that wants the
// outcome calls wait_for_approval. Turning the wait on makes a single action
// block, which is a preference rather than something every caller pays for.
function inlineWaitOptions(current: number): number[] {
  const base = [30, 45, 60, 90, 120, 150, 180];
  const withCurrent = current > 0 && !base.includes(current) ? [...base, current] : base;
  return [...new Set(withCurrent)].sort((a, b) => a - b);
}

// ---------------------------------------------------------------------------
// Token settings presets (workspace model). A preset is a named snapshot of the
// token's full settings; the ACTIVE preset absorbs live edits when the admin
// switches to another preset, and applying the active preset reverts to its
// saved state. Enforcement never reads presets; apply routes through the same
// backend paths as manual edits.
// ---------------------------------------------------------------------------

const MAX_PRESETS = 8; // mirrors const.py MAX_PRESETS_PER_TOKEN

// Normalized comparable view of a settings snapshot, shared by the token's
// live state and a stored preset so dirty detection and diffs line up.
interface SettingsSnap {
  caps: Record<string, string>;
  pass_through: boolean;
  use_assist_exposure: boolean;
  announce_all_tools: boolean;
  confirm_inline_wait_seconds: number;
  rate_limit_requests: number;
  rate_limit_burst: number;
  permissions: import("../types").PermissionTree;
}

function snapOfAccessKey(tok: AccessKeyRecord): SettingsSnap {
  return {
    caps: Object.fromEntries(CAP_NAMES.map((c) => [c, tok[c]])),
    pass_through: tok.pass_through,
    use_assist_exposure: tok.pass_through ? (tok.use_assist_exposure ?? false) : false,
    announce_all_tools: tok.announce_all_tools ?? false,
    confirm_inline_wait_seconds: tok.confirm_inline_wait_seconds ?? 0,
    rate_limit_requests: tok.rate_limit_requests,
    rate_limit_burst: tok.rate_limit_burst,
    permissions: tok.permissions,
  };
}

function snapOfPreset(p: import("../types").AccessKeyPreset): SettingsSnap {
  return {
    caps: Object.fromEntries(CAP_NAMES.map((c) => [c, p.caps[c] ?? "deny"])),
    pass_through: p.pass_through,
    use_assist_exposure: p.pass_through ? p.use_assist_exposure : false,
    announce_all_tools: p.announce_all_tools,
    confirm_inline_wait_seconds: p.confirm_inline_wait_seconds ?? 0,
    rate_limit_requests: p.rate_limit_requests,
    rate_limit_burst: p.rate_limit_burst,
    permissions: p.permissions,
  };
}

function nodeSig(n?: import("../types").PermissionNode): string {
  return n ? `${n.state}|${n.hint ?? ""}` : "GREY|";
}

// Human-readable list of what changes going FROM one snapshot TO another.
// Empty means the two are identical (used as the dirty check).
function snapDiffLines(from: SettingsSnap, to: SettingsSnap): string[] {
  const lines: string[] = [];
  for (const c of CAP_NAMES) {
    if (from.caps[c] !== to.caps[c]) lines.push(`${c}: ${from.caps[c]} -> ${to.caps[c]}`);
  }
  if (from.pass_through !== to.pass_through) {
    lines.push(t("accessKeys.diffPassThrough", {
      from: from.pass_through ? t("accessKeys.on") : t("accessKeys.off"),
      to: to.pass_through ? t("accessKeys.on") : t("accessKeys.off"),
    }));
  }
  if (from.use_assist_exposure !== to.use_assist_exposure) {
    lines.push(t("accessKeys.diffAssistExposure", {
      from: from.use_assist_exposure ? t("accessKeys.on") : t("accessKeys.off"),
      to: to.use_assist_exposure ? t("accessKeys.on") : t("accessKeys.off"),
    }));
  }
  if (from.announce_all_tools !== to.announce_all_tools) {
    lines.push(t("accessKeys.diffAnnounceAll", {
      from: from.announce_all_tools ? t("accessKeys.on") : t("accessKeys.off"),
      to: to.announce_all_tools ? t("accessKeys.on") : t("accessKeys.off"),
    }));
  }
  if (from.confirm_inline_wait_seconds !== to.confirm_inline_wait_seconds) {
    lines.push(t("accessKeys.diffInlineWait", {
      from: from.confirm_inline_wait_seconds
        ? t("accessKeys.secondsShort", { n: from.confirm_inline_wait_seconds })
        : t("accessKeys.off"),
      to: to.confirm_inline_wait_seconds
        ? t("accessKeys.secondsShort", { n: to.confirm_inline_wait_seconds })
        : t("accessKeys.off"),
    }));
  }
  if (from.rate_limit_requests !== to.rate_limit_requests || from.rate_limit_burst !== to.rate_limit_burst) {
    lines.push(t("accessKeys.diffRateLimit", {
      fromRequests: from.rate_limit_requests,
      fromBurst: from.rate_limit_burst,
      toRequests: to.rate_limit_requests,
      toBurst: to.rate_limit_burst,
    }));
  }
  let permDiff = 0;
  for (const level of ["domains", "devices", "entities"] as const) {
    const keys = new Set([...Object.keys(from.permissions[level]), ...Object.keys(to.permissions[level])]);
    for (const k of keys) {
      if (nodeSig(from.permissions[level][k]) !== nodeSig(to.permissions[level][k])) permDiff++;
    }
  }
  if (permDiff > 0) lines.push(t("accessKeys.diffPermissionTree", { count: permDiff }));
  return lines;
}

// Inline-editable preset name: same onblur-save pattern as EditableAccessKeyName.
function EditablePresetName(
  { accessKeyId, preset, onUpdate }:
  { accessKeyId: string; preset: import("../types").AccessKeyPreset; onUpdate: (t: AccessKeyRecord) => void },
) {
  const [value, setValue] = useState(preset.name);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { setValue(preset.name); setErr(null); }, [preset.name]);

  async function commit() {
    const next = value.trim();
    if (next === preset.name) { setValue(preset.name); setErr(null); return; }
    if (!next || next.length > 40) {
      setErr(t("accessKeys.presetNameError"));
      setValue(preset.name);
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      onUpdate(await api.renamePreset(accessKeyId, preset.id, next));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : t("accessKeys.renameFailed"));
      setValue(preset.name);
    } finally {
      setSaving(false);
    }
  }

  return (
    <span className="preset-name-edit">
      <BusyInput
        className="preset-name-input"
        value={value}
        busy={saving}
        spellCheck={false}
        maxLength={40}
        aria-label={t("accessKeys.presetNameAria", { name: preset.name })}
        title={t("accessKeys.presetRenameTitle")}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); (e.target as HTMLInputElement).blur(); }
          else if (e.key === "Escape") { setValue(preset.name); setErr(null); (e.target as HTMLInputElement).blur(); }
        }}
      />
      {err && <span className="access-key-name-error" role="alert">{err}</span>}
    </span>
  );
}

type PresetAction = { kind: "switch" | "revert" | "delete"; preset: import("../types").AccessKeyPreset };

function PresetsCard({ accessKey, onUpdate }: { accessKey: AccessKeyRecord; onUpdate: (t: AccessKeyRecord) => void }) {
  const [action, setAction] = useState<PresetAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [ptConfirmed, setPtConfirmed] = useState(false);

  const active = accessKey.presets.find((p) => p.id === accessKey.active_preset_id) ?? null;
  const liveSnap = snapOfAccessKey(accessKey);
  // Unsaved changes on the active preset: what a switch would save back into it,
  // and what a revert would discard.
  const dirtyLines = active ? snapDiffLines(snapOfPreset(active), liveSnap) : [];
  const dirty = dirtyLines.length > 0;

  async function runAction() {
    if (!action) return;
    setBusy(true);
    setError(null);
    try {
      if (action.kind === "delete") {
        onUpdate(await api.deletePreset(accessKey.id, action.preset.id));
      } else {
        const needsPtConfirm = action.preset.pass_through && !accessKey.pass_through;
        onUpdate(await api.applyPreset(accessKey.id, action.preset.id, needsPtConfirm));
      }
      setAction(null);
      setPtConfirmed(false);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.presetActionFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function createPreset() {
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    setError(null);
    try {
      onUpdate(await api.createPreset(accessKey.id, name));
      setNewName("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.presetCreateFailed"));
    } finally {
      setBusy(false);
    }
  }

  const needsPtConfirm = action != null && action.kind !== "delete"
    && action.preset.pass_through && !accessKey.pass_through;
  const applyLines = action && action.kind !== "delete"
    ? snapDiffLines(liveSnap, snapOfPreset(action.preset))
    : [];

  return (
    <div className="preset-list">
      {action && (
        <Modal titleId="preset-action-title" onClose={busy ? undefined : () => { setAction(null); setPtConfirmed(false); setError(null); }}>
          <h3 className="modal-title" id="preset-action-title">
            {action.kind === "switch" && t("accessKeys.presetSwitchTitle", { name: action.preset.name })}
            {action.kind === "revert" && t("accessKeys.presetRevertTitle", { name: action.preset.name })}
            {action.kind === "delete" && t("accessKeys.presetDeleteTitle", { name: action.preset.name })}
          </h3>
          {action.kind === "switch" && (
            <>
              {dirty && active && (
                <div className="preset-modal-section">
                  <p>{tRich("accessKeys.presetSwitchSaveInto", { strong: (c) => <strong>{c}</strong> }, { name: active.name })}</p>
                  <ul className="preset-diff-list">{dirtyLines.map((l) => <li key={l}>{l}</li>)}</ul>
                </div>
              )}
              <div className="preset-modal-section">
                {applyLines.length > 0
                  ? (
                    <>
                      <p>{tRich("accessKeys.presetApplying", { strong: (c) => <strong>{c}</strong> }, { name: action.preset.name })}</p>
                      <ul className="preset-diff-list">{applyLines.map((l) => <li key={l}>{l}</li>)}</ul>
                    </>
                  )
                  : <p>{t("accessKeys.presetNoChange")}</p>}
              </div>
            </>
          )}
          {action.kind === "revert" && (
            <div className="preset-modal-section">
              <p>{tRich("accessKeys.presetRevertBody", { strong: (c) => <strong>{c}</strong> }, { name: action.preset.name })}</p>
              <ul className="preset-diff-list">{dirtyLines.map((l) => <li key={l}>{l}</li>)}</ul>
            </div>
          )}
          {action.kind === "delete" && (
            <p>{tRich("accessKeys.presetDeleteBody", { strong: (c) => <strong>{c}</strong> }, { name: action.preset.name })}</p>
          )}
          {needsPtConfirm && (
            <>
              <div className="amber-block">
                <p>{tRich("accessKeys.presetPtWarning", { strong: (c) => <strong>{c}</strong> })}</p>
              </div>
              <label className="modal-check">
                <input type="checkbox" checked={ptConfirmed} onChange={(e) => setPtConfirmed(e.target.checked)} />
                {t("accessKeys.presetPtUnderstand")}
              </label>
            </>
          )}
          {error && <ErrorMsg msg={error} />}
          <div className="modal-actions">
            <BusyButton className="btn btn-text" onClick={() => { setAction(null); setPtConfirmed(false); setError(null); }} busy={busy}>{t("accessKeys.cancel")}</BusyButton>
            <BusyButton
              className={`btn ${action.kind === "delete" ? "btn-danger" : action.kind === "revert" ? "btn-warning" : "btn-primary"}`}
              onClick={runAction}
              busy={busy} disabled={(needsPtConfirm && !ptConfirmed)}
            >
              {busy ? t("accessKeys.working") : action.kind === "switch" ? t("accessKeys.presetSwitch") : action.kind === "revert" ? t("accessKeys.presetRevert") : t("accessKeys.delete")}
            </BusyButton>
          </div>
        </Modal>
      )}

      {accessKey.presets.map((p) => {
        const isActive = p.id === accessKey.active_preset_id;
        return (
          <div key={p.id} className={`preset-row${isActive ? " preset-row-active" : ""}`}>
            <EditablePresetName accessKeyId={accessKey.id} preset={p} onUpdate={onUpdate} />
            {isActive && <span className="badge badge-green">{t("accessKeys.presetActive")}</span>}
            {isActive && dirty && <span className="badge badge-amber" title={t("accessKeys.presetModifiedTitle")}>{t("accessKeys.presetModified")}</span>}
            <span className="preset-row-actions">
              {!isActive && (
                <BusyButton className="btn btn-outline btn-sm" busy={busy} onClick={() => setAction({ kind: "switch", preset: p })}>
                  {t("accessKeys.presetSwitchTo")}
                </BusyButton>
              )}
              {isActive && dirty && (
                <BusyButton className="btn btn-outline btn-sm" busy={busy} onClick={() => setAction({ kind: "revert", preset: p })}>
                  {t("accessKeys.presetRevertToSaved")}
                </BusyButton>
              )}
              {!isActive && (
                <BusyButton className="btn btn-ghost btn-sm" busy={busy} onClick={() => setAction({ kind: "delete", preset: p })} aria-label={t("accessKeys.presetDeleteAria", { name: p.name })}>
                  {t("accessKeys.delete")}
                </BusyButton>
              )}
            </span>
          </div>
        );
      })}
      {accessKey.presets.length === 0 && (
        <p className="preset-empty">{t("accessKeys.presetEmpty")}</p>
      )}

      <div className="preset-create-row">
        <BusyInput
          className="input"
          placeholder={t("accessKeys.presetNewName")}
          value={newName}
          maxLength={40}
          busy={busy} disabled={accessKey.presets.length >= MAX_PRESETS}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") createPreset(); }}
          aria-label={t("accessKeys.presetNewName")}
        />
        <BusyButton
          className="btn btn-outline btn-sm"
          busy={busy} disabled={!newName.trim() || accessKey.presets.length >= MAX_PRESETS}
          onClick={createPreset}
        >
          {t("accessKeys.presetSaveCurrent")}
        </BusyButton>
      </div>
      {accessKey.presets.length >= MAX_PRESETS && (
        <small className="preset-hint">{t("accessKeys.presetMax", { max: MAX_PRESETS })}</small>
      )}
      {!action && error && <ErrorMsg msg={error} />}
      <small className="preset-hint">
        {t("accessKeys.presetHint")}
      </small>
    </div>
  );
}

// Inline-editable token name shown as the detail heading. Click to select/edit;
// it auto-saves on blur (and on Enter), validating format client-side and letting
// the server reject a name that clashes with another token. Escape cancels.
const TOKEN_NAME_RE = /^[A-Za-z0-9_-]{3,32}$/;

function EditableAccessKeyName({ accessKey, onRenamed }: { accessKey: AccessKeyRecord; onRenamed: (t: AccessKeyRecord) => void }) {
  const [value, setValue] = useState(accessKey.name);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => { setValue(accessKey.name); setErr(null); }, [accessKey.name]);

  async function commit() {
    const next = value.trim();
    if (next === accessKey.name) { setValue(accessKey.name); setErr(null); return; }
    if (!TOKEN_NAME_RE.test(next)) {
      setErr(t("accessKeys.nameRuleError"));
      setValue(accessKey.name);
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      const updated = await api.patchAccessKey(accessKey.id, { name: next });
      onRenamed(updated);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : t("accessKeys.renameFailed"));
      setValue(accessKey.name);
    } finally {
      setSaving(false);
    }
  }

  return (
    <span className="access-key-name-edit">
      <BusyInput
        className="access-key-card-name access-key-name-input"
        value={value}
        busy={saving}
        spellCheck={false}
        maxLength={32}
        aria-label={t("accessKeys.accessKeyNameAria")}
        title={t("accessKeys.accessKeyRenameTitle")}
        onChange={(e) => setValue(e.target.value)}
        onFocus={(e) => e.target.select()}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); (e.target as HTMLInputElement).blur(); }
          else if (e.key === "Escape") { setValue(accessKey.name); setErr(null); (e.target as HTMLInputElement).blur(); }
        }}
      />
      {err && <span className="access-key-name-error" role="alert">{err}</span>}
    </span>
  );
}

export function AccessKeyDetailView({ accessKeyId, onBack, onRefresh, presetsEnabled = false, esphome = null }: Props) {
  const [accessKey, setAccessKey] = useState<AccessKeyRecord | null>(null);
  const [mesaProfileEntities, setMesaProfileEntities] = useState<Set<string>>(new Set());
  const [mesaProfileDevices, setMesaProfileDevices] = useState<Set<string>>(new Set());
  const [mesaProfileDomains, setMesaProfileDomains] = useState<Set<string>>(new Set());
  // The entity whose MESA profile is being edited inline (overlaid on this tab),
  // and the canonical tag vocabulary the editor needs.
  const [mesaEdit, setMesaEdit] = useState<{ scope: LinkScope; key: string; label?: string; isNew: boolean } | null>(null);
  const [canonicalTags, setCanonicalTags] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // One destructive action at a time, by construction: a single state makes
  // "two modals open at once" unrepresentable.
  const [action, setAction] = useState<{ kind: AccessKeyAction; busy: boolean } | null>(null);
  const openAction = (kind: AccessKeyAction) => setAction({ kind, busy: false });
  const closeAction = () => setAction(null);
  const setActionBusy = (busy: boolean) => setAction((a) => (a ? { ...a, busy } : a));
  const isOpen = (kind: AccessKeyAction) => action?.kind === kind;
  const isBusy = (kind: AccessKeyAction) => action?.kind === kind && action.busy;
  const [rotatedRawAccessKey, setRotatedRawAccessKey] = useState<string | null>(null);
  const [showSelectByPicker, setShowSelectByPicker] = useState(false);
  const [entityTree, setEntityTree] = useState<import("../types").EntityTree | null>(null);
  // Set when a change alters the announced tool list, to remind the operator to
  // reconnect the agent. Only surfaced when the token has actually been used.
  const [reconnectNeeded, setReconnectNeeded] = useState(false);
  const [selectedEntityId, setSelectedEntityId] = useState("");
  const [selectedDepth, setSelectedDepth] = useState<"entity" | "device" | "domain">("entity");
  // Bumped on every reveal request so clicking the same row re-triggers the
  // tree expand/scroll (selecting the same id/depth alone is a no-op in React).
  const [revealNonce, setRevealNonce] = useState(0);
  // opts.reveal defaults true (click-to-jump from elsewhere on the page, e.g.
  // the Permission Summary table, still scrolls/expands/flashes the target).
  // Editing a permission directly in the tree passes reveal: false: the row
  // is already on screen, so scrolling it to center only risks the page
  // moving mid-click and the next click landing on a different row.
  const revealNode = (eid: string, depth: "entity" | "device" | "domain" = "entity", opts?: { reveal?: boolean }) => {
    setSelectedEntityId(eid);
    setSelectedDepth(depth);
    if (opts?.reveal !== false) {
      setRevealNonce((n) => n + 1);
    }
  };
  const [permissionsVersion, setPermissionsVersion] = useState(0);
  const [collapseTreeKey, setCollapseTreeKey] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getAccessKey(accessKeyId);
      setAccessKey(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [accessKeyId]);

  useEffect(() => { load(); }, [load]);

  // setAccessKey wrapper for the config editors (persona, capabilities, tool
  // announcement, pass-through). If the change alters the announced tool list and
  // the token has been used by a client, raise the reconnect reminder.
  const applyAccessKeyUpdate = useCallback((updated: AccessKeyRecord) => {
    if (accessKey && updated.last_used_at && toolGatingChanged(accessKey, updated)) {
      setReconnectNeeded(true);
    }
    setAccessKey(updated);
  }, [accessKey]);

  useEffect(() => {
    api.getEntityTree().then(setEntityTree).catch(() => null);
  }, []);

  // Which entities already have a MESA profile, so the cards can show
  // "view" (MESA) vs "create" (+). Reloaded after the inline editor saves so a
  // newly created profile flips its "+" to "MESA" without leaving the tab.
  const loadMesaProfiles = useCallback(() => {
    api.listMesaProfiles({ all: true })
      .then((r) => setMesaProfileEntities(new Set(r.profiles.map((p) => p.entity_id))))
      .catch(() => null);
    // The tree offers device and domain profiles too, so it needs to know which
    // of those already exist to show "MESA" rather than "+". Separate reads
    // because each scope is its own endpoint; a failure just leaves that scope
    // showing "+", which opens the editor and loads the real profile anyway.
    api.listMesaDevices()
      .then((r) => setMesaProfileDevices(new Set(r.devices.map((d) => d.device_id))))
      .catch(() => null);
    api.listMesaDomains()
      .then((r) => setMesaProfileDomains(new Set(r.domains.map((d) => d.domain))))
      .catch(() => null);
  }, []);
  useEffect(() => { loadMesaProfiles(); }, [loadMesaProfiles]);

  // The canonical MESA tag vocabulary powers the inline editor's tag autocomplete.
  useEffect(() => { api.getMesaVocabulary().then((v) => setCanonicalTags(v.canonical_tags)).catch(() => null); }, []);

  // Open the MESA profile editor as an overlay on this tab (no tab switch). isNew
  // mirrors the +/MESA affordance that was clicked, which is driven by the same
  // set, so the editor opens in the mode the operator just saw.
  const openMesaInline = useCallback((key: string, scope: LinkScope = "entity", label?: string) => {
    const known = scope === "device" ? mesaProfileDevices
      : scope === "domain" ? mesaProfileDomains
      : mesaProfileEntities;
    setMesaEdit({ scope, key, label, isNew: !known.has(key) });
  }, [mesaProfileEntities, mesaProfileDevices, mesaProfileDomains]);

  async function revoke() {
    setActionBusy(true);
    try {
      await api.revokeAccessKey(accessKeyId);
      // Any open Agent Chat window (panel or floating) drops the revoked token
      // from its own list and reselects/clears, instead of continuing to show
      // (and let the user submit against) a token that no longer works.
      window.dispatchEvent(new CustomEvent("phx-access-keys-changed"));
      onBack();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.revokeFailed"));
      closeAction();
    }
  }

  async function rotate() {
    setActionBusy(true);
    try {
      const resp = await api.rotateAccessKey(accessKeyId);
      const { access_key: rawAccessKey } = resp;
      setRotatedRawAccessKey(rawAccessKey);
      onRefresh?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.rotateFailed"));
    } finally {
      // Closed on success and on failure alike.
      closeAction();
    }
  }

  async function clearPermissions() {
    setActionBusy(true);
    try {
      const updatedTree = await api.setPermissions(accessKeyId, { domains: {}, devices: {}, entities: {} });
      setAccessKey((prev) => prev ? { ...prev, permissions: updatedTree } : prev);
      setPermissionsVersion((v) => v + 1);
      setCollapseTreeKey((k) => k + 1);
      closeAction();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.clearPermsFailed"));
      // Unlike the others, this modal stays open on failure so the operator can
      // retry without reopening it.
      setActionBusy(false);
    }
  }

  async function enablePassThrough() {
    setActionBusy(true);
    try {
      const body: PatchAccessKeyBody = { pass_through: true, confirm_pass_through: true };
      const updated = await api.patchAccessKey(accessKeyId, body);
      applyAccessKeyUpdate(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.enablePtFailed"));
    } finally {
      closeAction();
    }
  }

  if (loading) return <Loading />;
  if (error && !accessKey) return <div><button className="btn btn-text" onClick={onBack}>{t("accessKeys.back")}</button><ErrorMsg msg={error} /></div>;
  if (!accessKey) return null;

  if (rotatedRawAccessKey) {
    return <RotatedAccessKeyModal rawAccessKey={rotatedRawAccessKey} accessKeyName={accessKey.name} onClose={() => setRotatedRawAccessKey(null)} />;
  }

  const status = accessKeyStatus(accessKey);
  const statusClass = status === "Active" ? "badge-green" : status === "Expired" ? "badge-grey" : "badge-red";

  // One-line summaries shown on the collapsed Advanced cards.
  const personaDef = PERSONAS.find((p) => p.key === accessKey.persona);
  const personaLabel = personaDef ? t(personaDef.labelKey) : t("personas.custom.label");
  const capCounts = CAP_NAMES.reduce(
    (acc, k) => { acc[accessKey[k]] = (acc[accessKey[k]] ?? 0) + 1; return acc; },
    { allow: 0, confirm: 0, deny: 0 } as Record<string, number>,
  );
  const capsSummary = t("accessKeys.capsSummary", {
    persona: personaLabel,
    allow: capCounts.allow,
    confirm: capCounts.confirm,
    deny: capCounts.deny,
  });
  const announceSummary = accessKey.announce_all_tools
    ? t("accessKeys.announceSummaryAll")
    : t("accessKeys.announceSummaryScoped");
  const rateSummary = accessKey.rate_limit_requests > 0
    ? t("accessKeys.rateSummary", { requests: accessKey.rate_limit_requests, burst: accessKey.rate_limit_burst })
    : t("accessKeys.rateSummaryNone");
  const activePreset = accessKey.presets.find((p) => p.id === accessKey.active_preset_id) ?? null;
  const presetsSummary = activePreset
    ? (snapDiffLines(snapOfPreset(activePreset), snapOfAccessKey(accessKey)).length > 0
      ? t("accessKeys.presetsSummaryModified", { name: activePreset.name })
      : activePreset.name)
    : t("accessKeys.presetsSummarySaved", { count: accessKey.presets.length });

  return (
    <div className="access-key-detail-wrap">

      {/* Modals */}
      {isOpen("rotate") && (
        <ConfirmModal
          title={t("accessKeys.rotateAccessKeyTitle")}
          body={
            <div className="amber-block">
              <p>{t("accessKeys.rotateBody")}</p>
            </div>
          }
          checkLabel={t("accessKeys.rotateCheck")}
          confirmLabel={t("accessKeys.rotateAccessKeyTitle")}
          confirmClass="btn-primary"
          loading={isBusy("rotate")}
          onConfirm={rotate}
          onClose={closeAction}
        />
      )}

      {isOpen("revoke") && (
        <ConfirmModal
          title={t("accessKeys.revokeAccessKeyTitle")}
          body={
            <div className="amber-block">
              <p>{t("accessKeys.revokeBody")}</p>
            </div>
          }
          checkLabel={t("accessKeys.revokeCheck")}
          confirmLabel={t("accessKeys.revokeAccessKeyTitle")}
          confirmClass="btn-danger"
          loading={isBusy("revoke")}
          onConfirm={revoke}
          onClose={closeAction}
        />
      )}

      {isOpen("passThrough") && (
        <ConfirmModal
          title={t("accessKeys.enablePtTitle")}
          body={
            <div className="amber-block">
              <p>{tRich("accessKeys.enablePtBody", { strong: (c) => <strong>{c}</strong> })}</p>
            </div>
          }
          checkLabel={t("accessKeys.enablePtCheck")}
          confirmLabel={t("accessKeys.enablePtLabel")}
          confirmClass="btn-warning"
          loading={isBusy("passThrough")}
          onConfirm={enablePassThrough}
          onClose={closeAction}
        />
      )}

      {/* Sticky top section */}
      <div className="access-key-detail-sticky">
        {error && <ErrorMsg msg={error} />}

        {reconnectNeeded && (
          <div className="banner banner-info reconnect-banner" role="status">
            <span className="reconnect-banner-text">
              {tRich("accessKeys.reconnectBanner", { strong: (c) => <strong>{c}</strong> })}
            </span>
            <button
              className="reconnect-banner-dismiss"
              onClick={() => setReconnectNeeded(false)}
              aria-label={t("accessKeys.reconnectDismiss")}
            >&times;</button>
          </div>
        )}

        {accessKey.pass_through && (
          <div className="pass-through-header-banner">
            <p>
              {tRich("accessKeys.ptHeaderBanner", { strong: (c) => <strong className="text-warning">{c}</strong> })}
            </p>
          </div>
        )}

        <div className="two-col">
          {/* Left: Token info card */}
          <div className="card access-key-info-card">
            <div className="access-key-card-header">
              <div className="access-key-card-name-row">
                <img src={PHOENIX_ICON} className="access-key-card-icon" alt="" />
                <EditableAccessKeyName accessKey={accessKey} onRenamed={(u) => { applyAccessKeyUpdate(u); onRefresh?.(); }} />
              </div>
              <div className="access-key-card-badges">
                <span className={`badge ${statusClass}`}>{accessKeyStatusLabel(status)}</span>
                {accessKey.pass_through
                  ? <span className="badge badge-amber">{t("accessKeys.passThrough")}</span>
                  : <span className="badge badge-blue">{t("accessKeys.scoped")}</span>}
              </div>
            </div>

            <div className="access-key-card-body">
              <div className="access-key-card-meta">
                <div className="access-key-meta-table">
                  <span className="stat-label">{t("accessKeys.colCreated")}</span>
                  <span title={accessKey.created_at ? localeDateTime(accessKey.created_at) : undefined}>{formatDateTime(accessKey.created_at)}</span>
                  <span className="stat-label">{t("accessKeys.colUpdated")}</span>
                  <span title={accessKey.updated_at ? localeDateTime(accessKey.updated_at) : undefined}>{formatDateTime(accessKey.updated_at)}</span>
                  <span className="stat-label">{t("accessKeys.colExpires")}</span>
                  <span>{formatDateTime(accessKey.expires_at)}</span>
                  <span className="stat-label">{t("accessKeys.colLastUsed")}</span>
                  <span>{formatDateTime(accessKey.last_used_at)}</span>
                </div>
              </div>

              <div className="access-key-card-actions">
                <button className="btn btn-outline btn-sm access-key-action-btn" onClick={() => openAction("rotate")}>
                  {t("accessKeys.rotate")}
                </button>
                {!accessKey.pass_through && (
                  <button className="btn btn-warning btn-sm access-key-action-btn" onClick={() => openAction("passThrough")}>
                    {t("accessKeys.enablePtLabel")}
                  </button>
                )}
                <button className="btn btn-danger btn-sm access-key-action-btn" onClick={() => openAction("revoke")}>
                  {t("accessKeys.revoke")}
                </button>
              </div>
            </div>
          </div>

          {/* Right: Permission emulator */}
          <div className="card epe-card">
            <h3 className="card-header">
              {t("accessKeys.emulatorTitle")}
              <DocsHelpLink path="panel.html#emulator" label={t("accessKeys.emulatorTitle")} />
            </h3>
            {accessKey.pass_through ? (
              <p style={{ fontSize: 13, color: "var(--phx-text-2)", margin: 0 }}>
                {t("accessKeys.emulatorPassThrough")}
              </p>
            ) : (
              <PermissionSimulator
                accessKeyId={accessKeyId}
                externalEntityId={selectedEntityId || undefined}
                resolveDepth={selectedDepth}
                triggerVersion={permissionsVersion}
                mesaProfileEntities={mesaProfileEntities}
                onOpenMesa={openMesaInline}
              />
            )}
          </div>
        </div>
      </div>

      {/* Scrollable body */}
      <div className="access-key-detail-body">
        <div className="two-col">
          <div>
            <CollapsibleCard title={t("accessKeys.cardPersona")} summary={personaLabel} defaultOpen persistKey="phx:fold:persona" docsPath="capabilities.html#personas">
              <PersonaPicker accessKey={accessKey} onUpdate={applyAccessKeyUpdate} esphome={esphome} />
            </CollapsibleCard>

            {presetsEnabled && (
              <CollapsibleCard title={t("accessKeys.cardPresets")} summary={presetsSummary} persistKey="phx:fold:presets" docsPath="panel.html#presets">
                <PresetsCard accessKey={accessKey} onUpdate={applyAccessKeyUpdate} />
              </CollapsibleCard>
            )}

            <div className="advanced-section-label">{t("accessKeys.advanced")}</div>
            <CollapsibleCard title={t("accessKeys.cardCapabilities")} summary={capsSummary} persistKey="phx:fold:capabilities" docsPath="capabilities.html#capability-flags">
              <CapabilityMatrix accessKey={accessKey} onUpdate={applyAccessKeyUpdate} esphome={esphome} />
            </CollapsibleCard>
            <CollapsibleCard title={t("accessKeys.cardToolAnnouncement")} summary={announceSummary} persistKey="phx:fold:announce" docsPath="operations.html#advanced-access-key-options">
              <ToolAnnouncementToggle accessKey={accessKey} onUpdate={applyAccessKeyUpdate} />
            </CollapsibleCard>
            <CollapsibleCard title={t("accessKeys.cardRateLimiting")} summary={rateSummary} persistKey="phx:fold:ratelimit" docsPath="operations.html#rate-limiting">
              <RateLimitConfig accessKey={accessKey} onUpdate={setAccessKey} />
            </CollapsibleCard>

            {!accessKey.pass_through && (
              <div className="card">
                <h3 className="card-header">
                  {t("accessKeys.permissionSummary")}
                  <DocsHelpLink path="panel.html#summary" label={t("accessKeys.permissionSummary")} />
                </h3>
                <PermissionSummary
                  permissions={accessKey.permissions}
                  entityTree={entityTree}
                  onEntityClick={revealNode}
                  mesaProfileEntities={mesaProfileEntities}
                  onOpenMesa={openMesaInline}
                />
              </div>
            )}
          </div>

          <div>
            {accessKey.pass_through ? (
              <div className="card">
                <h3 className="card-header">
                  {t("accessKeys.permissionsTree")}
                  <DocsHelpLink path="panel.html#permission-tree" label={t("accessKeys.permissionsTree")} />
                </h3>
                <PassThroughNotice accessKey={accessKey} onUpdate={applyAccessKeyUpdate} />
              </div>
            ) : (
              <div className="card">
                <div className="card-header card-header-stack">
                  <h3 className="card-header-title">
                    {t("accessKeys.permissionsTree")}
                    <DocsHelpLink path="panel.html#permission-tree" label={t("accessKeys.permissionsTree")} />
                  </h3>
                  <div className="tree-header-actions">
                    {entityTree && (
                      <button className="btn btn-outline btn-sm" onClick={() => setShowSelectByPicker(true)}>
                        {t("accessKeys.selectByAreaLabelIntegration")}
                      </button>
                    )}
                    <button
                      className="btn btn-danger btn-sm"
                      onClick={() => openAction("clearPerms")}
                    >
                      {t("accessKeys.clearAll")}
                    </button>
                  </div>
                </div>
                <EntityTree
                  accessKeyId={accessKeyId}
                  permissions={accessKey.permissions}
                  onPermissionsChange={(tree) => {
                    setAccessKey((current) => current?.id === accessKey.id ? { ...current, permissions: tree } : current);
                    setPermissionsVersion((v) => v + 1);
                  }}
                  onEntityClick={revealNode}
                  collapseKey={collapseTreeKey}
                  revealEntity={selectedEntityId || undefined}
                  revealDepth={selectedDepth}
                  revealNonce={revealNonce}
                  mesaProfileEntities={mesaProfileEntities}
                  mesaProfileDevices={mesaProfileDevices}
                  mesaProfileDomains={mesaProfileDomains}
                  onOpenMesa={openMesaInline}
                />
              </div>
            )}
          </div>
        </div>
      </div>

      {showSelectByPicker && entityTree && (
        <SelectByPicker
          accessKeyId={accessKeyId}
          entityTree={entityTree}
          onDone={() => {
            setShowSelectByPicker(false);
            load();
          }}
          onClose={() => setShowSelectByPicker(false)}
        />
      )}

      {isOpen("clearPerms") && (
        <Modal titleId="clear-perms-title" onClose={isBusy("clearPerms") ? undefined : closeAction}>
          <h3 className="modal-title" id="clear-perms-title">{t("accessKeys.clearPermsTitle")}</h3>
          <p className="clear-perms-body">
            {t("accessKeys.clearPermsBody")}
          </p>
          <div className="modal-actions">
            <button className="btn btn-danger" onClick={clearPermissions} disabled={isBusy("clearPerms")}>
              {isBusy("clearPerms") ? t("accessKeys.clearing") : t("accessKeys.clearAll")}
            </button>
            <button className="btn btn-text" onClick={closeAction} disabled={isBusy("clearPerms")}>
              {t("accessKeys.cancel")}
            </button>
          </div>
        </Modal>
      )}

      {mesaEdit && (
        <ProfileEditor
          scope={mesaEdit.scope}
          profileKey={mesaEdit.key}
          keyLabel={mesaEdit.label}
          isNew={mesaEdit.isNew}
          entityTree={entityTree}
          canonicalTags={canonicalTags}
          onClose={() => setMesaEdit(null)}
          onSaved={loadMesaProfiles}
          // The target is whichever row was clicked, so it is fixed rather than
          // picked. Without this the editor shows its combobox and validates the
          // pre-filled key against a picker source this view does not supply,
          // which rejects every device id as "no matching device". The in-context
          // injector locks for the same reason: the caller already knows the
          // target, so re-deriving it can only go wrong.
          lockedKey
        />
      )}
    </div>
  );
}
