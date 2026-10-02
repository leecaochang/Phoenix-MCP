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
  const field = route === "/approvals" ? "approvals" : route === "/audit" ? "entries"
    : route === "/versions" ? "versions" : route === "/mesa/profiles" ? "profiles" : null;
  if (field) {
    if (!records(value[field])) return false;
    if (field !== "profiles" && (!Number.isSafeInteger(value.total) || (value.total as number) < 0)) return false;
    if (field === "approvals" && !value[field].every(row => typeof row.id === "string" && object(row.args) && object(row.diff))) return false;
  }
  if (route === "/settings") {
    for (const key of ["kill_switch", "disable_all_logging", "log_allowed", "log_denied", "token_presets_enabled", "mesa_inject_enabled", "agentcli_global"]) {
      if (key in value && typeof value[key] !== "boolean") return false;
    }
    if ("mesa_mode" in value && !["off", "advisory", "enforced"].includes(value.mesa_mode as string)) return false;
  }
  return true;
}
