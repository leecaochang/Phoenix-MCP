import React, { useContext } from "react";
import type { CapMode, CapName, CapTier, AccessKeyRecord, PatchAccessKeyBody } from "../types";
import { api } from "../api";
import { t } from "../i18n";
import { tRich } from "../i18n/rich";
import { DisplayModeContext } from "../ui/ActionButton";
import { Icon } from "../ui/Icon";
import { capabilityGuide } from "../guide/targets";

export interface EsphomeAvailability {
  integration: boolean;
  builder: boolean;
}

interface Props {
  accessKey: AccessKeyRecord;
  onUpdate: (updated: AccessKeyRecord) => void;
  // What ESPHome surfaces this system has, when known. The row stays settable
  // either way so an access key can be prepared before ESPHome is installed; this only
  // explains why the tools are not showing up yet.
  esphome?: EsphomeAvailability | null;
}

function esphomeNotice(esphome: EsphomeAvailability | null | undefined): string | null {
  if (!esphome || esphome.builder) return null;
  return esphome.integration
    ? t("caps.noBuilder")
    : t("caps.noEsphome");
}

interface CapDef {
  key: CapName;
  labelKey: string;
  descriptionKey: string;
  tier: CapTier;
  confirmAvailable: boolean;
  /** False for a capability that always asks for approval. */
  allowAvailable?: boolean;
  /** Its description warns of a risk; in icons-only mode, where descriptions are hidden, the row is marked. */
  warns?: boolean;
}

const CAPS: CapDef[] = [
  {
    key: "cap_config_read",
    labelKey: "caps.cap_config_read.label",
    descriptionKey: "caps.cap_config_read.description",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_camera_read",
    labelKey: "caps.cap_camera_read.label",
    descriptionKey: "caps.cap_camera_read.description",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_template_render",
    labelKey: "caps.cap_template_render.label",
    descriptionKey: "caps.cap_template_render.description",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_log_read",
    labelKey: "caps.cap_log_read.label",
    descriptionKey: "caps.cap_log_read.description",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_search",
    labelKey: "caps.cap_search.label",
    descriptionKey: "caps.cap_search.description",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_registry_read",
    labelKey: "caps.cap_registry_read.label",
    descriptionKey: "caps.cap_registry_read.description",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_traces",
    labelKey: "caps.cap_traces.label",
    descriptionKey: "caps.cap_traces.description",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_diagnostics",
    labelKey: "caps.cap_diagnostics.label",
    descriptionKey: "caps.cap_diagnostics.description",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_broadcast",
    labelKey: "caps.cap_broadcast.label",
    descriptionKey: "caps.cap_broadcast.description",
    tier: "everyday",
    confirmAvailable: false,
  },
  {
    key: "cap_service_response",
    labelKey: "caps.cap_service_response.label",
    descriptionKey: "caps.cap_service_response.description",
    tier: "everyday",
    confirmAvailable: false,
  },
  {
    key: "cap_automation_write",
    labelKey: "caps.cap_automation_write.label",
    descriptionKey: "caps.cap_automation_write.description",
    tier: "config_write",
    confirmAvailable: true,
    warns: true,
  },
  {
    key: "cap_script_write",
    labelKey: "caps.cap_script_write.label",
    descriptionKey: "caps.cap_script_write.description",
    tier: "config_write",
    confirmAvailable: true,
    warns: true,
  },
  {
    key: "cap_blueprint_write",
    labelKey: "caps.cap_blueprint_write.label",
    descriptionKey: "caps.cap_blueprint_write.description",
    tier: "config_write",
    confirmAvailable: true,
  },
  {
    key: "cap_scene_write",
    labelKey: "caps.cap_scene_write.label",
    descriptionKey: "caps.cap_scene_write.description",
    tier: "config_write",
    confirmAvailable: true,
  },
  {
    key: "cap_helper_write",
    labelKey: "caps.cap_helper_write.label",
    descriptionKey: "caps.cap_helper_write.description",
    tier: "config_write",
    confirmAvailable: true,
  },
  {
    key: "cap_calendar_write",
    labelKey: "caps.cap_calendar_write.label",
    descriptionKey: "caps.cap_calendar_write.description",
    tier: "config_write",
    confirmAvailable: true,
  },
  {
    key: "cap_physical_control",
    labelKey: "caps.cap_physical_control.label",
    descriptionKey: "caps.cap_physical_control.description",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_notify",
    labelKey: "caps.cap_notify.label",
    descriptionKey: "caps.cap_notify.description",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_restart",
    labelKey: "caps.cap_restart.label",
    descriptionKey: "caps.cap_restart.description",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_log_control",
    labelKey: "caps.cap_log_control.label",
    descriptionKey: "caps.cap_log_control.description",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_integration_write",
    labelKey: "caps.cap_integration_write.label",
    descriptionKey: "caps.cap_integration_write.description",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_integration_reconfigure",
    labelKey: "caps.cap_integration_reconfigure.label",
    descriptionKey: "caps.cap_integration_reconfigure.description",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_lovelace_write",
    labelKey: "caps.cap_lovelace_write.label",
    descriptionKey: "caps.cap_lovelace_write.description",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_registry_write",
    labelKey: "caps.cap_registry_write.label",
    descriptionKey: "caps.cap_registry_write.description",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_radio_write",
    labelKey: "caps.cap_radio_write.label",
    descriptionKey: "caps.cap_radio_write.description",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_energy_write",
    labelKey: "caps.cap_energy_write.label",
    descriptionKey: "caps.cap_energy_write.description",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_backup",
    labelKey: "caps.cap_backup.label",
    descriptionKey: "caps.cap_backup.description",
    tier: "irreversible",
    confirmAvailable: true,
  },
  {
    key: "cap_filesystem",
    labelKey: "caps.cap_filesystem.label",
    descriptionKey: "caps.cap_filesystem.description",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_dashboard_resources",
    labelKey: "caps.cap_dashboard_resources.label",
    descriptionKey: "caps.cap_dashboard_resources.description",
    tier: "irreversible",
    confirmAvailable: true,
    allowAvailable: false,
  },
  {
    key: "cap_esphome_yaml",
    labelKey: "caps.cap_esphome_yaml.label",
    descriptionKey: "caps.cap_esphome_yaml.description",
    tier: "everyday",
    confirmAvailable: false,
  },
  {
    key: "cap_esphome_flash",
    labelKey: "caps.cap_esphome_flash.label",
    descriptionKey: "caps.cap_esphome_flash.description",
    tier: "system",
    confirmAvailable: true,
    warns: true,
  },
  {
    key: "cap_yaml_edit",
    labelKey: "caps.cap_yaml_edit.label",
    descriptionKey: "caps.cap_yaml_edit.description",
    tier: "system",
    confirmAvailable: true,
    warns: true,
  },
];

// All capability keys, in matrix order. Exported so other views (e.g. the
// Access key detail summary) can tally allow/confirm/deny without re-listing caps.
export const CAP_NAMES: CapName[] = CAPS.map((c) => c.key);

const TIER_LABEL_KEYS: Record<CapTier, string> = {
  read: "caps.tierRead",
  everyday: "caps.tierEveryday",
  config_write: "caps.tierConfigWrite",
  system: "caps.tierSystem",
  irreversible: "caps.tierIrreversible",
};

const TIER_ORDER: CapTier[] = ["read", "everyday", "config_write", "system", "irreversible"];

const MODE_LABEL_KEYS: Record<CapMode, string> = {
  deny: "caps.modeDeny",
  allow: "caps.modeAllow",
  confirm: "caps.modeConfirm",
};

/** A capability's name and a mode's label as the matrix shows them, for lists that describe a change. */
export function capLabel(cap: CapName): string {
  const def = CAPS.find((c) => c.key === cap);
  return def ? t(def.labelKey) : cap;
}

export function capModeLabel(mode: string): string {
  return mode in MODE_LABEL_KEYS ? t(MODE_LABEL_KEYS[mode as CapMode]) : mode;
}

const MODE_DESC_KEYS: Record<CapMode, string> = {
  deny: "caps.modeDescDeny",
  allow: "caps.modeDescAllow",
  confirm: "caps.modeDescConfirm",
};

export function CapabilityMatrix({ accessKey, onUpdate, esphome }: Props) {
  // The rows saving now. The API queues one key's changes in order, so a click on
  // another row is saved after it rather than dropped.
  const [saving, setSaving] = React.useState<ReadonlySet<CapName>>(() => new Set());
  const [error, setError] = React.useState<string | null>(null);
  // Descriptions show in icons-and-text mode; in icons-only mode they are in Help.
  const iconsOnly = useContext(DisplayModeContext) === "icons";

  async function setMode(cap: CapName, mode: CapMode) {
    if (saving.has(cap) || accessKey[cap] === mode) return;
    setSaving((prev) => new Set(prev).add(cap));
    setError(null);
    try {
      const body = { [cap]: mode } as unknown as PatchAccessKeyBody;
      const updated = await api.patchAccessKey(accessKey.id, body);
      onUpdate(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("caps.updateFailed"));
    } finally {
      setSaving((prev) => { const next = new Set(prev); next.delete(cap); return next; });
    }
  }

  // Group caps by tier and preserve order.
  const grouped: Record<CapTier, CapDef[]> = {
    read: [],
    everyday: [],
    config_write: [],
    system: [],
    irreversible: [],
  };
  for (const def of CAPS) grouped[def.tier].push(def);

  return (
    <div className="capability-matrix">
      {error && <div role="alert" className="banner banner-error mb-8">{error}</div>}
      {accessKey.pass_through && (
        <div className="amber-block mb-8" data-guide="topic.passThrough">
          <p>
            {tRich("caps.passThroughNotice", { strong: (c) => <strong>{c}</strong> })}
          </p>
        </div>
      )}
      {TIER_ORDER.map((tier) => {
        const items = grouped[tier];
        if (items.length === 0) return null;
        return (
          <div key={tier} className="cap-tier-group">
            <div className="cap-tier-header">{t(TIER_LABEL_KEYS[tier])}</div>
            {items.map((cap) => {
              const current = accessKey[cap.key];
              const isSaving = saving.has(cap.key);
              const notice = cap.key === "cap_esphome_yaml" || cap.key === "cap_esphome_flash"
                ? esphomeNotice(esphome) : null;
              return (
                <div key={cap.key} className="cap-row" data-guide={capabilityGuide(cap.key)}>
                  <div className="cap-row-label" data-help={cap.descriptionKey}>
                    <div className="cap-row-name">
                      {t(cap.labelKey)}
                      {iconsOnly && cap.warns && <Icon name="warning" size={14} className="cap-row-warning" />}
                      {notice && (
                        <span className="phx-outcome cap-row-notice" title={t("caps.esphomeBadgeTitle")}>
                          <Icon name="info" size={14} />
                          {notice}
                        </span>
                      )}
                    </div>
                    {iconsOnly
                      ? <span className="sr-only">{t(cap.descriptionKey)}</span>
                      : <div className="cap-row-desc">{t(cap.descriptionKey)}</div>}
                  </div>
                  <div className="cap-row-modes" role="group" aria-label={t(cap.labelKey)} aria-busy={isSaving}>
                    <ModeRadio
                      cap={cap.key}
                      mode="deny"
                      current={current}
                      onSelect={setMode}
                      disabled={false}
                      busy={isSaving}
                    />
                    <ModeRadio
                      cap={cap.key}
                      mode="allow"
                      current={current}
                      onSelect={setMode}
                      disabled={cap.allowAvailable === false}
                      busy={isSaving}
                      unavailable={cap.allowAvailable === false}
                    />
                    <ModeRadio
                      cap={cap.key}
                      mode="confirm"
                      current={current}
                      onSelect={setMode}
                      disabled={!cap.confirmAvailable}
                      busy={isSaving}
                      unavailable={!cap.confirmAvailable}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

interface ModeRadioProps {
  cap: CapName;
  mode: CapMode;
  current: CapMode;
  onSelect: (cap: CapName, mode: CapMode) => void;
  disabled: boolean;
  busy: boolean;
  unavailable?: boolean;
}

function ModeRadio({ cap, mode, current, onSelect, disabled, busy, unavailable }: ModeRadioProps) {
  const checked = current === mode;
  const id = `${cap}-${mode}`;
  const unavailableKey = mode === "allow" ? "caps.allowUnavailable" : "caps.confirmUnavailable";
  return (
    <button
      type="button"
      id={id}
      disabled={disabled}
      aria-disabled={busy || disabled}
      aria-pressed={checked}
      onClick={() => { if (!busy && !disabled) onSelect(cap, mode); }}
      className={`mode-radio mode-${mode}${checked ? " mode-radio-checked" : ""}${unavailable ? " mode-radio-unavailable" : ""}`}
      title={unavailable ? t(unavailableKey) : t(MODE_DESC_KEYS[mode])}
      data-help={unavailable ? unavailableKey : MODE_DESC_KEYS[mode]}
    >
      <span className="mode-radio-dot" />
      <span className="mode-radio-label">{t(MODE_LABEL_KEYS[mode])}</span>
    </button>
  );
}
