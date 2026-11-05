// The "connect Phoenix MCP to your agent" UI: editable URL, access key, per-agent tabs with
// prefilled commands/configs. Shared by the onboarding wizard's Connect step and
// the standard access-key-created modal so both show identical, copy-ready instructions.
import React, { useId, useState } from "react";
import { copyToClipboard } from "../utils";
import { buildAgentTabs, buildMcpUrl, buildSkillInstall, skillUrlFromMcp } from "../wizard_helpers";
import { t } from "../i18n";
import { tRich } from "../i18n/rich";
import { ActionButton } from "../ui/ActionButton";

// A full-width value box (read-only or editable) with the Copy button inside it,
// matching the look of the command/JSON blocks.
export function CopyCodeBox(
  { label, title, value, onChange }: { label?: string; title?: string; value: string; onChange?: (v: string) => void },
) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const copied = state === "copied";
  async function copy() {
    try {
      await copyToClipboard(value);
      setState("copied");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2000);
  }
  return (
    <div className="command-field">
      {label && <div className="command-field-label">{label}</div>}
      <div className="command-block-codewrap">
        {onChange
          ? <input className="command-block-input" data-help="help.connectMcpUrl" value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} />
          : <pre className="command-block-code" tabIndex={0} role="region" aria-label={label || title || t("common.codeBlock")}>{value}</pre>}
        {/* Beside the value, not over it. */}
        <ActionButton icon={copied ? "check" : "copy"} label={copied ? t("common.copied") : t("common.copy")} help="help.connectCopy" className="btn btn-outline btn-sm command-copy"
          name={!copied && (label || title) ? t("common.copyNamed", { item: label || title! }) : undefined}
          onClick={() => { void copy(); }} />
        {/* Announce the visual Copy -> Copied! swap to assistive tech. */}
        <span className="sr-only" role="status">{copied ? t("common.copiedToClipboard") : state === "failed" ? t("conversations.copyFailed") : ""}</span>
      </div>
    </div>
  );
}

function CommandBlock(
  { title, hint, code, fields }:
  { title?: string; hint?: string; code?: string; fields?: { label: string; value: string }[]; },
) {
  return (
    <div className="command-block">
      {title && <div className="command-block-title">{title}</div>}
      {hint && <small className="wizard-hint">{hint}</small>}
      {fields && fields.map((f) => <CopyCodeBox key={f.label} label={f.label} value={f.value} />)}
      {code && <CopyCodeBox title={title} value={code} />}
    </div>
  );
}

export function ConnectInstructions({ accessKey, accessKeyName }: { accessKey: string; accessKeyName: string }) {
  const [mcpUrl, setMcpUrl] = useState(buildMcpUrl(window.location.origin));
  const [agentTab, setAgentTab] = useState("claude");
  const uid = useId();
  const tabs = buildAgentTabs(mcpUrl, accessKey, accessKeyName);
  const current = tabs.find((t) => t.key === agentTab) ?? tabs[0];
  const skillUrl = skillUrlFromMcp(mcpUrl);

  function switchAgentTab(next: string, tablist?: HTMLDivElement) {
    setAgentTab(next);
    window.requestAnimationFrame(() => {
      tablist?.querySelector<HTMLButtonElement>(`[data-agent-tab="${next}"]`)?.focus();
    });
  }

  function handleAgentTabKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const idx = tabs.findIndex((t) => t.key === current?.key);
    const next = e.key === "Home"
      ? tabs[0]
      : e.key === "End"
        ? tabs[tabs.length - 1]
        : e.key === "ArrowRight"
          ? tabs[(idx + 1) % tabs.length]
          : tabs[(idx - 1 + tabs.length) % tabs.length];
    if (next) switchAgentTab(next.key, e.currentTarget);
  }

  return (
    <div className="connect-instructions">
      <p className="wizard-sub">
        {tRich("wizard.connectIntro", { code: (c) => <code>{c}</code> })}
      </p>
      <CopyCodeBox label={t("wizard.mcpServerUrl")} value={mcpUrl} onChange={setMcpUrl} />
      <small className="wizard-hint">{t("wizard.urlHint")}</small>
      <CopyCodeBox label={t("wizard.accessKeyLabel")} value={accessKey} />
      <small className="wizard-hint">
        {tRich("wizard.serverNameHint", { code: (c) => <code>{c}</code> })}
      </small>

      <div className="wizard-tabs-label">{t("wizard.chooseAgent")}</div>
      <div className="wizard-tabs" role="tablist" aria-label={t("wizard.agentTablist")} onKeyDown={handleAgentTabKeyDown}>
        {tabs.map((t) => (
          <button
            key={t.key}
            id={`${uid}tab-${t.key}`}
            data-agent-tab={t.key}
            role="tab"
            data-help="help.connectAgentTab"
            aria-selected={current?.key === t.key}
            aria-controls={`${uid}panel`}
            tabIndex={current?.key === t.key ? 0 : -1}
            className={`wizard-tab${current?.key === t.key ? " wizard-tab-active" : ""}`}
            onClick={() => switchAgentTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {current && (
        <div
          id={`${uid}panel`}
          className="wizard-tab-panel"
          role="tabpanel"
          aria-labelledby={`${uid}tab-${current.key}`}
        >
          {current.intro && <p className="wizard-sub">{current.intro}</p>}
          {current.blocks.map((b, i) => (
            <CommandBlock key={i} title={b.title} hint={b.hint} code={b.code} fields={b.fields} />
          ))}
          <a className="btn btn-text btn-sm" data-help="help.connectSetupGuide" href={current.href} target="_blank" rel="noopener noreferrer">
            {t("wizard.openSetupGuide", { label: current.label })}
          </a>
        </div>
      )}
      {current && (
        <div className="connect-skill-section">
          <div className="wizard-tabs-label">{t("wizard.skillSectionTitle")}</div>
          <p className="wizard-sub">{t("wizard.skillSectionBody", { label: current.label })}</p>
          {buildSkillInstall(skillUrl, current.key).map((b, i) => (
            <CommandBlock key={i} title={b.title} hint={b.hint} code={b.code} fields={b.fields} />
          ))}
        </div>
      )}
    </div>
  );
}
