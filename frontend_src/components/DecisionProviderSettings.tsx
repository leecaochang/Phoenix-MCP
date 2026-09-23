import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import type { AgentCliInstance, AgentCliProviderType, GlobalSettings } from "../types";

export function DecisionProviderSettings({ settings, onChange, saving }: {
  settings: GlobalSettings;
  onChange: (key: keyof GlobalSettings, value: string | number | boolean) => void;
  saving: boolean;
}) {
  const [accounts, setAccounts] = useState<AgentCliInstance[]>([]);
  const [types, setTypes] = useState<AgentCliProviderType[]>([]);
  const [kind, setKind] = useState("typesafe");
  const [values, setValues] = useState<Record<string, string>>({});
  const [model, setModel] = useState("jev-latest");
  const [budget, setBudget] = useState(String(settings.decision_timeout_ms ?? 5000));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const mounted = useRef(true);
  const selected = types.find(item => item.kind === kind);

  async function load() {
    const current = ++generation.current;
    try {
      const response = await api.getDecisionProviders();
      if (!mounted.current || current !== generation.current) return;
      setAccounts(response.instances);
      setTypes(response.provider_types);
    } catch {
      if (mounted.current && current === generation.current) setError(t("routing.failed"));
    }
  }
  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; generation.current++; };
  }, []);
  useEffect(() => { setBudget(String(settings.decision_timeout_ms ?? 5000)); }, [settings.decision_timeout_ms]);

  async function save() {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.createAgentCliProvider(selected.kind, { ...values, model: model.trim() });
      if (!mounted.current) return;
      setValues({});
      onChange("decision_provider_id", response.instance.id);
      await load();
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : t("routing.failed"));
    } finally { if (mounted.current) setBusy(false); }
  }
  async function remove() {
    if (!settings.decision_provider_id) return;
    setBusy(true);
    setError("");
    try {
      await api.deleteAgentCliProvider(settings.decision_provider_id);
      if (!mounted.current) return;
      window.dispatchEvent(new Event("phx-settings-refresh"));
      await load();
    } catch { if (mounted.current) setError(t("routing.failed")); }
    finally { if (mounted.current) setBusy(false); }
  }

  return <div>
    <h4>{t("routing.decisionTitle")}</h4>
    <p className="agentcli-settings-hint">{t("routing.decisionHint")}</p>
    <div className="toggle-row toggle-row-stacked-control">
      <label htmlFor="decision-account">{t("routing.decisionAccount")}</label>
      <select id="decision-account" className="input input-auto" disabled={saving || busy}
        value={settings.decision_provider_id ?? ""} onChange={e => onChange("decision_provider_id", e.target.value)}>
        <option value="">{t("common.none")}</option>
        {accounts.map(account => <option key={account.id} value={account.id}>{account.name} ({account.model})</option>)}
      </select>
      {settings.decision_provider_id && <button type="button" className="btn btn-text" disabled={saving || busy}
        onClick={() => void remove()}>{t("routing.removeDecision")}</button>}
    </div>
    <details>
      <summary>{t("routing.addDecision")}</summary>
      <form onSubmit={e => { e.preventDefault(); void save(); }}>
        <div className="toggle-row toggle-row-stacked-control">
          <label htmlFor="decision-kind">{t("routing.decisionService")}</label>
          <select id="decision-kind" className="input input-auto" value={kind} disabled={busy || saving}
            onChange={e => { setKind(e.target.value); setValues({}); setModel(e.target.value === "typesafe" ? "jev-latest" : ""); setError(""); }}>
            {types.map(provider => <option key={provider.kind} value={provider.kind}>{provider.label}</option>)}
          </select>
        </div>
        {selected?.fields.map(field => <div key={field.id} className="toggle-row toggle-row-stacked-control">
          <label htmlFor={`decision-${field.id}`}>{t(field.label_key)}</label>
          <input id={`decision-${field.id}`} className="input" type={field.type === "secret" ? "password" : "url"}
            autoComplete="off" required={field.required} placeholder={field.placeholder}
            value={values[field.id] ?? ""} disabled={busy || saving}
            onChange={e => setValues(current => ({ ...current, [field.id]: e.target.value }))} />
        </div>)}
        <div className="toggle-row toggle-row-stacked-control">
          <label htmlFor="decision-model">{t("settings.model")}</label>
          <input id="decision-model" className="input" value={model} required maxLength={200}
            disabled={busy || saving} onChange={e => setModel(e.target.value)} />
        </div>
        <button className="btn btn-secondary" type="submit" disabled={!selected || !model.trim() || busy || saving}>
          {busy ? t("common.loading") : t("routing.saveDecision")}
        </button>
      </form>
    </details>
    <div className="toggle-row">
      <label htmlFor="decision-shadow">{t("routing.decisionEnable")}</label>
      <input id="decision-shadow" type="checkbox" checked={settings.decision_shadow_enabled ?? false}
        disabled={!settings.decision_provider_id || saving || busy || settings.voice_agent_routing_mode === "hybrid"}
        onChange={e => onChange("decision_shadow_enabled", e.target.checked)} />
    </div>
    <p className="agentcli-settings-hint">{t("routing.decisionPrivacy")}</p>
    {settings.voice_agent_routing_mode === "hybrid" && <p className="agentcli-settings-hint">{t("routing.hybridShadowHint")}</p>}
    <div className="toggle-row toggle-row-stacked-control">
      <label htmlFor="decision-budget">{t("routing.decisionBudget")}</label>
      <input id="decision-budget" className="input input-auto" type="number" min={250} max={30000} step={1}
        value={budget} disabled={saving} onChange={e => setBudget(e.target.value)} onBlur={() => {
          const value = Number(budget);
          if (Number.isInteger(value) && value >= 250 && value <= 30000) {
            if (value !== settings.decision_timeout_ms) onChange("decision_timeout_ms", value);
          } else setBudget(String(settings.decision_timeout_ms ?? 5000));
        }} />
    </div>
    <p className="agentcli-settings-hint">{t("routing.decisionBudgetHint")}</p>
    {error && <p role="alert">{error}</p>}
  </div>;
}
