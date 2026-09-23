import { useEffect, useRef, useState } from "react";
import type { CommandRoutePreview, GlobalSettings, TokenRecord } from "../types";
import { api } from "../api";
import { localeNumber, t } from "../i18n";

// Extend the existing HA settings card style: plain labels, native controls,
// and an inert result. The experiment's scope and effect stay beside its mode.
export function CommandRoutingSettings({ settings, onChange, saving }: {
  settings: GlobalSettings;
  onChange: (key: keyof GlobalSettings, value: string | number | boolean) => void;
  saving: boolean;
}) {
  const [tokens, setTokens] = useState<TokenRecord[]>([]);
  const [tokenId, setTokenId] = useState(settings.voice_agent_token_id ?? "");
  const [sentence, setSentence] = useState("");
  const [budget, setBudget] = useState(String(settings.command_routing_timeout_ms ?? 500));
  const [result, setResult] = useState<CommandRoutePreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);

  useEffect(() => {
    let active = true;
    api.listTokens().then(value => { if (active) setTokens(value); })
      .catch(() => { if (active) setError(t("routing.failed")); });
    return () => { active = false; generation.current++; };
  }, []);
  useEffect(() => {
    setBudget(String(settings.command_routing_timeout_ms ?? 500));
    generation.current++;
    setResult(null);
    setBusy(false);
  }, [settings]);

  function invalidate() {
    generation.current++;
    setResult(null);
    setError("");
    setBusy(false);
  }

  async function preview() {
    const current = ++generation.current;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const next = await api.previewCommandRoute(tokenId, sentence);
      if (generation.current === current) setResult(next);
    } catch {
      if (generation.current === current) setError(t("routing.failed"));
    } finally {
      if (generation.current === current) setBusy(false);
    }
  }

  return <section className="card" aria-labelledby="command-routing-title">
    <h3 className="card-header" id="command-routing-title">{t("routing.title")}</h3>
    <p className="agentcli-settings-hint">{t("routing.hint")}</p>
    <div className="toggle-row toggle-row-stacked-control">
      <label htmlFor="routing-mode">{t("routing.mode")}</label>
      <select id="routing-mode" className="input input-auto" disabled={saving}
        value={settings.voice_agent_routing_mode ?? "off"}
        onChange={e => { invalidate(); onChange("voice_agent_routing_mode", e.target.value); }}>
        <option value="off">{t("routing.offMode")}</option>
        <option value="local_shadow">{t("routing.observe")}</option>
        <option value="local">{t("routing.localMode")}</option>
      </select>
    </div>
    <p className="agentcli-settings-hint">{t("routing.localHint")}</p>
    <div className="toggle-row toggle-row-stacked-control">
      <label htmlFor="routing-budget">{t("routing.budget")}</label>
      <input id="routing-budget" className="input input-auto" type="number" min={50} max={5000} step={1}
        disabled={saving} value={budget} onChange={e => setBudget(e.target.value)}
        onBlur={() => {
          const value = Number(budget);
          if (Number.isInteger(value) && value >= 50 && value <= 5000) {
            if (value !== settings.command_routing_timeout_ms) onChange("command_routing_timeout_ms", value);
          } else setBudget(String(settings.command_routing_timeout_ms ?? 500));
        }} />
    </div>
    <p className="agentcli-settings-hint">{t("routing.localBudgetHint")}</p>
    <form onSubmit={e => { e.preventDefault(); void preview(); }}>
      <div className="toggle-row toggle-row-stacked-control">
        <label htmlFor="routing-token">{t("routing.token")}</label>
        <select id="routing-token" className="input input-auto" required value={tokenId}
          onChange={e => { invalidate(); setTokenId(e.target.value); }}>
          <option value="">{t("common.none")}</option>
          {tokens.filter(token => !token.revoked).map(token => <option key={token.id} value={token.id}>{token.name}</option>)}
        </select>
      </div>
      <label htmlFor="routing-sentence">{t("routing.command")}</label>
      <textarea id="routing-sentence" className="input" rows={2} maxLength={512} required
        value={sentence} onChange={e => { invalidate(); setSentence(e.target.value); }} />
      <div className="toggle-row">
        <button className="btn btn-primary" type="submit" disabled={busy || !tokenId || !sentence.trim() || saving}>
          {busy ? t("common.loading") : t("routing.test")}
        </button>
      </div>
    </form>
    {error && <p role="alert">{error}</p>}
    {result && <div role="status">
      <p><strong>{t(result.route === "local_candidate" ? "routing.candidate" : "routing.fallback")}</strong></p>
      <p className="agentcli-settings-hint">{t(result.route === "local_candidate"
        ? "routing.resultHint"
        : result.reason === "no_match" ? "routing.noMatchHint" : "routing.fallbackHint")}</p>
      <p>{t("routing.elapsed", { ms: localeNumber(result.elapsed_ms) })}</p>
      {result.proposal && <ul>{result.proposal.entity_ids.map(id => <li key={id}><code>{id}</code></li>)}</ul>}
      <p>{t("routing.observations", { total: localeNumber(result.voice_observations.observations), candidates: localeNumber(result.voice_observations.candidates) })}</p>
      <details><summary>{t("routing.details")}</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ reason: result.reason, proposal: result.proposal }, null, 2)}</pre></details>
    </div>}
  </section>;
}
