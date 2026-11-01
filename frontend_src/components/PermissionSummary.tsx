import { useState, useMemo } from "react";
import type { PermissionTree, NodeState, EntityTree } from "../types";
import { MesaProfileLink } from "./MesaProfileLink";
import { t } from "../i18n";
import { ConfigTable, type Column } from "../ui/table/ConfigTable";
import { companionCap, companionDomainCap, type CompanionCap, type CompanionDomains } from "../utils/companion_reach";

interface Props {
  permissions: PermissionTree;
  entityTree?: EntityTree | null;
  onEntityClick?: (entityId: string, depth?: "entity" | "device" | "domain") => void;
  mesaProfileEntities?: Set<string>;
  onOpenMesa?: (entityId: string) => void;
  /** A Home Companion key's reach: rules it can never use read No Access, and Write in a read domain reads Read. */
  companion?: CompanionDomains | null;
}

// The state's own words, as the Permissions tree names them (user, 2026-10-28).
const STATE_LABEL_KEYS: Record<NodeState, string> = {
  GREY: "perms.levelInherit",
  YELLOW: "perms.levelRead",
  GREEN: "perms.levelWrite",
  RED: "perms.levelDeny",
};

const STATE_CLASS: Record<NodeState, string> = {
  GREY: "state-GREY",
  YELLOW: "state-YELLOW",
  GREEN: "state-GREEN",
  RED: "state-RED",
};

type NodeType = "domain" | "device" | "entity";

const TYPE_ORDER: Record<NodeType, number> = { domain: 0, device: 1, entity: 2 };
const STATE_ORDER: Record<NodeState, number> = { GREEN: 0, YELLOW: 1, RED: 2, GREY: 3 };

const TYPE_LABEL_KEYS: Record<NodeType, string> = {
  domain: "perms.typeDomain",
  device: "perms.typeDevice",
  entity: "perms.typeEntity",
};

interface SummaryItem {
  id: string;
  type: NodeType;
  friendlyName: string;
  state: NodeState;
}

function buildLookups(entityTree: EntityTree | null | undefined): {
  entityNames: Map<string, string>;
  deviceNames: Map<string, string>;
  domainFirstEntity: Map<string, string>;
  deviceFirstEntity: Map<string, string>;
  entityToDevice: Map<string, string>;
  /** Every domain a device has entities in; a device can span several. */
  deviceDomain: Map<string, string[]>;
  deviceParent: Map<string, string>;
} {
  const entityNames = new Map<string, string>();
  const deviceNames = new Map<string, string>();
  const domainFirstEntity = new Map<string, string>();
  const deviceFirstEntity = new Map<string, string>();
  const entityToDevice = new Map<string, string>();
  const deviceDomain = new Map<string, string[]>();
  const deviceParent = new Map<string, string>();
  if (!entityTree) return { entityNames, deviceNames, domainFirstEntity, deviceFirstEntity, entityToDevice, deviceDomain, deviceParent };
  for (const [domainKey, domain] of Object.entries(entityTree)) {
    for (const [eid, info] of Object.entries(domain.entity_details)) {
      if (info.friendly_name) entityNames.set(eid, info.friendly_name);
    }
    const domFirst = domain.deviceless_entities[0]
      ?? Object.values(domain.devices).find((device) => device.entities.length > 0)?.entities[0];
    if (domFirst) domainFirstEntity.set(domainKey, domFirst);
    for (const [did, device] of Object.entries(domain.devices)) {
      deviceNames.set(did, device.name);
      if (device.entities[0]) deviceFirstEntity.set(did, device.entities[0]);
      // A parent listed only for its parts has no entity of its own in this domain.
      if (device.entities.length > 0) deviceDomain.set(did, [...(deviceDomain.get(did) ?? []), domainKey]);
      if (device.parent_device_id) deviceParent.set(did, device.parent_device_id);
      for (const eid of device.entities) {
        entityToDevice.set(eid, did);
      }
    }
  }
  return { entityNames, deviceNames, domainFirstEntity, deviceFirstEntity, entityToDevice, deviceDomain, deviceParent };
}

function resolvedState(
  permissions: PermissionTree,
  item: SummaryItem,
  entityToDevice: Map<string, string>,
  deviceDomain: Map<string, string[]>,
  deviceParent: Map<string, string>,
): NodeState {
  // Pass 1: walk ancestor chain for RED
  const toCheck: NodeState[] = [];
  if (item.type === "entity") {
    const deviceId = entityToDevice.get(item.id);
    if (deviceId) toCheck.push(permissions.devices[deviceId]?.state ?? "GREY");
    const parentId = deviceId ? deviceParent.get(deviceId) : undefined;
    if (parentId) toCheck.push(permissions.devices[parentId]?.state ?? "GREY");
    toCheck.push(permissions.domains[item.id.split(".")[0]]?.state ?? "GREY");
  } else if (item.type === "device") {
    const parentId = deviceParent.get(item.id);
    if (parentId) toCheck.push(permissions.devices[parentId]?.state ?? "GREY");
    // A Deny on one of its domains covers only that part of the device; on all of them, the whole device.
    const domains = deviceDomain.get(item.id) ?? [];
    if (domains.length && domains.every((d) => permissions.domains[d]?.state === "RED")) toCheck.push("RED");
  }
  if (toCheck.some((s) => s === "RED")) return "RED";
  // Pass 2: item's own state is the most specific non-GREY grant
  return item.state;
}

const BY_TYPE = { column: "type", dir: "asc" } as const;
const keyOf = (item: SummaryItem) => `${item.type}:${item.id}`;

export function PermissionSummary({ permissions, entityTree, onEntityClick, mesaProfileEntities, onOpenMesa, companion = null }: Props) {
  const [chosen, setChosen] = useState<string | null>(null);

  const { entityNames, deviceNames, domainFirstEntity, deviceFirstEntity, entityToDevice, deviceDomain, deviceParent } = useMemo(
    () => buildLookups(entityTree),
    [entityTree],
  );

  const items = useMemo<SummaryItem[]>(() => {
    const result: SummaryItem[] = [];
    for (const [domain, node] of Object.entries(permissions.domains)) {
      if (node.state !== "GREY") {
        result.push({ id: domain, type: "domain", friendlyName: domain, state: node.state });
      }
    }
    for (const [deviceId, node] of Object.entries(permissions.devices)) {
      if (node.state !== "GREY") {
        result.push({
          id: deviceId,
          type: "device",
          friendlyName: deviceNames.get(deviceId) ?? deviceId,
          state: node.state,
        });
      }
    }
    for (const [entityId, node] of Object.entries(permissions.entities)) {
      if (node.state !== "GREY") {
        result.push({
          id: entityId,
          type: "entity",
          friendlyName: entityNames.get(entityId) ?? entityId,
          state: node.state,
        });
      }
    }
    return result;
  }, [permissions, entityNames, deviceNames]);

  if (items.length === 0) {
    return (
      <p className="perm-summary-empty" data-guide="window.permissionSummary#empty">{t("perms.summaryEmpty")}</p>
    );
  }

  const stateOf = (item: SummaryItem) => resolvedState(permissions, item, entityToDevice, deviceDomain, deviceParent);
  const capOf = (item: SummaryItem, d: CompanionDomains): CompanionCap => item.type === "entity"
    ? companionCap(item.id, d)
    : item.type === "domain" ? companionDomainCap(item.id, d)
      // A device reaches as far as the widest of its domains.
      : (deviceDomain.get(item.id) ?? []).map((domain) => companionDomainCap(domain, d))
        .reduce<CompanionCap>((best, cap) => (best === "control" || cap === "control" ? "control" : best ?? cap), null);
  // What the State column shows: the rule's state, or what a Home Companion key really gets from it.
  const shown = (item: SummaryItem): { cls: string; label: string; order: number } => {
    let state = stateOf(item);
    if (companion) {
      const cap = capOf(item, companion);
      if (cap === null) return { cls: STATE_CLASS.GREY, label: t("perms.stateNoAccess"), order: STATE_ORDER.GREY + 1 };
      if (cap === "read" && state === "GREEN") state = "YELLOW";
    }
    return { cls: STATE_CLASS[state], label: t(STATE_LABEL_KEYS[state]), order: STATE_ORDER[state] };
  };
  // Choosing a rule finds it in the Permissions tree: an entity itself, or a device's or domain's first entity.
  const target = (item: SummaryItem): { entity: string; depth: "entity" | "device" | "domain" } | null => {
    const entity = item.type === "entity" ? item.id : item.type === "domain" ? domainFirstEntity.get(item.id) : deviceFirstEntity.get(item.id);
    return entity ? { entity, depth: item.type } : null;
  };
  const nameOf = (item: SummaryItem) => (item.friendlyName !== item.id ? item.friendlyName : "");
  // A configurable table like the other workspaces' lists (user, 2026-10-28). Name and ID stay narrow
  // enough for all four columns to fit half the workspace; their full text is in the tooltip.
  const columns: Column<SummaryItem>[] = [
    { id: "type", label: t("perms.colType"), width: 100, sortValue: (item) => TYPE_ORDER[item.type], cell: (item) => t(TYPE_LABEL_KEYS[item.type]) },
    { id: "name", label: t("perms.colName"), width: 180, maxWidth: 180, sortValue: nameOf, title: (item) => nameOf(item) || undefined,
      cell: (item) => (
        <span className="perm-summary-name-wrap">
          {item.type === "entity" && onOpenMesa && (
            <MesaProfileLink targetKey={item.id} exists={!!mesaProfileEntities?.has(item.id)} onOpen={onOpenMesa} />
          )}
          {nameOf(item)}
        </span>
      ) },
    { id: "id", label: t("perms.colId"), width: 180, maxWidth: 180, sortValue: (item) => item.id, title: (item) => item.id, cell: (item) => item.id, className: "phx-cell-mono" },
    { id: "state", label: t("perms.colState"), width: 100, sortValue: (item) => shown(item).order,
      cell: (item) => { const s = shown(item); return <span className={s.cls}>{s.label}</span>; } },
  ];

  return (
    <ConfigTable
      id="permissionSummary"
      label={t("accessKeys.permissionSummary")}
      defaultSort={BY_TYPE}
      columns={columns}
      rows={items}
      rowKey={keyOf}
      selectedKey={chosen}
      onSelect={(item) => {
        setChosen(keyOf(item));
        const found = target(item);
        if (found) onEntityClick?.(found.entity, found.depth);
      }}
      rowLabel={(item) => [t(TYPE_LABEL_KEYS[item.type]), nameOf(item), item.id, shown(item).label].filter(Boolean).join(t("common.listSeparator"))}
    />
  );
}
