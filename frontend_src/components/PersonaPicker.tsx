import { BusyButton } from "./BusyButton";
import React, { useContext } from "react";
import type { Persona, AccessKeyRecord } from "../types";
import { api } from "../api";
import { PERSONAS, PERSONA_CAP_DEFAULTS } from "../personas";
import { CAP_NAMES, type EsphomeAvailability } from "./CapabilityMatrix";
import { t } from "../i18n";
import { Icon } from "../ui/Icon";
import { DisplayModeContext } from "../ui/ActionButton";
import { personaGuide } from "../guide/targets";
import { useNotify } from "../ui/notify";

// Patch mode: applies the persona to an existing access key (Access key detail).
// Select mode: no access key yet (onboarding wizard); reports the chosen persona to
// the parent, which applies it after the access key is created.
interface PatchProps {
  accessKey: AccessKeyRecord;
  onUpdate: (updated: AccessKeyRecord) => void;
  selected?: undefined;
  onSelect?: undefined;
  // Only the Access key detail view has settings in hand. The wizard leaves this
  // undefined, so no note renders there.
  esphome?: EsphomeAvailability | null;
}
interface SelectProps {
  accessKey?: undefined;
  onUpdate?: undefined;
  selected: Persona | null;
  onSelect: (persona: Persona) => void;
}
type Props = PatchProps | SelectProps;

// Each key's Custom capabilities from before another persona was chosen, so choosing Custom
// again brings them back. Kept while the panel stays open.
const customCaps = new Map<string, Record<string, string>>();

export function PersonaPicker(props: Props) {
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const notify = useNotify();

  const isSelectMode = props.onSelect !== undefined;
  // In icons-only mode the Persona window shows each persona's name alone; its description is in Help.
  // The wizard keeps the descriptions, since they are how a first persona is chosen.
  const iconsOnly = useContext(DisplayModeContext) === "icons" && !isSelectMode;
  const esphome = isSelectMode ? null : props.esphome;
  const activeKey = isSelectMode ? props.selected : props.accessKey.persona;

  async function choose(persona: Persona) {
    if (isSelectMode) {
      props.onSelect(persona);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      // Cap defaults come from a hardcoded mirror of personas.py so all caps
      // patch in one request. Backend validation stays the source of truth.
      const key = props.accessKey;
      const caps = persona === "custom" ? customCaps.get(key.id) : PERSONA_CAP_DEFAULTS[persona];
      const body: Record<string, unknown> = { persona };
      if (caps) {
        Object.assign(body, caps);
      }
      const updated = await api.patchAccessKey(key.id, body);
      if (key.persona === "custom" && persona !== "custom") customCaps.set(key.id, Object.fromEntries(CAP_NAMES.map((c) => [c, key[c]])));
      props.onUpdate(updated);
      // Home Companion turns both off on the server; say so rather than let them vanish.
      if ((key.pass_through && !updated.pass_through) || (key.announce_all_tools && !updated.announce_all_tools)) {
        notify(t("personas.home_companion.turnedOff", { name: key.name }));
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.personaApplyFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="persona-picker" data-help={isSelectMode ? undefined : "help.persona"}>
      {error && <div role="alert" className="banner banner-error mb-8">{error}</div>}
      {/* Home Companion needs setting up elsewhere: that stays in view while it is chosen. */}
      {!isSelectMode && activeKey === "home_companion" && (
        <p className="persona-helper">{t("personas.home_companion.setup")}</p>
      )}
      <div className={`persona-grid${iconsOnly ? " is-compact" : ""}`}>
        {PERSONAS.filter((p) =>
          isSelectMode
            ? p.key !== "custom"   // wizard: hide "Custom" (no matrix to configure)
            : !p.wizardOnly,       // access key detail: hide wizard-only presets
        ).map((p) => {
          const active = activeKey === p.key;
          return (
            <BusyButton
              key={p.key}
              type="button"
              className={`persona-card${active ? " persona-card-active" : ""}`}
              data-persona={p.key}
              onClick={() => !saving && !active && choose(p.key)}
              busy={saving}
              aria-pressed={active}
              data-help={p.descriptionKey}
              data-guide={isSelectMode ? undefined : personaGuide(p.key)}
            >
              <div className="persona-card-label">
                {t(p.labelKey)}
                {p.key === "home_companion" && (
                  <Icon name="star" className="persona-card-marker" filled />
                )}
              </div>
              {iconsOnly
                ? <span className="sr-only">{t(p.descriptionKey)}</span>
                : <div className="persona-card-desc">{t(p.descriptionKey)}</div>}
              {p.key === "esphome" && esphome && !esphome.integration && !esphome.builder && (
                <div className="persona-card-note">{t("personas.noEsphome")}</div>
              )}
            </BusyButton>
          );
        })}
      </div>
    </div>
  );
}
