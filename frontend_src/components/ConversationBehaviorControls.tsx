import { BusyInput, BusySelect } from "./BusyButton";
import type { ConversationStyle, DetailLevel } from "../types";
import { t } from "../i18n";
import { compareStrings } from "../i18n/format";
import { SettingLabel, helpRow } from "../ui/SettingLabel";

const STYLES: ConversationStyle[] = ["direct", "warm", "calm_guide", "lively", "technical", "explanatory"];
/** Character voices that work only in English replies; another language gets Direct. */
const ENGLISH_ONLY_STYLES: ConversationStyle[] = [
  "sarcastic", "caveman", "pirate", "butler", "shakespearean", "drama_queen",
  "grumpy_teenager", "nature_documentary", "starship_computer", "noir_detective",
];
const DETAILS: DetailLevel[] = ["concise", "balanced", "detailed"];

const STYLE_LABELS: Record<ConversationStyle, string> = {
  direct: "settings.styleDirect",
  warm: "settings.styleWarm",
  calm_guide: "settings.styleCalmGuide",
  lively: "settings.styleLively",
  technical: "settings.styleTechnical",
  explanatory: "settings.styleExplanatory",
  sarcastic: "settings.styleSarcastic",
  caveman: "settings.styleCaveman",
  pirate: "settings.stylePirate",
  butler: "settings.styleButler",
  shakespearean: "settings.styleShakespearean",
  drama_queen: "settings.styleDramaQueen",
  grumpy_teenager: "settings.styleGrumpyTeenager",
  nature_documentary: "settings.styleNatureDocumentary",
  starship_computer: "settings.styleStarshipComputer",
  noir_detective: "settings.styleNoirDetective",
};

const STYLE_HELP: Record<ConversationStyle, string> = {
  direct: "settings.styleDirectHelp",
  warm: "settings.styleWarmHelp",
  calm_guide: "settings.styleCalmGuideHelp",
  lively: "settings.styleLivelyHelp",
  technical: "settings.styleTechnicalHelp",
  explanatory: "settings.styleExplanatoryHelp",
  sarcastic: "settings.styleSarcasticHelp",
  caveman: "settings.styleCavemanHelp",
  pirate: "settings.stylePirateHelp",
  butler: "settings.styleButlerHelp",
  shakespearean: "settings.styleShakespeareanHelp",
  drama_queen: "settings.styleDramaQueenHelp",
  grumpy_teenager: "settings.styleGrumpyTeenagerHelp",
  nature_documentary: "settings.styleNatureDocumentaryHelp",
  starship_computer: "settings.styleStarshipComputerHelp",
  noir_detective: "settings.styleNoirDetectiveHelp",
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

/** Styles in the alphabetical order of their labels as the reader sees them. */
function byLabel(styles: ConversationStyle[]): ConversationStyle[] {
  return [...styles].sort((a, b) => compareStrings(t(STYLE_LABELS[a]), t(STYLE_LABELS[b])));
}

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
          {byLabel(STYLES).map((value) => (
            <option key={value} value={value}>{t(STYLE_LABELS[value])}</option>
          ))}
          <optgroup label={t("settings.styleEnglishOnly")}>
            {byLabel(ENGLISH_ONLY_STYLES).map((value) => (
              <option key={value} value={value}>{t(STYLE_LABELS[value])}</option>
            ))}
          </optgroup>
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
