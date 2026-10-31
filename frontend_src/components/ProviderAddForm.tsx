import { BusyInput, BusySelect } from "./BusyButton";
import { ActionButton } from "../ui/ActionButton";
import type { IconName } from "../ui/Icon";
import { helpRow } from "../ui/SettingLabel";
import { useClickAway } from "../ui/useClickAway";
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { api, localizedApiMessage } from "../api";
import { t } from "../i18n";
import type {
  AgentCliInstance,
  AgentCliProviderField,
  AgentCliProviderKind,
  AgentCliProviderType,
} from "../types";

interface FormState {
  values: Record<string, string>;
  model: string;
  models: string[];
  validating: boolean;
  validated: boolean;
  saving: boolean;
  error: string | null;
  probe: boolean;
}

const EMPTY_FORM: FormState = {
  values: {}, model: "", models: [], validating: false, validated: false,
  saving: false, error: null, probe: true,
};

export interface ProviderAddFormProps {
  providerTypes: AgentCliProviderType[];
  disabled?: boolean;
  allowCapabilityProbe?: boolean;
  requireModel?: boolean;
  /** Offer "Automatic" (follow the provider's newest model) and make it the
   *  default. Decision accounts are qualified against exact models and never use it. */
  allowAutomatic?: boolean;
  idPrefix?: string;
  completeLabel: string;
  completeIcon?: IconName;
  /** The "Adding {name}" line over the form; none when left out. */
  addingLabelKey?: string;
  /**
   * The Settings layout (user, 2026-10-31): the buttons sit beside the last field and
   * beside the model, none in inverse colours, the provider's notes read in Help, and a
   * click anywhere else cancels. The wizard keeps the full form.
   */
  compact?: boolean;
  onActiveChange?: (active: boolean) => void;
  onCreated: (instance: AgentCliInstance, probe: boolean) => Promise<void> | void;
}

function providerLabel(provider: AgentCliProviderType): string {
  return provider.label_key ? t(provider.label_key) : provider.label;
}

function fieldLabel(field: AgentCliProviderField): string {
  return t(field.label_key);
}

function initialValues(provider: AgentCliProviderType): Record<string, string> {
  return Object.fromEntries(provider.fields.map((field) => [
    field.id,
    field.type === "choice" ? field.choices?.[0]?.value ?? "" : "",
  ]));
}

export function ProviderAddForm({
  providerTypes, disabled, completeLabel, completeIcon = "check", addingLabelKey, onActiveChange, onCreated,
  allowCapabilityProbe = true, requireModel = false, allowAutomatic = false, idPrefix = "agentcli-add", compact = false,
}: ProviderAddFormProps) {
  const [pickerValue, setPickerValue] = useState("");
  const [adding, setAdding] = useState<AgentCliProviderType | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const generation = useRef(0);
  useEffect(() => () => { generation.current++; }, []);
  const pickerRef = useRef<HTMLSelectElement>(null);
  const firstFieldRef = useRef<HTMLInputElement | HTMLSelectElement>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => { onActiveChange?.(adding !== null); }, [adding, onActiveChange]);
  useEffect(() => {
    if (adding) window.requestAnimationFrame(() => firstFieldRef.current?.focus());
  }, [adding]);

  const selectProvider = (kind: string) => {
    generation.current++;
    setPickerValue(kind);
    const provider = providerTypes.find((item) => item.kind === kind) ?? null;
    setAdding(provider);
    setForm(provider ? { ...EMPTY_FORM, values: initialValues(provider) } : EMPTY_FORM);
  };

  const reset = (focus = true) => {
    generation.current++;
    setPickerValue("");
    setAdding(null);
    setForm(EMPTY_FORM);
    if (focus) window.requestAnimationFrame(() => pickerRef.current?.focus());
  };

  const setValue = (id: string, value: string) => {
    generation.current++;
    setForm((current) => ({
      ...current,
      values: { ...current.values, [id]: value },
      validated: false,
      models: [],
      model: "",
      error: null,
    }));
  };

  const validate = async () => {
    if (!adding) return;
    // The picker is a launch control, not persistent form state. Reset it on
    // every validation attempt while the open form retains the selected type.
    setPickerValue("");
    const missing = adding.fields.find(
      (field) => field.required && !(form.values[field.id] ?? "").trim(),
    );
    if (missing) {
      setForm((current) => ({
        ...current,
        error: t("settings.agentcliFieldRequired", { field: fieldLabel(missing) }),
      }));
      return;
    }
    const currentGeneration = ++generation.current;
    setForm((current) => ({ ...current, validating: true, error: null }));
    try {
      const payload = Object.fromEntries(
        Object.entries(form.values).map(([key, value]) => [key, value.trim()]),
      );
      const response = await api.probeAgentCliProvider(
        adding.kind as AgentCliProviderKind,
        payload,
      );
      if (generation.current !== currentGeneration) return;
      if (!response || !response.ok || !Array.isArray(response.models)) {
        const error = response?.error
          ? localizedApiMessage(response.error, response.message_key, response.message_params)
          : t("settings.agentcliConnectionFailed");
        setForm((current) => ({
          ...current, validating: false, validated: false, models: [], error,
        }));
        return;
      }
      setForm((current) => ({
        ...current,
        validating: false,
        validated: true,
        error: null,
        models: response.models,
        model: response.models.includes(current.model)
          ? current.model : (allowAutomatic ? "" : response.models[0] ?? ""),
      }));
    } catch (error: unknown) {
      if (generation.current !== currentGeneration) return;
      setForm((current) => ({
        ...current,
        validating: false,
        validated: false,
        models: [],
        error: error instanceof Error ? error.message : t("settings.agentcliConnectionFailed"),
      }));
    }
  };

  useClickAway(rootRef, () => reset(false), compact && adding !== null && !form.validating && !form.saving, undefined, "click");

  // An empty model is a valid choice only where "Automatic" is offered.
  const modelMissing = requireModel && !(allowAutomatic && form.model === "") && !form.models.includes(form.model);

  const complete = async () => {
    if (!adding || !form.validated || modelMissing) return;
    const currentGeneration = ++generation.current;
    setForm((current) => ({ ...current, saving: true, error: null }));
    try {
      const payload = {
        ...Object.fromEntries(
          Object.entries(form.values).map(([key, value]) => [key, value.trim()]),
        ),
        ...(form.model ? { model: form.model } : {}),
      };
      const response = await api.createAgentCliProvider(adding.kind, payload);
      if (generation.current !== currentGeneration) return;
      await onCreated(response.instance, allowCapabilityProbe && form.probe && Boolean(form.model || response.instance.model));
      if (generation.current === currentGeneration) reset(false);
    } catch (error: unknown) {
      if (generation.current !== currentGeneration) return;
      setForm((current) => ({
        ...current,
        saving: false,
        error: error instanceof Error ? error.message : t("settings.agentcliSaveFailed"),
      }));
    }
  };


  const actionClass = compact ? "btn btn-outline btn-sm" : "btn btn-primary btn-sm";
  const cancel = <ActionButton icon="undo" label={t("settings.cancel")} className="btn btn-outline btn-sm" busy={form.saving} onClick={() => reset()} />;
  const validateActions = (
    <div className="agentcli-settings-form-actions">
      <ActionButton icon="probe" label={t("settings.agentcliValidate")} className={actionClass}
        busy={form.validating || form.saving} onClick={() => void validate()} />
      {cancel}
    </div>
  );
  const completeActions = (
    <div className="agentcli-settings-form-actions">
      <ActionButton icon={completeIcon} label={completeLabel} className={actionClass}
        busy={form.saving || Boolean(disabled)} disabled={modelMissing} onClick={() => void complete()} />
      {cancel}
    </div>
  );
  // A provider's note on getting its key right; in Settings it reads in Help.
  const note = adding?.kind === "tencent" ? t("settings.agentcliTencentHint") : adding?.kind === "mimo" ? t("settings.agentcliMiMoHint") : null;
  /** In Settings, a row's buttons sit beside it rather than under it. */
  const inline = (row: ReactNode, actions: ReactNode) => <div className="agentcli-add-inline">{row}{actions}</div>;
  const probeLabel = compact ? t("settings.agentcliVerifyOptions") : t("settings.agentcliProbeOnAdd");
  const probeToggle = allowCapabilityProbe && (
    <div className="agentcli-settings-probe-opt" {...(compact ? helpRow(t("settings.agentcliProbeOnAdd")) : {})}>
      <label className="toggle-switch" htmlFor="agentcli-add-probe-on-save">
        <BusyInput
          id="agentcli-add-probe-on-save"
          busy={form.saving}
          type="checkbox"
          checked={form.probe}
          onChange={(event) => setForm((current) => ({ ...current, probe: event.target.checked }))}
        />
        <span className="toggle-switch-track" />
      </label>
      <label className="agentcli-settings-probe-copy" htmlFor="agentcli-add-probe-on-save">
        {probeLabel}
        {compact && <span className="sr-only">{t("settings.agentcliProbeOnAdd")}</span>}
      </label>
    </div>
  );
  const modelRow = (
    <label className="agentcli-settings-model-row" htmlFor={`${idPrefix}-model`}>
      <span>{t("settings.agentcliSelectModel")}</span>
      <BusySelect
        id={`${idPrefix}-model`}
        className="input input-auto"
        value={form.model}
        busy={form.saving} disabled={!form.models.length && !allowAutomatic}
        onChange={(event) => setForm((current) => ({ ...current, model: event.target.value }))}
      >
        {allowAutomatic && <option value="">{t("settings.agentcliModelAuto")}</option>}
        {form.models.length
          ? form.models.map((model) => <option key={model} value={model}>{model}</option>)
          : !allowAutomatic && <option value="">{t("settings.agentcliNoModels")}</option>}
      </BusySelect>
    </label>
  );

  return (
    <div ref={rootRef} className="agentcli-add">
      <div className="agentcli-add-row">
        <BusySelect
          ref={pickerRef}
          className="input input-auto"
          value={pickerValue}
          busy={Boolean(disabled)} disabled={adding !== null || providerTypes.length === 0}
          onChange={(event) => selectProvider(event.target.value)}
          aria-label={t("settings.agentcliProviderType")}
        >
          <option value="" disabled>{t("settings.agentcliAddProvider")}</option>
          {providerTypes.map((provider) => (
            <option key={provider.kind} value={provider.kind}>{providerLabel(provider)}</option>
          ))}
        </BusySelect>
      </div>

      {adding && (
        <div className={`agentcli-settings-form agentcli-add-form${compact ? " is-compact" : ""}`} {...(compact ? helpRow(note) : {})}>
          {addingLabelKey && (
            <div className="agentcli-settings-hint">
              {t(addingLabelKey, { name: providerLabel(adding), label: providerLabel(adding) })}
            </div>
          )}
          {note && (compact ? <span className="sr-only">{note}</span> : <div className="agentcli-settings-hint">{note}</div>)}
          {adding.fields.map((field, index) => {
            const id = `${idPrefix}-${adding.kind}-${field.id}`;
            const row = (
              <label className="agentcli-settings-field" htmlFor={id} key={field.id}>
                <span>{fieldLabel(field)}</span>
                {field.type === "choice" ? (
                  <BusySelect
                    id={id}
                    className="input"
                    ref={index === 0 ? (element) => { firstFieldRef.current = element; } : undefined}
                    value={form.values[field.id] ?? ""}
                    busy={form.validating || form.saving}
                    aria-describedby={form.error ? `${idPrefix}-error` : undefined}
                    aria-invalid={form.error ? true : undefined}
                    onChange={(event) => setValue(field.id, event.target.value)}
                  >
                    {(field.choices ?? []).map((choice) => (
                      <option value={choice.value} key={choice.value}>
                        {choice.label_key ? t(choice.label_key) : choice.label}
                      </option>
                    ))}
                  </BusySelect>
                ) : (
                  <BusyInput
                    id={id}
                    ref={index === 0 ? (element) => { firstFieldRef.current = element; } : undefined}
                    type={field.type === "secret" ? "password" : "url"}
                    placeholder={field.placeholder ?? fieldLabel(field)}
                    value={form.values[field.id] ?? ""}
                    busy={form.validating || form.saving}
                    aria-describedby={form.error ? `${idPrefix}-error` : undefined}
                    aria-invalid={form.error ? true : undefined}
                    onChange={(event) => setValue(field.id, event.target.value)}
                  />
                )}
              </label>
            );
            // Validate and Cancel beside the last field, the API key on most providers.
            return compact && !form.validated && index === adding.fields.length - 1
              ? <Fragment key={field.id}>{inline(row, validateActions)}</Fragment>
              : row;
          })}
          {form.validating && (
            <div className="agentcli-settings-hint" role="status">
              {t("settings.agentcliValidating")}
            </div>
          )}
          <span className="sr-only" role="status" aria-live="polite">
            {form.validated ? t("settings.agentcliValidated") : ""}
          </span>
          {form.error && (
            <div id={`${idPrefix}-error`} className="banner banner-error" role="alert">
              {form.error}
            </div>
          )}
          {compact ? (
            form.validated && <>
              {inline(modelRow, completeActions)}
              {probeToggle}
            </>
          ) : <>
            {form.validated && modelRow}
            {form.validated && (
              <div className="agentcli-settings-model-actions">
                {probeToggle}
                {completeActions}
              </div>
            )}
            {!form.validated && validateActions}
          </>}
        </div>
      )}
    </div>
  );
}
