import { BusyInput, BusySelect } from "../components/BusyButton";
import { BusyButton } from "../components/BusyButton";
import React, { createContext, memo, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { AccessKeyRecord, EntityTree as EntityTreeData, PatchAccessKeyBody, PermissionTree } from "../types";
import { api } from "../api";
import { Loading, ErrorMsg } from "../components/common";
import type { LinkScope } from "../components/MesaProfileLink";
import { Modal } from "../components/Modal";
import { RawAccessKeyDisplay } from "../components/AccessKeyCreateModal";
import { ConnectInstructions } from "../components/ConnectInstructions";
import { CapabilityMatrix, CAP_NAMES } from "../components/CapabilityMatrix";
import type { EsphomeAvailability } from "../components/CapabilityMatrix";
import { PersonaPicker } from "../components/PersonaPicker";
import { AccessKeyNotes } from "../components/AccessKeyNotes";
import { RateLimitConfig } from "../components/RateLimitConfig";
import { PassThroughNotice } from "../components/PassThroughNotice";
import { EntityTree } from "../components/EntityTree";
import { PermissionSummary } from "../components/PermissionSummary";
import { SelectByPicker } from "../components/SelectByPicker";
import { ProfileEditor } from "./MesaView";
import { t } from "../i18n";
import { tRich } from "../i18n/rich";
import { useToolbarTools, type ToolGroup } from "../shell/tools";
import type { MenuEntry } from "../ui/Menu";

// Whether an access key change alters which tools the MCP client is shown (tools/list),
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
// 30-180 range (const.py MIN/MAX_CONFIRM_INLINE_WAIT_SECONDS), plus the access key's
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
// Access key settings presets (workspace model). A preset is a named snapshot of the
// access key's full settings; the ACTIVE preset absorbs live edits when the admin
// switches to another preset, and applying the active preset reverts to its
// saved state. Enforcement never reads presets; apply routes through the same
// backend paths as manual edits.
// ---------------------------------------------------------------------------

const MAX_PRESETS = 8; // mirrors const.py MAX_PRESETS_PER_ACCESS_KEY

// Normalized comparable view of a settings snapshot, shared by the access key's
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

// Inline-editable access key name, shown in the key list while renaming. It saves
// on blur (and on Enter), validating format client-side and letting the server
// reject a name that clashes with another access key. Escape cancels.
const ACCESS_KEY_NAME_RE = /^[A-Za-z0-9_-]{3,32}$/;

export function EditableAccessKeyName({ accessKey, onRenamed, onDone }: {
  accessKey: AccessKeyRecord;
  onRenamed: (t: AccessKeyRecord) => void;
  /** Called when editing ends: saved, unchanged, cancelled or refused. */
  onDone?: () => void;
}) {
  const [value, setValue] = useState(accessKey.name);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement | null>(null);

  useEffect(() => { setValue(accessKey.name); setErr(null); }, [accessKey.name]);
  useEffect(() => { input.current?.focus(); input.current?.select(); }, []);

  async function commit() {
    const next = value.trim();
    if (next === accessKey.name) { setValue(accessKey.name); setErr(null); onDone?.(); return; }
    if (!ACCESS_KEY_NAME_RE.test(next)) {
      // Keep the field open with the refused name, so it can be corrected.
      setErr(t("accessKeys.nameRuleError"));
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      const updated = await api.patchAccessKey(accessKey.id, { name: next });
      onRenamed(updated);
      onDone?.();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : t("accessKeys.renameFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <span className="access-key-name-edit" onClick={(e) => e.stopPropagation()}>
      <BusyInput
        ref={input}
        className="access-key-name-input"
        value={value}
        busy={saving}
        spellCheck={false}
        maxLength={32}
        aria-label={t("accessKeys.accessKeyNameAria")}
        aria-invalid={!!err}
        title={t("accessKeys.accessKeyRenameTitle")}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); void commit(); }
          else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setValue(accessKey.name); setErr(null); onDone?.(); }
        }}
      />
      {err && <span className="access-key-name-error" role="alert">{err}</span>}
    </span>
  );
}

// ---------------------------------------------------------------------------
// The selected access key, shared by the Access Keys workspace's windows. The
// key list sits at the top; selecting a key fills Persona, Capabilities, the
// Permissions tree, Permission Summary, Key Settings and Presets below it.
// ---------------------------------------------------------------------------

type Depth = "entity" | "device" | "domain";

export interface AccessKeyDetail {
  accessKeyId: string;
  accessKey: AccessKeyRecord | null;
  loading: boolean;
  error: string | null;
  presetsEnabled: boolean;
  esphome: EsphomeAvailability | null;
  /** Apply an edit, raising the reconnect reminder when the announced tools change. */
  applyUpdate: (updated: AccessKeyRecord) => void;
  /** Apply an edit that never changes the announced tools (notes, rate limits). */
  setAccessKey: (updated: AccessKeyRecord) => void;
  reconnectNeeded: boolean;
  dismissReconnect: () => void;
  openAction: (kind: AccessKeyAction) => void;
  /** Whether the key list shows this key's name as an editable field. */
  renaming: boolean;
  setRenaming: (on: boolean) => void;
  entityTree: EntityTreeData | null;
  mesaProfileEntities: Set<string>;
  mesaProfileDevices: Set<string>;
  mesaProfileDomains: Set<string>;
  openMesa: (key: string, scope?: LinkScope, label?: string) => void;
  selectedEntityId: string;
  selectedDepth: Depth;
  revealNonce: number;
  revealNode: (eid: string, depth?: Depth, opts?: { reveal?: boolean }) => void;
  collapseTreeKey: number;
  onPermissionsChange: (tree: PermissionTree) => void;
  openSelectBy: () => void;
}

const AccessKeyDetailContext = createContext<AccessKeyDetail | null>(null);

/** The selected access key's shared state, or null while none is selected. */
export function useAccessKeyDetail(): AccessKeyDetail | null {
  return useContext(AccessKeyDetailContext);
}

interface ProviderProps {
  /** The selected access key, or null while none is selected. */
  accessKeyId: string | null;
  /** Clear the selection, after the key is revoked. */
  onDeselect: () => void;
  onRefresh?: () => void;
  /** Bring the Permissions tree into view, after a jump from elsewhere. */
  onReveal?: () => void;
  // settings.access_key_presets_enabled; the Presets window shows only while it is on.
  presetsEnabled?: boolean;
  // Which ESPHome surfaces exist on this system; null while settings are loading,
  // when the ESPHome controls simply show no note.
  esphomeIntegration?: boolean | null;
  esphomeBuilder?: boolean | null;
}

export function AccessKeyDetailProvider({ accessKeyId, children, ...rest }: ProviderProps & { children: ReactNode }) {
  const [value, setValue] = useState<AccessKeyDetail | null>(null);
  return (
    <AccessKeyDetailContext.Provider value={accessKeyId && value?.accessKeyId === accessKeyId ? value : null}>
      {children}
      {accessKeyId && <SelectedAccessKey key={accessKeyId} accessKeyId={accessKeyId} {...rest} publish={setValue} />}
    </AccessKeyDetailContext.Provider>
  );
}

// Holds one access key's state and its confirmations. Keyed by the key's id, so
// selecting another key starts from fresh state and late replies for the old one
// are dropped. Memoized: it renders only when its own state changes, then
// publishes the result to the windows.
const SelectedAccessKey = memo(function SelectedAccessKey({
  accessKeyId, onDeselect, onRefresh, onReveal, presetsEnabled = false, esphomeIntegration = null, esphomeBuilder = null, publish,
}: Omit<ProviderProps, "accessKeyId"> & { accessKeyId: string; publish: (value: AccessKeyDetail | null) => void }) {
  const [accessKey, setAccessKey] = useState<AccessKeyRecord | null>(null);
  const [mesaProfileEntities, setMesaProfileEntities] = useState<Set<string>>(new Set());
  const [mesaProfileDevices, setMesaProfileDevices] = useState<Set<string>>(new Set());
  const [mesaProfileDomains, setMesaProfileDomains] = useState<Set<string>>(new Set());
  // The entity whose MESA profile is being edited inline, and the canonical tag
  // vocabulary the editor needs.
  const [mesaEdit, setMesaEdit] = useState<{ scope: LinkScope; key: string; label?: string; isNew: boolean } | null>(null);
  const [canonicalTags, setCanonicalTags] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // One destructive action at a time, by construction: a single state makes
  // "two modals open at once" unrepresentable.
  const [action, setAction] = useState<{ kind: AccessKeyAction; busy: boolean } | null>(null);
  const openAction = useCallback((kind: AccessKeyAction) => setAction({ kind, busy: false }), []);
  const closeAction = () => setAction(null);
  const setActionBusy = (busy: boolean) => setAction((a) => (a ? { ...a, busy } : a));
  const isOpen = (kind: AccessKeyAction) => action?.kind === kind;
  const isBusy = (kind: AccessKeyAction) => action?.kind === kind && action.busy;
  const [rotatedRawAccessKey, setRotatedRawAccessKey] = useState<string | null>(null);
  const [showSelectByPicker, setShowSelectByPicker] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [entityTree, setEntityTree] = useState<EntityTreeData | null>(null);
  // Set when a change alters the announced tool list, to remind the operator to
  // reconnect the agent. Only surfaced when the access key has actually been used.
  const [reconnectNeeded, setReconnectNeeded] = useState(false);
  const [selectedEntityId, setSelectedEntityId] = useState("");
  const [selectedDepth, setSelectedDepth] = useState<Depth>("entity");
  // Bumped on every reveal request so clicking the same row re-triggers the
  // tree expand/scroll (selecting the same id/depth alone is a no-op in React).
  const [revealNonce, setRevealNonce] = useState(0);
  // opts.reveal defaults true (a jump from elsewhere, e.g. the Permission Summary,
  // still brings the tree into view and scrolls/expands/flashes the target).
  // Editing a permission directly in the tree passes reveal: false: the row is
  // already on screen, so scrolling it to center only risks the page moving
  // mid-click and the next click landing on a different row.
  const revealNode = useCallback((eid: string, depth: Depth = "entity", opts?: { reveal?: boolean }) => {
    setSelectedEntityId(eid);
    setSelectedDepth(depth);
    if (opts?.reveal !== false) {
      setRevealNonce((n) => n + 1);
      onReveal?.();
    }
  }, [onReveal]);
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
  // the access key has been used by a client, raise the reconnect reminder.
  const applyUpdate = useCallback((updated: AccessKeyRecord) => {
    setAccessKey((current) => {
      if (current && updated.last_used_at && toolGatingChanged(current, updated)) setReconnectNeeded(true);
      return updated;
    });
  }, []);

  useEffect(() => {
    api.getEntityTree().then(setEntityTree).catch(() => null);
  }, []);

  // Which entities already have a MESA profile, so the windows can show
  // "view" (MESA) vs "create" (+). Reloaded after the inline editor saves so a
  // newly created profile flips its "+" to "MESA" in place.
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

  // Open the MESA profile editor over the workspace. isNew mirrors the +/MESA
  // affordance that was clicked, which is driven by the same set, so the editor
  // opens in the mode the operator just saw.
  const openMesa = useCallback((key: string, scope: LinkScope = "entity", label?: string) => {
    const known = scope === "device" ? mesaProfileDevices
      : scope === "domain" ? mesaProfileDomains
      : mesaProfileEntities;
    setMesaEdit({ scope, key, label, isNew: !known.has(key) });
  }, [mesaProfileEntities, mesaProfileDevices, mesaProfileDomains]);

  async function revoke() {
    setActionBusy(true);
    try {
      await api.revokeAccessKey(accessKeyId);
      // Any open Agent Chat window (panel or floating) drops the revoked access key
      // from its own list and reselects/clears, instead of continuing to show
      // (and let the user submit against) an access key that no longer works.
      window.dispatchEvent(new CustomEvent("phx-access-keys-changed"));
      onDeselect();
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
      applyUpdate(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.enablePtFailed"));
    } finally {
      closeAction();
    }
  }

  const onPermissionsChange = useCallback((tree: PermissionTree) => {
    setAccessKey((current) => (current?.id === accessKeyId ? { ...current, permissions: tree } : current));
  }, [accessKeyId]);

  const value = useMemo<AccessKeyDetail>(() => ({
    accessKeyId,
    accessKey,
    loading,
    error,
    presetsEnabled,
    esphome: esphomeIntegration === null || esphomeBuilder === null ? null : { integration: esphomeIntegration, builder: esphomeBuilder },
    applyUpdate,
    setAccessKey,
    reconnectNeeded,
    dismissReconnect: () => setReconnectNeeded(false),
    openAction,
    renaming,
    setRenaming,
    entityTree,
    mesaProfileEntities,
    mesaProfileDevices,
    mesaProfileDomains,
    openMesa,
    selectedEntityId,
    selectedDepth,
    revealNonce,
    revealNode,
    collapseTreeKey,
    onPermissionsChange,
    openSelectBy: () => setShowSelectByPicker(true),
  }), [accessKeyId, accessKey, loading, error, presetsEnabled, esphomeIntegration, esphomeBuilder, applyUpdate, reconnectNeeded, openAction,
    renaming, entityTree, mesaProfileEntities, mesaProfileDevices, mesaProfileDomains, openMesa, selectedEntityId, selectedDepth, revealNonce,
    revealNode, collapseTreeKey, onPermissionsChange]);

  useLayoutEffect(() => { publish(value); }, [publish, value]);
  useLayoutEffect(() => () => publish(null), [publish]);

  return (
    <>
      {rotatedRawAccessKey && accessKey && (
        <RotatedAccessKeyModal rawAccessKey={rotatedRawAccessKey} accessKeyName={accessKey.name} onClose={() => setRotatedRawAccessKey(null)} />
      )}

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
    </>
  );
});

// ---------------------------------------------------------------------------
// The windows. Each shows the selected key, or a prompt to select one.
// ---------------------------------------------------------------------------

/** The selected key once loaded; otherwise what to show instead. */
function useLoadedKey(): { detail: AccessKeyDetail; accessKey: AccessKeyRecord } | { placeholder: ReactNode } {
  const detail = useAccessKeyDetail();
  if (!detail) return { placeholder: <p className="phx-window-empty">{t("workspace.selectAccessKey")}</p> };
  if (detail.loading && !detail.accessKey) return { placeholder: <Loading /> };
  if (!detail.accessKey) return { placeholder: <ErrorMsg msg={detail.error ?? t("accessKeys.loadFailed")} /> };
  return { detail, accessKey: detail.accessKey };
}

/** Notices about the selected key, shown above the key list. */
export function AccessKeyNotices() {
  const detail = useAccessKeyDetail();
  const accessKey = detail?.accessKey;
  if (!detail || !accessKey) return null;
  return (
    <>
      {detail.error && <ErrorMsg msg={detail.error} />}
      {detail.reconnectNeeded && (
        <div className="banner banner-info reconnect-banner" role="status">
          <span className="reconnect-banner-text">
            {tRich("accessKeys.reconnectBanner", { strong: (c) => <strong>{c}</strong> })}
          </span>
          <button
            className="reconnect-banner-dismiss"
            onClick={detail.dismissReconnect}
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
    </>
  );
}

/** Key Actions for the selected key, offered on the toolbar with the key list's tools. */
export function useKeyActionsTool(): ToolGroup | null {
  const detail = useAccessKeyDetail();
  const accessKey = detail?.accessKey;
  if (!detail || !accessKey) return null;
  const menu = (): MenuEntry[] => [
    { id: "rename", label: t("workspace.rename"), onSelect: () => detail.setRenaming(true) },
    { id: "rotate", label: t("accessKeys.rotate"), onSelect: () => detail.openAction("rotate") },
    { id: "passThrough", label: t("accessKeys.enablePtLabel"), disabled: accessKey.pass_through, onSelect: () => detail.openAction("passThrough") },
    "separator",
    { id: "revoke", label: t("accessKeys.revoke"), onSelect: () => detail.openAction("revoke") },
  ];
  return { id: "selectedKey", label: accessKey.name, tools: [{ id: "keyActions", label: t("workspace.keyActions"), icon: "keyActions", menu }] };
}

export function PersonaWindow() {
  const k = useLoadedKey();
  if ("placeholder" in k) return <>{k.placeholder}</>;
  return (
    <div className="phx-window-pad">
      <PersonaPicker accessKey={k.accessKey} onUpdate={k.detail.applyUpdate} esphome={k.detail.esphome} />
    </div>
  );
}

export function CapabilitiesWindow() {
  const k = useLoadedKey();
  if ("placeholder" in k) return <>{k.placeholder}</>;
  return (
    <div className="phx-window-pad">
      <CapabilityMatrix accessKey={k.accessKey} onUpdate={k.detail.applyUpdate} esphome={k.detail.esphome} />
    </div>
  );
}

/** Notes, Tool Announcement and Rate Limiting: the key's specialized settings. */
export function KeySettingsWindow() {
  const k = useLoadedKey();
  if ("placeholder" in k) return <>{k.placeholder}</>;
  return (
    <div className="phx-window-pad phx-key-settings">
      <section className="phx-key-settings-section" aria-labelledby="phx-key-notes">
        <h3 id="phx-key-notes" className="phx-section-title">{t("accessKeys.cardNotes")}</h3>
        <AccessKeyNotes accessKey={k.accessKey} onUpdate={k.detail.setAccessKey} />
      </section>
      <section className="phx-key-settings-section" aria-labelledby="phx-key-announce">
        <h3 id="phx-key-announce" className="phx-section-title">{t("accessKeys.cardToolAnnouncement")}</h3>
        <ToolAnnouncementToggle accessKey={k.accessKey} onUpdate={k.detail.applyUpdate} />
      </section>
      <section className="phx-key-settings-section" aria-labelledby="phx-key-rate">
        <h3 id="phx-key-rate" className="phx-section-title">{t("accessKeys.cardRateLimiting")}</h3>
        <RateLimitConfig accessKey={k.accessKey} onUpdate={k.detail.setAccessKey} />
      </section>
    </div>
  );
}

export function PresetsWindow() {
  const k = useLoadedKey();
  if ("placeholder" in k) return <>{k.placeholder}</>;
  return (
    <div className="phx-window-pad">
      <PresetsCard accessKey={k.accessKey} onUpdate={k.detail.applyUpdate} />
    </div>
  );
}

export function PermissionsWindow() {
  const k = useLoadedKey();
  const detail = "detail" in k ? k.detail : null;
  const scoped = "accessKey" in k && !k.accessKey.pass_through;
  // Select By and Clear All act on the tree, so they sit on the toolbar while it is shown.
  const onToolbar = useToolbarTools(detail && scoped ? [{
    id: "permissions",
    label: t("accessKeys.permissionsTree"),
    tools: [
      { id: "selectBy", label: t("accessKeys.selectByAreaLabelIntegration"), icon: "selectBy", disabled: !detail.entityTree, onSelect: detail.openSelectBy },
      { id: "clearAll", label: t("workspace.clearAllPermissions"), icon: "delete", tone: "danger", onSelect: () => detail.openAction("clearPerms") },
    ],
  }] : []);
  if ("placeholder" in k) return <>{k.placeholder}</>;
  const { accessKey } = k;
  if (accessKey.pass_through) {
    return <div className="phx-window-pad"><PassThroughNotice accessKey={accessKey} onUpdate={k.detail.applyUpdate} /></div>;
  }
  return (
    <div className="phx-window-pad">
      {!onToolbar && (
        <div className="tree-header-actions">
          {k.detail.entityTree && (
            <button className="btn btn-outline btn-sm" onClick={k.detail.openSelectBy}>
              {t("accessKeys.selectByAreaLabelIntegration")}
            </button>
          )}
          <button className="btn btn-danger btn-sm" onClick={() => k.detail.openAction("clearPerms")}>
            {t("accessKeys.clearAll")}
          </button>
        </div>
      )}
      <EntityTree
        accessKeyId={accessKey.id}
        permissions={accessKey.permissions}
        onPermissionsChange={k.detail.onPermissionsChange}
        onEntityClick={k.detail.revealNode}
        collapseKey={k.detail.collapseTreeKey}
        revealEntity={k.detail.selectedEntityId || undefined}
        revealDepth={k.detail.selectedDepth}
        revealNonce={k.detail.revealNonce}
        mesaProfileEntities={k.detail.mesaProfileEntities}
        mesaProfileDevices={k.detail.mesaProfileDevices}
        mesaProfileDomains={k.detail.mesaProfileDomains}
        onOpenMesa={k.detail.openMesa}
      />
    </div>
  );
}

export function PermissionSummaryWindow() {
  const k = useLoadedKey();
  if ("placeholder" in k) return <>{k.placeholder}</>;
  if (k.accessKey.pass_through) return <p className="phx-window-empty">{t("workspace.summaryPassThrough")}</p>;
  return (
    <div className="phx-window-pad">
      <PermissionSummary
        permissions={k.accessKey.permissions}
        entityTree={k.detail.entityTree}
        onEntityClick={k.detail.revealNode}
        mesaProfileEntities={k.detail.mesaProfileEntities}
        onOpenMesa={k.detail.openMesa}
      />
    </div>
  );
}
