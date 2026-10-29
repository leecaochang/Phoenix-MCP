import type { ApprovalDiff } from "../types";
import { hasMessage, t, type Params } from "../i18n";
import { dashboardDisplayName, withDashboardLabel } from "./dashboard_names";

export interface FriendlyApprovalSummary {
  title: string;
  body: string;
}

export const ENERGY_SOURCE_LABEL_KEYS: Readonly<Record<string, string>> = {
  grid: "approvals.energyGrid",
  solar: "approvals.energySolar",
  battery: "approvals.energyBattery",
  gas: "approvals.energyGas",
  water: "approvals.energyWater",
};

/**
 * A request's summary params in the reader's language: a sentinel dashboard
 * name, a blueprint's automation or script domain, and an Energy source type
 * are backend slugs that would otherwise sit untranslated inside the sentence.
 */
export function approvalParams(diff: ApprovalDiff | undefined): Params | undefined {
  const params = withDashboardLabel(diff?.summary_params, diff?.target?.type);
  if (!params) return params;
  const out: Params = { ...params };
  const key = diff?.summary_key ?? "";
  if (key.startsWith("diff.blueprint.") && typeof out.domain === "string") {
    const kind = `approvalSummary.named.kind.${out.domain}`;
    if (hasMessage(kind)) out.domain = t(kind);
  }
  if (key.startsWith("diff.edit_energy_config.") && typeof out.source_type === "string") {
    const label = ENERGY_SOURCE_LABEL_KEYS[out.source_type];
    if (label) out.source_type = t(label);
  }
  return out;
}

function basename(value: string): string {
  const clean = value.replace(/\\/g, "/").replace(/\/$/, "");
  return clean.slice(clean.lastIndexOf("/") + 1) || value;
}

function targetValue(diff: ApprovalDiff | undefined): string | null {
  const value = diff?.target?.label || diff?.target?.id;
  return typeof value === "string" && value.trim() ? dashboardDisplayName(value.trim(), diff?.target?.type) : null;
}

function registryEntityLabel(diff: ApprovalDiff, fallback: string | null): string | null {
  if (fallback && fallback !== diff.target?.id) return fallback;
  const aliases = diff.preview?.aliases;
  if (!aliases || typeof aliases !== "object") return fallback;
  const before = (aliases as Record<string, unknown>).before;
  if (!Array.isArray(before)) return fallback;
  const resolved = before.find((value) =>
    typeof value === "string" && value.trim() && value.trim() !== diff.target?.id,
  );
  return typeof resolved === "string" ? resolved.trim() : fallback;
}

function readableIdentifier(value: string | null): string | null {
  if (!value) return null;
  return /^[a-f0-9]{24,}$/i.test(value) || /^[0-9A-HJKMNP-TV-Z]{26}$/i.test(value)
    ? null
    : value;
}

function removalIntegration(diff: ApprovalDiff): string {
  const owner = diff.preview?.selected_owner;
  if (!owner || typeof owner !== "object") return t("approvalSummary.named.removeDevice.integrationFallback");
  const record = owner as Record<string, unknown>;
  for (const key of ["title", "domain"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return t("approvalSummary.named.removeDevice.integrationFallback");
}

function resourceKind(kind: string, diff: ApprovalDiff): string {
  if (kind !== "helper") return t(`approvalSummary.named.kind.${kind}`);
  const helperType = String(diff.summary_params?.helper_type || "");
  const known = new Set([
    "input_boolean", "input_number", "input_text", "input_select",
    "input_datetime", "input_button", "counter", "timer", "schedule", "zone", "tag", "person",
  ]);
  return t(`approvalSummary.named.kind.${known.has(helperType) ? helperType : "helper"}`);
}

function namedResource(key: string, diff: ApprovalDiff, label: string | null): FriendlyApprovalSummary | null {
  const spec: Record<string, { action: "create" | "edit" | "delete"; kind: string }> = {
    create_script: { action: "create", kind: "script" },
    edit_script: { action: "edit", kind: "script" },
    delete_script: { action: "delete", kind: "script" },
    edit_scene: { action: "edit", kind: "scene" },
    delete_scene: { action: "delete", kind: "scene" },
    edit_helper: { action: "edit", kind: "helper" },
    delete_helper: { action: "delete", kind: "helper" },
    delete_automation: { action: "delete", kind: "automation" },
  };
  const resource = spec[key];
  if (!resource) return null;
  const kind = resourceKind(resource.kind, diff);
  if (!label || label === diff.target?.id) {
    if (resource.action !== "delete") return null;
    return {
      title: t("approvalSummary.named.resource.deleteUnnamedTitle", { kind }),
      body: t("approvalSummary.named.resource.deleteUnnamedBody", { kind }),
    };
  }
  const title = t(`approvalSummary.named.resource.${resource.action}Title`, {
    kind, label,
  });
  const body = t(`approvalSummary.named.resource.${resource.action}Body`, {
    kind, label,
  });
  return { title, body };
}

/** Resolve operator copy without interpreting rendered YAML or generating text. */
function baseApprovalSummary(diff: ApprovalDiff | undefined): FriendlyApprovalSummary {
  const summaryKey = diff?.summary_key;
  const params = approvalParams(diff);
  if (diff && summaryKey?.startsWith("diff.")) {
    const key = summaryKey.slice("diff.".length);
    const label = targetValue(diff);
    const resource = namedResource(key, diff, label);
    if (resource) return resource;
    if (key === "edit_automation" && label) {
      return {
        title: t("approvalSummary.named.editAutomation.title", { label }),
        body: t("approvalSummary.edit_automation.body", params),
      };
    }
    const registryLabel = key === "set_entity" ? registryEntityLabel(diff, label) : label;
    if ((key === "set_entity" || key === "set_device") && registryLabel) {
      return {
        title: t("approvalSummary.fallback.item.title", { label: registryLabel }),
        body: t(`approvalSummary.${key}.body`, params),
      };
    }
    if (key === "remove_device") {
      const device = readableIdentifier(label);
      const integration = removalIntegration(diff);
      return device ? {
        title: t("approvalSummary.named.removeDevice.title", { device, integration }),
        body: t("approvalSummary.named.removeDevice.body", { device, integration }),
      } : {
        title: t("approvalSummary.named.removeDevice.unnamedTitle", { integration }),
        body: t("approvalSummary.named.removeDevice.unnamedBody", { integration }),
      };
    }
    const titleKey = `approvalSummary.${key}.title`;
    const bodyKey = `approvalSummary.${key}.body`;
    if (hasMessage(titleKey) && hasMessage(bodyKey)) {
      return {
        title: t(titleKey, params),
        body: t(bodyKey, params),
      };
    }
  }

  const target = diff?.target;
  const label = targetValue(diff);
  const type = (target?.type || "").toLowerCase();
  const preview = diff?.preview || {};
  const previewFile = typeof preview.file === "string" ? preview.file : null;
  const file = basename(label || previewFile || "");

  if ((type.includes("file") || type.includes("yaml") || previewFile) && file) {
    return {
      title: t("approvalSummary.fallback.file.title", { file }),
      body: "",
    };
  }
  if (type.includes("integration") && label) {
    return {
      title: t("approvalSummary.fallback.integration.title", { label }),
      body: "",
    };
  }
  if ((type.includes("device") || type.includes("entity")) && label) {
    return {
      title: t("approvalSummary.fallback.item.title", { label }),
      body: "",
    };
  }
  if (type.includes("service") && label) {
    return {
      title: t("approvalSummary.fallback.service.title", { label }),
      body: "",
    };
  }
  return {
    title: t("approvalSummary.fallback.unknown.title"),
    body: "",
  };
}

/** Explain server-declared target semantics without interpreting saved configuration. */
export function friendlyApprovalSummary(diff: ApprovalDiff | undefined): FriendlyApprovalSummary {
  const summary = baseApprovalSummary(diff);
  const semantics = diff?.preview?.target_semantics;
  const message = semantics === "exact" ? "targets.exact"
    : semantics === "future_native_execution" ? "targets.futureNative"
    : semantics === "dynamic_accessible_devices" ? "targets.dynamicAccessible" : null;
  if (!message) return summary;
  const note = t(`approvalSummary.${message}`);
  // A request with no consequence copy of its own keeps the note alone.
  return { ...summary, body: summary.body ? [summary.body, note].join(t("common.sentenceSeparator")) : note };
}
