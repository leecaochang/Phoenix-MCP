/** Validate response envelopes before malformed data reaches rendering state. */
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function records(value: unknown): value is Record<string, unknown>[] {
  return Array.isArray(value) && value.every(object);
}

/** Unknown optional fields remain compatible with newer server revisions. */
export function validApiResponse(path: string, method: string, value: unknown): boolean {
  const route = path.split("?")[0];
  if (route === "/tokens" && method === "GET" || route === "/tokens/archived" && method === "GET") {
    return records(value) && value.every(row => typeof row.id === "string" && typeof row.name === "string");
  }
  if (method === "GET" && /^\/tokens\/[^/]+\/audit$/.test(route)) return records(value);
  if (!object(value)) return false;
  if (route.startsWith("/attention/") && method === "POST") return value.reviewed === true;
  if (route === "/attention" && method === "GET") {
    return records(value.items) && value.items.every(row => typeof row.id === "string"
      && ["agent", "calendar", "mesa", "calendar_storage"].includes(row.kind as string)
      && (row.kind !== "agent" && row.kind !== "calendar" ||
        typeof row.tool === "string" && typeof row.token_name === "string"
        && typeof row.started_at === "string" && typeof row.dispatch_result === "string"));
  }
  const field = route === "/approvals" ? "approvals" : route === "/audit" ? "entries"
    : route === "/versions" ? "versions" : route === "/mesa/profiles" ? "profiles" : null;
  if (field) {
    if (!records(value[field])) return false;
    if (field !== "profiles" && (!Number.isSafeInteger(value.total) || (value.total as number) < 0)) return false;
    if (field === "approvals" && !value[field].every(row => typeof row.id === "string" && object(row.args) && object(row.diff))) return false;
  }
  if (route === "/conversations" && method === "GET") {
    const summary = (row: Record<string, unknown>) => typeof row.id === "string" && typeof row.excerpt === "string"
      && typeof row.started_at === "string" && typeof row.updated_at === "string"
      && typeof row.source === "string" && typeof row.status === "string"
      && typeof row.execution_status === "string" && typeof row.coverage === "string"
      && typeof row.tool_count === "number" && typeof row.change_count === "number"
      && Array.isArray(row.omissions) && row.omissions.every(v => typeof v === "string");
    return records(value.entries) && Number.isSafeInteger(value.total) && (value.total as number) >= 0
      && typeof value.enabled === "boolean" && typeof value.available === "boolean"
      && value.entries.every(row => summary(row) && (row.turns === undefined || records(row.turns) && row.turns.every(summary)));
  }
  if (route.startsWith("/conversations/") && method === "GET") {
    return typeof value.id === "string" && typeof value.response_status === "string" && object(value.metadata)
      && Array.isArray(value.omissions) && value.omissions.every(v => typeof v === "string")
      && records(value.events) && value.events.every(row => typeof row.kind === "string" && typeof row.timestamp === "string");
  }
  if (route === "/settings") {
    for (const key of ["kill_switch", "disable_all_logging", "log_allowed", "log_denied", "token_presets_enabled", "mesa_inject_enabled", "agentcli_global", "conversation_log_enabled"]) {
      if (key in value && typeof value[key] !== "boolean") return false;
    }
    if ("mesa_mode" in value && !["off", "advisory", "enforced"].includes(value.mesa_mode as string)) return false;
  }
  return true;
}
