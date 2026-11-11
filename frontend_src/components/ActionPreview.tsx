/**
 * The review of a system action (a delete, a rename, a helper, an integration, a Zigbee
 * operation): why it needs approval, what happens, what uses the item and which Phoenix
 * settings name it, with the whole preview kept as YAML in a fold at the bottom.
 */

import React from "react";
import { YamlView, toYaml } from "./YamlView";
import { friendlyToolName } from "../utils";
import { localeNumber } from "../i18n/format";
import { t } from "../i18n";

type Preview = Record<string, unknown>;

/** Keys drawn in their own section, so they never repeat as plain rows. */
const STRUCTURED_KEYS = new Set(["approval_reason", "warning", "warnings", "relationships", "phoenix_configuration", "mesa"]);

/** Row labels for preview fields that several tools share; any other key shows as it is. */
const FIELD_LABEL_KEYS: Record<string, string> = {
  name: "perms.colName",
  area_id: "mesa.scopeLabelArea",
  helper_type: "approvals.previewFieldHelperType",
  domain: "perms.typeDomain",
  state: "perms.colState",
  enabled: "approvals.previewFieldEnabled",
  device: "perms.typeDevice",
  note: "approvals.previewFieldNote",
};

/** Consumer kinds in the order they are listed, each with its heading and count. */
const CONSUMER_KINDS: Array<[string, string]> = [
  ["automation", "approvals.previewKindAutomation"],
  ["script", "approvals.previewKindScript"],
  ["scene", "approvals.previewKindScene"],
  ["group", "approvals.previewKindGroup"],
  ["person", "approvals.previewKindPerson"],
  ["dashboard", "approvals.previewKindDashboard"],
  ["config_entry", "approvals.previewKindConfigEntry"],
];

const ROLE_LABEL_KEYS: Record<string, string> = {
  trigger: "mesa.roleTrigger",
  condition: "mesa.roleCondition",
  action: "mesa.roleAction",
  sequence: "approvals.previewRoleSequence",
  member: "approvals.previewRoleMember",
  reference: "approvals.previewRoleReference",
  target: "approvals.previewRoleTarget",
  card: "approvals.previewRoleCard",
};

const DECISION_LABEL_KEYS: Record<string, string> = {
  allow: "caps.modeAllow",
  confirm: "mesa.modeConfirm",
  deny: "caps.modeDeny",
};

/** Dashboard path segments that name a place a reader can find; other keys are skipped. */
const PATH_LABEL_KEYS: Record<string, string> = {
  views: "approvals.previewPathView",
  sections: "approvals.previewPathSection",
  cards: "approvals.previewPathCard",
  badges: "approvals.previewPathBadge",
};

/** The most consumers listed under one kind before the rest are counted. */
export const CONSUMER_ROW_CAP = 8;

type Consumer = {
  kind?: unknown; id?: unknown; name?: unknown; entity_id?: unknown; roles?: unknown; paths?: unknown;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item !== "") : [];

const label = (key: string | undefined, raw: string) => (key ? t(key) : raw);

function scalarText(value: unknown): string {
  if (value === null || value === undefined) return t("common.none");
  if (typeof value === "boolean") return t(value ? "mesa.yes" : "mesa.no");
  if (typeof value === "number") return localeNumber(value);
  return String(value);
}

/** One side of a changed field: a scalar as a row shows it, a list joined, a small mapping as label: value. */
function sideText(value: unknown): string {
  if (Array.isArray(value)) {
    const items = value.map((item) => (item === null || typeof item !== "object" ? scalarText(item) : JSON.stringify(item)));
    return items.length > 0 ? items.join(t("common.listSeparator")) : t("common.none");
  }
  if (isRecord(value)) {
    const pairs = Object.entries(value).map(([k, v]) => `${k}: ${v === null || typeof v !== "object" ? scalarText(v) : JSON.stringify(v)}`);
    return pairs.length > 0 ? pairs.join(t("common.listSeparator")) : t("common.none");
  }
  return scalarText(value);
}

/** A field the request changes, stored as {before, after}. */
const isChange = (value: unknown): value is { before: unknown; after: unknown } =>
  isRecord(value) && "before" in value && "after" in value;

function decisionLabel(decision: unknown): string {
  return typeof decision === "string" ? label(DECISION_LABEL_KEYS[decision], decision) : "";
}

function roleText(roles: unknown): string {
  return strings(roles).map((role) => label(ROLE_LABEL_KEYS[role], role)).join(t("common.listSeparator"));
}

/** A dashboard hit's patch path as the places it names: "view 1 > card 3". */
function dashboardPlace(path: unknown): string {
  if (!Array.isArray(path)) return "";
  const parts: string[] = [];
  for (let i = 0; i < path.length - 1; i++) {
    const key = PATH_LABEL_KEYS[String(path[i])];
    const index = path[i + 1];
    if (key && typeof index === "number") {
      parts.push(t(key, { number: localeNumber(index + 1) }));
      i++;
    }
  }
  return parts.join(" > ");
}

function consumerDetail(consumer: Consumer): string {
  if (consumer.kind === "dashboard" && Array.isArray(consumer.paths)) {
    const places = [...new Set(consumer.paths.map(dashboardPlace).filter(Boolean))];
    if (places.length) return places.join(t("common.listSeparator"));
  }
  return roleText(consumer.roles);
}

function consumerName(consumer: Consumer): string {
  for (const value of [consumer.name, consumer.entity_id, consumer.id]) {
    if (typeof value === "string" && value) return value;
  }
  return t("common.none");
}

function ConsumerGroup({ heading, consumers }: { heading: string; consumers: Consumer[] }) {
  const shown = consumers.slice(0, CONSUMER_ROW_CAP);
  const hidden = consumers.length - shown.length;
  return (
    <div className="action-preview-group">
      <h5 className="action-preview-subheading">{heading}</h5>
      <ul className="action-preview-list">
        {shown.map((consumer, index) => (
          <li key={`${String(consumer.id ?? "")}/${index}`} className="action-preview-consumer">
            <span className="action-preview-name">{consumerName(consumer)}</span>
            <span className="action-preview-detail">{consumerDetail(consumer)}</span>
          </li>
        ))}
        {hidden > 0 && <li className="action-preview-more">{t("approvals.previewMore", { count: localeNumber(hidden) })}</li>}
      </ul>
    </div>
  );
}

function UsedBy({ relationships }: { relationships: Record<string, unknown> }) {
  const consumers = (Array.isArray(relationships.consumers) ? relationships.consumers : []).filter(isRecord) as Consumer[];
  const searched = strings(relationships.searched);
  const notChecked = (Array.isArray(relationships.not_searched) ? relationships.not_searched : [])
    .filter(isRecord)
    .map((gap) => (typeof gap.reason === "string" && gap.reason ? gap.reason : typeof gap.kind === "string" ? gap.kind : ""))
    .filter(Boolean);
  if (!consumers.length && !searched.length && !notChecked.length) return null;
  const known = new Set(CONSUMER_KINDS.map(([kind]) => kind));
  const others = [...new Set(consumers.map((c) => String(c.kind ?? "")).filter((kind) => !known.has(kind)))];
  const heading = (kind: string, key: string | undefined, count: number) => key
    ? t(key, { count: localeNumber(count) })
    : t("common.labelValue", { label: kind, value: localeNumber(count) });
  const groups: Array<[string, string | undefined]> = [...CONSUMER_KINDS, ...others.map((kind): [string, undefined] => [kind, undefined])];
  const count = typeof relationships.consumer_count === "number" ? relationships.consumer_count : consumers.length;
  return (
    <section className="action-preview-section">
      <h4 className="action-preview-heading">
        {consumers.length ? t("approvals.previewUsedByCount", { count: localeNumber(count) }) : t("approvals.previewUsedByHeading")}
      </h4>
      {consumers.length === 0 && searched.length > 0 && <p className="action-preview-text">{t("approvals.previewNothingUses")}</p>}
      {groups.map(([kind, key]) => {
        const members = consumers.filter((c) => String(c.kind ?? "") === kind);
        return members.length ? <ConsumerGroup key={kind} heading={heading(kind, key, members.length)} consumers={members} /> : null;
      })}
      {notChecked.length > 0 && (
        <ul className="mesa-preview-warnings">
          {[...new Set(notChecked)].map((reason) => <li key={reason}>{t("approvals.previewNotChecked", { reason })}</li>)}
        </ul>
      )}
    </section>
  );
}

function phoenixReferenceText(ref: Record<string, unknown>): string {
  const text = (value: unknown) => (typeof value === "string" || typeof value === "number" ? String(value) : "");
  switch (ref.kind) {
    case "access_key_permission":
      return t("approvals.previewRefAccessKey", { name: text(ref.access_key_name) || text(ref.access_key_id) });
    case "preset_permission":
      return t("approvals.previewRefPreset", {
        preset: text(ref.preset_name) || text(ref.preset_id), name: text(ref.access_key_name) || text(ref.access_key_id),
      });
    case "global_hint":
      return t("approvals.previewRefHint");
    case "pending_approval":
      return t("approvals.previewRefPending", { tool: friendlyToolName(text(ref.tool_name)), id: text(ref.approval_id) });
    default:
      return text(ref.kind);
  }
}

/** The registry shapes of a MESA verdict (one decision, or one per entity) as rows. */
function mesaRows(mesa: unknown): Array<{ key: string; label: string; value: string }> {
  const items = Array.isArray(mesa) ? mesa.filter(isRecord) : isRecord(mesa) ? [mesa] : [];
  return items.flatMap((item, index) => {
    const decision = decisionLabel(item.decision);
    if (!decision) return [];
    const reason = typeof item.reason === "string" && item.reason ? item.reason : "";
    const entity = typeof item.entity_id === "string" ? item.entity_id : "";
    const value = reason ? t("common.labelValue", { label: decision, value: reason }) : decision;
    return [{ key: `mesa-${index}`, label: t("audit.rowMesa"), value: entity ? t("common.labelValue", { label: entity, value }) : value }];
  });
}

/** The service-call shape, which MesaPreviewBlock already draws. */
function isServiceMesa(mesa: unknown): mesa is Record<string, unknown> {
  return isRecord(mesa) && ("confirm_entities" in mesa || "allowed_entities" in mesa || "blocked" in mesa);
}

export function MesaPreviewBlock({ mesa }: { mesa: Record<string, unknown> }) {
  const confirm = (mesa.confirm_entities as string[]) ?? [];
  const allowed = (mesa.allowed_entities as string[]) ?? [];
  const blocked = (mesa.blocked as Array<{ entity_id: string; rule: string }>) ?? [];
  const warnings = (mesa.warnings as string[]) ?? [];
  return (
    <div className="mesa-preview-block">
      <div className="approval-detail-meta">
        <span className="stat-label">{t("approvals.mesaConfirm")}</span>
        <span><code>{confirm.length ? confirm.join(t("common.listSeparator")) : t("common.none")}</code></span>
        <span className="stat-label">{t("approvals.mesaAllowed")}</span>
        <span><code>{allowed.length ? allowed.join(t("common.listSeparator")) : t("common.none")}</code></span>
        {blocked.length > 0 && (
          <>
            <span className="stat-label">{t("approvals.mesaBlocked")}</span>
            <span><code>{blocked.map((b) => `${b.entity_id} (${b.rule})`).join(t("common.listSeparator"))}</code></span>
          </>
        )}
      </div>
      {warnings.length > 0 && (
        <ul className="mesa-preview-warnings">
          {warnings.map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      )}
    </div>
  );
}

/** A system action's preview, laid out for a decision, with the full data folded below. */
export function ActionPreview({ preview }: { preview: Preview }) {
  if (Object.keys(preview).length === 0) return null;
  const reason = typeof preview.approval_reason === "string" ? preview.approval_reason : "";
  const warnings = [
    ...(typeof preview.warning === "string" && preview.warning ? [preview.warning] : []),
    ...strings(preview.warnings),
  ];
  const mesa = preview.mesa;
  const serviceMesa = isServiceMesa(mesa) ? mesa : null;
  const mesaNotes = !serviceMesa && isRecord(mesa)
    ? [...strings(mesa.warnings), ...(typeof mesa.note === "string" && mesa.note ? [mesa.note] : [])]
    : [];
  const rows = [
    ...Object.entries(preview)
      .filter(([key, value]) => !STRUCTURED_KEYS.has(key) && (value === null || typeof value !== "object" || isChange(value)))
      .map(([key, value]) => ({
        key,
        label: label(FIELD_LABEL_KEYS[key], key),
        value: isChange(value)
          ? t("approvals.previewChange", { before: sideText(value.before), after: sideText(value.after) })
          : scalarText(value),
      })),
    ...(serviceMesa ? [] : mesaRows(mesa)),
  ];
  const relationships = isRecord(preview.relationships) ? preview.relationships : null;
  const references = (Array.isArray(preview.phoenix_configuration) ? preview.phoenix_configuration : []).filter(isRecord);
  const happens = warnings.length > 0 || rows.length > 0 || mesaNotes.length > 0 || serviceMesa !== null;
  return (
    <div className="action-preview">
      {reason && (
        <section className="action-preview-section">
          <h4 className="action-preview-heading">{t("approvals.previewWhyHeading")}</h4>
          <p className="action-preview-text">{reason}</p>
        </section>
      )}
      {happens && (
        <section className="action-preview-section">
          <h4 className="action-preview-heading">{t("approvals.previewWhatHeading")}</h4>
          {warnings.length > 0 && (
            <div className="banner banner-warn action-preview-warning" role="note">
              {warnings.length === 1 ? warnings[0] : <ul>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
            </div>
          )}
          {rows.length > 0 && (
            <div className="approval-detail-meta">
              {rows.map((row) => (
                <React.Fragment key={row.key}>
                  <span className="stat-label">{row.label}</span>
                  <span className="action-preview-value">{row.value}</span>
                </React.Fragment>
              ))}
            </div>
          )}
          {mesaNotes.length > 0 && (
            <ul className="mesa-preview-warnings">
              {mesaNotes.map((note, i) => <li key={i}>{note}</li>)}
            </ul>
          )}
          {serviceMesa && <MesaPreviewBlock mesa={serviceMesa} />}
        </section>
      )}
      {relationships && <UsedBy relationships={relationships} />}
      {references.length > 0 && (
        <section className="action-preview-section">
          <h4 className="action-preview-heading">{t("approvals.previewPhoenixHeading")}</h4>
          <ul className="action-preview-list">
            {references.map((ref, i) => <li key={i}>{phoenixReferenceText(ref)}</li>)}
          </ul>
        </section>
      )}
      <details className="action-preview-details">
        <summary data-help="help.previewAllDetails">{t("approvals.previewAllDetails")}</summary>
        <YamlView value={toYaml(preview)} />
      </details>
    </div>
  );
}
