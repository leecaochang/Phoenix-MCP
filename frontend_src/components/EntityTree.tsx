import { BusyInput } from "./BusyButton";
import React, { createContext, useState, useCallback, useContext, useEffect, useId, useMemo, useRef, forwardRef } from "react";
import type { EntityTree, DomainTree, IntegrationPermissionOption, PermissionTree, NodeState } from "../types";
import { PermissionSelector } from "./PermissionSelector";
import { MesaProfileLink, type LinkScope } from "./MesaProfileLink";
import { api, currentHass } from "../api";
import { HIGH_RISK_DOMAINS, PERMISSION_LABEL_KEYS } from "../utils";
import { compareStrings, foldForSearch, t, tn } from "../i18n";
import { ActionButton, DisplayModeContext } from "../ui/ActionButton";
import { Icon } from "../ui/Icon";
import { MenuButton, type MenuEntry } from "../ui/Menu";
import { useWindowVisible } from "../shell/visibility";
import { useClickAway } from "../ui/useClickAway";

const INDIRECT_CONTROL_DOMAINS = new Set([
  "automation", "script", "scene",
]);

/** Match both literal text and common identifier punctuation variants.
 *
 * Home Assistant commonly derives entity IDs from addresses and hostnames, so
 * an operator may know a device as `1.1.1.1` while the registry row contains
 * `binary_sensor.1_1_1_1`. Keeping the literal match as well as a compact form
 * makes both searches useful without changing what is displayed.
 */
export function matchesTreeSearch(query: string, ...values: Array<string | null | undefined>): boolean {
  const literalQuery = foldForSearch(query.trim());
  if (!literalQuery) return true;
  const compactQuery = literalQuery.replace(/[\s._-]+/g, "");

  return values.some((value) => {
    if (!value) return false;
    const literalValue = foldForSearch(value);
    if (literalValue.includes(literalQuery)) return true;
    return compactQuery.length > 0
      && literalValue.replace(/[\s._-]+/g, "").includes(compactQuery);
  });
}

function ghostEntitiesForDomain(
  domainKey: string,
  permissions: PermissionTree,
  allEntityIds: Set<string>,
): string[] {
  return Object.keys(permissions.entities).filter(
    (entityId) => entityId.startsWith(`${domainKey}.`) && !allEntityIds.has(entityId),
  );
}

function domainMatchesSearch(
  query: string,
  domainKey: string,
  domainData: DomainTree,
  permissions: PermissionTree,
  allEntityIds: Set<string>,
): boolean {
  if (matchesTreeSearch(query, domainKey)) return true;
  if (Object.values(domainData.entity_details).some((detail) =>
    matchesTreeSearch(query, detail.entity_id, detail.friendly_name))) return true;
  if (Object.entries(domainData.devices).some(([deviceId, device]) =>
    matchesTreeSearch(query, deviceId, device.name))) return true;
  return ghostEntitiesForDomain(domainKey, permissions, allEntityIds).some((entityId) =>
    matchesTreeSearch(query, entityId));
}

/** What Select By narrows the tree to: devices, shown with their entities, and single entities. */
export interface TreeScope {
  deviceIds: ReadonlySet<string>;
  entityIds: ReadonlySet<string>;
}

/** Whether an entity is in the Select By scope. A device in scope brings its own
 * entities and those of its child devices, which inherit its permission. */
function inScope(scope: TreeScope | null, entityId: string, deviceId: string | null, parentDeviceId?: string | null): boolean {
  if (!scope) return true;
  return scope.entityIds.has(entityId)
    || (deviceId != null && scope.deviceIds.has(deviceId))
    || (parentDeviceId != null && scope.deviceIds.has(parentDeviceId));
}

function domainInScope(scope: TreeScope | null, domainData: DomainTree): boolean {
  if (!scope) return true;
  return domainData.deviceless_entities.some((entityId) => inScope(scope, entityId, null))
    || Object.entries(domainData.devices).some(([deviceId, device]) =>
      device.entities.some((entityId) => inScope(scope, entityId, deviceId, device.parent_device_id)));
}

/** Search opens the hierarchy temporarily; it must not rewrite the operator's
 * expansion choices. Capture the state only when entering search and restore it
 * only when leaving, so changing one non-empty query to another does not keep
 * moving the snapshot. Select By opens it the same way.
 *
 * A layout effect, so the tree never draws the in-between state: the filter gone
 * but every branch still open. Firefox and Safari measured that frame (50,000
 * pixels of tree) and the workspace sized itself to it (user, 2026-10-31).
 */
function useTemporarySearchExpansion(
  isSearching: boolean,
  expanded: boolean,
  setExpanded: React.Dispatch<React.SetStateAction<boolean>>,
): void {
  const wasSearching = React.useRef(false);
  const expandedBeforeSearch = React.useRef(false);

  React.useLayoutEffect(() => {
    if (isSearching === wasSearching.current) return;

    if (isSearching) {
      expandedBeforeSearch.current = expanded;
      setExpanded(true);
    } else {
      setExpanded(expandedBeforeSearch.current);
    }
    wasSearching.current = isSearching;
  }, [expanded, isSearching, setExpanded]);
}

interface Props {
  accessKeyId: string;
  permissions: PermissionTree;
  onPermissionsChange: (tree: PermissionTree) => void;
  onEntityClick?: (entityId: string, depth?: "entity" | "device" | "domain", opts?: { reveal?: boolean }) => void;
  collapseKey?: number;
  // When set, only these domains render. Used by the onboarding wizard to show
  // a single, less-daunting domain (e.g. ["light"]).
  domainAllowlist?: string[];
  // When set, the tree expands the path to this entity and scrolls it into view
  // (e.g. after selecting it in the Permission Summary card).
  revealEntity?: string;
  // Which node the reveal targets. For "domain"/"device" the matching group
  // header is flashed and scrolled to (revealEntity is a representative child);
  // for "entity" the entity row itself is the target. Defaults to "entity".
  revealDepth?: "entity" | "device" | "domain";
  // Bumped by the parent on every reveal request so re-selecting the same
  // target still re-runs the expand/scroll effects.
  revealNonce?: number;
  // Entities that have a MESA profile, and the handler to open one. When given,
  // each entity row shows a "MESA"/"+" jump to its profile.
  mesaProfileEntities?: Set<string>;
  mesaProfileDevices?: Set<string>;
  mesaProfileDomains?: Set<string>;
  onOpenMesa?: (targetKey: string, scope: LinkScope, targetName?: string) => void;
  // Offer Select By beside the filter, narrowing the tree to an area, label or integration.
  selectBy?: boolean;
}

// A child device's main parent sits between the child device and the domain,
// matching the backend resolver.
function chainStates(
  states: NodeState[],
): string {
  if (states.includes("RED")) return "DENY";
  for (const state of states) {
    if (state === "GREEN") return "WRITE";
    if (state === "YELLOW") return "READ";
  }
  return "NO_ACCESS";
}

function deviceStates(
  deviceId: string | null,
  parentDeviceId: string | null | undefined,
  permissions: PermissionTree,
): NodeState[] {
  const states: NodeState[] = [];
  if (deviceId) states.push(permissions.devices[deviceId]?.state ?? "GREY");
  if (deviceId && parentDeviceId) states.push(permissions.devices[parentDeviceId]?.state ?? "GREY");
  return states;
}

function effectivePermission(
  entityId: string,
  domainKey: string,
  deviceId: string | null,
  parentDeviceId: string | null | undefined,
  permissions: PermissionTree,
): string {
  return chainStates([
    permissions.entities[entityId]?.state ?? "GREY",
    ...deviceStates(deviceId, parentDeviceId, permissions),
    permissions.domains[domainKey]?.state ?? "GREY",
  ]);
}

function effectiveForNode(
  nodeType: "domain" | "device",
  nodeId: string,
  domainKey: string,
  permissions: PermissionTree,
  parentDeviceId?: string | null,
): string {
  const domainState = permissions.domains[domainKey]?.state ?? "GREY";
  if (nodeType === "domain") return chainStates([domainState]);
  return chainStates([...deviceStates(nodeId, parentDeviceId, permissions), domainState]);
}

/** The entity whose hint editor is open: one at a time in a tree. */
const HintEditorContext = createContext<{ openId: string | null; setOpenId: (id: string | null) => void }>({
  openId: null, setOpenId: () => undefined,
});

/** Opens an entity's hint editor; a populated hint is marked and its text is the tooltip. */
const HintButton = forwardRef<HTMLButtonElement, {
  currentHint: string | null;
  globalHint: string | null;
  open: boolean;
  onToggle: () => void;
}>(function HintButton({ currentHint, globalHint, open, onToggle }, ref) {
  const iconsOnly = useContext(DisplayModeContext) === "icons";
  // This key's own hint takes precedence over the global one.
  const shown = currentHint || globalHint;
  const label = shown ? t("perms.editHint") : t("perms.addHint");
  return (
    <button ref={ref} type="button" className={`tree-hint-btn${shown ? " has-hint" : ""}${iconsOnly ? " is-icon-only" : ""}`}
      aria-label={label} aria-expanded={open} title={shown ? `${label}: ${shown}` : label} onClick={onToggle}>
      <Icon name={shown ? "hint" : "hintAdd"} size={14} />
      {!iconsOnly && <span>{label}</span>}
    </button>
  );
});

interface HintEditorProps {
  accessKeyId: string;
  entityId: string;
  currentHint: string | null;
  globalHint: string | null;
  currentState: NodeState;
  onSaved: (tree: PermissionTree) => void;
  onGlobalHintsChange: (hints: Record<string, string>) => void;
  onClose: () => void;
  /** A click elsewhere closed it: focus stays where the click put it. */
  onDismiss: () => void;
  /** The entity's Hint button, which opens and closes the editor itself. */
  opener: React.RefObject<HTMLButtonElement | null>;
}

/**
 * The hint editor, opened under its entity's row without covering the rest of
 * the panel. Cancel, Escape or a click anywhere else closes it unsaved (user, 2026-10-28).
 */
function HintEditor({ accessKeyId, entityId, currentHint, globalHint, currentState, onSaved, onGlobalHintsChange, onClose, onDismiss, opener }: HintEditorProps) {
  const [allAccessKeys, setAllAccessKeys] = useState(true);
  const [value, setValue] = useState(globalHint ?? "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const titleId = useId();
  const root = useRef<HTMLDivElement | null>(null);
  useClickAway(root, onDismiss, !saving, opener);
  // The hint saved in the chosen scope, which Remove clears.
  const saved = allAccessKeys ? globalHint : currentHint;
  const scopeHelp = allAccessKeys ? "perms.hintNoteGlobal" : "perms.hintNoteLocal";

  function switchScope(all: boolean) {
    setAllAccessKeys(all);
    // Load the target scope's saved hint if it has one, but never wipe the box:
    // switching scope must not discard text the admin is in the middle of writing.
    const target = all ? globalHint : currentHint;
    if (target) setValue(target);
  }

  async function save(hint: string | null) {
    setSaving(true);
    setSaveError(null);
    try {
      if (allAccessKeys) {
        const r = await api.setEntityHint(entityId, hint);
        onGlobalHintsChange(r.entity_hints);
      } else {
        const tree = await api.patchEntityPermission(accessKeyId, entityId, {
          state: currentState,
          hint,
        });
        onSaved(tree);
      }
      onClose();
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : t("perms.hintSaveFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div ref={root} className="tree-hint-editor" role="group" aria-labelledby={titleId}
      onKeyDown={(e) => {
        if (e.key !== "Escape" || saving) return;
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }}>
      <div className="tree-hint-editor-head">
        <strong id={titleId}>{t("perms.hintModalTitle")}</strong>
        <code>{entityId}</code>
      </div>
      <input
        className="input"
        aria-label={t("perms.hintAria", { id: entityId })}
        aria-description={t("perms.hintCaution")}
        data-help="perms.hintCaution"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={t("perms.hintPlaceholder")}
        maxLength={200}
        onKeyDown={(e) => { if (e.key === "Enter" && !saving) void save(value.trim() || null); }}
        autoFocus
      />
      {/* Who receives a hint, and what each scope means, is in Help (user, 2026-10-28). */}
      <div className="hint-scope" data-help={scopeHelp}>
        <span className={allAccessKeys ? "" : "hint-scope-active"}>{t("perms.hintThisAccessKey")}</span>
        <label className={`toggle-switch${saving ? " disabled" : ""}`}>
          <BusyInput
            type="checkbox"
            aria-label={t("perms.hintAllAria")}
            aria-description={t(scopeHelp)}
            data-help={scopeHelp}
            checked={allAccessKeys}
            busy={saving}
            onChange={(e) => switchScope(e.target.checked)}
          />
          <span className="toggle-switch-track" />
        </label>
        <span className={allAccessKeys ? "hint-scope-active" : ""}>{t("perms.hintAllAccessKeys")}</span>
      </div>
      {saveError && <p className="tree-hint-error" role="alert">{saveError}</p>}
      <div className="tree-hint-editor-actions">
        <ActionButton icon="save" label={t("common.save")} help="help.hintSave" busy={saving}
          onClick={() => { void save(value.trim() || null); }} className="btn btn-primary btn-sm" />
        {saved && (
          <ActionButton icon="delete" label={t("perms.removeHint")} help="help.hintRemove" busy={saving}
            onClick={() => { void save(null); }} className="btn btn-outline btn-sm" />
        )}
        <ActionButton icon="undo" label={t("common.cancel")} busy={saving} onClick={onClose} className="btn btn-text btn-sm" />
      </div>
    </div>
  );
}

interface EntityRowProps {
  entityId: string;
  friendlyName: string | null;
  deviceId: string | null;
  parentDeviceId?: string | null;
  domainKey: string;
  permissions: PermissionTree;
  accessKeyId: string;
  filterText: string;
  scope: TreeScope | null;
  isGhost: boolean;
  onPermChange: (tree: PermissionTree) => void;
  onEntityClick?: (entityId: string, depth?: "entity" | "device" | "domain", opts?: { reveal?: boolean }) => void;
  revealEntity?: string;
  revealDepth?: "entity" | "device" | "domain";
  revealNonce?: number;
  mesaProfileEntities?: Set<string>;
  onOpenMesa?: (targetKey: string, scope: LinkScope, targetName?: string) => void;
  globalHints: Record<string, string>;
  onGlobalHintsChange: (hints: Record<string, string>) => void;
}

function EntityRow({
  entityId, friendlyName, deviceId, parentDeviceId, domainKey, permissions,
  accessKeyId, filterText, scope, isGhost, onPermChange, onEntityClick, revealEntity, revealDepth, revealNonce, mesaProfileEntities, onOpenMesa, globalHints, onGlobalHintsChange,
}: EntityRowProps) {
  const entityNode = permissions.entities[entityId];
  const state: NodeState = entityNode?.state ?? "GREY";
  const effective = effectivePermission(entityId, domainKey, deviceId, parentDeviceId, permissions);
  const rowRef = React.useRef<HTMLDivElement>(null);
  const [permError, setPermError] = useState<string | null>(null);
  const isRevealed = (revealDepth ?? "entity") === "entity" && revealEntity === entityId;
  const hintEditor = useContext(HintEditorContext);
  const hintOpen = hintEditor.openId === entityId;
  const hintButton = useRef<HTMLButtonElement | null>(null);

  // Scroll to this row only on an explicit reveal request (revealNonce bump),
  // never merely because it became the selected entity: a permission edit in
  // this same tree selects the row (for the emulator) without requesting a
  // reveal, and isRevealed in the dependency array would fire on that
  // transition and scroll during the click.
  useEffect(() => {
    if (isRevealed && rowRef.current) {
      rowRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealNonce]);

  if (filterText) {
    if (!matchesTreeSearch(filterText, entityId, friendlyName)) return null;
  }
  if (!inScope(scope, entityId, deviceId, parentDeviceId)) return null;

  async function setEntityState(newState: NodeState) {
    setPermError(null);
    try {
      const tree = await api.patchEntityPermission(accessKeyId, entityId, {
        state: newState,
        hint: entityNode?.hint ?? null,
      });
      onPermChange(tree);
      // Sync the emulator to the entity just edited, but do not scroll: the
      // row is already on screen, and re-centering it would make clicks land
      // on the wrong row mid-scroll.
      onEntityClick?.(entityId, "entity", { reveal: false });
    } catch (e: unknown) {
      setPermError(e instanceof Error ? e.message : t("perms.permSaveFailed"));
    }
  }

  return (
    <>
    <div ref={rowRef} className={`tree-node${isRevealed ? " tree-node-revealed" : ""}`}>
      <span className="tree-spacer" />
      {onOpenMesa && !isGhost && (
        <MesaProfileLink
          targetKey={entityId}
          // The friendly name when there is one: the tooltip is prose, and an
          // entity_id in the middle of a sentence reads as debris next to the
          // device and domain links, which both name their target properly.
          targetName={friendlyName ?? undefined}
          exists={!!mesaProfileEntities?.has(entityId)}
          onOpen={onOpenMesa}
        />
      )}
      {onEntityClick ? (
        <button
          type="button"
          className="tree-name tree-cursor-pointer"
          onClick={() => onEntityClick(entityId, "entity")}
          title={t("perms.simulateEntityTitle", { id: entityId })}
        >
          <span className="tree-friendly">{friendlyName ?? entityId}</span>
          <span className="tree-entity-id">{entityId}</span>
        </button>
      ) : (
        <div className="tree-name">
          <div className="tree-friendly">{friendlyName ?? entityId}</div>
          <div className="tree-entity-id">{entityId}</div>
        </div>
      )}
      {isGhost && (
        <span className="tree-badge tree-badge-ghost" title={t("perms.missingTitle")}>{t("perms.missingBadge")}</span>
      )}
      <span className="tree-effective" title={t("perms.effectiveTitle", { state: effectiveLabel(effective) })}>({effectiveLabel(effective)})</span>
      {state !== "GREY" && (
        <HintButton
          ref={hintButton}
          currentHint={entityNode?.hint ?? null}
          globalHint={globalHints[entityId] ?? null}
          open={hintOpen}
          onToggle={() => hintEditor.setOpenId(hintOpen ? null : entityId)}
        />
      )}
      <PermissionSelector value={state} onChange={setEntityState} label={t("perms.permFor", { name: friendlyName ?? entityId })} />
      {permError && <span className="tree-perm-error" role="alert" title={permError}>{t("perms.saveFailedShort")}</span>}
    </div>
    {hintOpen && state !== "GREY" && (
      <HintEditor
        accessKeyId={accessKeyId}
        entityId={entityId}
        currentHint={entityNode?.hint ?? null}
        globalHint={globalHints[entityId] ?? null}
        currentState={state}
        onSaved={onPermChange}
        onGlobalHintsChange={onGlobalHintsChange}
        onClose={() => { hintEditor.setOpenId(null); hintButton.current?.focus(); }}
        onDismiss={() => hintEditor.setOpenId(null)}
        opener={hintButton}
      />
    )}
    </>
  );
}

interface DeviceGroupProps {
  deviceId: string;
  deviceName: string;
  domainKey: string;
  entityIds: string[];
  // Child devices of this main device in the same domain, rendered nested.
  partDeviceIds?: string[];
  domainData: DomainTree;
  permissions: PermissionTree;
  accessKeyId: string;
  filterText: string;
  scope: TreeScope | null;
  allEntityIds: Set<string>;
  onPermChange: (tree: PermissionTree) => void;
  onEntityClick?: (entityId: string, depth?: "entity" | "device" | "domain", opts?: { reveal?: boolean }) => void;
  collapseKey?: number;
  revealEntity?: string;
  revealDepth?: "entity" | "device" | "domain";
  revealNonce?: number;
  mesaProfileEntities?: Set<string>;
  mesaProfileDevices?: Set<string>;
  onOpenMesa?: (targetKey: string, scope: LinkScope, targetName?: string) => void;
  globalHints: Record<string, string>;
  onGlobalHintsChange: (hints: Record<string, string>) => void;
}

interface DeviceIdentityProps {
  deviceId: string;
  deviceName: string;
  expanded?: boolean;
  onToggle?: () => void;
}

function DeviceIdentity({ deviceId, deviceName, expanded, onToggle }: DeviceIdentityProps) {
  return (
    <div className="tree-name">
      {onToggle ? (
        <button
          type="button"
          className="tree-device-name tree-cursor-pointer"
          onClick={onToggle}
          aria-expanded={expanded}
        >
          <span className="tree-friendly">{deviceName}</span>
        </button>
      ) : (
        <span className="tree-friendly">{deviceName}</span>
      )}
      <code className="tree-device-id" title={deviceId}>{deviceId}</code>
    </div>
  );
}

function DeviceGroup({
  deviceId, deviceName, domainKey, entityIds, partDeviceIds = [], domainData,
  permissions, accessKeyId, filterText, scope, allEntityIds, onPermChange, onEntityClick, collapseKey, revealEntity, revealDepth, revealNonce, mesaProfileEntities, mesaProfileDevices, onOpenMesa, globalHints, onGlobalHintsChange,
}: DeviceGroupProps) {
  const [expanded, setExpanded] = useState(false);
  const [permError, setPermError] = useState<string | null>(null);
  const deviceNode = permissions.devices[deviceId];
  const state: NodeState = deviceNode?.state ?? "GREY";
  const parentDeviceId = domainData.devices[deviceId]?.parent_device_id ?? null;
  const effective = effectiveForNode("device", deviceId, domainKey, permissions, parentDeviceId);
  const isDynamic = state !== "GREY";
  const headerRef = React.useRef<HTMLDivElement>(null);
  const isRevealed = revealDepth === "device" && !!revealEntity && entityIds.includes(revealEntity);
  const sortedPartIds = [...partDeviceIds].sort((a, b) =>
    compareStrings(domainData.devices[a]?.name ?? a, domainData.devices[b]?.name ?? b));
  const partEntityIds = sortedPartIds.flatMap((id) => domainData.devices[id]?.entities ?? []);

  // Entities sorted by friendly name (falling back to entity id).
  const sortedEntityIds = [...entityIds].sort((a, b) => {
    const an = domainData.entity_details[a]?.friendly_name ?? a;
    const bn = domainData.entity_details[b]?.friendly_name ?? b;
    return compareStrings(an, bn);
  });

  useTemporarySearchExpansion(!!filterText.trim() || !!scope, expanded, setExpanded);

  // Expand when an entity inside this device is the reveal target. Skip for
  // domain-depth reveals: those target the domain header, not every device.
  // Keyed on revealNonce only (bumped by an explicit "jump to" reveal), NOT on
  // revealEntity: editing a permission in place selects a target with reveal:false
  // (no nonce bump) to refresh the summary, and that must not expand this row.
  // The effect still runs on mount, so a deep-linked reveal expands correctly.
  useEffect(() => {
    if (revealDepth !== "domain" && revealEntity
      && (entityIds.includes(revealEntity) || partEntityIds.includes(revealEntity))) setExpanded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealNonce]);

  // Scroll only on an explicit reveal request; see the matching comment in
  // EntityRow for why isRevealed must not drive this directly.
  useEffect(() => {
    if (isRevealed && headerRef.current) {
      headerRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealNonce]);

  // Collapse when collapseKey changes, but NOT on initial mount: this group is
  // lazily mounted when its domain expands (often due to a reveal), and a
  // mount-time collapse would immediately undo the reveal-driven expand above.
  const skipFirstCollapse = React.useRef(true);
  useEffect(() => {
    if (skipFirstCollapse.current) { skipFirstCollapse.current = false; return; }
    setExpanded(false);
  }, [collapseKey]);

  async function setDeviceState(newState: NodeState) {
    setPermError(null);
    try {
      const tree = await api.patchDevicePermission(accessKeyId, deviceId, { state: newState });
      onPermChange(tree);
      const representative = entityIds[0] ?? partEntityIds[0];
      if (representative) onEntityClick?.(representative, "device", { reveal: false });
    } catch (e: unknown) {
      setPermError(e instanceof Error ? e.message : t("perms.permSaveFailed"));
    }
  }

  // Check the device itself as well as its children. The device-ID branch is
  // load-bearing: a raw registry ID has no representation in entity_details.
  const deviceMatches = matchesTreeSearch(filterText, deviceId, deviceName);
  const hasVisibleChild = filterText
    ? [...entityIds, ...partEntityIds].some((eid) => {
        const detail = domainData.entity_details[eid];
        return matchesTreeSearch(filterText, eid, detail?.friendly_name);
      }) || sortedPartIds.some((id) => matchesTreeSearch(filterText, id, domainData.devices[id]?.name))
    : true;

  if (filterText && !hasVisibleChild && !deviceMatches) return null;
  // Select By keeps a device that has an entity in scope, its own or a child device's.
  if (scope && !entityIds.some((eid) => inScope(scope, eid, deviceId, parentDeviceId))
    && !sortedPartIds.some((id) => (domainData.devices[id]?.entities ?? []).some((eid) => inScope(scope, eid, id, deviceId)))) return null;

  return (
    <div>
      <div ref={headerRef} className={`tree-node${isRevealed ? " tree-node-revealed" : ""}`}>
        <button type="button" className="tree-expand" onClick={() => setExpanded((x) => !x)} aria-expanded={expanded} aria-label={expanded ? t("perms.treeCollapse", { name: deviceName }) : t("perms.treeExpand", { name: deviceName })}>
          <span className={`collapsible-chevron${expanded ? " open" : ""}`} aria-hidden="true" />
        </button>
        {onOpenMesa && (
          // Device scope profiles every entity this device owns in one write,
          // which is the level an operator usually means for a physical thing.
          // The name is passed because a device key is an opaque registry id.
          <MesaProfileLink
            targetKey={deviceId}
            scope="device"
            targetName={deviceName}
            exists={!!mesaProfileDevices?.has(deviceId)}
            onOpen={onOpenMesa}
          />
        )}
        <DeviceIdentity
          deviceId={deviceId}
          deviceName={deviceName}
          expanded={expanded}
          onToggle={() => setExpanded((x) => !x)}
        />
        {isDynamic && (
          <span className="tree-badge tree-badge-dynamic" title={t("perms.dynamicTitle")}>{t("perms.dynamicBadge")}</span>
        )}
        <span className="tree-effective" title={t("perms.effectiveTitle", { state: effectiveLabel(effective) })}>({effectiveLabel(effective)})</span>
        <PermissionSelector value={state} onChange={setDeviceState} label={t("perms.permForDevice", { name: deviceName })} />
        {permError && <span className="tree-perm-error" role="alert" title={permError}>{t("perms.saveFailedShort")}</span>}
      </div>
      {expanded && (
        <div className="tree-children-flat">
          {sortedEntityIds.map((eid) => {
            const detail = domainData.entity_details[eid];
            return (
              <EntityRow
                key={eid}
                entityId={eid}
                friendlyName={detail?.friendly_name ?? null}
                deviceId={deviceId}
                parentDeviceId={parentDeviceId}
                domainKey={domainKey}
                permissions={permissions}
                accessKeyId={accessKeyId}
                filterText={filterText}
                scope={scope}
                isGhost={!allEntityIds.has(eid)}
                onPermChange={onPermChange}
                onEntityClick={onEntityClick}
                revealEntity={revealEntity}
                revealDepth={revealDepth}
                revealNonce={revealNonce}
                mesaProfileEntities={mesaProfileEntities}
                onOpenMesa={onOpenMesa}
                globalHints={globalHints}
                onGlobalHintsChange={onGlobalHintsChange}
              />
            );
          })}
          {sortedPartIds.length > 0 && (
            <div className="tree-children">
              {sortedPartIds.map((partId) => (
                <DeviceGroup
                  key={partId}
                  deviceId={partId}
                  deviceName={domainData.devices[partId]?.name ?? partId}
                  domainKey={domainKey}
                  entityIds={domainData.devices[partId]?.entities ?? []}
                  domainData={domainData}
                  permissions={permissions}
                  accessKeyId={accessKeyId}
                  filterText={filterText}
                  scope={scope}
                  allEntityIds={allEntityIds}
                  onPermChange={onPermChange}
                  onEntityClick={onEntityClick}
                  collapseKey={collapseKey}
                  revealEntity={revealEntity}
                  revealDepth={revealDepth}
                  revealNonce={revealNonce}
                  mesaProfileEntities={mesaProfileEntities}
                  mesaProfileDevices={mesaProfileDevices}
                  onOpenMesa={onOpenMesa}
                  globalHints={globalHints}
                  onGlobalHintsChange={onGlobalHintsChange}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface GhostDevicePermissionsProps {
  deviceIds: string[];
  permissions: PermissionTree;
  accessKeyId: string;
  onPermChange: (tree: PermissionTree) => void;
}

function GhostDevicePermissions({
  deviceIds, permissions, accessKeyId, onPermChange,
}: GhostDevicePermissionsProps) {
  return (
    <section className="tree-stale-devices" aria-labelledby="tree-stale-devices-title">
      <h4 id="tree-stale-devices-title" className="tree-stale-title">
        {t("perms.unmatchedDevicesTitle")}
      </h4>
      <p className="tree-stale-note">{t("perms.unmatchedDevicesNote")}</p>
      {deviceIds.map((deviceId) => (
        <GhostDeviceRow
          key={deviceId}
          deviceId={deviceId}
          permissions={permissions}
          accessKeyId={accessKeyId}
          onPermChange={onPermChange}
        />
      ))}
    </section>
  );
}

function GhostDeviceRow({
  deviceId, permissions, accessKeyId, onPermChange,
}: Omit<GhostDevicePermissionsProps, "deviceIds"> & { deviceId: string }) {
  const [permError, setPermError] = useState<string | null>(null);
  const state: NodeState = permissions.devices[deviceId]?.state ?? "GREY";

  async function setDeviceState(newState: NodeState) {
    setPermError(null);
    try {
      const next = await api.patchDevicePermission(accessKeyId, deviceId, { state: newState });
      onPermChange(next);
    } catch (e: unknown) {
      setPermError(e instanceof Error ? e.message : t("perms.permSaveFailed"));
    }
  }

  return (
    <div className="tree-node tree-stale-device-row">
      <span className="tree-spacer" />
      <DeviceIdentity deviceId={deviceId} deviceName={t("perms.unmatchedDeviceName")} />
      <span className="tree-badge tree-badge-ghost" title={t("perms.unmatchedDeviceBadgeTitle")}>
        {t("perms.unmatchedDeviceBadge")}
      </span>
      <span className="tree-effective" title={t("perms.effectiveTitle", { state: effectiveLabel("NOT_FOUND") })}>
        ({effectiveLabel("NOT_FOUND")})
      </span>
      <PermissionSelector
        value={state}
        onChange={setDeviceState}
        label={t("perms.permForDevice", { name: deviceId })}
      />
      {permError && <span className="tree-perm-error" role="alert" title={permError}>{t("perms.saveFailedShort")}</span>}
    </div>
  );
}

interface DomainGroupProps {
  domainKey: string;
  domainData: DomainTree;
  permissions: PermissionTree;
  accessKeyId: string;
  filterText: string;
  scope: TreeScope | null;
  allEntityIds: Set<string>;
  onPermChange: (tree: PermissionTree) => void;
  onEntityClick?: (entityId: string, depth?: "entity" | "device" | "domain", opts?: { reveal?: boolean }) => void;
  collapseKey?: number;
  revealEntity?: string;
  revealDepth?: "entity" | "device" | "domain";
  revealNonce?: number;
  mesaProfileEntities?: Set<string>;
  mesaProfileDevices?: Set<string>;
  mesaProfileDomains?: Set<string>;
  onOpenMesa?: (targetKey: string, scope: LinkScope, targetName?: string) => void;
  globalHints: Record<string, string>;
  onGlobalHintsChange: (hints: Record<string, string>) => void;
}

function DomainGroup({
  domainKey, domainData, permissions, accessKeyId, filterText, scope, allEntityIds, onPermChange, onEntityClick, collapseKey, revealEntity, revealDepth, revealNonce, mesaProfileEntities, mesaProfileDevices, mesaProfileDomains, onOpenMesa, globalHints, onGlobalHintsChange,
}: DomainGroupProps) {
  const [expanded, setExpanded] = useState(false);
  const [permError, setPermError] = useState<string | null>(null);
  const domainNode = permissions.domains[domainKey];
  const state: NodeState = domainNode?.state ?? "GREY";
  const effective = effectiveForNode("domain", domainKey, domainKey, permissions);
  const isRisk = HIGH_RISK_DOMAINS.has(domainKey);
  const isIndirect = INDIRECT_CONTROL_DOMAINS.has(domainKey);
  const isDynamic = state !== "GREY";
  const headerRef = React.useRef<HTMLDivElement>(null);
  const isRevealed = revealDepth === "domain" && !!revealEntity && revealEntity.split(".")[0] === domainKey;

  useTemporarySearchExpansion(!!filterText.trim() || !!scope, expanded, setExpanded);

  // Expand when the reveal target lives in this domain. Keyed on revealNonce only
  // (an explicit "jump to"), NOT revealEntity: editing a permission in place
  // refreshes the summary with reveal:false (no nonce bump) and must not expand
  // the row. Still runs on mount, so a deep-linked reveal expands correctly.
  useEffect(() => {
    if (revealEntity && revealEntity.split(".")[0] === domainKey) setExpanded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealNonce]);

  // Scroll only on an explicit reveal request; see the matching comment in
  // EntityRow for why isRevealed must not drive this directly.
  useEffect(() => {
    if (isRevealed && headerRef.current) {
      headerRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealNonce]);

  // Collapse when collapseKey changes, but NOT on initial mount (see DeviceGroup).
  const skipFirstCollapse = React.useRef(true);
  useEffect(() => {
    if (skipFirstCollapse.current) { skipFirstCollapse.current = false; return; }
    setExpanded(false);
  }, [collapseKey]);

  async function setDomainState(newState: NodeState) {
    setPermError(null);
    try {
      const tree = await api.patchDomainPermission(accessKeyId, domainKey, { state: newState });
      onPermChange(tree);
      const firstEntity = domainData.deviceless_entities[0]
        ?? Object.values(domainData.devices).find((device) => device.entities.length > 0)?.entities[0];
      if (firstEntity) onEntityClick?.(firstEntity, "domain", { reveal: false });
    } catch (e: unknown) {
      setPermError(e instanceof Error ? e.message : t("perms.permSaveFailed"));
    }
  }

  const ghostEntityIds = ghostEntitiesForDomain(domainKey, permissions, allEntityIds);
  // A child device nests under its main parent, which the backend lists in
  // every domain where one of its parts appears.
  const partsByParent = new Map<string, string[]>();
  for (const [deviceId, device] of Object.entries(domainData.devices)) {
    const parentId = device.parent_device_id;
    if (parentId && parentId in domainData.devices) {
      partsByParent.set(parentId, [...(partsByParent.get(parentId) ?? []), deviceId]);
    }
  }
  const topLevelDevices = Object.entries(domainData.devices).filter(([, device]) =>
    !device.parent_device_id || !(device.parent_device_id in domainData.devices));
  const hasChildren = domainData.deviceless_entities.length > 0
    || Object.keys(domainData.devices).length > 0
    || ghostEntityIds.length > 0;

  const hasVisible = domainMatchesSearch(
    filterText, domainKey, domainData, permissions, allEntityIds,
  );

  if (filterText && !hasVisible) return null;
  if (!domainInScope(scope, domainData)) return null;

  return (
    <div className="tree-domain-group">
      <div ref={headerRef} className={`tree-node${isRevealed ? " tree-node-revealed" : ""}`}>
        {hasChildren ? (
          <button type="button" className="tree-expand" onClick={() => setExpanded((x) => !x)} aria-expanded={expanded} aria-label={expanded ? t("perms.treeCollapse", { name: domainKey }) : t("perms.treeExpand", { name: domainKey })}>
            <span className={`collapsible-chevron${expanded ? " open" : ""}`} aria-hidden="true" />
          </button>
        ) : <span className="tree-spacer" />}
        {onOpenMesa && (
          // Domain scope is the broadest level the tree can offer, and the
          // bluntest: it reaches every entity in the domain, including ones
          // added later. No name is passed because the key IS the domain.
          <MesaProfileLink
            targetKey={domainKey}
            scope="domain"
            exists={!!mesaProfileDomains?.has(domainKey)}
            onOpen={onOpenMesa}
          />
        )}
        {hasChildren ? (
          <button type="button" className="tree-name tree-cursor-pointer" onClick={() => setExpanded((x) => !x)} aria-expanded={expanded}>
            <span className="tree-friendly tree-domain-label">{domainKey}</span>
          </button>
        ) : (
          <span className="tree-name">
            <span className="tree-friendly tree-domain-label">{domainKey}</span>
          </span>
        )}
        {isDynamic && (
          <span className="tree-badge tree-badge-dynamic" title={t("perms.dynamicDomainTitle")}>{t("perms.dynamicBadge")}</span>
        )}
        {isRisk && (
          <span className="tree-badge tree-badge-risk" title={t("perms.riskDomainTitle")}>!</span>
        )}
        {isIndirect && (
          <span className="tree-badge tree-badge-risk" title={t("perms.indirectTitle")}>!</span>
        )}
        <span className="tree-effective" title={t("perms.effectiveTitle", { state: effectiveLabel(effective) })}>({effectiveLabel(effective)})</span>
        <PermissionSelector value={state} onChange={setDomainState} label={t("perms.permForDomain", { name: domainKey })} />
        {permError && <span className="tree-perm-error" role="alert" title={permError}>{t("perms.saveFailedShort")}</span>}
      </div>
      {hasChildren && expanded && (
        <div className="tree-children">
          {domainData.deviceless_entities.length > 0 && (
            <div>
              {Object.keys(domainData.devices).length > 0 && (
                <div className="tree-node">
                  <span className="tree-spacer" />
                  <span className="tree-name tree-orphan-label">
                    {t("perms.deviceless")}
                  </span>
                </div>
              )}
              {[...domainData.deviceless_entities]
                .sort((a, b) => compareStrings(domainData.entity_details[a]?.friendly_name ?? a, domainData.entity_details[b]?.friendly_name ?? b))
                .map((eid) => {
                  const detail = domainData.entity_details[eid];
                  return (
                    <EntityRow
                      key={eid}
                      entityId={eid}
                      friendlyName={detail?.friendly_name ?? null}
                      deviceId={null}
                      domainKey={domainKey}
                      permissions={permissions}
                      accessKeyId={accessKeyId}
                      filterText={filterText}
                      scope={scope}
                      isGhost={!allEntityIds.has(eid)}
                      onPermChange={onPermChange}
                      onEntityClick={onEntityClick}
                      revealEntity={revealEntity}
                      revealDepth={revealDepth}
                      revealNonce={revealNonce}
                      mesaProfileEntities={mesaProfileEntities}
                      onOpenMesa={onOpenMesa}
                      globalHints={globalHints}
                      onGlobalHintsChange={onGlobalHintsChange}
                    />
                  );
                })}
            </div>
          )}
          {topLevelDevices
            .sort(([, a], [, b]) => compareStrings(a.name, b.name))
            .map(([deviceId, device]) => (
              <DeviceGroup
                key={deviceId}
                deviceId={deviceId}
                deviceName={device.name}
                domainKey={domainKey}
                entityIds={device.entities}
                partDeviceIds={partsByParent.get(deviceId)}
                domainData={domainData}
                permissions={permissions}
                accessKeyId={accessKeyId}
                filterText={filterText}
                scope={scope}
                allEntityIds={allEntityIds}
                onPermChange={onPermChange}
                onEntityClick={onEntityClick}
                collapseKey={collapseKey}
                revealEntity={revealEntity}
                revealDepth={revealDepth}
                revealNonce={revealNonce}
                mesaProfileEntities={mesaProfileEntities}
                mesaProfileDevices={mesaProfileDevices}
                onOpenMesa={onOpenMesa}
                globalHints={globalHints}
                onGlobalHintsChange={onGlobalHintsChange}
              />
            ))}
          {[...ghostEntityIds].sort().map((eid) => (
            <EntityRow
              key={eid}
              entityId={eid}
              friendlyName={null}
              deviceId={null}
              domainKey={domainKey}
              permissions={permissions}
              accessKeyId={accessKeyId}
              filterText={filterText}
              scope={scope}
              isGhost={true}
              onPermChange={onPermChange}
              onEntityClick={onEntityClick}
              revealEntity={revealEntity}
              revealDepth={revealDepth}
              revealNonce={revealNonce}
              mesaProfileEntities={mesaProfileEntities}
              onOpenMesa={onOpenMesa}
              globalHints={globalHints}
              onGlobalHintsChange={onGlobalHintsChange}
            />
          ))}
        </div>
      )}
    </div>
  );
}

type SelectByMode = "area" | "label" | "integration";
type SelectByChoice = { mode: SelectByMode; value: string };

/**
 * Select By: a menu choosing Area, Label or Integration, then a value selector
 * beside it. The chosen group narrows the tree to its entities, with their
 * devices and domains; it never grants anything. Reset shows the whole tree.
 */
function SelectByControls({ tree, choice, integrations, integrationsError, onChange }: {
  tree: EntityTree;
  choice: SelectByChoice | null;
  integrations: IntegrationPermissionOption[] | null;
  integrationsError: string | null;
  onChange: (choice: SelectByChoice | null) => void;
}) {
  const iconsOnly = useContext(DisplayModeContext) === "icons";
  const mode = choice?.mode ?? null;
  const valueSelect = useRef<HTMLSelectElement | null>(null);
  // Choosing a grouping moves focus on to its value selector, beside the menu.
  const focusValue = useRef(false);
  useEffect(() => {
    if (!focusValue.current) return;
    focusValue.current = false;
    valueSelect.current?.focus();
  }, [mode]);
  const choose = (next: SelectByMode | null) => {
    if (next === mode) return;
    focusValue.current = next !== null;
    onChange(next ? { mode: next, value: "" } : null);
  };

  const groups = useMemo(() => {
    if (mode === "integration") {
      return (integrations ?? []).map((option) => ({
        id: option.entry_id,
        // An entry may have no title of its own; its integration's domain still names it.
        name: option.title ? `${option.title} (${option.domain})` : option.domain,
        count: tn("selectBy.resources", option.device_count + option.deviceless_entity_count),
      })).sort((a, b) => compareStrings(a.name, b.name));
    }
    const found = new Map<string, { id: string; name: string; n: number }>();
    for (const domain of Object.values(tree)) {
      for (const detail of Object.values(domain.entity_details)) {
        const named = mode === "area"
          ? (detail.area_id && detail.area_name ? [{ id: detail.area_id, name: detail.area_name }] : [])
          : mode === "label" ? detail.labels : [];
        for (const group of named) {
          const existing = found.get(group.id);
          if (existing) existing.n++;
          else found.set(group.id, { id: group.id, name: group.name, n: 1 });
        }
      }
    }
    return [...found.values()]
      .map((g) => ({ id: g.id, name: g.name, count: tn("selectBy.entities", g.n) }))
      .sort((a, b) => compareStrings(a.name, b.name));
  }, [tree, mode, integrations]);

  const items: MenuEntry[] = [
    { id: "reset", label: t("selectBy.reset"), disabled: !mode, onSelect: () => choose(null) },
    "separator",
    { id: "area", label: t("selectBy.area"), checked: mode === "area", onSelect: () => choose("area") },
    { id: "label", label: t("selectBy.label"), checked: mode === "label", onSelect: () => choose("label") },
    { id: "integration", label: t("selectBy.integration"), checked: mode === "integration", onSelect: () => choose("integration") },
  ];
  const loadingIntegrations = mode === "integration" && !integrations && !integrationsError;
  const selectLabel = mode === "area" ? t("selectBy.area") : mode === "label" ? t("selectBy.label") : t("selectBy.integration");
  const placeholder = loadingIntegrations ? t("selectBy.loadingIntegrations")
    : mode === "area" ? t("selectBy.selectArea") : mode === "label" ? t("selectBy.selectLabel") : t("selectBy.selectIntegration");
  const empty = mode === "area" ? t("selectBy.noAreas") : mode === "label" ? t("selectBy.noLabels") : t("selectBy.noIntegrations");

  return (
    <>
      <MenuButton label={t("selectBy.menu")} help="help.selectBy" icon={<Icon name="filter" size={16} />} items={items}
        pressed={!!choice?.value} className={`btn btn-ghost btn-sm phx-action${iconsOnly ? " is-icon-only" : ""}`}>
        {!iconsOnly && <span>{t("selectBy.menu")}</span>}
      </MenuButton>
      {choice && (
        <select ref={valueSelect} className="input input-auto tree-filter-select" aria-label={selectLabel} value={choice.value}
          disabled={loadingIntegrations} onChange={(e) => onChange({ mode: choice.mode, value: e.target.value })}>
          <option value="">{placeholder}</option>
          {groups.map((group) => <option key={group.id} value={group.id}>{group.name} ({group.count})</option>)}
        </select>
      )}
      {choice && mode === "integration" && integrationsError && <span className="tree-filter-note" role="alert">{integrationsError}</span>}
      {choice && !loadingIntegrations && !integrationsError && groups.length === 0 && <span className="tree-filter-note">{empty}</span>}
    </>
  );
}

// The resolver returns a raw Permission value; the tree shows the label.
function effectiveLabel(effective: string): string {
  const key = PERMISSION_LABEL_KEYS[effective];
  return key ? t(key) : effective;
}

/** Home Assistant's registries whose changes the tree shows: entities, devices, areas and labels. */
const REGISTRY_EVENTS = ["entity_registry_updated", "device_registry_updated", "area_registry_updated", "label_registry_updated"];
/** A burst of registry changes, as when an integration is set up, is read once it settles. */
const REGISTRY_SETTLE_MS = 1000;

type Unsubscribe = () => void;
type EventConnection = { subscribeEvents?: (cb: () => void, event: string) => Promise<Unsubscribe> };

/**
 * Call `onChange` once Home Assistant's registries change. Returns a function
 * that stops; without a connection (outside Home Assistant) it does nothing.
 */
function followRegistries(onChange: () => void): Unsubscribe {
  const conn = (currentHass() as { connection?: EventConnection } | null)?.connection;
  if (!conn?.subscribeEvents) return () => {};
  let active = true;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const unsubscribers: Unsubscribe[] = [];
  const later = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { if (active) onChange(); }, REGISTRY_SETTLE_MS);
  };
  for (const event of REGISTRY_EVENTS) {
    conn.subscribeEvents(later, event)
      .then((unsubscribe) => { if (active) unsubscribers.push(unsubscribe); else unsubscribe(); })
      .catch(() => undefined);
  }
  return () => {
    active = false;
    clearTimeout(timer);
    for (const unsubscribe of unsubscribers) unsubscribe();
  };
}

// The tree and hints last read, so a tree drawn for another access key starts from them
// instead of a loading state, then reads them again behind what it shows.
let lastTree: EntityTree | null = null;
let lastHints: Record<string, string> = {};

/** Keep a tree read elsewhere, so the first tree drawn after a pass-through key needs no loading state either. */
export function rememberEntityTree(tree: EntityTree): void { lastTree = tree; }

export function EntityTree({ accessKeyId, permissions, onPermissionsChange, onEntityClick, collapseKey, domainAllowlist, revealEntity, revealDepth, revealNonce, mesaProfileEntities, mesaProfileDevices, mesaProfileDomains, onOpenMesa, selectBy = false }: Props) {
  const [tree, setTree] = useState<EntityTree | null>(lastTree);
  const [loading, setLoading] = useState(!lastTree);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [globalHints, setGlobalHints] = useState<Record<string, string>>(lastHints);
  const [hintOpenId, setHintOpenId] = useState<string | null>(null);
  const hintEditor = useMemo(() => ({ openId: hintOpenId, setOpenId: setHintOpenId }), [hintOpenId]);
  const [choice, setChoice] = useState<SelectByChoice | null>(null);
  const [integrations, setIntegrations] = useState<IntegrationPermissionOption[] | null>(null);
  const [integrationsError, setIntegrationsError] = useState<string | null>(null);

  // `quiet` reads the tree again behind the one shown, keeping what is expanded and the filters.
  const loadTree = useCallback(async (force = false, quiet = false) => {
    if (!quiet) setLoading(true);
    setError(null);
    // Integration members change with the registry, so reading the tree again reads them too.
    setIntegrations(null);
    setIntegrationsError(null);
    try {
      const data = await api.getEntityTree(force);
      lastTree = data;
      setTree(data);
    } catch (e: unknown) {
      if (!quiet) setError(e instanceof Error ? e.message : t("perms.loadTreeFailed"));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => { loadTree(false, !!lastTree); }, [loadTree]);
  // No Reload button (user, 2026-10-28): the tree follows the registries, and is built afresh each
  // time it comes back into view, which also picks up entities that have no registry entry.
  useEffect(() => followRegistries(() => void loadTree(false, true)), [loadTree]);
  const visible = useWindowVisible();
  const wasVisible = useRef(visible);
  useEffect(() => {
    if (visible && !wasVisible.current) void loadTree(true, true);
    wasVisible.current = visible;
  }, [visible, loadTree]);
  const changeHints = useCallback((hints: Record<string, string>) => { lastHints = hints; setGlobalHints(hints); }, []);
  useEffect(() => { api.getEntityHints().then((r) => changeHints(r.entity_hints)).catch(() => undefined); }, [changeHints]);

  const allEntityIds = React.useMemo(() => {
    if (!tree) return new Set<string>();
    const ids = new Set<string>();
    for (const domain of Object.values(tree)) {
      for (const eid of Object.keys(domain.entity_details)) ids.add(eid);
    }
    return ids;
  }, [tree]);

  const allDeviceIds = React.useMemo(() => {
    if (!tree) return new Set<string>();
    const ids = new Set<string>();
    for (const domain of Object.values(tree)) {
      for (const deviceId of Object.keys(domain.devices)) ids.add(deviceId);
    }
    return ids;
  }, [tree]);

  // Integration members come from the server, read when Integration is first chosen.
  const wantsIntegrations = choice?.mode === "integration" && !integrations && !integrationsError;
  useEffect(() => {
    if (!wantsIntegrations) return;
    let active = true;
    api.getPermissionIntegrationOptions(accessKeyId)
      .then((result) => { if (active) setIntegrations(result.integrations); })
      .catch((reason: unknown) => {
        if (active) setIntegrationsError(reason instanceof Error ? reason.message : t("selectBy.loadIntegrationsFailed"));
      });
    return () => { active = false; };
  }, [wantsIntegrations, accessKeyId]);

  const scope = useMemo<TreeScope | null>(() => {
    if (!tree || !choice?.value) return null;
    if (choice.mode === "integration") {
      const option = integrations?.find((o) => o.entry_id === choice.value);
      return option ? { deviceIds: new Set(option.device_ids), entityIds: new Set(option.entity_ids) } : null;
    }
    const entityIds = new Set<string>();
    for (const domain of Object.values(tree)) {
      for (const [entityId, detail] of Object.entries(domain.entity_details)) {
        const member = choice.mode === "area"
          ? detail.area_id === choice.value
          : detail.labels.some((label) => label.id === choice.value);
        if (member) entityIds.add(entityId);
      }
    }
    return { deviceIds: new Set<string>(), entityIds };
  }, [tree, choice, integrations]);

  if (loading) return <div className="loading-wrap"><div className="spinner" /></div>;
  if (error) return <div className="banner banner-error">{error}</div>;
  if (!tree) return null;

  const domainKeys = Object.keys(tree)
    .filter((d) => !domainAllowlist || domainAllowlist.includes(d))
    .sort();
  const unmatchedDeviceIds = domainAllowlist
    ? []
    : Object.keys(permissions.devices)
      .filter((deviceId) => !allDeviceIds.has(deviceId))
      .sort();
  const visibleUnmatchedDeviceIds = unmatchedDeviceIds.filter((deviceId) =>
    matchesTreeSearch(filter, deviceId, t("perms.unmatchedDeviceName")) && (!scope || scope.deviceIds.has(deviceId)));
  const hasDomainMatch = domainKeys.some((domainKey) => domainMatchesSearch(
    filter, domainKey, tree[domainKey], permissions, allEntityIds,
  ) && domainInScope(scope, tree[domainKey]));
  const hasFilterResults = (!filter.trim() && !scope) || hasDomainMatch || visibleUnmatchedDeviceIds.length > 0;

  return (
    <HintEditorContext.Provider value={hintEditor}>
    <div>
      <div className="tree-filter">
        {selectBy && (
          <SelectByControls tree={tree} choice={choice} integrations={integrations} integrationsError={integrationsError} onChange={setChoice} />
        )}
        <input
          className="input"
          placeholder={t("perms.filterPlaceholder")}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label={t("perms.filterAria")}
        />
      </div>
      <div aria-label={t("perms.treeAria")}>
        {domainKeys.map((domain) => (
          <DomainGroup
            key={domain}
            domainKey={domain}
            domainData={tree[domain]}
            permissions={permissions}
            accessKeyId={accessKeyId}
            filterText={filter}
            scope={scope}
            allEntityIds={allEntityIds}
            onPermChange={onPermissionsChange}
            onEntityClick={onEntityClick}
            collapseKey={collapseKey}
            revealEntity={revealEntity}
            revealDepth={revealDepth}
            revealNonce={revealNonce}
            mesaProfileEntities={mesaProfileEntities}
            mesaProfileDevices={mesaProfileDevices}
            mesaProfileDomains={mesaProfileDomains}
            onOpenMesa={onOpenMesa}
            globalHints={globalHints}
            onGlobalHintsChange={changeHints}
          />
        ))}
        {visibleUnmatchedDeviceIds.length > 0 && (
          <GhostDevicePermissions
            deviceIds={visibleUnmatchedDeviceIds}
            permissions={permissions}
            accessKeyId={accessKeyId}
            onPermChange={onPermissionsChange}
          />
        )}
        {!hasFilterResults && (
          <p className="tree-empty" role="status">{t("perms.filterEmpty")}</p>
        )}
      </div>
    </div>
    </HintEditorContext.Provider>
  );
}
