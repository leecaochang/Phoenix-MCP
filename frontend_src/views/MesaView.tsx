/**
 * MESA profile editing shared across the panel: the profile editor used by the
 * MESA workspace, Home Assistant's injected [+]/MESA buttons and the Access
 * Keys tree, the per-scope storage endpoints, the list helpers, and export
 * and import of the whole policy.
 */

import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type {
  EntityTree as EntityTreeData,
  MesaExportArchive,
  MesaImportResult,
  MesaProfileDetail,
  MesaProfileDocument,
  MesaProfileScope,
  MesaValidationIssue,
} from "../types";
import { api, ApiError } from "../api";
import { Modal } from "../components/Modal";
import { TagInput } from "../components/TagInput";
import { Loading, ErrorMsg } from "../components/common";
import { compareStrings, t, tn } from "../i18n";
import { ActionButton } from "../ui/ActionButton";
import { useNotify } from "../ui/notify";
import { InlineConfirm } from "../ui/InlineConfirm";
import { helpRow } from "../ui/SettingLabel";
// A cycle (shell/mesa reads exportMesaArchive), safe because neither module uses the other at load.
import { useMesa } from "../shell/mesa";

// Maps an HA domain to the canonical tag namespace roots that describe an ENTITY
// of that domain, used to surface "Suggested" tags. Intent namespaces
// (automation, scene) describe automations/scenes, not entities, so they are
// only suggested for those domains. Falls back to a general set when unmapped.
const DOMAIN_TAG_ROOTS: Record<string, string[]> = {
  light: ["lighting"],
  switch: ["energy", "resource"],
  climate: ["climate"],
  fan: ["climate"],
  cover: ["security"],
  lock: ["security"],
  alarm_control_panel: ["security"],
  camera: ["security", "diagnostic"],
  binary_sensor: ["presence", "security"],
  sensor: ["energy", "diagnostic", "presence"],
  media_player: ["media", "audio"],
  person: ["person", "presence"],
  device_tracker: ["presence", "person"],
  scene: ["scene"],
  automation: ["automation"],
  script: ["automation"],
  vacuum: ["resource"],
  number: ["helper"],
  select: ["helper"],
  input_boolean: ["helper"],
  input_number: ["helper"],
  input_select: ["helper"],
  input_text: ["helper"],
  input_datetime: ["helper"],
};
const FALLBACK_TAG_ROOTS = ["space", "zone", "diagnostic"];

// Option value plus a human-readable label. The stored value is always the slug
// (what mesa-core expects); the label is only for display.
type Opt = { value: string; label: string };
// A fixed option set instead carries the catalog key for its label.
type LabeledOpt = { value: string; labelKey: string };

// "" leaves control_mode undeclared, so the profile makes no control decision
// and the inherited or built-in mode applies (Spec 5.7 Rule E).
const CONTROL_MODES: LabeledOpt[] = [
  { value: "", labelKey: "mesa.unset" },
  { value: "autonomous", labelKey: "mesa.modeAutonomous" },
  { value: "confirm", labelKey: "mesa.modeConfirmOption" },
  { value: "read_only", labelKey: "mesa.modeReadOnly" },
  { value: "prohibited", labelKey: "mesa.modeProhibited" },
];
const TRIGGERS: LabeledOpt[] = [
  { value: "unknown", labelKey: "mesa.trigUnknown" },
  { value: "none", labelKey: "mesa.trigNone" },
  { value: "likely", labelKey: "mesa.trigLikely" },
  { value: "deployment_defined", labelKey: "mesa.trigDeploymentDefined" },
];
const REVERSIBILITY_COSTS: LabeledOpt[] = [
  { value: "", labelKey: "mesa.unset" },
  { value: "none", labelKey: "mesa.costNone" },
  { value: "trivial", labelKey: "mesa.costTrivial" },
  { value: "moderate", labelKey: "mesa.costModerate" },
  { value: "high", labelKey: "mesa.costHigh" },
];
const SCOPES: LabeledOpt[] = [
  { value: "", labelKey: "mesa.unset" },
  { value: "entity_only", labelKey: "mesa.scopeEntityOnly" },
  { value: "device_localized", labelKey: "mesa.scopeDeviceLocalized" },
  { value: "room_localized", labelKey: "mesa.scopeRoomLocalized" },
  { value: "zone_wide", labelKey: "mesa.scopeZoneWide" },
  { value: "deployment_wide", labelKey: "mesa.scopeDeploymentWide" },
];
const PRIVACY_LEVELS: LabeledOpt[] = [
  { value: "public", labelKey: "mesa.privPublic" },
  { value: "normal", labelKey: "mesa.privNormal" },
  { value: "sensitive", labelKey: "mesa.privSensitive" },
  { value: "restricted", labelKey: "mesa.privRestricted" },
];
const REVERSIBLE: LabeledOpt[] = [
  { value: "", labelKey: "mesa.unset" },
  { value: "true", labelKey: "mesa.yes" },
  { value: "false", labelKey: "mesa.no" },
];
const ENFORCEMENT_MODES: LabeledOpt[] = [
  { value: "advisory", labelKey: "mesa.modeAdvisory" },
  { value: "enforced", labelKey: "mesa.modeEnforced" },
];

// Scope help is keyed by the scope union so a new level cannot be forgotten;
// the field help below it is a plain map with no such constraint.
const SCOPE_HELP: Record<ProfileScope, string> = {
  entity: "mesa.helpEntity",
  device: "mesa.helpDevice",
  area: "mesa.helpArea",
  integration: "mesa.helpIntegration",
  domain: "mesa.helpDomain",
};

const HELP = {
  tags: "mesa.helpTags",
  control_mode: "mesa.helpControlMode",
  enforcement_mode: "mesa.helpEnforcementMode",
  triggers_automations: "mesa.helpTriggersAutomations",
  reversible: "mesa.helpReversible",
  reversibility_cost: "mesa.helpReversibilityCost",
  side_effect_scope: "mesa.helpSideEffectScope",
  privacy_level: "mesa.helpPrivacyLevel",
};

type ProfileScope = MesaProfileScope;

const SCOPE_LABEL: Record<ProfileScope, string> = {
  entity: "mesa.scopeLabelEntity",
  device: "mesa.scopeLabelDevice",
  area: "mesa.scopeLabelArea",
  integration: "mesa.scopeLabelIntegration",
  domain: "mesa.scopeLabelDomain",
};
/** What a profile's key is called, for the line naming the profile the editor holds. */
const SCOPE_ID_LABEL: Record<ProfileScope, string> = {
  entity: "mesa.idEntity",
  device: "mesa.idDevice",
  area: "mesa.idArea",
  integration: "mesa.idIntegration",
  domain: "mesa.idDomain",
};
const SCOPE_PLACEHOLDER: Record<ProfileScope, string> = {
  entity: "mesa.phEntity",
  device: "mesa.phDevice",
  area: "mesa.phArea",
  integration: "mesa.phIntegration",
  domain: "mesa.phDomain",
};

// One whole sentence per scope. A scope noun is never lowercased or slotted
// into a sentence assembled for English word order.
const CHOOSE_VALID: Record<ProfileScope, string> = {
  entity: "mesa.chooseValidEntity",
  device: "mesa.chooseValidDevice",
  area: "mesa.chooseValidArea",
  integration: "mesa.chooseValidIntegration",
  domain: "mesa.chooseValidDomain",
};
const NO_MATCH: Record<ProfileScope, string> = {
  entity: "mesa.noMatchingEntity",
  device: "mesa.noMatchingDevice",
  area: "mesa.noMatchingArea",
  integration: "mesa.noMatchingIntegration",
  domain: "mesa.noMatchingDomain",
};
const ADD_TITLE: Record<ProfileScope, string> = {
  entity: "mesa.addEntityProfile",
  device: "mesa.addDeviceProfile",
  area: "mesa.addAreaProfile",
  integration: "mesa.addIntegrationProfile",
  domain: "mesa.addDomainProfile",
};
const EDIT_TITLE: Record<ProfileScope, string> = {
  entity: "mesa.editEntityProfile",
  device: "mesa.editDeviceProfile",
  area: "mesa.editAreaProfile",
  integration: "mesa.editIntegrationProfile",
  domain: "mesa.editDomainProfile",
};

// Deleting a profile un-covers what it applied to, so the confirm, the warning
// and the button all name the level and the target.
export type CascadingScope = Exclude<ProfileScope, "entity">;
const DELETE_CONFIRM: Record<ProfileScope, string> = {
  entity: "mesa.deleteEntityConfirm",
  device: "mesa.deleteDeviceConfirm",
  area: "mesa.deleteAreaConfirm",
  integration: "mesa.deleteIntegrationConfirm",
  domain: "mesa.deleteDomainConfirm",
};
const DELETE_WARN: Record<ProfileScope, string> = {
  entity: "mesa.deleteEntityWarn",
  device: "mesa.deleteDeviceWarn",
  area: "mesa.deleteAreaWarn",
  integration: "mesa.deleteIntegrationWarn",
  domain: "mesa.deleteDomainWarn",
};
const DELETE_BUTTON: Record<ProfileScope, string> = {
  entity: "mesa.deleteEntityProfile",
  device: "mesa.deleteDeviceProfile",
  area: "mesa.deleteAreaProfile",
  integration: "mesa.deleteIntegrationProfile",
  domain: "mesa.deleteDomainProfile",
};

// Which inheritance level resolved a field, for display. mesa-core reports the
// raw level name, and an untranslated one dropped into a sentence reads as
// English inside every other locale.
const LEVEL_LABEL: Record<string, string> = {
  entity: "mesa.scopeLabelEntity",
  device: "mesa.scopeLabelDevice",
  // The main device of an HA child device; its profile applies to the parts.
  parent_device: "mesa.scopeLabelParentDevice",
  area: "mesa.scopeLabelArea",
  integration: "mesa.scopeLabelIntegration",
  domain: "mesa.scopeLabelDomain",
  deployment_default: "mesa.levelDeploymentDefault",
  built_in_baseline: "mesa.levelBuiltInBaseline",
};

/** Where an entity appears in an automation, as mesa-core reports it. */
const MESA_ROLE_KEYS: Readonly<Record<string, string>> = {
  trigger: "mesa.roleTrigger",
  condition: "mesa.roleCondition",
  action: "mesa.roleAction",
};

function levelText(level: string): string {
  const key = LEVEL_LABEL[level];
  return key ? t(key) : level;
}

function tagsOf(doc: MesaProfileDocument | null): string[] {
  const tags = doc?.semantic_profile?.semantic_tags;
  return Array.isArray(tags) ? (tags as string[]) : [];
}

interface EditorState {
  key: string;
  tags: string[];
  control_mode: string;
  enforcement_mode: string;
  triggers_automations: string;
  reversible: string; // "", "true", "false"
  reversibility_cost: string;
  side_effect_scope: string;
  privacy_level: string;
}

function docToEditor(key: string, doc: MesaProfileDocument | null): EditorState {
  const sp = (doc?.semantic_profile ?? {}) as Record<string, unknown>;
  const ob = (sp.operational_boundaries ?? {}) as Record<string, unknown>;
  const pc = (doc?.privacy_classification ?? {}) as Record<string, unknown>;
  const rev = ob.reversible;
  return {
    key,
    tags: tagsOf(doc),
    control_mode: (ob.control_mode as string) ?? "",
    enforcement_mode: (ob.enforcement_mode as string) ?? "advisory",
    triggers_automations: (ob.triggers_automations as string) ?? "unknown",
    reversible: rev === true ? "true" : rev === false ? "false" : "",
    reversibility_cost: (ob.reversibility_cost as string) ?? "",
    side_effect_scope: (ob.side_effect_scope as string) ?? "",
    privacy_level: (pc.level as string) ?? "normal",
  };
}

function editorToDoc(s: EditorState): MesaProfileDocument {
  const ob: Record<string, unknown> = { triggers_automations: s.triggers_automations };
  if (s.control_mode !== "") ob.control_mode = s.control_mode;
  // Omit when advisory (the default) to keep stored docs clean, matching how
  // mesa-core serialises the field.
  if (s.enforcement_mode === "enforced") ob.enforcement_mode = "enforced";
  if (s.reversible !== "") ob.reversible = s.reversible === "true";
  if (s.reversibility_cost !== "") ob.reversibility_cost = s.reversibility_cost;
  if (s.side_effect_scope !== "") ob.side_effect_scope = s.side_effect_scope;
  return {
    semantic_profile: { semantic_tags: s.tags, operational_boundaries: ob },
    privacy_classification: { level: s.privacy_level },
  };
}

// One endpoint trio per scope, as an exhaustive table rather than an if-chain.
// A Record keyed by the scope union makes a scope with no entry a build failure;
// a chain would silently route an unnamed scope to another level's endpoint and
// store the profile against the wrong level.
const SCOPE_API: Record<ProfileScope, {
  load: (key: string) => Promise<MesaProfileDocument | null>;
  save: (key: string, doc: MesaProfileDocument, createOnly?: boolean) => Promise<MesaValidationIssue[]>;
  remove: (key: string) => Promise<void>;
}> = {
  entity: {
    load: async (k) => (await api.getMesaProfile(k)).stored,
    save: async (k, doc, createOnly) => (await api.putMesaProfile(k, doc, createOnly)).warnings,
    remove: async (k) => { await api.deleteMesaProfile(k); },
  },
  device: {
    load: async (k) => (await api.getMesaDevice(k)).stored,
    save: async (k, doc, createOnly) => { await api.putMesaDevice(k, doc, createOnly); return []; },
    remove: async (k) => { await api.deleteMesaDevice(k); },
  },
  area: {
    load: async (k) => (await api.getMesaArea(k)).stored,
    save: async (k, doc, createOnly) => { await api.putMesaArea(k, doc, createOnly); return []; },
    remove: async (k) => { await api.deleteMesaArea(k); },
  },
  integration: {
    load: async (k) => (await api.getMesaIntegration(k)).stored,
    save: async (k, doc, createOnly) => { await api.putMesaIntegration(k, doc, createOnly); return []; },
    remove: async (k) => { await api.deleteMesaIntegration(k); },
  },
  domain: {
    load: async (k) => (await api.getMesaDomain(k)).stored,
    save: async (k, doc, createOnly) => { await api.putMesaDomain(k, doc, createOnly); return []; },
    remove: async (k) => { await api.deleteMesaDomain(k); },
  },
};

// Derived from the dispatch table rather than restated, so the two cannot
// disagree. Pinned against the backend's canonical list by a contract test.
export const EDITOR_SCOPES = Object.keys(SCOPE_API) as ProfileScope[];

/** The catalog key naming each scope, such as "Entity". */
export const SCOPE_LABEL_KEYS: Readonly<Record<ProfileScope, string>> = SCOPE_LABEL;
/** The catalog key for adding a profile of each scope. */
export const ADD_TITLE_KEYS: Readonly<Record<ProfileScope, string>> = ADD_TITLE;

// The levels that cascade to many entities, in the order the list renders
// them. Entity profiles are absent: they render grouped by domain instead.
export const CASCADING_SCOPES: CascadingScope[] = ["device", "area", "integration", "domain"];

export function loadProfile(scope: ProfileScope, key: string): Promise<MesaProfileDocument | null> {
  return SCOPE_API[scope].load(key);
}

export function saveProfile(scope: ProfileScope, key: string, doc: MesaProfileDocument, createOnly = false): Promise<MesaValidationIssue[]> {
  return SCOPE_API[scope].save(key, doc, createOnly);
}

export function deleteProfile(scope: ProfileScope, key: string): Promise<void> {
  return SCOPE_API[scope].remove(key);
}

/**
 * A field's name. Its explanation is no longer a "?" badge beside it: it reads
 * in the Help window while the label or the field has the pointer or the
 * keyboard (user, 2026-10-30).
 */
function FieldLabel({ id, text, help }: { id?: string; text: string; help: string }) {
  return (
    <label htmlFor={id} className="mesa-field-label" data-help-text={help}>
      {text}
    </label>
  );
}

// A select rendered with friendly labels but storing slug values, full width so
// every control lines up on the grid.
function SelectField({
  id, label, helpKey, guide, value, options, onChange,
}: { id: string; label: string; helpKey: string; guide: string; value: string; options: LabeledOpt[]; onChange: (v: string) => void }) {
  return (
    <div className="field" data-guide={guide}>
      <FieldLabel id={id} text={label} help={t(helpKey)} />
      {/* The field's explanation also shows in the Help window while it has the pointer or focus. */}
      <select id={id} className="input" value={value} data-help={helpKey} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o.value} value={o.value}>{t(o.labelKey)}</option>)}
      </select>
    </div>
  );
}

// Fuzzy combobox over a fixed option set. Selecting an option sets `value` to
// the option's slug; free typing is allowed but the parent validates exactness.
function Combo({
  id, value, options, placeholder, invalid, onChange,
}: {
  id: string;
  value: string;
  options: Opt[];
  placeholder?: string;
  invalid?: boolean;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  // What to SHOW for a stored value. The parent stores the option's slug, and
  // for a device that slug is an opaque 32-character registry id, so echoing it
  // back into the input after a pick would replace the name the admin just chose
  // with something they cannot read. Falls back to the value itself, which is
  // right for free-typed text and for a slug no longer in the options.
  const labelFor = useCallback(
    (v: string) => options.find((o) => o.value === v)?.label ?? v,
    [options],
  );
  const [query, setQuery] = useState(() => labelFor(value));
  const [active, setActive] = useState(0);
  const listboxId = useId();
  useEffect(() => { setQuery(labelFor(value)); }, [value, labelFor]);

  const matches = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const pool = terms.length === 0
      ? options
      : options.filter((o) => {
          const hay = `${o.value} ${o.label}`.toLowerCase();
          return terms.every((t) => hay.includes(t));
        });
    return pool.slice(0, 10);
  }, [query, options]);

  function pick(v: string) {
    onChange(v);
    setQuery(labelFor(v));
    setActive(0);
    setOpen(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, Math.max(matches.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Home" && open) {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End" && open) {
      e.preventDefault();
      setActive(Math.max(matches.length - 1, 0));
    } else if (e.key === "Enter" && open && matches.length > 0) {
      e.preventDefault();
      pick(matches[Math.min(active, matches.length - 1)].value);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="combo">
      <input
        id={id}
        className={`input${invalid ? " error" : ""}`}
        value={query}
        placeholder={placeholder}
        autoComplete="off"
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        aria-controls={matches.length > 0 ? listboxId : undefined}
        aria-activedescendant={open && matches.length > 0 ? `${listboxId}-option-${Math.min(active, matches.length - 1)}` : undefined}
        onChange={(e) => { setQuery(e.target.value); onChange(e.target.value); setOpen(true); setActive(0); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={handleKeyDown}
      />
      {open && matches.length > 0 && (
        <ul className="combo-list" role="listbox" id={listboxId}>
          {matches.map((o, i) => (
            <li
              key={o.value}
              className="combo-option"
              id={`${listboxId}-option-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => { e.preventDefault(); pick(o.value); }}
              onMouseEnter={() => setActive(i)}
            >
              <span className="combo-option-label">{o.label}</span>
              {o.label !== o.value && <code className="combo-option-sub">{o.value}</code>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Effective (inherited) control + enforcement mode from a resolved profile detail,
// preferring the explanation's resolved value, then the effective doc, then the
// mesa-core defaults. Drives the "Effective"/"Currently" panel and the pre-fill of
// a new entity profile.
function effectiveModes(detail: MesaProfileDetail): { control_mode: string; enforcement_mode: string; cmLevel?: string; enLevel?: string } {
  const exps = detail.explanation?.explanation ?? [];
  const find = (suffix: string) => exps.find((e) => e.field_path.endsWith(suffix));
  const ob = (detail.effective?.semantic_profile as { operational_boundaries?: Record<string, unknown> } | undefined)?.operational_boundaries ?? {};
  const cm = find("control_mode");
  const en = find("enforcement_mode");
  return {
    control_mode: String(cm?.effective_value ?? ob.control_mode ?? "autonomous"),
    enforcement_mode: String(en?.effective_value ?? ob.enforcement_mode ?? "advisory"),
    cmLevel: cm?.provided_by_level,
    enLevel: en?.provided_by_level,
  };
}

/**
 * The MESA profile editor, shared by every caller. As a modal it serves Home
 * Assistant's injected [+]/MESA buttons and the Access Keys tree; as a window
 * it is the MESA workspace's Profile Editor, with its actions above the form.
 */
/**
 * Whether the user left Effective Resolution open. It starts folded on every
 * profile, and stays open for the rest of the session once it is opened
 * (user, 2026-10-30).
 */
let explainLeftOpen = false;

/** Forget the explanation's open state, as Reset This Workspace does (user, 2026-10-31). */
export function resetProfileEditorDisclosures(): void {
  explainLeftOpen = false;
}

export function ProfileEditor({
  scope,
  profileKey,
  isNew,
  entityTree,
  canonicalTags,
  integrationOptions,
  deviceOptions,
  onClose,
  onSaved,
  lockedKey,
  keyLabel,
  seedControlMode,
  seedReason,
  taken,
  variant = "modal",
  onShowSaved,
  notice,
}: {
  scope: ProfileScope;
  profileKey: string | null;
  isNew: boolean;
  entityTree: EntityTreeData | null;
  canonicalTags: string[];
  // Installed integrations (id = component name, name = friendly title) for the
  // integration-scope picker. Only the MESA tab supplies these.
  integrationOptions?: { id: string; name: string }[];
  // Supplied by the MESA tab only, to populate the picker. The in-context
  // injector always passes a lockedKey and so needs no picker source; it
  // supplies keyLabel instead, which is what NAMES the locked target.
  deviceOptions?: { id: string; name: string }[];
  onClose: () => void;
  onSaved: () => void;
  // When true, the target id is fixed (supplied by the caller, e.g. the in-context
  // injector) rather than picked from the registry. Hides the combobox and skips
  // its validation, so entities not in the registry (e.g. "unmanageable" ones) work.
  lockedKey?: boolean;
  // What to CALL profileKey in the title, the locked target field and the
  // delete warning. A device key is an opaque 32-character registry id, so
  // rendering it raw names nothing an operator recognises; every caller that
  // knows the display name passes it here. Absent falls back to the key, which
  // is right for domain scope (its key is its name) and for a profile whose
  // target the registry no longer knows.
  keyLabel?: string;
  // Initial control_mode for a NEW profile, overriding the effective-mode seed.
  // Used by the suggestions Review flow so the editor opens at the suggested mode.
  seedControlMode?: string;
  /** The reason a suggestion gave, recorded with a profile created from it. */
  seedReason?: string;
  /** Keys in this scope that already have a profile; a new profile's picker leaves them out. */
  taken?: ReadonlySet<string>;
  /** A dialog over the page, or the MESA workspace's window. */
  variant?: "modal" | "window";
  /** Window: show the profile just saved, instead of closing. */
  onShowSaved?: (key: string) => void;
  /** Window: whether the form holds unsaved changes, so switching profiles can ask first. */
  /** Window: a message shown above the form, such as a question before switching profiles. */
  notice?: React.ReactNode;
}) {
  // A toast names the profile as the lists do: a device by its name, not its id. Outside the
  // MESA workspace (the in-context editor) there is no name list, and the key is shown.
  const mesaNames = useMesa();
  const profileName = (key: string) => mesaNames?.nameOf(scope, key) || key;
  const initialState = () => {
    const base = docToEditor(profileKey ?? "", null);
    if (isNew && seedControlMode) base.control_mode = seedControlMode;
    return base;
  };
  const [detail, setDetail] = useState<MesaProfileDetail | null>(null);
  const [state, setState] = useState<EditorState>(initialState);
  const [loading, setLoading] = useState(!isNew);
  // An existing profile is only editable once its stored document has loaded:
  // saving the blank form a failed read leaves behind would replace the authored
  // restrictions with defaults.
  const [loaded, setLoaded] = useState(isNew || !profileKey);
  const [loadAttempt, setLoadAttempt] = useState(0);
  // A new entity profile starts from the entity's inherited modes, so it is not
  // saved until that read has succeeded for the chosen entity.
  const [seed, setSeed] = useState<{ key: string; status: "pending" | "done" | "failed" } | null>(null);
  const [seedAttempt, setSeedAttempt] = useState(0);
  // Fields the operator set, so a late seed never replaces a deliberate choice,
  // including one that happens to equal the value it started with.
  const touched = useRef(new Set<keyof EditorState>());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<MesaValidationIssue[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [showReco, setShowReco] = useState(false);
  const notify = useNotify();
  const [explainOpen, setExplainOpen] = useState(explainLeftOpen);
  // Snapshot of the last persisted (or freshly initialised) state, for the
  // unsaved-changes guard.
  const cleanSnapshot = useRef<string>(JSON.stringify(initialState()));
  // Entity key already pre-filled from effective, so a re-render does not re-seed
  // and clobber the user's edits.
  const seededForKey = useRef<string | null>(null);

  useEffect(() => {
    if (isNew || !profileKey) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    // One entity read carries both the stored document and the effective
    // resolution; the other scopes have no effective panel.
    const read: Promise<[MesaProfileDocument | null, MesaProfileDetail | null]> = scope === "entity"
      ? api.getMesaProfile(profileKey).then((d) => [d.stored, d])
      : loadProfile(scope, profileKey).then((stored) => [stored, null]);
    read
      .then(([stored, d]) => {
        if (cancelled) return;
        setDetail(d);
        const next = docToEditor(profileKey, stored);
        setState(next);
        cleanSnapshot.current = JSON.stringify(next);
        setLoaded(true);
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : t("mesa.loadProfileFailed")); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [scope, profileKey, isNew, loadAttempt]);

  function set<K extends keyof EditorState>(key: K, value: EditorState[K]) {
    touched.current.add(key);
    setState((s) => ({ ...s, [key]: value }));
  }

  // Valid keys for the current scope, derived from the live registry.
  const allKeyOptions = useMemo<Opt[]>(() => {
    if (scope === "device") {
      return (deviceOptions ?? [])
        .map((d) => ({ value: d.id, label: d.name }))
        .sort((a, b) => compareStrings(a.label, b.label));
    }
    if (scope === "integration") {
      return (integrationOptions ?? [])
        .map((i) => ({ value: i.id, label: i.name && i.name !== i.id ? `${i.name} (${i.id})` : i.id }))
        .sort((a, b) => compareStrings(a.label, b.label));
    }
    if (!entityTree) return [];
    if (scope === "domain") {
      return Object.keys(entityTree).sort().map((d) => ({ value: d, label: d }));
    }
    if (scope === "area") {
      const seen = new Map<string, string>();
      for (const dt of Object.values(entityTree)) {
        for (const info of Object.values(dt.entity_details)) {
          if (info.area_id && !seen.has(info.area_id)) seen.set(info.area_id, info.area_name || info.area_id);
        }
      }
      return [...seen.entries()].map(([value, label]) => ({ value, label })).sort((a, b) => compareStrings(a.label, b.label));
    }
    const out: Opt[] = [];
    for (const dt of Object.values(entityTree)) {
      for (const [eid, info] of Object.entries(dt.entity_details)) {
        out.push({ value: eid, label: info.friendly_name || eid });
      }
    }
    return out.sort((a, b) => compareStrings(a.label, b.label));
  }, [entityTree, scope, integrationOptions, deviceOptions]);
  // A new profile cannot be given a target that already has one, so the picker
  // does not offer it (user, 2026-10-30).
  const isTaken = useCallback((key: string) => isNew && !lockedKey && !!taken?.has(key), [isNew, lockedKey, taken]);
  const keyOptions = useMemo(() => allKeyOptions.filter((o) => !isTaken(o.value)), [allKeyOptions, isTaken]);
  // A boolean, so another picker list arriving does not re-run (and cancel)
  // the seed read below.
  const keyKnown = useMemo(() => keyOptions.some((o) => o.value === state.key.trim()), [keyOptions, state.key]);

  // New entity profiles: seed control + enforcement from the entity's effective
  // (inherited) mode, so creating a profile starts from what MESA already applies
  // (e.g. a lock opens at "prohibited") rather than a generic Autonomous default.
  // Seeds once per selected entity. A mode the operator set is kept, and so are
  // their other edits: only the seeded modes and the chosen key join the clean
  // baseline. A caller-supplied key is a fact, so it is read even before the
  // picker list knows it.
  const seedKey = isNew && scope === "entity" && (keyKnown || !!lockedKey) ? state.key.trim() : "";
  useEffect(() => {
    if (!isNew || scope !== "entity") return;
    const key = seedKey;
    if (!key) {
      setDetail(null);
      seededForKey.current = null;
      return;
    }
    if (seededForKey.current === key) return;
    setSeed({ key, status: "pending" });
    let cancelled = false;
    api.getMesaProfile(key)
      .then((d) => {
        if (cancelled) return;
        // Marked only once a read is accepted, so a cancelled one is retried.
        seededForKey.current = key;
        setSeed({ key, status: "done" });
        setDetail(d);
        const eff = effectiveModes(d);
        // A caller-supplied seed (suggestions Review) wins over the
        // effective-mode default; enforcement still seeds from effective.
        const seed = { control_mode: seedControlMode ?? eff.control_mode, enforcement_mode: eff.enforcement_mode };
        const base = JSON.parse(cleanSnapshot.current) as EditorState;
        cleanSnapshot.current = JSON.stringify({ ...base, key: state.key, ...seed });
        setState((s) => ({
          ...s,
          control_mode: touched.current.has("control_mode") ? s.control_mode : seed.control_mode,
          enforcement_mode: touched.current.has("enforcement_mode") ? s.enforcement_mode : seed.enforcement_mode,
        }));
      })
      .catch(() => {
        if (cancelled) return;
        setDetail(null);
        setSeed({ key, status: "failed" });
      });
    return () => { cancelled = true; };
  }, [isNew, scope, seedKey, state.key, seedControlMode, seedAttempt]);
  const seedReady = !seedKey || (seed?.key === seedKey && seed.status === "done");
  const seedFailed = !!seedKey && seed?.key === seedKey && seed.status === "failed";

  // Integration: validate against the picker list when it loaded; if that list is
  // empty (e.g. the integration-options endpoint isn't reachable yet), fall back to
  // validating the typed component-name format so the field is never a dead end.
  const keyValid = !!lockedKey || !isNew
    || (scope === "integration" && allKeyOptions.length === 0
      ? /^[a-z][a-z0-9_]*$/.test(state.key.trim()) && !isTaken(state.key.trim())
      : keyOptions.some((o) => o.value === state.key.trim()));
  const keyInvalidShown = !lockedKey && isNew && state.key.trim() !== "" && !keyValid;
  const dirty = JSON.stringify(state) !== cleanSnapshot.current;
  const canSave = !saving && !loading && loaded && keyValid && seedReady;

  // Suggested tags for this scope's domain, ordered by root priority and
  // interleaved so the most relevant namespace leads (not alphabetical, which
  // would let an early root like "automation" crowd out "lighting").
  const recommendedTags = useMemo(() => {
    const domain = scope === "entity" ? state.key.split(".")[0] : scope === "domain" ? state.key.trim() : "";
    let roots = DOMAIN_TAG_ROOTS[domain] ?? FALLBACK_TAG_ROOTS;
    let byRoot = roots.map((r) => canonicalTags.filter((t) => t.split(".")[0] === r));
    if (byRoot.every((l) => l.length === 0)) {
      roots = FALLBACK_TAG_ROOTS;
      byRoot = roots.map((r) => canonicalTags.filter((t) => t.split(".")[0] === r));
    }
    const out: string[] = [];
    for (let col = 0; out.length < 8; col++) {
      let advanced = false;
      for (const list of byRoot) {
        if (list[col]) { out.push(list[col]); advanced = true; if (out.length >= 8) break; }
      }
      if (!advanced) break;
    }
    return out;
  }, [scope, state.key, canonicalTags]);

  function attemptClose() {
    if (dirty) { setConfirmDiscard(true); return; }
    onClose();
  }

  // After a save: the modal closes; the window shows the saved profile.
  const finish = () => {
    if (variant === "window" && onShowSaved) onShowSaved(state.key.trim());
    else onClose();
  };

  // Window: Discard Changes returns the form to the saved profile, or leaves a new one.
  function discard() {
    if (isNew) { onClose(); return; }
    touched.current.clear();
    setState(JSON.parse(cleanSnapshot.current) as EditorState);
    setWarnings([]);
    setError(null);
    setConfirmDelete(false);
  }

  async function save() {
    if (!keyValid) { setError(t(CHOOSE_VALID[scope])); return; }
    setSaving(true);
    setError(null);
    try {
      const doc = editorToDoc(state);
      // A profile created from a suggestion keeps the suggestion's reason, as applying it did.
      const boundaries = doc.semantic_profile?.operational_boundaries as Record<string, unknown> | undefined;
      if (isNew && seedReason && boundaries) boundaries.control_reason = seedReason;
      const w = await saveProfile(scope, state.key.trim(), doc, isNew);
      cleanSnapshot.current = JSON.stringify(state);
      setWarnings(w);
      // The window keeps this form for the profile it just saved, so the
      // Effective Resolution it shows is read again here; the form is left as
      // it is, so nothing typed after Save is replaced.
      if (!isNew && scope === "entity" && profileKey) {
        const savedKey = profileKey;
        void (async () => {
          try { setDetail(await api.getMesaProfile(savedKey)); } catch { /* the earlier resolution stays shown */ }
        })();
      }
      if (w.length === 0) { notify(t("mesa.savedToast", { key: profileName(state.key.trim()) })); onSaved(); finish(); }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("mesa.saveProfileFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!profileKey) return;
    setSaving(true);
    try {
      await deleteProfile(scope, profileKey);
      notify(t("mesa.deletedToast", { key: profileName(profileKey) }));
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("mesa.deleteProfileFailed"));
      setSaving(false);
    }
  }

  const titleVerb = isNew
    ? t(ADD_TITLE[scope])
    : t(EDIT_TITLE[scope], { key: keyLabel || String(profileKey) });
  const target = keyLabel || String(profileKey);

  // Deleting names the target and what falls back once its profile is gone.
  const deleteConfirmation = confirmDelete && !isNew && (
    <div data-guide="window.mesaEditor#delete">
    <InlineConfirm label={t(DELETE_CONFIRM[scope])} tone="danger" confirmLabel={t(DELETE_BUTTON[scope])} confirmIcon="delete"
      confirmClassName="btn btn-danger btn-sm" cancelLabel={t("mesa.cancel")} busy={saving}
      onConfirm={() => { void remove(); }} onCancel={() => setConfirmDelete(false)} help={t(DELETE_WARN[scope], { key: target })} />
    </div>
  );

  const body = (
      <div className="mesa-editor-body">
        {error && <ErrorMsg msg={error} />}
        {seedFailed && (
          <>
            <ErrorMsg msg={t("mesa.loadProfileFailed")} />
            <button className="btn btn-ghost" onClick={() => setSeedAttempt((n) => n + 1)}>{t("common.refresh")}</button>
          </>
        )}
        {loading ? <Loading /> : !loaded ? (
          <button className="btn btn-ghost" onClick={() => setLoadAttempt((n) => n + 1)}>{t("common.refresh")}</button>
        ) : (
          <>
            {isNew && !lockedKey && (
              <div className="field" data-guide="window.mesaEditor#target" {...helpRow(t(SCOPE_HELP[scope]))}>
                <FieldLabel id="mesa-key" text={t(SCOPE_LABEL[scope])} help={t(SCOPE_HELP[scope])} />
                <Combo
                  id="mesa-key"
                  value={state.key}
                  options={keyOptions}
                  placeholder={t(SCOPE_PLACEHOLDER[scope])}
                  invalid={keyInvalidShown}
                  onChange={(v) => set("key", v)}
                />
                {keyInvalidShown && (
                  <span className="field-error">{t(NO_MATCH[scope])}</span>
                )}
              </div>
            )}
            {(lockedKey || !isNew) && (
              // What the profile covers, by its familiar name, on every profile that has
              // one already: only a new one is chosen here (user, 2026-10-30).
              <div className="field" data-guide="window.mesaEditor#target" {...helpRow(t(SCOPE_HELP[scope]))}>
                <FieldLabel id="mesa-key" text={t(SCOPE_LABEL[scope])} help={t(SCOPE_HELP[scope])} />
                <input id="mesa-key" className="input" value={keyLabel || state.key || String(profileKey ?? "")} readOnly disabled />
              </div>
            )}

            <div className="field" data-guide="window.mesaEditor#tags" {...helpRow(t(HELP.tags))}>
              <FieldLabel id="mesa-tags" text={t("mesa.semanticTags")} help={t(HELP.tags)} />
              {/* The suggested tags open from the end of the input itself, closed every time (user, 2026-10-30). */}
              <div className="mesa-tag-row">
                <TagInput
                  value={state.tags}
                  onChange={(t) => set("tags", t)}
                  canonicalTags={canonicalTags}
                  recommended={recommendedTags}
                  showRecommended={showReco}
                />
                {recommendedTags.length > 0 && (
                  <ActionButton icon="suggestions" label={showReco ? t("mesa.hideSuggestions") : t("mesa.showSuggestions")}
                    pressed={showReco} className="btn btn-outline btn-sm" onClick={() => setShowReco((s) => !s)} />
                )}
              </div>
            </div>

            <div className="mesa-grid">
              <SelectField id="mesa-cm" guide="window.mesaEditor#control-mode" label={t("mesa.fieldControlMode")} helpKey={HELP.control_mode}
                value={state.control_mode} options={CONTROL_MODES} onChange={(v) => set("control_mode", v)} />
              <SelectField id="mesa-em" guide="window.mesaEditor#enforcement" label={t("mesa.fieldEnforcement")} helpKey={HELP.enforcement_mode}
                value={state.enforcement_mode} options={ENFORCEMENT_MODES} onChange={(v) => set("enforcement_mode", v)} />
              <SelectField id="mesa-ta" guide="window.mesaEditor#triggers-automations" label={t("mesa.fieldTriggersAutomations")} helpKey={HELP.triggers_automations}
                value={state.triggers_automations} options={TRIGGERS} onChange={(v) => set("triggers_automations", v)} />
              <SelectField id="mesa-rev" guide="window.mesaEditor#effect" label={t("mesa.fieldReversible")} helpKey={HELP.reversible}
                value={state.reversible} options={REVERSIBLE} onChange={(v) => set("reversible", v)} />
              <SelectField id="mesa-rc" guide="window.mesaEditor#effect" label={t("mesa.fieldReversibilityCost")} helpKey={HELP.reversibility_cost}
                value={state.reversibility_cost} options={REVERSIBILITY_COSTS} onChange={(v) => set("reversibility_cost", v)} />
              <SelectField id="mesa-ses" guide="window.mesaEditor#effect" label={t("mesa.fieldSideEffectScope")} helpKey={HELP.side_effect_scope}
                value={state.side_effect_scope} options={SCOPES} onChange={(v) => set("side_effect_scope", v)} />
              <SelectField id="mesa-pl" guide="window.mesaEditor#privacy-level" label={t("mesa.fieldPrivacyLevel")} helpKey={HELP.privacy_level}
                value={state.privacy_level} options={PRIVACY_LEVELS} onChange={(v) => set("privacy_level", v)} />
            </div>

            {warnings.length > 0 && (
              <div className="banner banner-warn" data-guide="window.mesaEditor#save-warnings">
                <strong>{t("mesa.savedWithWarnings")}</strong>
                <ul>
                  {warnings.map((w, i) => (
                    <li key={i}>{t("mesa.warningRow", { recommendation: w.recommendation, automationId: w.automation_id, role: MESA_ROLE_KEYS[w.role] ? t(MESA_ROLE_KEYS[w.role]) : w.role })}</li>
                  ))}
                </ul>
                <button className="btn btn-sm" onClick={() => { onSaved(); finish(); }}>{t("mesa.dismiss")}</button>
              </div>
            )}

            {variant === "modal" && deleteConfirmation}

            {/* Resolution warnings say that a rule fired and changed the outcome:
                an override that was ignored, a malformed field that was dropped,
                a vendor-declared capability that overrode an operator decision.
                They arrive on every profile read and are the only signal an
                operator gets when a deliberate setting did not take effect. The
                text is mesa-core's own and stays English, like the resolution
                table below it. */}
            {!isNew && scope === "entity" && detail && detail.explanation.warnings.length > 0 && (
              <div className="banner banner-warn mesa-resolution-warnings" data-guide="window.mesaEditor#effective-resolution">
                <strong>{t("mesa.resolutionWarnings")}</strong>
                <ul>
                  {detail.explanation.warnings.map((w) => <li key={w}>{w}</li>)}
                </ul>
              </div>
            )}

            {!isNew && scope === "entity" && detail && (
              <details className="mesa-explain" open={explainOpen} onToggle={(e) => { explainLeftOpen = e.currentTarget.open; setExplainOpen(e.currentTarget.open); }}>
                <summary data-help-text={t("mesa.helpEffectiveResolution")} data-guide="window.mesaEditor#effective-resolution">{t("mesa.effectiveResolution")}</summary>
                <table className="data-table">
                  <thead><tr><th>{t("mesa.colField")}</th><th>{t("mesa.colEffective")}</th><th>{t("mesa.colFrom")}</th><th>{t("mesa.colOrigin")}</th></tr></thead>
                  <tbody>
                    {detail.explanation.explanation.map((row) => (
                      <tr key={row.field_path}>
                        <td><code>{row.field_path}</code></td>
                        <td>{String(row.effective_value)}</td>
                        <td>{levelText(row.provided_by_level)}</td>
                        <td>{row.provided_by_origin}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
          </>
        )}
      </div>
  );

  if (variant === "window") {
    const name = isNew && !lockedKey ? t(ADD_TITLE[scope]) : keyLabel || state.key || String(profileKey);
    const id = isNew ? state.key : String(profileKey);
    return (
      <div className="mesa-editor-window">
        <div className="mesa-editor-head">
          <h3 className="mesa-editor-target">
            {isNew && !lockedKey
              ? <strong>{name}</strong>
              : <><strong className="mesa-editor-scope">{t(SCOPE_ID_LABEL[scope])}</strong><code>{id || name}</code></>}
          </h3>
          <div className="phx-window-actions mesa-editor-actions" data-guide="window.mesaEditor#save">
            {/* A new profile is itself the change; an existing one waits for an edit (user, 2026-10-30). */}
            <ActionButton icon="save" label={t("mesa.save")} busy={saving} disabled={!canSave || (!isNew && !dirty)} onClick={() => { void save(); }} className="btn btn-outline btn-sm" />
            <ActionButton icon="undo" label={t("mesa.discardChanges")} busy={saving} disabled={!isNew && !dirty} onClick={discard} className="btn btn-outline btn-sm" />
            {/* Always offered, so the row does not change shape; there is nothing to delete until a profile is open. */}
            <ActionButton icon="delete" label={t("mesa.delete")} busy={saving} disabled={isNew || confirmDelete || !loaded} onClick={() => setConfirmDelete(true)} className="btn btn-outline btn-sm" />
          </div>
        </div>
        {notice}
        {deleteConfirmation}
        {body}
      </div>
    );
  }

  return (
    <>
    {/* Over the whole workspace, with UI2 buttons; Delete turns red only in its confirmation (user, 2026-10-28). */}
    <Modal titleId="mesa-editor-title" onClose={attemptClose} overWorkspace>
      <h3 className="modal-title" id="mesa-editor-title">{titleVerb}</h3>
      {body}
      <div className="modal-actions">
        {!isNew && !confirmDelete && (
          <ActionButton icon="delete" label={t("mesa.delete")} className="btn btn-outline btn-sm" onClick={() => setConfirmDelete(true)} busy={saving} />
        )}
        <ActionButton icon="undo" label={t("mesa.cancel")} className="btn btn-outline btn-sm" onClick={attemptClose} busy={saving} />
        <ActionButton icon="save" label={saving ? t("mesa.saving") : t("mesa.save")} className="btn btn-primary btn-sm" onClick={() => void save()} disabled={!canSave} />
      </div>
    </Modal>
    {confirmDiscard && (
      <Modal titleId="mesa-discard-title" onClose={() => setConfirmDiscard(false)} overWorkspace>
        <h3 className="modal-title" id="mesa-discard-title">{t("mesa.discardTitle")}</h3>
        <p className="modal-body-text">{t("mesa.discardBody")}</p>
        <div className="modal-actions">
          <ActionButton icon="undo" label={t("mesa.keepEditing")} className="btn btn-outline btn-sm" onClick={() => setConfirmDiscard(false)} />
          <ActionButton icon="delete" label={t("mesa.discardChanges")} className="btn btn-danger btn-sm" onClick={onClose} />
        </div>
      </Modal>
    )}
    </>
  );
}

// Control modes as the profile list names them; a profile without one inherits.
export const CONTROL_MODE_LABEL_KEYS: Record<string, string> = {
  autonomous: "mesa.modeAutonomous",
  confirm: "mesa.modeConfirm",
  read_only: "mesa.modeReadOnly",
  prohibited: "mesa.modeProhibited",
  inherited: "mesa.modeInherited",
};

export function rawControlMode(doc: MesaProfileDocument | null): string {
  const ob = (doc?.semantic_profile?.operational_boundaries ?? {}) as Record<string, unknown>;
  return (ob.control_mode as string) ?? "inherited";
}

export function isEnforced(doc: MesaProfileDocument | null): boolean {
  const ob = (doc?.semantic_profile?.operational_boundaries ?? {}) as Record<string, unknown>;
  return ob.enforcement_mode === "enforced";
}

// Provenance of a stored profile. "developer" means it was imported from an
// integration's mesa_profile.json sidecar (a vendor-supplied profile). The
// serialized document nests metadata_origin under semantic_profile (matching
// SemanticProfile.to_dict); a top-level copy is tolerated as a fallback.
export function profileSource(doc: MesaProfileDocument | null): string {
  const sp = (doc?.semantic_profile ?? {}) as Record<string, unknown>;
  const mo = (sp.metadata_origin ?? doc?.metadata_origin ?? {}) as { source?: string };
  return mo.source ?? "";
}

export { tagsOf };

/**
 * Download the full mesa-core portability archive: every profile and the
 * deployment defaults. Callers confirm first, because the file is the
 * deployment's whole safety policy.
 */
export async function exportMesaArchive(): Promise<void> {
  const archive = await api.exportMesaProfiles();
  const blob = new Blob([JSON.stringify(archive, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `phoenix-mcp-mesa-profiles-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

type ImportCounts = { entities: number; devices: number; domains: number; integrations: number; areas: number; defaults: boolean };

/**
 * Import: pick a file, see what it contains, choose the conflict policy, then
 * confirm. Default is skip (existing profiles are never touched); "replace"
 * turns the confirm explicitly destructive. The result stays until another
 * file is picked.
 */
export function MesaImport({ onImported, onCancel }: { onImported: () => void; onCancel?: () => void }) {
  const [archive, setArchive] = useState<MesaExportArchive | null>(null);
  const [counts, setCounts] = useState<ImportCounts | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [replaceExisting, setReplaceExisting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MesaImportResult | null>(null);
  const [fileName, setFileName] = useState("");
  const fileInput = useRef<HTMLInputElement | null>(null);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    setFileName(file?.name ?? "");
    setArchive(null);
    setCounts(null);
    setParseError(null);
    setError(null);
    setResult(null);
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as MesaExportArchive;
      const inner = parsed?.mesa_export;
      if (!inner || typeof inner !== "object") {
        setParseError(t("mesa.importNotArchive"));
        return;
      }
      setArchive(parsed);
      setCounts({
        entities: Object.keys(inner.entities ?? {}).length,
        devices: Object.keys(inner.devices ?? {}).length,
        domains: Object.keys(inner.domains ?? {}).length,
        integrations: Object.keys(inner.integrations ?? {}).length,
        areas: Object.keys(inner.areas ?? {}).length,
        defaults: inner.deployment_defaults != null,
      });
    } catch {
      setParseError(t("mesa.importNotJson"));
    }
  }

  async function doImport() {
    if (!archive) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.importMesaProfiles(archive, replaceExisting ? "overwrite" : "skip"));
      setArchive(null);
      setCounts(null);
      if (fileInput.current) fileInput.current.value = "";
      setFileName("");
      onImported();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("mesa.importFailed"));
    } finally {
      setBusy(false);
    }
  }

  const summary = counts
    ? [
        counts.entities && tn("mesa.countEntity", counts.entities),
        counts.devices && tn("mesa.countDevice", counts.devices),
        counts.domains && tn("mesa.countDomain", counts.domains),
        counts.integrations && tn("mesa.countIntegration", counts.integrations),
        counts.areas && tn("mesa.countArea", counts.areas),
      ].filter(Boolean).join(t("common.listSeparator"))
    : "";
  const invalidEntries = result ? Object.entries(result.invalid) : [];

  return (
    // What importing does, and what it leaves alone, reads in the Help window (user, 2026-10-30).
    <div className="phx-window-pad mesa-import" data-help-text={`${t("mesa.importIntro")} ${t("mesa.skipNote")}`} data-help-controls="">
      {error && <ErrorMsg msg={error} />}
      <div className="field mesa-import-file">
        <input ref={fileInput} type="file" accept="application/json,.json" className="sr-only" aria-label={t("mesa.archiveFileAria")} onChange={onFile} />
        <div className="mesa-import-browse">
          <ActionButton icon="import" label={t("mesa.browse")} onClick={() => fileInput.current?.click()} className="btn btn-outline btn-sm" />
          <span className="mesa-import-filename">{fileName || t("mesa.noFileChosen")}</span>
        </div>
        {parseError && <span className="field-error">{parseError}</span>}
        {counts && (
          <p className="modal-body-text mesa-import-summary">
            {counts.defaults
              ? t("mesa.archiveContainsDefaults", { summary: summary || t("mesa.archiveNone") })
              : t("mesa.archiveContains", { summary: summary || t("mesa.archiveNone") })}
          </p>
        )}
      </div>
      {replaceExisting && <div className="banner banner-warn">{t("mesa.replaceWarn")}</div>}
      {/* Replacing, importing and leaving all sit on one line (user, 2026-10-30). */}
      <div className="phx-window-actions mesa-import-actions">
        <div className="toggle-row mesa-import-replace">
          <div className="toggle-label"><span>{t("mesa.replaceExisting")}</span></div>
          <label className="toggle-switch">
            <input type="checkbox" aria-label={t("mesa.replaceExisting")} checked={replaceExisting} onChange={(e) => setReplaceExisting(e.target.checked)} />
            <span className="toggle-switch-track" />
          </label>
        </div>
        <ActionButton icon="import" label={replaceExisting ? t("mesa.importAndReplace") : t("mesa.import")} busy={busy} disabled={!archive}
          onClick={() => { void doImport(); }} className={`btn btn-sm ${replaceExisting ? "btn-danger" : "btn-outline"}`} />
        {onCancel && <ActionButton icon="undo" label={t("mesa.cancel")} busy={busy} onClick={onCancel} className="btn btn-outline btn-sm" />}
      </div>
      {result && (
        <div className="mesa-import-result" role="status">
          <h4 className="phx-section-title">{t("mesa.importComplete")}</h4>
          <p className="modal-body-text">
            {tn("mesa.importResult", result.imported, { overwritten: result.overwritten, skipped: result.skipped_existing.length })}
          </p>
          {invalidEntries.length > 0 && (
            <div className="banner banner-warn">
              <strong>{tn("mesa.importInvalid", invalidEntries.length)}</strong>
              <ul>
                {invalidEntries.map(([key, msg]) => <li key={key}><code>{key}</code>: {msg}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
