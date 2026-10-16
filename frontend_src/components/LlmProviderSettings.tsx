import { BusyInput, BusySelect } from "./BusyButton";
import { BusyButton } from "./BusyButton";
import { IconButton, EditIcon, RefreshIcon, ProbeIcon, TrashIcon } from "./ProviderAccountControls";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Modal } from "./Modal";
import { DocsHelpLink } from "./common";
import { effortLevelLabel, formatDateTime } from "../utils";
import type { AgentCliInstance, AgentCliProviderType, GlobalSettings } from "../types";
import { t } from "../i18n";
import { tRich } from "../i18n/rich";
import { ProviderAddForm } from "./ProviderAddForm";

// Which account warnings the operator has closed. Persisted so a closed
// warning stays closed across visits.
//
// The key includes the MODEL, so a warning about a different model is a
// different warning and shows again: a dismissal never silently covers a NEW
// problem.
const DISMISS_KEY = "phx-agentcli-dismissed";
/** The model select's value for "follow the provider's newest model". */
const AUTO_MODEL = "";
/** What the model select shows for an account before the operator changes it. */
const currentDraft = (inst: AgentCliInstance) => (inst.model_mode === "auto" ? AUTO_MODEL : inst.model);

function readDismissed(): Record<string, true> {
  try {
    const raw = window.localStorage.getItem(DISMISS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" ? (parsed as Record<string, true>) : {};
  } catch {
    return {};
  }
}

function writeDismissed(next: Record<string, true>) {
  try {
    window.localStorage.setItem(DISMISS_KEY, JSON.stringify(next));
  } catch { /* storage blocked: the warning keeps showing, which is safe */ }
}

/** A warning the operator can close, leaving a marker that reopens it. */
function DismissibleWarning({ text, onDismiss }: { text: string; onDismiss: () => void }) {
  return (
    <div className="banner banner-warn agentcli-warn">
      <span>{text}</span>
      <button type="button" className="agentcli-warn-close" onClick={onDismiss}
              aria-label={t("settings.agentcliDismissWarning")}>&times;</button>
    </div>
  );
}

/** The marker a closed warning leaves behind, next to the model it is about. */
function WarningBadge({ text, onShow }: { text: string; onShow: () => void }) {
  return (
    <button type="button" className="agentcli-warn-badge" onClick={onShow}
            title={text} aria-label={text}>!</button>
  );
}

/** What a refresh or an options check found, held as data rather than prose.
 *
 *  A sentence rendered when the action ran would keep its language after a
 *  panel language switch repaints the card, so state holds the facts and the
 *  text is translated at RENDER time. */
type CardResult =
  | { kind: "refreshed"; models: number; declared: boolean }
  | { kind: "probed"; calls: number; levels?: string[]; effortCheckable: boolean; answered: boolean;
      /** The image check's finding, present only when it ran. */
      vision?: "seen" | "unseen" | "inconclusive" }
  | { kind: "failed"; message: string };

/** Turn a probe response into its card result, or throw so the caller reports a failure.
 *
 *  Built synchronously, not inside the `setRefreshResult` updater that consumes
 *  it: React runs an updater during the NEXT render, where a throw is an
 *  unhandled render error that the `try` around the request cannot see and
 *  that takes the whole settings component down. The shape is checked rather
 *  than assumed because the client can hand back values the return type does
 *  not admit: `undefined` for a 204, and an `{error, message}` object for a
 *  success that did not parse as JSON.
 */
function probedCard(r: Awaited<ReturnType<typeof api.probeAgentCliCapabilities>>): CardResult {
  if (!r || typeof r !== "object" || typeof r.probed !== "object" || r.probed === null) {
    throw new Error(t("settings.agentcliConnectionFailed"));
  }
  // Every FIELD is checked, not just the container: a string `effort_levels`
  // has a truthy `.length` but no `.map`, so cardResultText would throw during
  // render. Levels degrade to absent rather than throwing, because "the
  // provider did not report levels" is an answer this card already renders and
  // a malformed list is better reported as that than as a failed connection.
  const levels = Array.isArray(r.probed.effort_levels)
    && r.probed.effort_levels.every((l) => typeof l === "string")
    ? r.probed.effort_levels
    : undefined;
  return {
    kind: "probed",
    calls: typeof r.calls === "number" ? r.calls : 0,
    answered: r.answered === true,
    levels,
    effortCheckable: r.effort_checkable === true,
    ...(r.vision_checked === true ? {
      vision: r.probed.vision === true ? "seen" : r.probed.vision === false ? "unseen" : "inconclusive",
    } as const : {}),
  };
}

function cardResultText(r: CardResult): string {
  if (r.kind === "failed") return r.message;
  if (r.kind === "refreshed") {
    return r.declared
      ? t("settings.agentcliRefreshed", { models: r.models })
      : t("settings.agentcliRefreshedNoCaps", { models: r.models });
  }
  // Four outcomes. The provider declining every question is not a finding about
  // the model, so it is reported first and on its own.
  if (!r.answered) return t("settings.agentcliProbedUnanswered", { calls: r.calls });
  const found = r.levels?.length
    ? t("settings.agentcliProbedLevels", {
        levels: r.levels.map(effortLevelLabel).join(t("common.listSeparator")),
      })
    : r.effortCheckable
      ? t("settings.agentcliProbedNothing")
      : t("settings.agentcliProbedNoLevels");
  const vision = r.vision === "seen" ? t("settings.agentcliProbedVisionSeen")
    : r.vision === "unseen" ? t("settings.agentcliProbedVisionUnseen")
      : r.vision === "inconclusive" ? t("settings.agentcliProbedVisionInconclusive") : "";
  return [t("settings.agentcliProbed", { calls: r.calls, found }), vision].filter(Boolean).join(" ");
}

function notifyChanged() {
  window.dispatchEvent(new CustomEvent("phx-agentcli-providers-changed"));
}

interface Props {
  settings: GlobalSettings;
  onChange: (key: keyof GlobalSettings, value: string | number | boolean) => void;
  saving: boolean;
}

/** Hours between automatic provider model checks: the allowed range. */
const CHECK_HOURS_MIN = 6;
const CHECK_HOURS_MAX = 720;

export function LlmProviderSettings({ settings, onChange, saving }: Props) {
  const [instances, setInstances] = useState<AgentCliInstance[] | null>(null);
  const [providerTypes, setProviderTypes] = useState<AgentCliProviderType[]>([]);
  const [busy, setBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState<AgentCliInstance | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);

  // Each account's live model list, keyed by instance id. Absent means "not
  // checked or the provider could not be reached", which is NOT the same as an
  // empty list and must never be reported as one: a provider that is down would
  // otherwise be indistinguishable from one that dropped every model.
  const [liveModels, setLiveModels] = useState<Record<string, string[]>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [modelDraft, setModelDraft] = useState("");
  const [probeOnSave, setProbeOnSave] = useState(true);
  const [modelError, setModelError] = useState<string | null>(null);

  const load = () => api.getAgentCliProviders().then((response) => {
    setInstances(response.instances);
    setProviderTypes(response.provider_types ?? []);
  }).catch(() => {
    setInstances([]);
    setProviderTypes([]);
  });
  useEffect(() => { void load(); }, []);

  // Refresh every account's model list when the card is shown. A model list is
  // a cheap authenticated GET that costs no completion tokens. Best-effort and
  // parallel; a provider that fails records nothing.
  useEffect(() => {
    if (!instances?.length) return;
    let cancelled = false;
    void Promise.all(instances.map(async (inst) => {
      try {
        const r = await api.getAgentCliModels(inst.id);
        return [inst.id, r.models] as const;
      } catch {
        return null;
      }
    })).then((pairs) => {
      if (cancelled) return;
      setLiveModels(Object.fromEntries(pairs.filter((p): p is readonly [string, string[]] => p !== null)));
    });
    return () => { cancelled = true; };
  }, [instances]);

  /** The account's default model is not in the provider's live model list.
   *
   *  Claimed only against a non-empty list that was actually fetched: an
   *  unreachable provider, or one that reports nothing, says nothing about the
   *  model, and flagging it would send the operator to fix a working account.
   */
  const modelIsStale = (inst: AgentCliInstance) => {
    const models = liveModels[inst.id];
    return Boolean(
      inst.model_mode !== "auto" && inst.model && models && models.length > 0 && !models.includes(inst.model),
    );
  };

  const [dismissed, setDismissed] = useState<Record<string, true>>(readDismissed);

  // A warning is identified by the account, which warning it is, and the model
  // it is about, so changing the model retires the dismissal with it.
  const warnKey = (inst: AgentCliInstance, kind: string) => `${inst.id}|${kind}|${inst.model}`;
  const isDismissed = (inst: AgentCliInstance, kind: string) => dismissed[warnKey(inst, kind)] === true;
  const setDismissedFlag = (inst: AgentCliInstance, kind: string, on: boolean) =>
    setDismissed((prev) => {
      const next = { ...prev };
      if (on) next[warnKey(inst, kind)] = true;
      else delete next[warnKey(inst, kind)];
      writeDismissed(next);
      return next;
    });

  const [refreshing, setRefreshing] = useState<string | null>(null);
  const [refreshResult, setRefreshResult] = useState<Record<string, CardResult | null>>({});

  // Explicit refresh: re-read the model list AND whatever the provider declares
  // about each model. Separate from the on-open list fetch because Ollama's
  // capabilities cost one request PER MODEL. Still no completion tokens.
  const refresh = async (inst: AgentCliInstance) => {
    setRefreshing(inst.id);
    setRefreshResult((r) => ({ ...r, [inst.id]: null }));
    try {
      const r = await api.refreshAgentCliProvider(inst.id);
      // Read off the response HERE, not inside the updaters below: see probedCard.
      if (!r || !Array.isArray(r.models)) throw new Error(t("settings.agentcliConnectionFailed"));
      const models = r.models;
      // "This provider publishes none" is a real answer and must not read like a
      // failed refresh: most providers report only an id and an owner.
      const card: CardResult = { kind: "refreshed", models: models.length, declared: r.declared };
      setLiveModels((m) => ({ ...m, [inst.id]: models }));
      await load();
      setRefreshResult((res) => ({ ...res, [inst.id]: card }));
    } catch (err: unknown) {
      setRefreshResult((res) => ({
        ...res,
        [inst.id]: { kind: "failed", message: err instanceof Error ? err.message : t("settings.agentcliConnectionFailed") },
      }));
    } finally {
      setRefreshing(null);
    }
  };

  /** This model declared it cannot call tools, so Agent Chat cannot use it.
   *
   *  Surfaced ON THE OPTION, not as a banner: a provider library can hold
   *  hundreds of unusable models, there is nothing to fix, and a permanent
   *  banner about a choice nobody can make teaches the reader to skip banners,
   *  which a genuinely broken account needs read. A BANNER means only "this
   *  account is broken, fix it".
   */
  const cannotCallTools = (inst: AgentCliInstance, model: string) =>
    inst.capabilities?.[model]?.tools === false;

  // Capability probe. Its own button and its own confirmation, because unlike
  // everything else on this card it spends the operator's credit: a handful of
  // one-token completions against the selected model.
  const [probing, setProbing] = useState<string | null>(null);
  const [confirmProbe, setConfirmProbe] = useState<AgentCliInstance | null>(null);

  /** Run the check and REPORT it, whichever path asked for it.
   *
   *  One helper so the result reaches the operator identically from the manual
   *  button, adding an account, and changing the default model: a check that
   *  spends credit must always show its answer. Never throws: the account is
   *  already stored and working.
   */
  const runProbe = async (id: string) => {
    try {
      const card = probedCard(await api.probeAgentCliCapabilities(id));
      setRefreshResult((res) => ({ ...res, [id]: card }));
    } catch (err: unknown) {
      setRefreshResult((res) => ({
        ...res,
        [id]: { kind: "failed", message: err instanceof Error ? err.message : t("settings.agentcliConnectionFailed") },
      }));
    }
  };

  const probeCaps = async (inst: AgentCliInstance) => {
    setProbing(inst.id);
    setConfirmProbe(null);
    setRefreshResult((r) => ({ ...r, [inst.id]: null }));
    try {
      await runProbe(inst.id);
      await load();
      // Both chat-window hosts hold their accounts as state and reload on this
      // event. Without it the newly stored capabilities stay invisible to the
      // window until a page reload, which reads like the check not working.
      notifyChanged();
    } finally {
      setProbing(null);
    }
  };

  const saveModel = async (id: string) => {
    setBusy(true);
    setModelError(null);
    try {
      if (modelDraft === AUTO_MODEL) await api.setAgentCliProviderAuto(id);
      else await api.setAgentCliProviderModel(id, modelDraft);
      // A different model has different options, and nothing is known about the
      // new one yet. Same offer as the add flow: check before the first
      // conversation rather than after one behaves oddly.
      if (probeOnSave) await runProbe(id);
      await load();
      notifyChanged();
      setEditing(null);
    } catch (err: unknown) {
      setModelError(err instanceof Error ? err.message : t("settings.agentcliSaveFailed"));
    } finally {
      setBusy(false);
    }
  };

  const providerCreated = async (instance: AgentCliInstance, probe: boolean) => {
    if (probe && instance.id) await runProbe(instance.id);
    await load();
    notifyChanged();
  };

  const remove = async (id: string) => {
    setBusy(true);
    setRemoveError(null);
    try {
      await api.deleteAgentCliProvider(id);
      await load();
      notifyChanged();
      setConfirmRemove(null);
    } catch (err: unknown) {
      // Keep the dialog open and report the failure; a silent one would look
      // as if the account had been removed.
      setRemoveError(err instanceof Error ? err.message : t("settings.agentcliRemoveAccountFailed"));
    } finally {
      setBusy(false);
    }
  };

  const [hoursInput, setHoursInput] = useState(String(settings.provider_catalog_refresh_hours ?? 24));
  useEffect(() => { setHoursInput(String(settings.provider_catalog_refresh_hours ?? 24)); },
            [settings.provider_catalog_refresh_hours]);
  const hoursTimer = useRef<number | undefined>(undefined);
  const commitHours = (raw: string) => {
    let n = parseInt(raw, 10);
    if (Number.isNaN(n)) { setHoursInput(String(settings.provider_catalog_refresh_hours ?? 24)); return; }
    n = Math.max(CHECK_HOURS_MIN, Math.min(CHECK_HOURS_MAX, n));
    setHoursInput(String(n));
    if (n !== settings.provider_catalog_refresh_hours) onChange("provider_catalog_refresh_hours", n);
  };
  const onHoursInput = (raw: string) => {
    setHoursInput(raw);
    window.clearTimeout(hoursTimer.current);
    hoursTimer.current = window.setTimeout(() => commitHours(raw), 600);
  };
  const commitHoursNow = (raw: string) => {
    window.clearTimeout(hoursTimer.current);
    commitHours(raw);
  };
  useEffect(() => () => window.clearTimeout(hoursTimer.current), []);
  const checksOn = settings.provider_catalog_refresh_enabled !== false;

  return (
    <div className="card">
      <h3 className="card-header">
        {t("settings.providersCard")}
        <DocsHelpLink path="agentcli.html#providers" label={t("settings.providersCard")} />
      </h3>
      <p className="settings-info-note" style={{ marginTop: 0 }}>
        {t("settings.providersIntro")}
      </p>

      <ProviderAddForm
        providerTypes={providerTypes}
        allowAutomatic
        disabled={busy}
        completeLabel={t("settings.agentcliDone")}
        addingLabelKey="settings.agentcliAdding"
        onCreated={providerCreated}
      />

      {/* Configured accounts. */}
      <div className="agentcli-settings-providers">
        {(instances ?? []).map((inst) => (
          <div key={inst.id} className="agentcli-settings-provider">
            <div className="agentcli-settings-provider-main">
            <div className="agentcli-settings-provider-head">
              <span>{inst.name}</span>
            </div>
            {modelIsStale(inst) && !isDismissed(inst, "retired") && (
              <DismissibleWarning
                text={t("settings.agentcliModelRetired", { model: inst.model })}
                onDismiss={() => setDismissedFlag(inst, "retired", true)}
              />
            )}
            {inst.model_replaced_from && (
              <div className="agentcli-settings-hint" role="status">
                {t("settings.agentcliModelReplaced", { retired: inst.model_replaced_from, model: inst.model })}
              </div>
            )}
            {editing === inst.id ? (
              <div className="agentcli-settings-model-edit">
                <BusySelect className="input input-auto" value={modelDraft} busy={busy}
                        aria-label={t("settings.agentcliSelectModel")}
                        onChange={(e) => setModelDraft(e.target.value)}>
                  <option value={AUTO_MODEL}>{t("settings.agentcliModelAuto")}</option>
                  {(liveModels[inst.id] ?? []).map((m) => (
                    <option key={m} value={m} disabled={cannotCallTools(inst, m)}>
                      {cannotCallTools(inst, m) ? t("settings.agentcliModelNoTools", { model: m }) : m}
                    </option>
                  ))}
                  {/* The stored model when the provider no longer lists it. It has
                      to be PRESENT or the select silently displays some other
                      model as though it were the configured one, and DISABLED or
                      a deleted model could be re-selected. */}
                  {modelIsStale(inst) && (
                    <option value={inst.model} disabled>
                      {t("settings.agentcliModelUnavailable", { model: inst.model })}
                    </option>
                  )}
                </BusySelect>
                <div className="agentcli-settings-model-actions">
                  <div className="agentcli-settings-probe-opt">
                    <label className="toggle-switch" htmlFor={`agentcli-probe-on-save-${inst.id}`}>
                      <input id={`agentcli-probe-on-save-${inst.id}`} type="checkbox" checked={probeOnSave}
                             onChange={(e) => setProbeOnSave(e.target.checked)} />
                      <span className="toggle-switch-track" />
                    </label>
                    <label className="agentcli-settings-probe-copy" htmlFor={`agentcli-probe-on-save-${inst.id}`}>
                      {t("settings.agentcliProbeOnAdd")}
                    </label>
                  </div>
                  <div className="agentcli-settings-form-actions">
                    <BusyButton className="btn btn-primary btn-sm"
                            busy={busy} disabled={modelDraft === currentDraft(inst)}
                            onClick={() => void saveModel(inst.id)}>{t("common.save")}</BusyButton>
                    <BusyButton className="btn btn-text btn-sm" busy={busy}
                            onClick={() => { setEditing(null); setModelError(null); }}>{t("settings.cancel")}</BusyButton>
                  </div>
                </div>
              </div>
            ) : (
              <span className="agentcli-settings-model">
                  {inst.model_mode === "auto"
                    ? (inst.model
                      ? t("settings.agentcliDefaultModelAuto", { model: inst.model })
                      : t("settings.agentcliDefaultModelAutoPending"))
                    : t("settings.agentcliDefaultModel", { model: inst.model || t("settings.agentcliNotSet") })}
                  {modelIsStale(inst) && isDismissed(inst, "retired") && (
                    <WarningBadge text={t("settings.agentcliModelRetired", { model: inst.model })}
                                  onShow={() => setDismissedFlag(inst, "retired", false)} />
                  )}
                  {inst.model && cannotCallTools(inst, inst.model) && isDismissed(inst, "notools") && (
                    <WarningBadge text={t("settings.agentcliDefaultNoTools", { model: inst.model })}
                                  onShow={() => setDismissedFlag(inst, "notools", false)} />
                  )}
              </span>
            )}
            {refreshResult[inst.id] && (
              <div className="agentcli-settings-hint" role="status">
                {cardResultText(refreshResult[inst.id]!)}
              </div>
            )}
            {inst.model && cannotCallTools(inst, inst.model) && !isDismissed(inst, "notools") && (
              <DismissibleWarning
                text={t("settings.agentcliDefaultNoTools", { model: inst.model })}
                onDismiss={() => setDismissedFlag(inst, "notools", true)}
              />
            )}
            {editing === inst.id && modelError && (
              <div className="banner banner-error" role="alert">{modelError}</div>
            )}
            </div>
            {editing !== inst.id && (
              <div className="agentcli-account-actions">
                  <IconButton label={t("settings.agentcliChangeModel")} blocked={busy}
                              onClick={() => { setEditing(inst.id); setModelDraft(currentDraft(inst)); setModelError(null); }}>
                    <EditIcon />
                  </IconButton>
                  <IconButton label={t("settings.agentcliRefresh")} busy={refreshing === inst.id}
                              busyLabel={t("settings.agentcliRefreshing")}
                              blocked={busy || refreshing !== null} onClick={() => void refresh(inst)}>
                    <RefreshIcon />
                  </IconButton>
                  {/* The last-checked time rides on this button's own tooltip
                      rather than a separate status line, to keep the card's
                      controls uncluttered. */}
                  <IconButton busy={probing === inst.id}
                              busyLabel={t("settings.agentcliProbing")}
                              label={inst.capabilities_checked_at
                                ? t("settings.agentcliProbeCapsChecked", { when: formatDateTime(inst.capabilities_checked_at) })
                                : t("settings.agentcliProbeCaps")}
                              blocked={busy || probing !== null} disabled={!inst.model}
                              onClick={() => setConfirmProbe(inst)}>
                    <ProbeIcon />
                  </IconButton>
                  <IconButton label={t("settings.agentcliRemoveAccount", { name: inst.name })} danger
                              blocked={busy} onClick={() => setConfirmRemove(inst)}>
                    <TrashIcon />
                  </IconButton>
              </div>
            )}
          </div>
        ))}
        {instances && instances.length === 0 && (
          <div className="agentcli-settings-hint">{t("settings.agentcliNoAccounts")}</div>
        )}
      </div>

      {confirmProbe && (
        <Modal titleId="agentcli-probe-title" onClose={() => setConfirmProbe(null)}>
          <h3 className="modal-title" id="agentcli-probe-title">{t("settings.agentcliProbeTitle")}</h3>
          <p>{tRich("settings.agentcliProbeBody", { strong: (c) => <strong>{c}</strong> },
                    { model: confirmProbe.model, name: confirmProbe.name })}</p>
          <div className="modal-actions">
            <BusyButton className="btn btn-primary btn-sm" busy={busy}
                    onClick={() => void probeCaps(confirmProbe)}>{t("settings.agentcliProbeRun")}</BusyButton>
            <BusyButton className="btn btn-text" busy={busy}
                    onClick={() => setConfirmProbe(null)}>{t("settings.cancel")}</BusyButton>
          </div>
        </Modal>
      )}

      {confirmRemove && (
        <Modal titleId="agentcli-remove-title" onClose={() => { setConfirmRemove(null); setRemoveError(null); }}>
          <h3 className="modal-title" id="agentcli-remove-title">{t("settings.agentcliRemoveTitle")}</h3>
          <p>{tRich("settings.agentcliRemoveBody", { strong: (c) => <strong>{c}</strong> }, { name: confirmRemove.name })}</p>
          {removeError && <div className="banner banner-error" role="alert">{removeError}</div>}
          <div className="modal-actions">
            <BusyButton className="btn btn-danger btn-sm" busy={busy} onClick={() => void remove(confirmRemove.id)}>{t("settings.remove")}</BusyButton>
            <BusyButton className="btn btn-text" busy={busy} onClick={() => { setConfirmRemove(null); setRemoveError(null); }}>{t("settings.cancel")}</BusyButton>
          </div>
        </Modal>
      )}


      <hr className="settings-divider" />
      <div className="toggle-row toggle-row-plain">
        <div className="toggle-label">
          <span>{t("settings.providerChecksLabel")}</span>
          <small>{t("settings.providerChecksHelp")}</small>
        </div>
        <label className={`toggle-switch${saving ? " disabled" : ""}`}>
          <BusyInput
            type="checkbox"
            aria-label={t("settings.providerChecksLabel")}
            checked={checksOn}
            busy={saving}
            onChange={(e) => onChange("provider_catalog_refresh_enabled", e.target.checked)}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
      <div className="toggle-row toggle-row-plain" style={{ marginTop: 14 }}>
        <div className="toggle-label">
          <span>{t("settings.providerChecksHoursLabel")}</span>
          <small>{t("settings.providerChecksHoursHelp", { min: CHECK_HOURS_MIN, max: CHECK_HOURS_MAX })}</small>
        </div>
        <BusyInput
          className="input input-auto"
          type="number" min={CHECK_HOURS_MIN} max={CHECK_HOURS_MAX} step={1}
          aria-label={t("settings.providerChecksHoursAria")}
          value={hoursInput}
          busy={saving}
          disabled={!checksOn}
          onChange={(e) => onHoursInput(e.target.value)}
          onBlur={() => commitHoursNow(hoursInput)}
          onKeyDown={(e) => { if (e.key === "Enter") commitHoursNow(hoursInput); }}
        />
      </div>
    </div>
  );
}
