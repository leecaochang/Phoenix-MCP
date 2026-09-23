import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const selectionVersion = useRef(0);
  const currentSelectionVersion = selectionVersion.current;
  const mounted = useRef(true);
  const [budget, setBudget] = useState(String(settings.decision_timeout_ms ?? 5000));
  useEffect(() => { setBudget(String(settings.decision_timeout_ms ?? 5000)); }, [settings.decision_timeout_ms]);

  async function load() {
    const current = ++generation.current;
    try {
      const response = await api.getDecisionProviders();
      if (!mounted.current || current !== generation.current) return;
      setAccounts(response.instances);
      setTypes(response.provider_types);
      const catalogs = await Promise.allSettled(response.instances.map(async account =>
        [account.id, (await api.getAgentCliModels(account.id)).models] as const));
      if (!mounted.current || current !== generation.current) return;
      setModels(Object.fromEntries(catalogs.flatMap(result => result.status === "fulfilled" ? [result.value] : [])));
      if (catalogs.some(result => result.status === "rejected")) setError(t("routing.failed"));
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

  async function changeModel(id: string, model: string) {
    setBusy(true);
    setError("");
    generation.current++;
    try {
      await api.setAgentCliProviderModel(id, model);
      if (mounted.current) await load();
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : t("routing.failed"));
    } finally { if (mounted.current) setBusy(false); }
  }
  async function remove(id: string) {
    setBusy(true);
    setError("");
    generation.current++;
    try {
      await api.deleteAgentCliProvider(id);
      if (!mounted.current) return;
      window.dispatchEvent(new Event("phx-settings-refresh"));
      await load();
    } catch { if (mounted.current) setError(t("routing.failed")); }
    finally { if (mounted.current) setBusy(false); }
  }

  return <div className="routing-provider-section">
    <h4>{t("routing.providerSection")}</h4>
    <p className="agentcli-settings-hint" id="routing-provider-hint">{t("routing.providerHint")}</p>
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
        <select className="input input-auto agentcli-settings-model" value={account.model ?? ""}
          aria-label={t("routing.accountModel", { name: account.name })}
          disabled={saving || busy || !models[account.id]?.length}
          onChange={event => void changeModel(account.id, event.target.value)}>
          {!models[account.id]?.includes(account.model ?? "") && <option value={account.model ?? ""} disabled>
            {models[account.id] ? t("settings.agentcliModelUnavailable", { model: account.model ?? "" }) : account.model}
          </option>}
          {models[account.id]?.map(model => <option value={model} key={model}>{model}</option>)}
        </select>
        </div>
        <div className="agentcli-account-actions">
          <button type="button" className="btn btn-sm btn-text" disabled={saving || busy}
            aria-label={t("routing.removeAccount", { name: account.name })}
            onClick={() => void remove(account.id)}>{t("tokens.delete")}</button>
        </div>
      </div>)}
    </div>
    <ProviderAddForm providerTypes={types} disabled={saving || busy}
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
    </details>}
    {error && <p className="banner banner-error" role="alert">{error}</p>}
  </div>;
}
