import { BusyInput } from "./BusyButton";
import { CommandRoutingToggle } from "./CommandRoutingToggle";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { RoutingStatistics } from "./RoutingStatistics";
import type { ConversationStyle, DetailLevel } from "../types";
import { t } from "../i18n";
import { SettingLabel, helpRow } from "../ui/SettingLabel";
import { ConversationBehaviorControls } from "./ConversationBehaviorControls";
import { isImeKey } from "../utils/ime";
import { NumberField } from "../ui/NumberField";

/** Chat memory bounds, as the server clamps them (provider_constants.py). */
const AGENT_CHAT_MEMORY_CHARS_MIN = 0;
const AGENT_CHAT_MEMORY_CHARS_MAX = 960_000;

interface Props {
  routingMode: string | undefined;
  onRoutingChange: (mode: "auto" | "off") => void;
  memoryChars: number;
  onMemoryCharsChange: (n: number) => void;
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
  /** How the routing counts are reached: in the shell a button opening their window; by default they unfold in place. */
  statistics?: ReactNode;
}

export function AgentCliSettings({
  routingMode, onRoutingChange, memoryChars, onMemoryCharsChange, maxIterations, onMaxIterationsChange,
  globalVisible, onGlobalChange, conversationStyle, onConversationStyleChange,
  detailLevel, onDetailLevelChange, homeFocused, onHomeFocusedChange, saving, statistics = <RoutingStatistics surface="chat" />,
}: Props) {
  const [memoryInput, setMemoryInput] = useState(String(memoryChars));
  const [maxIterInput, setMaxIterInput] = useState(String(maxIterations));
  useEffect(() => { setMemoryInput(String(memoryChars)); }, [memoryChars]);
  useEffect(() => { setMaxIterInput(String(maxIterations)); }, [maxIterations]);

  // Commit the memory value. Takes the raw string explicitly so the debounced
  // path is not subject to a stale closure over memoryInput.
  const commitMemory = (raw: string) => {
    let n = parseInt(raw, 10);
    if (Number.isNaN(n)) { setMemoryInput(String(memoryChars)); return; }
    n = Math.max(AGENT_CHAT_MEMORY_CHARS_MIN, Math.min(AGENT_CHAT_MEMORY_CHARS_MAX, n));
    setMemoryInput(String(n));
    if (n !== memoryChars) onMemoryCharsChange(n);
  };

  // Auto-save shortly after the last change so number-spinner arrows persist
  // without needing an explicit blur or Enter.
  const commitTimer = useRef<number | undefined>(undefined);
  const onMemoryInput = (raw: string) => {
    setMemoryInput(raw);
    window.clearTimeout(commitTimer.current);
    commitTimer.current = window.setTimeout(() => commitMemory(raw), 600);
  };
  const commitNow = (raw: string) => {
    window.clearTimeout(commitTimer.current);
    commitMemory(raw);
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
    // What Agent Chat is shows in Help over the window's empty space.
    <div className="phx-settings-form" data-help-text={t("settings.agentcliIntro")}>
      <CommandRoutingToggle surface="chat" mode={routingMode} onChange={onRoutingChange} saving={saving} divider={false} />
      <div className="toggle-row" {...helpRow(t("settings.agentcliGlobalHelp"))}>
        <SettingLabel help={t("settings.agentcliGlobalHelp")}><span>{t("settings.agentcliGlobalLabel")}</span></SettingLabel>
        <label className={`toggle-switch${saving ? " disabled" : ""}`}>
          <BusyInput
            type="checkbox"
            aria-label={t("settings.agentcliGlobalLabel")}
            checked={globalVisible}
            busy={saving}
            onChange={(e) => onGlobalChange(e.target.checked)}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
      <div className="toggle-row" {...helpRow(t("settings.agentcliMemoryHelp"))}>
        <SettingLabel help={t("settings.agentcliMemoryHelp")}><span>{t("settings.agentcliMemoryLabel")}</span></SettingLabel>
        <NumberField
          className="input input-auto" min={AGENT_CHAT_MEMORY_CHARS_MIN} max={AGENT_CHAT_MEMORY_CHARS_MAX} step={10000}
          aria-label={t("settings.agentcliMemoryLabel")}
          value={memoryInput}
          busy={saving}
          onChange={(e) => onMemoryInput(e.target.value)}
          onBlur={() => commitNow(memoryInput)}
          onKeyDown={(e) => { if (e.key === "Enter" && !isImeKey(e)) commitNow(memoryInput); }}
        />
      </div>
      <div className="toggle-row" {...helpRow(t("settings.agentcliStepsHelp"))}>
        <SettingLabel help={t("settings.agentcliStepsHelp")}><span>{t("settings.agentcliStepsLabel")}</span></SettingLabel>
        <NumberField
          className="input input-auto" min={3} max={100} step={1}
          aria-label={t("settings.agentcliStepsLabel")}
          value={maxIterInput}
          busy={saving}
          onChange={(e) => onMaxIterInput(e.target.value)}
          onBlur={() => commitMaxIterNow(maxIterInput)}
          onKeyDown={(e) => { if (e.key === "Enter" && !isImeKey(e)) commitMaxIterNow(maxIterInput); }}
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
      {statistics}
    </div>
  );
}
