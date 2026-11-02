import { BusyInput, BusySelect } from "./BusyButton";
import type { ConversationStyle, DetailLevel } from "../types";
import { t } from "../i18n";
import { SettingLabel, helpRow } from "../ui/SettingLabel";

const STYLES: ConversationStyle[] = ["direct", "warm", "calm_guide", "lively", "technical"];
const DETAILS: DetailLevel[] = ["concise", "balanced", "detailed"];

const STYLE_LABELS: Record<ConversationStyle, string> = {
  direct: "settings.styleDirect",
  warm: "settings.styleWarm",
  calm_guide: "settings.styleCalmGuide",
  lively: "settings.styleLively",
  technical: "settings.styleTechnical",
};

const STYLE_HELP: Record<ConversationStyle, string> = {
  direct: "settings.styleDirectHelp",
  warm: "settings.styleWarmHelp",
  calm_guide: "settings.styleCalmGuideHelp",
  lively: "settings.styleLivelyHelp",
  technical: "settings.styleTechnicalHelp",
};

const DETAIL_LABELS: Record<DetailLevel, string> = {
  concise: "settings.detailConcise",
  balanced: "settings.detailBalanced",
  detailed: "settings.detailDetailed",
};

const DETAIL_HELP: Record<DetailLevel, string> = {
  concise: "settings.detailConciseHelp",
  balanced: "settings.detailBalancedHelp",
  detailed: "settings.detailDetailedHelp",
};

export function ConversationBehaviorControls({
  surface,
  style,
  detail,
  homeFocused,
  saving,
  onStyleChange,
  onDetailChange,
  onHomeFocusedChange,
  aiTaskFreeTextOnly = false,
}: {
  surface: string;
  style: ConversationStyle;
  detail: DetailLevel;
  homeFocused?: boolean;
  saving: boolean;
  onStyleChange: (value: ConversationStyle) => void;
  onDetailChange: (value: DetailLevel) => void;
  onHomeFocusedChange?: (value: boolean) => void;
  aiTaskFreeTextOnly?: boolean;
}) {
  const sep = t("common.sentenceSeparator");
  // Each row says what it sets, then what the chosen option does, named as the select names it.
  const styleHelp = t("settings.conversationStyleHelp") + sep + t("settings.selectedOptionHelp", { option: t(STYLE_LABELS[style]), description: t(STYLE_HELP[style]) });
  const detailHelp = t("settings.detailLevelHelp") + sep + t("settings.selectedOptionHelp", { option: t(DETAIL_LABELS[detail]), description: t(DETAIL_HELP[detail]) })
    + (aiTaskFreeTextOnly ? `${sep}${t("settings.aiTaskBehaviorFreeTextOnly")}` : "");
  return (
    <>
      <hr className="settings-divider" />
      <div className="toggle-row toggle-row-stacked-control" {...helpRow(styleHelp)}>
        <SettingLabel help={styleHelp}><span>{t("settings.conversationStyle")}</span></SettingLabel>
        <BusySelect
          className="input input-auto"
          value={style}
          busy={saving}
          aria-label={t("settings.conversationStyleAria", { surface })}
          onChange={(event) => onStyleChange(event.target.value as ConversationStyle)}
        >
          {STYLES.map((value) => (
            <option key={value} value={value}>{t(STYLE_LABELS[value])}</option>
          ))}
        </BusySelect>
      </div>
      <div className="toggle-row toggle-row-stacked-control" {...helpRow(detailHelp)}>
        <SettingLabel help={detailHelp}><span>{t("settings.detailLevel")}</span></SettingLabel>
        <BusySelect
          className="input input-auto"
          value={detail}
          busy={saving}
          aria-label={t("settings.detailLevelAria", { surface })}
          onChange={(event) => onDetailChange(event.target.value as DetailLevel)}
        >
          {DETAILS.map((value) => (
            <option key={value} value={value}>{t(DETAIL_LABELS[value])}</option>
          ))}
        </BusySelect>
      </div>
      {homeFocused !== undefined && onHomeFocusedChange && (
        <div className="toggle-row" {...helpRow(t("settings.homeFocusedHelp"))}>
          <SettingLabel help={t("settings.homeFocusedHelp")}><span>{t("settings.homeFocused")}</span></SettingLabel>
          <label className={`toggle-switch${saving ? " disabled" : ""}`}>
            <BusyInput
              type="checkbox"
              checked={homeFocused}
              busy={saving}
              aria-label={t("settings.homeFocusedAria", { surface })}
              onChange={(event) => onHomeFocusedChange(event.target.checked)}
            />
            <span className="toggle-switch-track" />
          </label>
        </div>
      )}
    </>
  );
}
