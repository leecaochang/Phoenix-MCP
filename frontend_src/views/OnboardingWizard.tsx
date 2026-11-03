/**
 * The setup wizard. Installing Phoenix is shown as its completed first step,
 * and it opens on the second: Agent Chat or an external app. Persona, the
 * access key and one light's access follow, then the chosen path's own steps.
 * Each step reports itself to the shell, so the Guide shows its topic,
 * pinned beside it. Its buttons always show an icon and text.
 */

import { BusySelect } from "../components/BusyButton";
import { useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { AgentCliInstance, AgentCliProviderType, CreateAccessKeyBody, EntityTree as EntityTreeData, Persona, PermissionTree } from "../types";
import { api, ApiError } from "../api";
import { PersonaPicker } from "../components/PersonaPicker";
import { EntityTree } from "../components/EntityTree";
import { CopyButton } from "../components/AccessKeyCreate";
import { ConnectInstructions } from "../components/ConnectInstructions";
import { ProviderAddForm } from "../components/ProviderAddForm";
import { PERSONA_CAP_DEFAULTS } from "../personas";
import { buildTestPrompt, firstGreenTarget, freeAccessKeyName } from "../wizard_helpers";
import { patchDurable as patchAgentCliDurable } from "../utils/agentcli_state";
import { ErrorMsg } from "../components/common";
import { localeNumber, t } from "../i18n";
import { tRich } from "../i18n/rich";
import { WizardGuideContext } from "../shell/guide";
import { Icon } from "../ui/Icon";
import { ActionButton, DisplayModeContext } from "../ui/ActionButton";

const NAME_REGEX = /^[A-Za-z0-9_\-]{3,32}$/;


// The choice comes first and sets the branch: "chat" runs the agent inside Home
// Assistant via Agent Chat; "app" connects an external MCP app (the
// Connect/Test flow). Persona, access key and access are shared.
type Branch = "app" | "chat";
type Step = "choose" | "persona" | "accessKey" | "access" | "provider" | "try" | "connect" | "test" | "done";
const SHARED_STEPS: Step[] = ["choose", "persona", "accessKey", "access"];
const BRANCH_STEPS: Record<Branch, Step[]> = { chat: ["provider", "try"], app: ["connect", "test", "done"] };
/** Every step, each with a Guide topic `wizard.<step>`. */
export const WIZARD_STEPS: readonly Step[] = [...SHARED_STEPS, ...BRANCH_STEPS.chat, ...BRANCH_STEPS.app];
const STEP_LABEL_KEYS: Record<Step, string> = {
  choose: "wizard.stepChoose", persona: "wizard.stepPersona", accessKey: "wizard.stepAccessKey", access: "wizard.stepAccess",
  provider: "wizard.stepProvider", try: "wizard.stepTry", connect: "wizard.stepConnect", test: "wizard.stepTest", done: "wizard.stepDone",
};

type TtlUnit = "minutes" | "hours" | "days" | "weeks" | "none";

function addMinutes(m: number): string {
  return new Date(Date.now() + m * 60000).toISOString();
}

interface Props {
  onCancel: () => void;
  onFinish: (accessKeyId: string) => void;
}

export function OnboardingWizard({ onCancel, onFinish }: Props) {
  const [step, setStep] = useState<Step>("choose");
  const [branch, setBranch] = useState<Branch | null>(null);
  const [persona, setPersona] = useState<Persona | null>("new_user");
  const [name, setName] = useState(() => t("accessKeys.namePlaceholder"));
  const [ttlUnit, setTtlUnit] = useState<TtlUnit>("none");
  const [ttlValue, setTtlValue] = useState("24");
  // The suggested name moves on to a free one when an earlier run used it, until the person types their own.
  const nameTyped = useRef(false);
  useEffect(() => {
    let live = true;
    api.listAccessKeys()
      .then((keys) => { if (live && !nameTyped.current) setName((current) => freeAccessKeyName(current, keys.map((k) => k.name))); })
      .catch(() => { /* the server still refuses a taken name when the key is created */ });
    return () => { live = false; };
  }, []);
  // An expiry needs a whole number of 1 or more: anything else would create a key that is already expired.
  const ttlAmount = Number(ttlValue);
  const ttlInvalid = ttlUnit !== "none" && !(ttlValue.trim() !== "" && Number.isInteger(ttlAmount) && ttlAmount >= 1);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A check on one field shows under it, is tied to it for a screen reader, and takes focus there.
  const [fieldError, setFieldError] = useState<{ field: "name" | "expiry"; text: string } | null>(null);
  const nameInput = useRef<HTMLInputElement | null>(null);
  const expiryInput = useRef<HTMLInputElement | null>(null);

  const [rawAccessKey, setRawAccessKey] = useState<string | null>(null);
  const [accessKeyId, setAccessKeyId] = useState<string | null>(null);
  const [permissions, setPermissions] = useState<PermissionTree>({ domains: {}, devices: {}, entities: {} });

  const [entityTree, setEntityTree] = useState<EntityTreeData | null>(null);
  const [mesaEnforced, setMesaEnforced] = useState(false);
  // Home Companion answers an app with the Voice provider; without one its requests fail.
  const [voiceProvider, setVoiceProvider] = useState(true);
  // With the kill switch on Agent Chat cannot open, so Try Now says so rather than waiting.
  const [killSwitch, setKillSwitch] = useState(false);
  const [showAllDomains, setShowAllDomains] = useState(false);

  const [connected, setConnected] = useState(false);

  useEffect(() => {
    api.getEntityTree().then(setEntityTree).catch(() => null);
    api.getSettings().then((s) => { setMesaEnforced(s.mesa_mode === "enforced"); setVoiceProvider(!!s.voice_agent_provider_id); setKillSwitch(!!s.kill_switch); }).catch(() => null);
  }, []);

  // Keyboard focus lands on the new step's heading after Next/Back; the
  // clicked button unmounts with the old step, which would otherwise drop
  // focus to <body>. Skipped on first render (initial focus is fine).
  const bodyRef = useRef<HTMLDivElement>(null);
  const prevStepRef = useRef(step);
  useEffect(() => {
    if (prevStepRef.current === step) return;
    prevStepRef.current = step;
    const heading = bodyRef.current?.querySelector<HTMLElement>(".wizard-title");
    if (heading) {
      heading.setAttribute("tabindex", "-1");
      heading.focus();
    }
  }, [step]);

  const grantedEntityId = firstGreenTarget(permissions, entityTree);
  const grantedFriendlyName = (() => {
    if (!grantedEntityId) return "";
    if (!entityTree) return grantedEntityId;
    const domain = grantedEntityId.split(".")[0];
    return entityTree[domain]?.entity_details[grantedEntityId]?.friendly_name ?? grantedEntityId;
  })();

  const hasLights = !!(entityTree && entityTree["light"] && Object.keys(entityTree["light"].entity_details).length > 0);

  async function createAndAdvance() {
    if (!persona) return;
    setSaving(true);
    setError(null);
    setFieldError(null);
    try {
      const caps = PERSONA_CAP_DEFAULTS[persona];
      const patchBody: Record<string, unknown> = { persona, ...(caps || {}) };
      if (!accessKeyId) {
        if (name && !NAME_REGEX.test(name)) {
          setFieldError({ field: "name", text: t("wizard.nameError") });
          nameInput.current?.focus();
          return;
        }
        if (ttlInvalid) {
          setFieldError({ field: "expiry", text: t("accessKeys.expiryAmountError") });
          expiryInput.current?.focus();
          return;
        }
        let expiresAt: string | undefined;
        if (ttlUnit !== "none") {
          const n = parseInt(ttlValue, 10);
          const minutes = ttlUnit === "minutes" ? n : ttlUnit === "hours" ? n * 60 : ttlUnit === "days" ? n * 1440 : n * 10080;
          expiresAt = addMinutes(minutes);
        }
        const body: CreateAccessKeyBody = { name, expires_at: expiresAt, pass_through: false };
        const resp = await api.createAccessKey(body);
        const { access_key: raw, ...record } = resp;
        // Stash the irreversible bits BEFORE the persona patch so a patch
        // failure leaves a recoverable (retry-only-the-patch) state.
        setRawAccessKey(raw);
        setAccessKeyId(record.id);
        setPermissions(record.permissions);
        // Creation changes the access key list every host caches: the panel shell
        // gates its header "Agent Chat" button on a non-empty list and hands
        // the panel-hosted chat window its accessKeys prop, and the floating
        // window refetches on this event. Without it an access key made here stays
        // invisible to both until a page reload.
        window.dispatchEvent(new CustomEvent("phx-access-keys-changed"));
        await api.patchAccessKey(record.id, patchBody);
      } else {
        // Access key already created (back-navigation): re-apply persona only.
        await api.patchAccessKey(accessKeyId, patchBody);
      }
      setStep("access");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("wizard.createFailed"));
    } finally {
      setSaving(false);
    }
  }

  // Poll for connection while on the Test step.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const poll = useCallback(async () => {
    if (!accessKeyId) return;
    try {
      const c = await api.getAccessKeyConnection(accessKeyId);
      if (c.request_count > 0) setConnected(true);
    } catch {
      // transient; keep polling
    }
  }, [accessKeyId]);

  useEffect(() => {
    if (step !== "test" || connected) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    poll();
    pollRef.current = setInterval(poll, 3000);
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [step, connected, poll]);

  // Agent Chat branch: the wizard's job is done the moment the user sends
  // their first prompt through the window it opened; close to the access key page.
  useEffect(() => {
    if (step !== "try") return;
    const done = () => { if (accessKeyId) onFinish(accessKeyId); };
    window.addEventListener("phx-agentcli-sent", done);
    return () => window.removeEventListener("phx-agentcli-sent", done);
  }, [step, accessKeyId, onFinish]);

  // Open Agent Chat on the new access key with the chosen account/model preselected,
  // the test prompt pre-typed and an empty conversation (user, 2026-10-28). Closing
  // any already-open global window first forces a remount, so the freshly patched
  // durable selections are picked up.
  const launchAgentChat = useCallback((instanceId: string, model: string) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__phxAgentChat?.close?.();
    patchAgentCliDurable({ instanceId, model, prefill: t("wizard.promptChat"), freshConversation: true });
    window.dispatchEvent(new CustomEvent("phx-open-agentcli", { detail: { accessKeyId } }));
    setStep("try");
  }, [accessKeyId]);

  const prompts = buildTestPrompt(grantedFriendlyName || t("wizard.yourDevice"), mesaEnforced);

  // Until a path is chosen, the stepper shows the external app's steps.
  const steps = [...SHARED_STEPS, ...BRANCH_STEPS[branch ?? "app"]];
  const at = steps.indexOf(step);

  // Each step reports itself, with the part its Guide topic explains, so the
  // Guide can sit beside it and show its topic.
  const guide = useContext(WizardGuideContext);
  const report = guide?.report;
  useLayoutEffect(() => {
    const content = bodyRef.current;
    if (!report || !content) return;
    report({ step, part: content.querySelector<HTMLElement>("[data-guide-part]") ?? content, content });
  }, [report, step]);
  useEffect(() => () => report?.(null), [report]);

  function Stepper() {
    // Installing Phoenix is done before the wizard opens, so it leads as a completed step.
    const items = [{ id: "install", label: t("wizard.stepInstall"), done: true, current: false },
      ...steps.map((id, i) => ({ id, label: t(STEP_LABEL_KEYS[id]), done: i < at, current: i === at }))];
    return (
      <ol className="wizard-stepper" aria-label={t("wizard.stepperAria")}>
        {items.map((item, i) => (
          <li
            key={item.id}
            className={`wizard-step${item.current ? " wizard-step-active" : ""}${item.done ? " wizard-step-done" : ""}`}
            aria-current={item.current ? "step" : undefined}
          >
            <span className="wizard-step-num">{item.done ? <Icon name="check" size={12} /> : localeNumber(i + 1)}</span>
            <span className="wizard-step-label">{item.label}</span>
            {item.done && <span className="sr-only">{t("wizard.stepCompleted")}</span>}
          </li>
        ))}
      </ol>
    );
  }

  function renderBody() {
    switch (step) {
      case "choose":
        return (
          <>
            <h3 className="wizard-title">{t("wizard.chooseTitle")}</h3>
            <p className="wizard-sub">{t("wizard.chooseSub")}</p>
            <div className="wizard-choice-row" data-guide-part>
              <button type="button" className={`wizard-choice${branch === "chat" ? " is-chosen" : ""}`} aria-pressed={branch === "chat"} data-help="help.wizardChoiceChat" onClick={() => { setBranch("chat"); setStep("persona"); }}>
                <Icon name="agentChat" size={18} />
                <strong>{t("wizard.choiceChatTitle")}</strong>
                <small>{t("wizard.choiceChatBody")}</small>
              </button>
              <button type="button" className={`wizard-choice${branch === "app" ? " is-chosen" : ""}`} aria-pressed={branch === "app"} data-help="help.wizardChoiceApp" onClick={() => { setBranch("app"); setStep("persona"); }}>
                <Icon name="popOut" size={18} />
                <strong>{t("wizard.choiceAppTitle")}</strong>
                <small>{t("wizard.choiceAppBody")}</small>
              </button>
            </div>
            <div className="wizard-actions">
              <ActionButton icon="undo" label={t("common.cancel")} help="help.wizardCancel" className="btn btn-text" onClick={onCancel} />
            </div>
          </>
        );
      case "persona":
        return (
          <>
            <h3 className="wizard-title">{t("wizard.connectTitle")}</h3>
            <p className="wizard-sub">{tRich("wizard.personaSub", { strong: (c) => <strong>{c}</strong> })}</p>
            <div data-guide-part><PersonaPicker selected={persona} onSelect={setPersona} /></div>
            <div className="wizard-actions">
              <ActionButton icon="back" label={t("wizard.back")} help="help.wizardBack" className="btn btn-text" onClick={() => setStep("choose")} />
              <ActionButton icon="forward" label={t("wizard.next")} help="help.wizardNextPersona" className="btn btn-primary" disabled={!persona} onClick={() => setStep("accessKey")} />
            </div>
          </>
        );
      case "accessKey":
        return (
          <>
            <h3 className="wizard-title">{t("wizard.accessKeyTitle")}</h3>
            <p className="wizard-sub">{t("wizard.accessKeySub")}</p>
            <div className="field" data-guide-part>
              <label htmlFor="wiz-name">{t("wizard.nameLabel")}</label>
              <input ref={nameInput} id="wiz-name" className={`input${fieldError?.field === "name" ? " error" : ""}`} data-help="help.accessKeyName" value={name} disabled={!!accessKeyId}
                aria-invalid={fieldError?.field === "name" || undefined} aria-describedby={fieldError?.field === "name" ? "wiz-name-error" : undefined}
                maxLength={32} onChange={(e) => { nameTyped.current = true; setName(e.target.value); if (fieldError?.field === "name") setFieldError(null); }} placeholder={t("accessKeys.namePlaceholder")} />
              {fieldError?.field === "name" && <span id="wiz-name-error" className="field-error" role="alert">{fieldError.text}</span>}
            </div>
            <div className="field">
              <label htmlFor="wiz-expiry-unit">{t("wizard.expiryLabel")}</label>
              <div className="access-key-create-expiry-row">
                <select id="wiz-expiry-unit" className="input input-auto" data-help="help.keyExpiry" value={ttlUnit} disabled={!!accessKeyId}
                  onChange={(e) => setTtlUnit(e.target.value as TtlUnit)}>
                  <option value="none">{t("wizard.expiryNone")}</option>
                  <option value="minutes">{t("wizard.expiryMinutes")}</option>
                  <option value="hours">{t("wizard.expiryHours")}</option>
                  <option value="days">{t("wizard.expiryDays")}</option>
                  <option value="weeks">{t("wizard.expiryWeeks")}</option>
                </select>
                {ttlUnit !== "none" && (
                  <input ref={expiryInput} className={`input access-key-create-expiry-value${fieldError?.field === "expiry" ? " error" : ""}`} type="number" min={1} data-help="help.keyExpiry"
                    aria-label={t("wizard.expiryAmountAria")} aria-invalid={ttlInvalid}
                    aria-describedby={fieldError?.field === "expiry" ? "wiz-expiry-error" : undefined}
                    value={ttlValue} disabled={!!accessKeyId} onChange={(e) => { setTtlValue(e.target.value); if (fieldError?.field === "expiry") setFieldError(null); }} />
                )}
              </div>
              {fieldError?.field === "expiry" && <span id="wiz-expiry-error" className="field-error" role="alert">{fieldError.text}</span>}
            </div>
            {accessKeyId && <div className="banner banner-info">{t("wizard.accessKeyCreatedBanner")}</div>}
            <div className="wizard-actions">
              <ActionButton icon="back" label={t("wizard.back")} help="help.wizardBack" className="btn btn-text" onClick={() => setStep("persona")} />
              <ActionButton icon={accessKeyId ? "forward" : "add"} help="help.wizardCreateKey" className="btn btn-primary" busy={saving} onClick={() => void createAndAdvance()}
                label={saving ? t("wizard.working") : accessKeyId ? t("wizard.next") : t("wizard.createAccessKey")} />
            </div>
          </>
        );
      case "access":
        return (
          <>
            <h3 className="wizard-title">{t("wizard.accessTitle")}</h3>
            <p className="wizard-sub">{tRich("wizard.accessSub", { code: (c) => <code>{c}</code>, strong: (c) => <strong>{c}</strong> })}</p>
            {!hasLights && !showAllDomains && (
              <div className="banner banner-warn">
                {t("wizard.noLights")}{" "}
                <ActionButton icon="preview" label={t("wizard.showAllDevices")} help="help.wizardShowAllDevices" className="btn btn-text btn-sm" onClick={() => setShowAllDomains(true)} />
              </div>
            )}
            {accessKeyId && (
              <div data-guide-part>
                <EntityTree
                  accessKeyId={accessKeyId}
                  permissions={permissions}
                  onPermissionsChange={setPermissions}
                  domainAllowlist={showAllDomains ? undefined : ["light"]}
                />
              </div>
            )}
            <div className="wizard-actions">
              <ActionButton icon="back" label={t("wizard.back")} help="help.wizardBack" className="btn btn-text" onClick={() => setStep("accessKey")} />
              <ActionButton icon="forward" label={t("wizard.next")} help="help.wizardNextAccess" className="btn btn-primary" disabled={!grantedEntityId}
                onClick={() => setStep(branch === "chat" ? "provider" : "connect")} />
            </div>
          </>
        );
      case "provider":
        return (
          <WizardProviderSetup
            onBack={() => setStep("access")}
            onTryNow={launchAgentChat}
          />
        );
      case "connect":
        return (
          <>
            <h3 className="wizard-title">{t("wizard.connectAgentTitle")}</h3>
            {rawAccessKey && <div data-guide-part><ConnectInstructions accessKey={rawAccessKey} accessKeyName={name} /></div>}
            <div className="wizard-actions">
              <ActionButton icon="back" label={t("wizard.back")} help="help.wizardBack" className="btn btn-text" onClick={() => setStep("access")} />
              <ActionButton icon="pending" label={t("wizard.connectLater")} help="help.wizardConnectLater" className="btn btn-ghost" onClick={() => setStep("done")} />
              <ActionButton icon="probe" label={t("wizard.testConnection")} help="help.wizardTestConnection" className="btn btn-primary" onClick={() => setStep("test")} />
            </div>
          </>
        );
      case "try":
        return (
          <>
            <h3 className="wizard-title">{t("wizard.chatReadyTitle")}</h3>
            {killSwitch
              ? <p className="wizard-hint wizard-hint-warning" role="status">{t("shell.killSwitchTitle")}</p>
              : <p className="wizard-sub">{tRich("wizard.chatReadySub", { strong: (c) => <strong>{c}</strong> })}</p>}
            <div className="wizard-actions">
              <ActionButton icon="accessKeys" label={t("wizard.goToAccessKey")} help="help.wizardGoToKey" className="btn btn-primary" onClick={() => { if (accessKeyId) onFinish(accessKeyId); }} />
            </div>
          </>
        );
      case "test":
        return (
          <>
            <h3 className="wizard-title">{t("wizard.testTitle")}</h3>
            <p className="wizard-sub">{t("wizard.testSub")}</p>
            <div className="connect-field" data-guide-part>
              <span className="connect-field-label">{t("wizard.tryLabel")}</span>
              <code className="connect-field-value">{prompts.read}</code>
              <CopyButton text={prompts.read} label={t("common.copy")} name={t("wizard.copyFirstPrompt")} help="help.wizardCopyPrompt" />
            </div>
            {prompts.action && (
              <div className="connect-field">
                <span className="connect-field-label">{t("wizard.thenLabel")}</span>
                <code className="connect-field-value">{prompts.action}</code>
                <CopyButton text={prompts.action} label={t("common.copy")} name={t("wizard.copyNextPrompt")} help="help.wizardCopyPrompt" />
              </div>
            )}
            {mesaEnforced && (
              <p className="wizard-hint">{t("wizard.mesaHint")}</p>
            )}
            {persona === "home_companion" && !voiceProvider && (
              <p className="wizard-hint wizard-hint-warning" role="status">{t("wizard.companionNoVoiceProvider")}</p>
            )}
            <div className={`wizard-connect-status${connected ? " wizard-connect-status-ok" : ""}`} role="status">
              {connected ? t("wizard.connected") : t("wizard.waitingForAgent")}
            </div>
            <div className="wizard-actions">
              <ActionButton icon="back" label={t("wizard.back")} help="help.wizardBack" className="btn btn-text" onClick={() => setStep("connect")} />
              <ActionButton icon="forward" label={t("wizard.next")} help="help.wizardNextTest" className="btn btn-primary" disabled={!connected} onClick={() => setStep("done")} />
            </div>
          </>
        );
      case "done":
        return (
          <>
            <h3 className="wizard-title">{t("wizard.doneTitle")}</h3>
            <p className="wizard-sub">{tRich("wizard.doneSub", { code: (c) => <code>{c}</code> }, { name })}</p>
            <div className="wizard-actions">
              <ActionButton icon="accessKeys" label={t("wizard.goToAccessKey")} help="help.wizardGoToKey" className="btn btn-primary" onClick={() => { if (accessKeyId) onFinish(accessKeyId); }} />
            </div>
          </>
        );
    }
  }

  return (
    // The wizard's buttons always show their text, whatever the toolbar's display mode (user, 2026-10-28).
    <DisplayModeContext.Provider value="icon-text">
      <div className={`view-root wizard-root${guide?.guideBeside ? " is-guided" : ""}`}>
        <div className="wizard-center">
          <Stepper />
          {error && <ErrorMsg msg={error} />}
          <div className="card wizard-body" ref={bodyRef}>{renderBody()}</div>
        </div>
      </div>
    </DisplayModeContext.Provider>
  );
}

// The Agent Chat branch's provider step: pick an already-configured account, or
// add a new one (kind + API key / Ollama URL, validated via the store-nothing
// probe, exactly like the Settings > Agent Chat card). "Try now" commits a
// validated new account (or uses the selected existing one) and hands its
// instance id + model to the wizard to launch the chat window.
export function WizardProviderSetup({ onBack, onTryNow }: {
  onBack: () => void;
  onTryNow: (instanceId: string, model: string) => void;
}) {
  const [instances, setInstances] = useState<AgentCliInstance[] | null>(null);
  const [providerTypes, setProviderTypes] = useState<AgentCliProviderType[]>([]);
  const [selected, setSelected] = useState("");
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.getAgentCliProviders()
      .then((r) => {
        setInstances(r.instances);
        setProviderTypes(r.provider_types ?? []);
        if (r.instances.length > 0) setSelected(r.instances[0].id);
      })
      .catch(() => { setInstances([]); setProviderTypes([]); });
  }, []);

  const tryExisting = async () => {
    setBusy(true);
    setErr(null);
    try {
      const inst = instances?.find((i) => i.id === selected);
      onTryNow(selected, inst?.model ?? "");
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : t("wizard.providerSaveFailed"));
      setBusy(false);
    }
  };

  const providerCreated = async (instance: AgentCliInstance, probe: boolean) => {
    if (probe) {
      try { await api.probeAgentCliCapabilities(instance.id); } catch { /* advisory */ }
    }
    window.dispatchEvent(new CustomEvent("phx-agentcli-providers-changed"));
    onTryNow(instance.id, instance.model ?? "");
  };

  return (
    <>
      <h3 className="wizard-title">{t("wizard.providerTitle")}</h3>
      <p className="wizard-sub">
        {t("wizard.providerIntro")}{" "}
        {instances && instances.length > 0
          ? t("wizard.providerPickExisting")
          : t("wizard.providerAddFirst")}
      </p>

      {instances && instances.length > 0 && (
        <div className="field">
          <label htmlFor="wiz-provider-account">{t("wizard.providerAccount")}</label>
          <BusySelect id="wiz-provider-account" className="input" data-help="help.wizardProviderAccount" value={selected}
                  busy={busy} disabled={adding}
                  onChange={(e) => setSelected(e.target.value)}>
            {instances.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
          </BusySelect>
        </div>
      )}

      <div data-guide-part>
      <ProviderAddForm
        providerTypes={providerTypes}
        allowAutomatic
        disabled={busy}
        completeLabel={t("wizard.tryNow")}
        completeIcon="agentChat"
        completeHelp="help.providerSaveAndTry"
        addingLabelKey="wizard.addingProvider"
        onActiveChange={setAdding}
        onCreated={providerCreated}
      />
      </div>
      {!adding && err && <div className="banner banner-error" role="alert">{err}</div>}

      <div className="wizard-actions">
        <ActionButton icon="back" label={t("wizard.back")} help="help.wizardBack" className="btn btn-text" busy={busy} onClick={onBack} />
        {!adding && (
          <ActionButton icon="agentChat" label={busy ? t("wizard.working") : t("wizard.tryNow")} help="help.wizardTryNow" className="btn btn-primary" busy={busy}
            disabled={!selected} onClick={() => void tryExisting()} />
        )}
      </div>
    </>
  );
}
