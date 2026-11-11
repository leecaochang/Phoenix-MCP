// The "connect Phoenix MCP to your agent" UI, shared by the onboarding wizard's Connect step,
// the access-key-created dialog and the rotate dialog. Two methods, chosen at the top and kept
// apart: paste the access key (editable URL, the key, per-client commands and configs), or sign
// in with OAuth (the key's switch, address and countdown, then per-client steps). It opens on
// OAuth when the key already allows it.
import React, { useId, useState } from "react";
import { buildAgentTabs, buildMcpUrl, buildOAuthTabs, buildSkillInstall, skillUrlFromMcp, type AgentBlock, type AgentTab } from "../wizard_helpers";
import { Segmented } from "../ui/Segmented";
import { t } from "../i18n";
import { tRich } from "../i18n/rich";
import { CopyButton } from "../ui/CopyButton";
import { AccessKeyOAuth } from "./AccessKeyOAuth";

// A full-width value box (read-only or editable) with the Copy button inside it,
// matching the look of the command/JSON blocks.
export function CopyCodeBox(
  { label, title, value, onChange }: { label?: string; title?: string; value: string; onChange?: (v: string) => void },
) {
  return (
    <div className="command-field">
      {label && <div className="command-field-label">{label}</div>}
      <div className="command-block-codewrap">
        {onChange
          ? <input className="command-block-input" data-help="help.connectMcpUrl" value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} />
          : <pre className="command-block-code" tabIndex={0} role="region" aria-label={label || title || t("common.codeBlock")}>{value}</pre>}
        {/* Beside the value, not over it. */}
        <CopyButton text={value} className="command-copy" help="help.connectCopy"
          label={label || title ? t("common.copyNamed", { item: label || title! }) : t("common.copy")} />
      </div>
    </div>
  );
}

function CommandBlock({ title, hint, code, fields, steps }: AgentBlock) {
  return (
    <div className="command-block">
      {title && <div className="command-block-title">{title}</div>}
      {hint && <small className="wizard-hint">{hint}</small>}
      {steps && <ol className="connect-steps">{steps.map((step, i) => <li key={i}>{step}</li>)}</ol>}
      {fields && fields.map((f) => <CopyCodeBox key={f.label} label={f.label} value={f.value} />)}
      {code && <CopyCodeBox title={title} value={code} />}
    </div>
  );
}

/** One row of client tabs and the chosen client's panel. Arrow keys, Home and End move between tabs. */
function ClientTabs({ tabs, after }: { tabs: AgentTab[]; after?: (tab: AgentTab) => React.ReactNode }) {
  const [chosen, setChosen] = useState(tabs[0]?.key ?? "");
  const uid = useId();
  const current = tabs.find((tab) => tab.key === chosen) ?? tabs[0];

  function choose(next: string, tablist?: HTMLDivElement) {
    setChosen(next);
    window.requestAnimationFrame(() => {
      tablist?.querySelector<HTMLButtonElement>(`[data-agent-tab="${next}"]`)?.focus();
    });
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const idx = tabs.findIndex((tab) => tab.key === current?.key);
    const next = e.key === "Home"
      ? tabs[0]
      : e.key === "End"
        ? tabs[tabs.length - 1]
        : e.key === "ArrowRight"
          ? tabs[(idx + 1) % tabs.length]
          : tabs[(idx - 1 + tabs.length) % tabs.length];
    if (next) choose(next.key, e.currentTarget);
  }

  return (
    <>
      <div className="wizard-tabs-label">{t("wizard.chooseAgent")}</div>
      <div className="wizard-tabs" role="tablist" aria-label={t("wizard.agentTablist")} onKeyDown={handleKeyDown}>
        {tabs.map((tab) => (
          <button
            key={tab.key}
            id={`${uid}tab-${tab.key}`}
            data-agent-tab={tab.key}
            role="tab"
            data-help="help.connectAgentTab"
            aria-selected={current?.key === tab.key}
            aria-controls={`${uid}panel`}
            tabIndex={current?.key === tab.key ? 0 : -1}
            className={`wizard-tab${current?.key === tab.key ? " wizard-tab-active" : ""}`}
            onClick={() => choose(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {current && (
        <div id={`${uid}panel`} className="wizard-tab-panel" role="tabpanel" aria-labelledby={`${uid}tab-${current.key}`}>
          {current.intro && <p className="wizard-sub">{current.intro}</p>}
          {current.blocks.map((b, i) => <CommandBlock key={i} {...b} />)}
          {current.href && (
            <a className="btn btn-text btn-sm" data-help="help.connectSetupGuide" href={current.href} target="_blank" rel="noopener noreferrer">
              {t("wizard.openSetupGuide", { label: current.label })}
            </a>
          )}
        </div>
      )}
      {current && after?.(current)}
    </>
  );
}

type Method = "key" | "oauth";

export function ConnectInstructions({ accessKey, accessKeyName, accessKeyId, oauthEnabled = false }: {
  accessKey: string;
  accessKeyName: string;
  /** When known, the OAuth method offers this key's switch, address and countdown. */
  accessKeyId?: string;
  /** The key already allows OAuth sign-in: open on that method. */
  oauthEnabled?: boolean;
}) {
  const [method, setMethod] = useState<Method>(oauthEnabled ? "oauth" : "key");
  const [mcpUrl, setMcpUrl] = useState(buildMcpUrl(window.location.origin));
  // The HTTPS address the server reports for apps; the browser's own address until it does.
  const [oauthUrl, setOauthUrl] = useState<string | null>(null);
  const skillUrl = skillUrlFromMcp(mcpUrl);

  return (
    <div className="connect-instructions">
      <div className="connect-method" data-help="help.connectMethod" data-guide="topic.connecting#methods">
        <div className="wizard-tabs-label">{t("wizard.connectMethodLabel")}</div>
        <Segmented label={t("wizard.connectMethodLabel")} value={method} onChange={setMethod}
          segments={[{ value: "key", label: t("wizard.methodKey") }, { value: "oauth", label: t("wizard.methodOAuth") }]} />
      </div>
      {method === "key" ? (
        <>
          <p className="wizard-sub">
            {tRich("wizard.connectIntro", { code: (c) => <code>{c}</code> })}
          </p>
          <CopyCodeBox label={t("wizard.mcpServerUrl")} value={mcpUrl} onChange={setMcpUrl} />
          <small className="wizard-hint">{t("wizard.urlHint")}</small>
          <CopyCodeBox label={t("wizard.accessKeyLabel")} value={accessKey} />
          <small className="wizard-hint">
            {tRich("wizard.serverNameHint", { code: (c) => <code>{c}</code> })}
          </small>
          <ClientTabs key="key" tabs={buildAgentTabs(mcpUrl, accessKey, accessKeyName)} after={(current) => {
            const skillBlocks = buildSkillInstall(skillUrl, current.key);
            return skillBlocks.length > 0 && (
              <div className="connect-skill-section">
                <div className="wizard-tabs-label">{t("wizard.skillSectionTitle")}</div>
                <p className="wizard-sub">{t("wizard.skillSectionBody", { label: current.label })}</p>
                {skillBlocks.map((b, i) => <CommandBlock key={i} {...b} />)}
              </div>
            );
          }} />
        </>
      ) : (
        <>
          <p className="wizard-sub">{t("wizard.oauthIntro")}</p>
          {accessKeyId
            ? <AccessKeyOAuth accessKeyId={accessKeyId} compact onAddress={setOauthUrl} />
            : <p className="wizard-hint">{t("wizard.oauthNoKey")}</p>}
          <ClientTabs key="oauth" tabs={buildOAuthTabs(oauthUrl ?? mcpUrl)} />
        </>
      )}
    </div>
  );
}
