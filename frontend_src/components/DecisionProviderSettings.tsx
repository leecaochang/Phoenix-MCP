import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import { Modal } from "./Modal";
import { IconButton, EditIcon, RefreshIcon, ProbeIcon, TrashIcon } from "./ProviderAccountControls";
import { ProviderAddForm } from "./ProviderAddForm";
import type { AgentCliInstance, AgentCliProviderType, GlobalSettings } from "../types";

export function DecisionProviderSettings({ settings, onChange, saving }: {
  settings: GlobalSettings;
  onChange: (key: keyof GlobalSettings, value: string | number | boolean) => void;
  saving: boolean;
}) {
  const [accounts, setAccounts] = useState<AgentCliInstance[]>([]);
  const [types, setTypes] = useState<AgentCliProviderType[]>([]);
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [modelDraft, setModelDraft] = useState("");
  const [refreshing, setRefreshing] = useState<string | null>(null);
  const [refreshed, setRefreshed] = useState<Record<string, { models: number; declared: boolean }>>({});
  const [options, setOptions] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<AgentCliInstance | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const selectionVersion = useRef(0);
  const currentSelectionVersion = selectionVersion.current;
  const mounted = useRef(true);
  const [budget, setBudget] = useState(String(settings.decision_timeout_ms ?? 5000));
  useEffect(() => { setBudget(String(settings.decision_timeout_ms ?? 5000)); }, [settings.decision_timeout_ms]);
  const [requestLimit, setRequestLimit] = useState(String(settings.decision_request_budget ?? 0));
  useEffect(() => { setRequestLimit(String(settings.decision_request_budget ?? 0)); }, [settings.decision_request_budget]);

  async function load() {
    const current = ++generation.current;
    try {
      const response = await api.getDecisionProviders();
      if (!mounted.current || current !== generation.current) return;
      setAccounts(response.instances);
      setTypes(response.provider_types);
      setError("");
    } catch {
      if (mounted.current && current === generation.current) setError(t("routing.failed"));
    }
  }
  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; generation.current++; };
  }, []);

  function selectAccount(id: string) {
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
  async function remove(id: string) {
    setBusy(true);
    setError("");
    generation.current++;
    try {
      await api.deleteAgentCliProvider(id);
      if (!mounted.current) return;
      setConfirmRemove(null);
      window.dispatchEvent(new Event("phx-settings-refresh"));
      await load();
    } catch { if (mounted.current) setError(t("routing.failed")); }
    finally { if (mounted.current) setBusy(false); }
  }

  return <div className="routing-provider-section">
    <p className="agentcli-settings-hint" id="routing-provider-hint">{t("routing.providerHint", { voice: t("settings.voiceCard"), chat: t("settings.agentcliCard") })}</p>
    <div className="agentcli-settings-providers" role="radiogroup" aria-label={t("routing.decisionAccount")}
      aria-describedby="routing-provider-hint">
      <label className="routing-account-choice">
        <input type="radio" name="decision-account" checked={!settings.decision_provider_id}
          disabled={saving || busy} onChange={() => selectAccount("")} />
        {t("routing.localOnly")}
      </label>
      {accounts.map(account => <div className="agentcli-settings-provider" key={account.id}>
        <div className="agentcli-settings-provider-main">
        <label className="routing-account-choice">
          <input type="radio" name="decision-account" checked={account.id === settings.decision_provider_id}
            disabled={saving || busy} onChange={() => selectAccount(account.id)} />
          <span className="agentcli-settings-provider-head">{account.name}</span>
        </label>
        {editing === account.id ? <div className="agentcli-settings-model-edit">
          <select className="input input-auto" value={modelDraft} autoFocus
            aria-label={t("routing.accountModel", { name: account.name })}
            disabled={saving || busy || !models[account.id]?.length}
            onChange={event => setModelDraft(event.target.value)}>
            {!models[account.id]?.includes(account.model ?? "") && <option value={account.model ?? ""} disabled>
              {models[account.id]?.length ? t("settings.agentcliModelUnavailable", { model: account.model ?? "" }) : account.model || t("settings.agentcliNotSet")}
            </option>}
            {models[account.id]?.map(model => <option value={model} key={model}>{model}</option>)}
          </select>
          <div className="agentcli-settings-model-actions">
            <div className="agentcli-settings-form-actions">
              <button type="button" className="btn btn-primary btn-sm"
                disabled={saving || busy || !modelDraft || modelDraft === account.model || !models[account.id]?.includes(modelDraft)}
                onClick={() => void changeModel(account.id, modelDraft)}>{t("common.save")}</button>
              <button type="button" className="btn btn-text btn-sm" disabled={saving || busy}
                onClick={() => { setEditing(null); setError(""); }}>{t("settings.cancel")}</button>
            </div>
          </div>
        </div> : <span className="agentcli-settings-model">
          {t("settings.agentcliDefaultModel", { model: account.model || t("settings.agentcliNotSet") })}
        </span>}
        {models[account.id]?.length > 0 && account.model && !models[account.id].includes(account.model) &&
          <p className="agentcli-settings-hint">{t("settings.agentcliModelUnavailable", { model: account.model })}</p>}
        {refreshed[account.id] && <p className="agentcli-settings-hint" role="status">
          {t(refreshed[account.id].declared ? "settings.agentcliRefreshed" : "settings.agentcliRefreshedNoCaps",
            { models: refreshed[account.id].models })}
        </p>}
        {options === account.id && <p className="agentcli-settings-hint" role="status">{t("routing.providerOptions")}</p>}
        </div>
        {editing !== account.id && <div className="agentcli-account-actions">
          <IconButton label={t("settings.agentcliChangeModel")} disabled={saving || busy}
            onClick={() => void editModel(account)}><EditIcon /></IconButton>
          <IconButton label={t("settings.agentcliRefresh")} busyLabel={t("settings.agentcliRefreshing")}
            busy={refreshing === account.id} disabled={saving || busy} onClick={() => void refresh(account.id)}><RefreshIcon /></IconButton>
          <IconButton label={t("settings.agentcliProbeCaps")} expanded={options === account.id} disabled={saving || busy}
            onClick={() => setOptions(options === account.id ? null : account.id)}><ProbeIcon /></IconButton>
          <IconButton label={t("settings.agentcliRemoveAccount", { name: account.name })} danger disabled={saving || busy}
            onClick={() => { setConfirmRemove(account); setError(""); }}><TrashIcon /></IconButton>
        </div>}
      </div>)}
    </div>
    {confirmRemove && <Modal titleId="decision-remove-title" onClose={busy ? undefined : () => setConfirmRemove(null)}>
      <h3 className="modal-title" id="decision-remove-title">{t("settings.agentcliRemoveTitle")}</h3>
      <p>{t("routing.removeAccount", { name: confirmRemove.name })}?</p>
      {error && <p className="banner banner-error" role="alert">{error}</p>}
      <div className="modal-actions">
        <button type="button" className="btn btn-danger btn-sm" disabled={saving || busy}
          onClick={() => void remove(confirmRemove.id)}>{t("settings.remove")}</button>
        <button type="button" className="btn btn-text" disabled={busy}
          onClick={() => setConfirmRemove(null)}>{t("settings.cancel")}</button>
      </div>
    </Modal>}
    <ProviderAddForm providerTypes={types} disabled={saving || busy || editing !== null}
      idPrefix="decision-add" requireModel allowCapabilityProbe={false}
      completeLabel={t("settings.agentcliDone")} addingLabelKey="settings.agentcliAdding"
      connectionHint={t("routing.connectionHint")}
      onCreated={async instance => {
        if (!mounted.current) return;
        if (selectionVersion.current === currentSelectionVersion) selectAccount(instance.id);
        await load();
      }} />
    {settings.decision_provider_id && <details className="routing-disclosure">
      <summary>{t("routing.connectionSettings")}</summary>
      <label className="agentcli-settings-field" htmlFor="routing-decision-timeout">
        <span>{t("routing.decisionBudget")}</span>
        <input id="routing-decision-timeout" className="input" type="number" min={250} max={30000} step={1}
          value={budget} disabled={saving || busy} onChange={e => setBudget(e.target.value)}
          onBlur={() => {
            const value = Number(budget);
            if (Number.isInteger(value) && value >= 250 && value <= 30000) {
              if (value !== (settings.decision_timeout_ms ?? 5000)) onChange("decision_timeout_ms", value);
            } else setBudget(String(settings.decision_timeout_ms ?? 5000));
          }} />
      </label>
      <p className="agentcli-settings-hint">{t("routing.timeoutHint")}</p>
      <label className="agentcli-settings-field" htmlFor="routing-request-limit">
        <span>{t("routing.requestLimit")}</span>
        <input id="routing-request-limit" className="input" type="number" min={0} max={100000} step={1}
          aria-describedby="routing-request-limit-hint" value={requestLimit} disabled={saving || busy}
          onChange={event => setRequestLimit(event.target.value)} onBlur={() => {
            const value = Number(requestLimit);
            if (requestLimit.trim() && Number.isInteger(value) && value >= 0 && value <= 100000) {
              if (value !== (settings.decision_request_budget ?? 0)) onChange("decision_request_budget", value);
            } else setRequestLimit(String(settings.decision_request_budget ?? 0));
          }} />
      </label>
      <p className="agentcli-settings-hint" id="routing-request-limit-hint">{t("routing.requestLimitHint")}</p>
    </details>}
    {error && !confirmRemove && <p className="banner banner-error" role="alert">{error}</p>}
  </div>;
}
