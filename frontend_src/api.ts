import type {
  AttentionItem,
  ConversationEntry, ConversationListResponse,
  CommandRoutePreview,
  RoutingStatisticsResponse,
  AgentCliProviderKind,
  AgentCliInstance,
  AgentCliProvidersResponse,
  AiTaskPreferredStatus,
  ApprovalListParams,
  ApprovalListResponse,
  ApprovalRecord,
  BulkPermissionResult,
  BatchApproveResult,
  AuditEntry,
  AuditListResponse,
  AuditQueryParams,
  CardCatalogResponse,
  ArchivedAccessKeyRecord,
  CreateAccessKeyBody,
  DeclaredModelCaps,
  EntityTree,
  GlobalSettings,
  IntegrationPermissionOption,
  MesaExportArchive,
  MesaImportResult,
  MesaIssuesResponse,
  MesaSuggestionActionResponse,
  MesaProfileDetail,
  MesaProfileDocument,
  MesaProfilesResponse,
  MesaPutResponse,
  PatchAccessKeyBody,
  PermissionPatchBody,
  PermissionTree,
  ResolveResult,
  ScopeResult,
  AccessKeyConnection,
  AccessKeyCreateResponse,
  AccessKeyRecord,
  AccessKeyStats,
  VersionListResponse,
  VersionRecord,
  VersionRestoreResponse,
} from "./types";

import { validApiResponse } from "./api_response";

import { hasMessage, resolveLanguage, t } from "./i18n";

import { setAgentCliOwner } from "./utils/agentcli_state";

const BASE = "/api/phoenix-mcp/admin";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let hassInstance: any = null;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function setHass(hass: any) {
  hassInstance = hass;
  setAgentCliOwner(typeof hass?.user?.id === "string" ? hass.user.id : null);
}

// The hass to authenticate with: the live one on the page's <home-assistant>
// element, else the one passed to setHass. The in-context inject modal can run
// in a module realm where setHass was never called (a second injector copy, or
// one stood down by the singleton guard); without the live lookup its requests
// would carry no Authorization header and HA would 401 and ban-log them. The
// panel reads the same object either way.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function authHass(): any {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const live = (document.querySelector("home-assistant") as any)?.hass;
    if (typeof live?.fetchWithAuth === "function") return live;
  } catch { /* use the object most recently supplied by the panel */ }
  return hassInstance;
}

// Read the current HA client for scoped card-example harvesting.
export function currentHass(): unknown {
  return authHass();
}

export function localizedApiMessage(
  message: string,
  messageKey?: string,
  messageParams?: Record<string, string | number>,
): string {
  return messageKey && hasMessage(messageKey) ? t(messageKey, messageParams) : message;
}

class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string, messageKey?: string, messageParams?: Record<string, string | number>) {
    // `message` is the English the backend sent and stays the fallback: it is
    // what every un-migrated endpoint returns, and what an older backend
    // returns to a newer panel. A key, when present, localizes it.
    super(localizedApiMessage(message, messageKey, messageParams));
    this.status = status;
    this.code = code;
  }
}

async function _doReq<T>(method: string, path: string, body?: unknown, extraHeaders?: Record<string, string>): Promise<T> {
  const hass = authHass();
  if (typeof hass?.fetchWithAuth !== "function") {
    throw new ApiError(401, "unauthorized", t("common.noSession"));
  }
  const headers: Record<string, string> = { "Content-Type": "application/json", ...extraHeaders };
  const opts: RequestInit = { method, headers };
  if (body !== undefined) opts.body = JSON.stringify(body);

  // HA owns access-token refresh and request authentication. Calling
  // refreshAccessToken here races its WebSocket/session lifecycle in browsers.
  const res = await hass.fetchWithAuth(`${BASE}${path}`, opts);

  if (res.status === 204) return undefined as T;
  let json: unknown;
  try { json = await res.json(); }
  catch {
    if (!res.ok) throw new ApiError(res.status, "parse_error", res.statusText || t("common.invalidResponse"));
    throw new ApiError(502, "invalid_response", t("common.invalidResponse"));
  }
  const error = json !== null && typeof json === "object" && !Array.isArray(json)
    ? json as Record<string, unknown> : {};

  if (!res.ok) {
    throw new ApiError(res.status, typeof error.error === "string" ? error.error : "unknown", typeof error.message === "string" ? error.message : res.statusText,
                       typeof error.message_key === "string" ? error.message_key : undefined,
                       error.message_params as Record<string, string | number> | undefined);
  }
  if (!validApiResponse(path, method, json)) throw new ApiError(502, "invalid_response", t("common.invalidResponse"));
  return json as T;
}

const accessKeyMutations = new Map<string, Promise<unknown>>();

async function req<T>(method: string, path: string, body?: unknown, extraHeaders?: Record<string, string>): Promise<T> {
  const accessKey = method === "GET" ? undefined : /^\/access-keys\/([^/]+)(?:\/|$)/.exec(path)?.[1];
  if (!accessKey) return _doReq<T>(method, path, body, extraHeaders);
  const owner = authHass()?.user?.id;
  const previous = accessKeyMutations.get(accessKey);
  const result = (previous ?? Promise.resolve()).catch(() => {}).then(() => {
    if (authHass()?.user?.id !== owner) throw new ApiError(401, "unauthorized", t("common.noSession"));
    return _doReq<T>(method, path, body, extraHeaders);
  });
  accessKeyMutations.set(accessKey, result);
  try { return await result; }
  finally { if (accessKeyMutations.get(accessKey) === result) accessKeyMutations.delete(accessKey); }
}

function buildQuery(params?: AuditQueryParams): string {
  if (!params) return "";
  const p = new URLSearchParams();
  if (params.limit !== undefined) p.set("limit", String(params.limit));
  if (params.offset !== undefined) p.set("offset", String(params.offset));
  if (params.access_key_id) p.set("access_key_id", params.access_key_id);
  if (params.outcome) p.set("outcome", params.outcome);
  if (params.ip) p.set("ip", params.ip);
  if (params.method) p.set("method", params.method);
  if (params.resource) p.set("resource", params.resource);
  if (params.since) p.set("since", params.since);
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  listAttention: () => req<{ items: AttentionItem[] }>("GET", "/attention"),
  reviewAttention: (kind: "agent" | "calendar", id: string) =>
    req<{ reviewed: boolean }>("POST", `/attention/${kind}/${encodeURIComponent(id)}`, { checked_outcome: true }),
  listAccessKeys: () => req<AccessKeyRecord[]>("GET", "/access-keys"),
  getAccessKey: (id: string) => req<AccessKeyRecord>("GET", `/access-keys/${id}`),
  createAccessKey: (body: CreateAccessKeyBody) =>
    req<AccessKeyCreateResponse>("POST", "/access-keys", body),
  patchAccessKey: (id: string, body: PatchAccessKeyBody) =>
    req<AccessKeyRecord>("PATCH", `/access-keys/${id}`, body),
  revokeAccessKey: (id: string) => req<void>("DELETE", `/access-keys/${id}`),
  rotateAccessKey: (id: string) => req<AccessKeyCreateResponse>("POST", `/access-keys/${id}/rotate`),

  createPreset: (accessKeyId: string, name: string) =>
    req<AccessKeyRecord>("POST", `/access-keys/${accessKeyId}/presets`, { name }),
  renamePreset: (accessKeyId: string, presetId: string, name: string) =>
    req<AccessKeyRecord>("PATCH", `/access-keys/${accessKeyId}/presets/${presetId}`, { name }),
  deletePreset: (accessKeyId: string, presetId: string) =>
    req<AccessKeyRecord>("DELETE", `/access-keys/${accessKeyId}/presets/${presetId}`),
  applyPreset: (accessKeyId: string, presetId: string, confirmPassThrough = false) =>
    req<AccessKeyRecord>(
      "POST",
      `/access-keys/${accessKeyId}/presets/${presetId}/apply`,
      confirmPassThrough ? { confirm_pass_through: true } : {},
    ),

  listArchivedAccessKeys: () => req<ArchivedAccessKeyRecord[]>("GET", "/access-keys/archived"),
  deleteArchivedAccessKey: (id: string) => req<void>("DELETE", `/access-keys/archived/${id}`),

  getPermissions: (id: string) => req<PermissionTree>("GET", `/access-keys/${id}/permissions`),
  setPermissions: (id: string, tree: PermissionTree) =>
    req<PermissionTree>("PUT", `/access-keys/${id}/permissions`, tree),
  patchDomainPermission: (accessKeyId: string, domain: string, body: PermissionPatchBody) =>
    req<PermissionTree>("PATCH", `/access-keys/${accessKeyId}/permissions/domains/${encodeURIComponent(domain)}`, body),
  patchDevicePermission: (accessKeyId: string, deviceId: string, body: PermissionPatchBody) =>
    req<PermissionTree>("PATCH", `/access-keys/${accessKeyId}/permissions/devices/${encodeURIComponent(deviceId)}`, body),
  patchEntityPermission: (accessKeyId: string, entityId: string, body: PermissionPatchBody) =>
    req<PermissionTree>("PATCH", `/access-keys/${accessKeyId}/permissions/entities/${encodeURIComponent(entityId)}`, body),
  getPermissionIntegrationOptions: (accessKeyId: string) =>
    req<{ integrations: IntegrationPermissionOption[] }>(
      "GET", `/access-keys/${accessKeyId}/permissions/integration-options`,
    ),
  bulkSelectPermissions: (
    accessKeyId: string,
    selectorType: "area" | "label" | "integration",
    selectorId: string,
    state: PermissionPatchBody["state"],
  ) => req<BulkPermissionResult>(
    "POST",
    `/access-keys/${accessKeyId}/permissions/bulk-select`,
    { selector_type: selectorType, selector_id: selectorId, state },
  ),

  resolve: (accessKeyId: string, entityId: string) =>
    req<ResolveResult>("GET", `/access-keys/${accessKeyId}/resolve/${encodeURIComponent(entityId)}`),
  getScope: (accessKeyId: string) => req<ScopeResult>("GET", `/access-keys/${accessKeyId}/scope`),

  getEntityTree: (forceReload = false) =>
    req<EntityTree>("GET", `/entities${forceReload ? "?force_reload=1" : ""}`),

  getEntityHints: () =>
    req<{ entity_hints: Record<string, string> }>("GET", "/entity-hints"),
  setEntityHint: (entityId: string, hint: string | null) =>
    req<{ entity_hints: Record<string, string> }>("PUT", `/entity-hints/${encodeURIComponent(entityId)}`, { hint }),

  getAccessKeyStats: (accessKeyId: string) => req<AccessKeyStats>("GET", `/access-keys/${accessKeyId}/stats`),
  getAccessKeyConnection: (accessKeyId: string) => req<AccessKeyConnection>("GET", `/access-keys/${accessKeyId}/connection`),
  getAccessKeyAudit: (accessKeyId: string, params?: AuditQueryParams) =>
    req<AuditEntry[]>("GET", `/access-keys/${accessKeyId}/audit${buildQuery(params)}`),
  getAudit: (params?: AuditQueryParams) =>
    req<AuditListResponse>("GET", `/audit${buildQuery(params)}`),

  getConversations: (params: { grouped?: boolean; limit?: number; offset?: number; search?: string; source?: string; outcome?: string; since?: string } = {}) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") query.set(key, String(value));
    return req<ConversationListResponse>("GET", `/conversations?${query}`);
  },
  getConversation: (id: string) => req<ConversationEntry>("GET", `/conversations/${encodeURIComponent(id)}`),
  clearConversations: () => req<{ cleared: boolean }>("DELETE", "/conversations", { confirm: true }),

  getInfo: () => req<{ version: string; min_ha_version: string; github_url: string }>("GET", "/info"),

  getRoutingStatistics: () => req<RoutingStatisticsResponse>("GET", "/command-routing/statistics"),
  resetRoutingStatistics: (surface: "voice" | "chat") => req<RoutingStatisticsResponse>("DELETE", "/command-routing/statistics", { surface, confirm: true }),
  previewCommandRoute: (accessKeyId: string, sentence: string, decision = false, targets = false, language = "en") =>
    req<CommandRoutePreview>("POST", "/command-routing/preview", { access_key_id: accessKeyId, sentence, language, ...(decision ? { decision: true, ...(targets ? { targets: true } : {}) } : {}) }),

  getSettings: () => req<GlobalSettings>("GET", "/settings"),
  patchSettings: (body: Partial<GlobalSettings>) =>
    req<GlobalSettings>("PATCH", "/settings", body),

  // One-click: create/remove an Assist pipeline pointed at Phoenix MCP's voice agent.
  createVoiceAgentPipeline: (preferred: boolean) =>
    req<{ pipeline_id: string; name: string; preferred: boolean }>(
      "POST", "/voice_agent/pipeline", { preferred },
    ),
  deleteVoiceAgentPipeline: () =>
    req<{ ok: boolean }>("DELETE", "/voice_agent/pipeline"),

  // The AI Task "Data generation tasks" default entity (one-click set/clear/status).
  getAiTaskPreferred: () =>
    req<AiTaskPreferredStatus>("GET", "/ai_task/preferred"),
  setAiTaskPreferred: () =>
    req<AiTaskPreferredStatus>("POST", "/ai_task/preferred"),
  clearAiTaskPreferred: () =>
    req<AiTaskPreferredStatus>("DELETE", "/ai_task/preferred"),

  wipe: (scope?: { wipe_core?: boolean; wipe_providers?: boolean; wipe_mesa?: boolean }) =>
    req<void>("DELETE", "/wipe", { confirm: "WIPE", ...(scope ?? {}) }),

  listApprovals: (params?: ApprovalListParams) => {
    const p = new URLSearchParams();
    if (params?.status) p.set("status", params.status);
    if (params?.access_key_id) p.set("access_key_id", params.access_key_id);
    if (params?.limit !== undefined) p.set("limit", String(params.limit));
    if (params?.offset !== undefined) p.set("offset", String(params.offset));
    const q = p.toString();
    return req<ApprovalListResponse>("GET", `/approvals${q ? `?${q}` : ""}`);
  },
  listMesaProfiles: async (params?: { domain?: string; tag?: string; area?: string; origin?: string; limit?: number; cursor?: string; all?: boolean }): Promise<MesaProfilesResponse> => {
    const p = new URLSearchParams();
    if (params?.domain) p.set("domain", params.domain);
    if (params?.tag) p.set("tag", params.tag);
    if (params?.area) p.set("area", params.area);
    if (params?.origin) p.set("origin", params.origin);
    if (params?.all) p.set("limit", "200");
    else if (params?.limit !== undefined) p.set("limit", String(params.limit));
    if (!params?.all && params?.cursor) p.set("cursor", params.cursor);
    const pages: MesaProfilesResponse["profiles"] = [];
    const seen = new Set<string>();
    let restarted = false;
    for (;;) {
      const q = p.toString();
      let page: MesaProfilesResponse;
      try {
        page = await req<MesaProfilesResponse>("GET", `/mesa/profiles${q ? `?${q}` : ""}`);
      } catch (error) {
        if (params?.all && p.has("cursor") && !restarted && error instanceof ApiError
          && error.status === 400 && error.code === "invalid_request"
          && error.message === "cursor invalidated by profile changes") {
          restarted = true;
          pages.length = 0;
          seen.clear();
          p.delete("cursor");
          continue;
        }
        throw error;
      }
      if (!params?.all) return page;
      pages.push(...page.profiles);
      if (!page.has_more) return { ...page, profiles: pages, has_more: false, next_cursor: null };
      if (!page.next_cursor || seen.has(page.next_cursor) || page.profiles.length === 0) {
        throw new ApiError(502, "invalid_response", t("mesa.loadFailed"));
      }
      seen.add(page.next_cursor);
      p.set("cursor", page.next_cursor);
    }
  },
  getMesaProfile: (entityId: string) =>
    req<MesaProfileDetail>("GET", `/mesa/profiles/${encodeURIComponent(entityId)}`),
  putMesaProfile: (entityId: string, doc: MesaProfileDocument, createOnly = false) =>
    req<MesaPutResponse>("PUT", `/mesa/profiles/${encodeURIComponent(entityId)}`, doc, createOnly ? { "If-None-Match": "*" } : undefined),
  deleteMesaProfile: (entityId: string) =>
    req<{ entity_id: string; deleted: boolean }>("DELETE", `/mesa/profiles/${encodeURIComponent(entityId)}`),

  listMesaDomains: () =>
    req<{ domains: { domain: string; document: MesaProfileDocument }[] }>("GET", "/mesa/domains"),
  getMesaDomain: (domain: string) =>
    req<{ domain: string; stored: MesaProfileDocument | null }>("GET", `/mesa/domains/${encodeURIComponent(domain)}`),
  putMesaDomain: (domain: string, doc: MesaProfileDocument, createOnly = false) =>
    req<{ domain: string; stored: MesaProfileDocument }>("PUT", `/mesa/domains/${encodeURIComponent(domain)}`, doc, createOnly ? { "If-None-Match": "*" } : undefined),
  deleteMesaDomain: (domain: string) =>
    req<{ domain: string; deleted: boolean }>("DELETE", `/mesa/domains/${encodeURIComponent(domain)}`),

  listMesaIntegrations: () =>
    req<{ integrations: { integration: string; document: MesaProfileDocument }[] }>("GET", "/mesa/integrations"),
  getMesaIntegration: (integration: string) =>
    req<{ integration: string; stored: MesaProfileDocument | null }>("GET", `/mesa/integrations/${encodeURIComponent(integration)}`),
  putMesaIntegration: (integration: string, doc: MesaProfileDocument, createOnly = false) =>
    req<{ integration: string; stored: MesaProfileDocument }>("PUT", `/mesa/integrations/${encodeURIComponent(integration)}`, doc, createOnly ? { "If-None-Match": "*" } : undefined),
  deleteMesaIntegration: (integration: string) =>
    req<{ integration: string; deleted: boolean }>("DELETE", `/mesa/integrations/${encodeURIComponent(integration)}`),
  listMesaDevices: () =>
    req<{ devices: { device_id: string; document: MesaProfileDocument }[] }>("GET", "/mesa/devices"),
  getMesaDevice: (deviceId: string) =>
    req<{ device_id: string; stored: MesaProfileDocument | null }>("GET", `/mesa/devices/${encodeURIComponent(deviceId)}`),
  putMesaDevice: (deviceId: string, doc: MesaProfileDocument, createOnly = false) =>
    req<{ device_id: string; stored: MesaProfileDocument }>("PUT", `/mesa/devices/${encodeURIComponent(deviceId)}`, doc, createOnly ? { "If-None-Match": "*" } : undefined),
  deleteMesaDevice: (deviceId: string) =>
    req<{ device_id: string; deleted: boolean }>("DELETE", `/mesa/devices/${encodeURIComponent(deviceId)}`),
  // A device id is an opaque registry id, so unlike areas the picker cannot show
  // the key itself and needs the display name Home Assistant shows.
  getMesaDeviceOptions: () =>
    req<{ devices: { id: string; name: string }[] }>("GET", "/mesa/device-options"),

  getMesaIntegrationOptions: () =>
    req<{ integrations: { id: string; name: string }[] }>("GET", "/mesa/integration-options"),

  listMesaAreas: () =>
    req<{ areas: { area_id: string; document: MesaProfileDocument }[] }>("GET", "/mesa/areas"),
  getMesaArea: (areaId: string) =>
    req<{ area_id: string; stored: MesaProfileDocument | null }>("GET", `/mesa/areas/${encodeURIComponent(areaId)}`),
  putMesaArea: (areaId: string, doc: MesaProfileDocument, createOnly = false) =>
    req<{ area_id: string; stored: MesaProfileDocument }>("PUT", `/mesa/areas/${encodeURIComponent(areaId)}`, doc, createOnly ? { "If-None-Match": "*" } : undefined),
  deleteMesaArea: (areaId: string) =>
    req<{ area_id: string; deleted: boolean }>("DELETE", `/mesa/areas/${encodeURIComponent(areaId)}`),

  getMesaVocabulary: () =>
    req<{ canonical_tags: string[]; canonical_roots: string[] }>("GET", "/mesa/vocabulary"),

  // refresh=true recomputes everything (trigger issues, orphans, suggestions);
  // refresh="suggestions" recomputes only suggestions, leaving the separate
  // issues/orphans banner untouched (the Suggestions card's own Rescan).
  getMesaIssues: (refresh: boolean | "suggestions" = false) =>
    req<MesaIssuesResponse>(
      "GET",
      `/mesa/issues${refresh === "suggestions" ? "?refresh=suggestions" : refresh ? "?refresh=1" : ""}`,
    ),
  clearMesaOrphans: () =>
    req<{ deleted: { entities: string[]; devices: string[]; areas: string[]; integrations: string[] }; count: number }>(
      "POST",
      "/mesa/orphans/clear",
    ),
  exportMesaProfiles: () => req<MesaExportArchive>("GET", "/mesa/export"),
  importMesaProfiles: (archive: MesaExportArchive, onConflict: "skip" | "overwrite") =>
    req<MesaImportResult>("POST", "/mesa/import", { archive, on_conflict: onConflict }),
  dismissMesaSuggestion: (key: string) =>
    req<MesaSuggestionActionResponse>("POST", "/mesa/suggestions/dismiss", { key }),
  restoreMesaSuggestions: (body: { key?: string; all?: boolean }) =>
    req<MesaSuggestionActionResponse>("POST", "/mesa/suggestions/restore", body),

  // The dashboard card catalog. The POST body is built by utils/card_harvest;
  // only a browser can see which custom cards exist (see that module for why).
  getCardCatalog: () => req<CardCatalogResponse>("GET", "/card_catalog"),
  postCardCatalog: (body: unknown) =>
    req<{ harvested: boolean; harvested_at: string | null; cards: number; available: number; failed_imports: number }>(
      "POST",
      "/card_catalog",
      body,
    ),

  getApproval: (id: string) => req<ApprovalRecord>("GET", `/approvals/${encodeURIComponent(id)}`),
  approveApproval: (id: string, body: { note?: string } = {}) =>
    req<ApprovalRecord>("POST", `/approvals/${encodeURIComponent(id)}/approve`, body),
  rejectApproval: (id: string, body: { reason?: string } = {}) =>
    req<ApprovalRecord>("POST", `/approvals/${encodeURIComponent(id)}/reject`, body),
  // Approves several pending approvals in one admin action. The server walks them
  // in the given order and STOPS at the first failure, so the result reports what
  // was applied, what failed, and what was left untouched and still pending.
  batchApproveApprovals: (ids: string[]) =>
    req<BatchApproveResult>("POST", "/approvals/batch/approve", { approval_ids: ids }),
  cancelApproval: (id: string) => req<void>("DELETE", `/approvals/${encodeURIComponent(id)}`),

  listVersions: (params?: { resource_type?: string; resource_id?: string; limit?: number; offset?: number }) => {
    const p = new URLSearchParams();
    if (params?.resource_type) p.set("resource_type", params.resource_type);
    if (params?.resource_id) p.set("resource_id", params.resource_id);
    if (params?.limit !== undefined) p.set("limit", String(params.limit));
    if (params?.offset !== undefined) p.set("offset", String(params.offset));
    const q = p.toString();
    return req<VersionListResponse>("GET", `/versions${q ? `?${q}` : ""}`);
  },
  getVersion: (id: string) => req<VersionRecord>("GET", `/versions/${encodeURIComponent(id)}`),
  restoreVersion: (id: string, side?: "before" | "after") =>
    req<VersionRestoreResponse>("POST", `/versions/${encodeURIComponent(id)}/restore`, side ? { side } : undefined),

  // agentCLI: provider accounts live on the admin surface. Multiple accounts of
  // the same kind are allowed; each has an opaque instance id.
  getDecisionProviders: () =>
    req<AgentCliProvidersResponse>("GET", "/agentcli/providers?purpose=decision"),
  getAgentCliProviders: () =>
    req<AgentCliProvidersResponse>("GET", "/agentcli/providers"),
  createAgentCliProvider: (
    kind: AgentCliProviderKind,
    body: { api_key?: string; base_url?: string; endpoint_id?: string; model?: string },
  ) => req<{ instance: AgentCliInstance }>("POST", "/agentcli/providers", { kind, ...body }),
  refreshAgentCliProvider: (id: string) =>
    req<{ models: string[]; capabilities: Record<string, DeclaredModelCaps>; declared: boolean; checked_at: string }>(
      "POST", `/agentcli/providers/${encodeURIComponent(id)}/refresh`),
  probeAgentCliCapabilities: (id: string) =>
    req<{ model: string; probed: DeclaredModelCaps & { effort_levels?: string[] }; calls: number; checked_at: string; effort_checkable: boolean; answered: boolean }>(
      "POST", `/agentcli/providers/${encodeURIComponent(id)}/probe`),
  setAgentCliProviderModel: (id: string, model: string) =>
    req<{ instance: { id: string; model: string } }>(
      "PATCH", `/agentcli/providers/${encodeURIComponent(id)}`, { model }),
  deleteAgentCliProvider: (id: string) =>
    req<{ deleted: string }>("DELETE", `/agentcli/providers/${encodeURIComponent(id)}`),
  getAgentCliModels: (id: string) =>
    req<{ models: string[] }>("GET", `/agentcli/providers/${encodeURIComponent(id)}/models`),
  probeAgentCliProvider: (
    kind: AgentCliProviderKind,
    body: { api_key?: string; base_url?: string; endpoint_id?: string },
  ) => req<{
    ok: boolean;
    models: string[];
    error?: string;
    message_key?: string;
    message_params?: Record<string, string | number>;
    message_passthrough?: boolean;
  }>(
    "POST", "/agentcli/probe", { kind, ...body }),
};

// The agentCLI chat endpoint streams Server-Sent Events over a POST, so it
// cannot use EventSource (GET-only). Read the response body as a stream and
// parse event:/data: frames, invoking onEvent for each. Returns when the stream
// ends (the server's final `done` event) or is aborted.
export interface AgentCliChatBody {
  log_conversation_id?: string;
  access_key_id: string;
  instance_id: string;
  model?: string;
  messages: unknown[];
  // The new user message. Omitted (with continue:true) when resuming a turn
  // that paused at the round-cap checkpoint: the model continues from the
  // existing conversation with no new user message.
  user?: string;
  continue?: boolean;
  // One-turn Home-focused override. Valid only alongside a new user message.
  home_focus_bypass?: boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  options?: Record<string, any>;
}

export async function agentCliChat(
  body: AgentCliChatBody,
  onEvent: (name: string, payload: unknown) => void,
  signal?: AbortSignal,
): Promise<void> {
  const hass = authHass();
  if (typeof hass?.fetchWithAuth !== "function") {
    throw new ApiError(401, "unauthorized", t("common.noSession"));
  }
  const res = await hass.fetchWithAuth("/api/phoenix-mcp/agentcli/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ...body, language: resolveLanguage(hass) }),
    signal,
  });
  if (!res.ok || !res.body) {
    const j = await res.json().catch(() => ({ error: "stream_error", message: res.statusText }));
    throw new ApiError(res.status, j.error ?? "unknown", j.message ?? res.statusText,
                       j.message_key, j.message_params);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // Frames are separated by a blank line; each frame has `event:` and `data:` lines.
  const flush = (frame: string) => {
    let name = "message";
    const dataLines: string[] = [];
    for (const raw of frame.split("\n")) {
      const line = raw.replace(/\r$/, "");
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (!dataLines.length) return;
    let payload: unknown = null;
    try { payload = JSON.parse(dataLines.join("\n")); } catch { payload = null; }
    onEvent(name, payload);
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (frame.trim()) flush(frame);
    }
  }
  if (buffer.trim()) flush(buffer);
}

export { ApiError };
