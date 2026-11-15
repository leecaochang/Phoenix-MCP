import { BusyInput, BusySelect } from "./BusyButton";
import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import { helpRow } from "../ui/SettingLabel";
import { ActionButton } from "../ui/ActionButton";
import { useClickAway } from "../ui/useClickAway";
import { ProviderAddForm } from "./ProviderAddForm";
import type { AgentCliInstance, AgentCliProviderType, GlobalSettings } from "../types";
import { NumberField } from "../ui/NumberField";
import { modelLabel } from "../utils";

const THRESHOLD_DEFAULT = 0.9;
const THRESHOLD_MIN = 0.7;
const THRESHOLD_MAX = 1;

export function DecisionProviderSettings({ settings, onChange, saving }: {
  settings: GlobalSettings;
  onChange: (key: keyof GlobalSettings, value: string | number | boolean) => void;
  saving: boolean;
}) {
  // Its window can show in two workspaces at once; Find Setting finds the fields by `data-setting`.
  const uid = useId();
  const [accounts, setAccounts] = useState<AgentCliInstance[]>([]);
  const [accountsLoaded, setAccountsLoaded] = useState(false);
  const [types, setTypes] = useState<AgentCliProviderType[]>([]);
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [modelDraft, setModelDraft] = useState("");
  const [refreshing, setRefreshing] = useState<string | null>(null);
  const [refreshed, setRefreshed] = useState<Record<string, { models: number; declared: boolean }>>({});
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  // The account whose options show under it; none until one is clicked.
  const [open, setOpen] = useState<string | null>(null);
  // Another account can be added beside the saved ones; only one is ever in use.
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const selectionVersion = useRef(0);
  const currentSelectionVersion = selectionVersion.current;
  const mounted = useRef(true);
  const [budget, setBudget] = useState(String(settings.decision_timeout_ms ?? 5000));
  useEffect(() => { setBudget(String(settings.decision_timeout_ms ?? 5000)); }, [settings.decision_timeout_ms]);
  const [requestLimit, setRequestLimit] = useState(String(settings.decision_request_budget ?? 0));
  // The threshold draft of the open account; saved when the field loses focus.
  const [thresholdDraft, setThresholdDraft] = useState("");
  useEffect(() => { setRequestLimit(String(settings.decision_request_budget ?? 0)); }, [settings.decision_request_budget]);

  async function load() {
    const current = ++generation.current;
    try {
      const response = await api.getDecisionProviders();
      if (!mounted.current || current !== generation.current) return;
      setAccounts(response.instances);
      setAccountsLoaded(true);
      setTypes(response.provider_types);
      setError("");
    } catch {
      if (mounted.current && current === generation.current) setError(t("routing.failed"));
    }
  }
  useEffect(() => {
    mounted.current = true;
    const pendingGeneration = generation;
    void load();
    return () => { mounted.current = false; pendingGeneration.current++; };
  }, []);

  // One radio group: No Decision Provider clears only the account in
  // use and the default stays; choosing an account puts it in use, and the server makes it
  // the default with it.
  function use(id: string) {
    selectionVersion.current++;
    onChange("decision_provider_id", id);
  }

  async function editModel(account: AgentCliInstance) {
    setEditing(account.id);
    setModelDraft(account.model ?? "");
    setError("");
    if (models[account.id]) return;
    setBusy(true);
    const current = ++generation.current;
    try {
      const response = await api.getAgentCliModels(account.id);
      if (!mounted.current || current !== generation.current) return;
      setModels(previous => ({ ...previous, [account.id]: response.models }));
    } catch (cause) {
      if (mounted.current && current === generation.current)
        setError(cause instanceof Error ? cause.message : t("settings.agentcliConnectionFailed"));
    } finally {
      if (mounted.current && current === generation.current) setBusy(false);
    }
  }

  async function changeModel(id: string, model: string) {
    setBusy(true);
    setError("");
    generation.current++;
    try {
      await api.setAgentCliProviderModel(id, model);
      if (mounted.current) { setEditing(null); await load(); }
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : t("routing.failed"));
    } finally { if (mounted.current) setBusy(false); }
  }
  async function refresh(id: string) {
    setBusy(true);
    setRefreshing(id);
    setError("");
    const current = ++generation.current;
    setRefreshed(previous => { const next = { ...previous }; delete next[id]; return next; });
    try {
      const response = await api.refreshAgentCliProvider(id);
      if (!mounted.current || current !== generation.current) return;
      if (!response || !Array.isArray(response.models)) throw new Error(t("routing.failed"));
      setModels(previous => ({ ...previous, [id]: response.models }));
      setRefreshed(previous => ({ ...previous, [id]: { models: response.models.length, declared: response.declared } }));
    } catch (cause) {
      if (mounted.current && current === generation.current) setError(cause instanceof Error ? cause.message : t("routing.failed"));
    } finally {
      if (mounted.current) { setBusy(false); setRefreshing(null); }
    }
  }
  async function saveThreshold(account: AgentCliInstance) {
    const value = Number(thresholdDraft);
    const current = account.decision_threshold ?? THRESHOLD_DEFAULT;
    // Only a whole hundredth within the bounds is sent; anything else restores the saved value.
    if (!thresholdDraft.trim() || !Number.isFinite(value) || value < THRESHOLD_MIN || value > THRESHOLD_MAX
        || Math.round(value * 100) !== value * 100) {
      setThresholdDraft(current.toFixed(2));
      return;
    }
    if (value === current) return;
    setBusy(true);
    setError("");
    try {
      await api.setDecisionThreshold(account.id, value);
      if (mounted.current) await load();
    } catch (cause) {
      if (mounted.current) {
        setError(cause instanceof Error ? cause.message : t("routing.failed"));
        setThresholdDraft(current.toFixed(2));
      }
    } finally { if (mounted.current) setBusy(false); }
  }
  async function remove(id: string) {
    setBusy(true);
    setError("");
    generation.current++;
    try {
      await api.deleteAgentCliProvider(id);
      if (!mounted.current) return;
      setConfirmRemove(null);
      setOpen(null);
      window.dispatchEvent(new Event("phx-settings-refresh"));
      await load();
    } catch { if (mounted.current) setError(t("routing.failed")); }
    finally { if (mounted.current) setBusy(false); }
  }

  const hasAccounts = accounts.length > 0;
  // One account must always be selected while any exists. The stored default can
  // be absent (older installs, or the default account was removed), so the first
  // account is selected for the operator rather than leaving the group empty.
  const storedDefault = settings.decision_provider_default_id || settings.decision_provider_id || "";
  const defaultId = accounts.some(account => account.id === storedDefault) ? storedDefault : (accounts[0]?.id ?? "");
  // Until the accounts have loaded, show what the setting says rather than
  // claiming the provider is off; a failed read leaves the switch locked.
  const inUse = accountsLoaded
    ? hasAccounts && !!settings.decision_provider_id && accounts.some(account => account.id === settings.decision_provider_id)
    : !!settings.decision_provider_id;
  useEffect(() => {
    if (accountsLoaded && hasAccounts && !saving && defaultId && defaultId !== settings.decision_provider_default_id) {
      onChange("decision_provider_default_id", defaultId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountsLoaded, hasAccounts, defaultId, settings.decision_provider_default_id, saving]);

  // A click anywhere but the open account and its options closes them; changing the model,
  // and a Remove waiting for its answer, cancel the same way. A running action keeps them.
  // Each waits for the click, so the account below still gets it after the options above fold away.
  const closeOptions = () => { setOpen(null); setEditing(null); setConfirmRemove(null); setError(""); };
  const openRow = useRef<HTMLDivElement | null>(null);
  useClickAway(openRow, closeOptions, open !== null && !busy, undefined, "click");
  const editBox = useRef<HTMLDivElement | null>(null);
  useClickAway(editBox, () => { setEditing(null); setError(""); }, editing !== null && !busy, undefined, "click");
  const askBox = useRef<HTMLSpanElement | null>(null);
  useClickAway(askBox, () => setConfirmRemove(null), confirmRemove !== null && !busy, undefined, "click");

  /** An account's name; a local server's is the word for "local", not a company's name. */
  const accountName = (account: AgentCliInstance) => account.kind === "local_decision"
    ? account.name.replace(/^Local(?= \(|$)/, t("routing.localAccount")) : account.name;

  /** An account's options, under it: a line for what an action found, then the actions. */
  const accountOptions = (account: AgentCliInstance) => {
    const unavailable = models[account.id]?.length > 0 && !!account.model && !models[account.id].includes(account.model);
    return <div className="phx-provider-account" aria-label={accountName(account)} role="group">
      {/* Hidden while there is nothing to say. */}
      <div className="agentcli-settings-hint phx-provider-info" role="status">
        {unavailable && <p>{t("settings.agentcliModelUnavailable", { model: account.model })}</p>}
        {refreshed[account.id] && <p>{t(refreshed[account.id].declared ? "settings.agentcliRefreshed" : "settings.agentcliRefreshedNoCaps",
          { models: refreshed[account.id].models })}</p>}
      </div>
      {editing === account.id ? <div ref={editBox} className="agentcli-settings-model-edit">
        {/* Refresh Models sits beside the model it refreshes. */}
        <div className="agentcli-add-inline">
          <BusySelect className="input input-auto" value={modelDraft} autoFocus
            aria-label={t("routing.accountModel", { name: accountName(account) })}
            busy={saving || busy} disabled={!models[account.id]?.length}
            onChange={event => setModelDraft(event.target.value)}>
            {!models[account.id]?.includes(account.model ?? "") && <option value={account.model ?? ""} disabled>
              {models[account.id]?.length ? t("settings.agentcliModelUnavailable", { model: account.model ?? "" }) : account.model || t("settings.agentcliNotSet")}
            </option>}
            {models[account.id]?.map(model => <option value={model} key={model}>{modelLabel(account.kind, model)}</option>)}
          </BusySelect>
          {/* A decision model has no options to check; what Refresh Models does and does not test reads in Help. */}
          <ActionButton icon="refresh" className="btn btn-outline btn-sm" helpText={t("routing.providerOptions")}
            label={refreshing === account.id ? t("settings.agentcliRefreshing") : t("settings.accountRefreshModels")}
            busy={saving || busy} onClick={() => void refresh(account.id)} />
        </div>
        {error && <p className="banner banner-error" role="alert">{error}</p>}
        <div className="agentcli-settings-form-actions">
          <ActionButton icon="save" label={t("common.save")} className="btn btn-outline btn-sm"
            busy={saving || busy} disabled={!modelDraft || modelDraft === account.model || !models[account.id]?.includes(modelDraft)} help="routing.saveModelHelp"
            onClick={() => void changeModel(account.id, modelDraft)} />
          <ActionButton icon="undo" label={t("settings.cancel")} className="btn btn-outline btn-sm" busy={saving || busy} help="settings.cancelModelHelp"
            onClick={() => { setEditing(null); setError(""); }} />
        </div>
      </div> : <div className="phx-window-actions agentcli-account-actions">
        <ActionButton icon="edit" label={t("settings.accountChangeModel")} className="btn btn-outline btn-sm" busy={saving || busy} help="routing.changeModelHelp"
          onClick={() => { setConfirmRemove(null); void editModel(account); }} />
        {/* Remove asks in place, as elsewhere in Phoenix; what it does reads in Help. */}
        {confirmRemove === account.id ? <span className="row-actions" ref={askBox}>
          <ActionButton icon="delete" label={t("settings.agentcliConfirmRemove", { name: accountName(account) })} className="btn btn-danger btn-sm"
            helpText={t("routing.removeAccountHelp", { name: accountName(account) })} busy={saving || busy} onClick={() => void remove(account.id)} />
          <ActionButton icon="undo" label={t("settings.cancel")} className="btn btn-outline btn-sm" busy={saving || busy} help="help.cancelAction"
            onClick={() => setConfirmRemove(null)} />
        </span> : <ActionButton icon="delete" label={t("settings.agentcliRemoveAccount", { name: accountName(account) })} className="btn btn-outline btn-sm phx-action-danger"
          helpText={t("routing.removeAccountHelp", { name: accountName(account) })} busy={saving || busy}
          onClick={() => { setConfirmRemove(account.id); setError(""); }} />}
      </div>}
      {/* How sure this account must be before it acts; the default suits every provider tested. */}
      <label className="agentcli-settings-tool-loading routing-threshold" htmlFor={`${uid}threshold-${account.id}`}
        data-guide="window.settingsProviders#decision-threshold" {...helpRow(t("routing.decisionThresholdHelp"))}>
        <span>{t("routing.decisionThreshold")}</span>
        <NumberField id={`${uid}threshold-${account.id}`} className="input" min={THRESHOLD_MIN} max={THRESHOLD_MAX} step={0.01}
          value={thresholdDraft} busy={saving || busy}
          onChange={event => setThresholdDraft(event.target.value)} onBlur={() => void saveThreshold(account)} />
      </label>
      {error && editing !== account.id && <p className="banner banner-error" role="alert">{error}</p>}
    </div>;
  };

  const hint = t("routing.providerHint", { voice: t("settings.voiceCard"), chat: t("settings.agentcliCard") });
  // What the Decision Provider is shows in Help over the section's empty space.
  return <div className="routing-provider-section" data-help-text={hint}>
    <span className="sr-only" id={`${uid}provider-hint`}>{hint}</span>
    <div className="routing-choices" role="radiogroup" aria-label={t("routing.decisionAccount")}
      aria-describedby={`${uid}provider-hint`}>
      <div className="routing-choice" {...helpRow(t("routing.noProviderHelp"))}>
        <label className="routing-account-choice">
          <BusyInput type="radio" name="decision-account" checked={!inUse}
            busy={saving || busy} disabled={!accountsLoaded}
            onChange={() => use("")} onClick={() => closeOptions()} />
          <span>{t("routing.noProvider")}</span>
        </label>
      </div>
      {accounts.map(account => <div className="routing-choice" key={account.id} ref={open === account.id ? openRow : undefined}>
        {/* Choosing an account opens its options under it; clicking it again closes them. */}
        <label className="routing-account-choice">
          <BusyInput type="radio" name="decision-account"
            checked={inUse && account.id === settings.decision_provider_id} busy={saving || busy} data-help="routing.accountChoiceHelp"
            onChange={() => use(account.id)}
            onClick={() => {
            if (open === account.id) closeOptions();
            else { closeOptions(); setOpen(account.id); setThresholdDraft((account.decision_threshold ?? THRESHOLD_DEFAULT).toFixed(2)); }
          }} />
          {/* Accounts at different providers can answer with different models, so each names its own. */}
          <span>{accountName(account)}{account.model && <>{" "}<span className="routing-account-model">{modelLabel(account.kind, account.model)}</span></>}</span>
        </label>
        {open === account.id && accountOptions(account)}
      </div>)}
    </div>
    {accountsLoaded && hasAccounts && !adding && <div className="phx-window-actions routing-add-account">
      <ActionButton icon="add" label={t("settings.addAccount")} className="btn btn-outline btn-sm" help="settings.addAccountHelp"
        busy={saving || busy} onClick={() => { closeOptions(); setAdding(true); }} />
    </div>}
    {accountsLoaded && (!hasAccounts || adding) && <ProviderAddForm providerTypes={types} disabled={saving || busy || editing !== null}
      idPrefix="decision-add" requireModel allowCapabilityProbe={false} compact
      completeLabel={t("settings.agentcliDone")}
      onClose={hasAccounts ? () => setAdding(false) : undefined}
      onCreated={async instance => {
        if (!mounted.current) return;
        // A new account is put in use, the first and every one added after it; choose another to switch back.
        if (selectionVersion.current === currentSelectionVersion) onChange("decision_provider_id", instance.id);
        setAdding(false);
        await load();
      }} />}
    {inUse && <details className="routing-disclosure">
      <summary data-guide="window.settingsProviders#decision-connection" data-help="routing.connectionSettingsHelp">{t("routing.connectionSettings")}</summary>
      <label className="agentcli-settings-field" htmlFor={`${uid}timeout`} {...helpRow(t("routing.timeoutHint"))}>
        <span>{t("routing.decisionBudget")}</span>
        <NumberField id={`${uid}timeout`} data-setting="routing-decision-timeout" className="input" min={250} max={30000} step={1}
          value={budget} busy={saving || busy} onChange={e => setBudget(e.target.value)}
          onBlur={() => {
            const value = Number(budget);
            if (Number.isInteger(value) && value >= 250 && value <= 30000) {
              if (value !== (settings.decision_timeout_ms ?? 5000)) onChange("decision_timeout_ms", value);
            } else setBudget(String(settings.decision_timeout_ms ?? 5000));
          }} />
      </label>
      <span className="sr-only">{t("routing.timeoutHint")}</span>
      <label className="agentcli-settings-field" htmlFor={`${uid}limit`} {...helpRow(t("routing.requestLimitHint"))}>
        <span>{t("routing.requestLimit")}</span>
        <NumberField id={`${uid}limit`} data-setting="routing-request-limit" className="input" min={0} max={100000} step={1}
          aria-describedby={`${uid}limit-hint`} value={requestLimit} busy={saving || busy}
          onChange={event => setRequestLimit(event.target.value)} onBlur={() => {
            const value = Number(requestLimit);
            if (requestLimit.trim() && Number.isInteger(value) && value >= 0 && value <= 100000) {
              if (value !== (settings.decision_request_budget ?? 0)) onChange("decision_request_budget", value);
            } else setRequestLimit(String(settings.decision_request_budget ?? 0));
          }} />
      </label>
      <span className="sr-only" id={`${uid}limit-hint`}>{t("routing.requestLimitHint")}</span>
    </details>}
    {error && open === null && <p className="banner banner-error" role="alert">{error}</p>}
  </div>;
}
