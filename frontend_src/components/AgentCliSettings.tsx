import { BusyInput } from "./BusyButton";
import { RoutingStatistics } from "./RoutingStatistics";
import { CommandRoutingToggle } from "./CommandRoutingToggle";
import { useEffect, useRef, useState } from "react";
import { DocsHelpLink } from "./common";
import type { ConversationStyle, DetailLevel } from "../types";
import { t } from "../i18n";
import { ConversationBehaviorControls } from "./ConversationBehaviorControls";

interface Props {
  routingMode: string | undefined;
  onRoutingChange: (mode: "auto" | "off") => void;
  scrollback: number;
  onScrollbackChange: (n: number) => void;
  maxIterations: number;
  onMaxIterationsChange: (n: number) => void;
  globalVisible: boolean;
  onGlobalChange: (v: boolean) => void;
  conversationStyle: ConversationStyle;
  onConversationStyleChange: (v: ConversationStyle) => void;
  detailLevel: DetailLevel;
  onDetailLevelChange: (v: DetailLevel) => void;
  homeFocused: boolean;
  onHomeFocusedChange: (v: boolean) => void;
  saving: boolean;
}

export function AgentCliSettings({
  routingMode, onRoutingChange, scrollback, onScrollbackChange, maxIterations, onMaxIterationsChange,
  globalVisible, onGlobalChange, conversationStyle, onConversationStyleChange,
  detailLevel, onDetailLevelChange, homeFocused, onHomeFocusedChange, saving,
}: Props) {
  const [scrollInput, setScrollInput] = useState(String(scrollback));
  const [maxIterInput, setMaxIterInput] = useState(String(maxIterations));
  useEffect(() => { setScrollInput(String(scrollback)); }, [scrollback]);
  useEffect(() => { setMaxIterInput(String(maxIterations)); }, [maxIterations]);

  // Commit the memory value. Takes the raw string explicitly so the debounced
  // path is not subject to a stale closure over scrollInput.
  const commitScrollback = (raw: string) => {
    let n = parseInt(raw, 10);
    if (Number.isNaN(n)) { setScrollInput(String(scrollback)); return; }
    n = Math.max(0, Math.min(5000, n));
    setScrollInput(String(n));
    if (n !== scrollback) onScrollbackChange(n);
  };

  // Auto-save shortly after the last change so number-spinner arrows persist
  // without needing an explicit blur or Enter.
  const commitTimer = useRef<number | undefined>(undefined);
  const onScrollInput = (raw: string) => {
    setScrollInput(raw);
    window.clearTimeout(commitTimer.current);
    commitTimer.current = window.setTimeout(() => commitScrollback(raw), 600);
  };
  const commitNow = (raw: string) => {
    window.clearTimeout(commitTimer.current);
    commitScrollback(raw);
  };
  useEffect(() => () => window.clearTimeout(commitTimer.current), []);

  // Max tool rounds before the "continue?" checkpoint. Same debounced
  // auto-save pattern as the memory field; clamped to [3, 100].
  const maxIterTimer = useRef<number | undefined>(undefined);
  const commitMaxIter = (raw: string) => {
    let n = parseInt(raw, 10);
    if (Number.isNaN(n)) { setMaxIterInput(String(maxIterations)); return; }
    n = Math.max(3, Math.min(100, n));
    setMaxIterInput(String(n));
    if (n !== maxIterations) onMaxIterationsChange(n);
  };
  const onMaxIterInput = (raw: string) => {
    setMaxIterInput(raw);
    window.clearTimeout(maxIterTimer.current);
    maxIterTimer.current = window.setTimeout(() => commitMaxIter(raw), 600);
  };
  const commitMaxIterNow = (raw: string) => {
    window.clearTimeout(maxIterTimer.current);
    commitMaxIter(raw);
  };
  useEffect(() => () => window.clearTimeout(maxIterTimer.current), []);

  return (
    <div className="card">
      <h3 className="card-header">
        {t("settings.agentcliCard")}
        <DocsHelpLink path="agentcli.html" label={t("settings.agentcliCard")} />
      </h3>
      <p className="settings-info-note" style={{ marginTop: 0 }}>
        {t("settings.agentcliIntro")}
      </p>

      <CommandRoutingToggle surface="chat" mode={routingMode} onChange={onRoutingChange} saving={saving} />

      <hr className="settings-divider" />
      <div className="toggle-row toggle-row-plain">
        <div className="toggle-label">
          <span>{t("settings.agentcliGlobalLabel")}</span>
          <small>
            {t("settings.agentcliGlobalHelp")}
          </small>
        </div>
        <label className={`toggle-switch${saving ? " disabled" : ""}`}>
          <BusyInput
            type="checkbox"
            aria-label={t("settings.agentcliGlobalAria")}
            checked={globalVisible}
            busy={saving}
            onChange={(e) => onGlobalChange(e.target.checked)}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
      <div className="toggle-row toggle-row-plain" style={{ marginTop: 14 }}>
        <div className="toggle-label">
          <span>{t("settings.agentcliScrollbackLabel")}</span>
          <small>
            {t("settings.agentcliScrollbackHelp")}
          </small>
        </div>
        <BusyInput
          className="input input-auto"
          type="number" min={0} max={5000} step={100}
          aria-label={t("settings.agentcliScrollbackAria")}
          value={scrollInput}
          busy={saving}
          onChange={(e) => onScrollInput(e.target.value)}
          onBlur={() => commitNow(scrollInput)}
          onKeyDown={(e) => { if (e.key === "Enter") commitNow(scrollInput); }}
        />
      </div>
      <div className="toggle-row toggle-row-plain" style={{ marginTop: 14 }}>
        <div className="toggle-label">
          <span>{t("settings.agentcliStepsLabel")}</span>
          <small>
            {t("settings.agentcliStepsHelp")}
          </small>
        </div>
        <BusyInput
          className="input input-auto"
          type="number" min={3} max={100} step={1}
          aria-label={t("settings.agentcliStepsAria")}
          value={maxIterInput}
          busy={saving}
          onChange={(e) => onMaxIterInput(e.target.value)}
          onBlur={() => commitMaxIterNow(maxIterInput)}
          onKeyDown={(e) => { if (e.key === "Enter") commitMaxIterNow(maxIterInput); }}
        />
      </div>
      <ConversationBehaviorControls
        surface={t("settings.agentcliCard")}
        style={conversationStyle}
        detail={detailLevel}
        homeFocused={homeFocused}
        saving={saving}
        onStyleChange={onConversationStyleChange}
        onDetailChange={onDetailLevelChange}
        onHomeFocusedChange={onHomeFocusedChange}
      />
      <RoutingStatistics surface="chat" />
    </div>
  );
}
