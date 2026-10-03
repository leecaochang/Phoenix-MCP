import { describe, expect, it } from "vitest";
import { previewFrameDocument, previewReadRequest, previewReadResult, previewSnapshot, sandboxModuleSource } from "../utils/dashboard_preview_sandbox";

describe("isolated dashboard preview boundaries", () => {
  const ids = new Set(["sensor.preview"]);
  const start = new Date(Date.now() - 3600000).toISOString();

  it("copies only referenced display state, without credentials, identities, private attributes or methods", () => {
    const snapshot = previewSnapshot({
      auth: { accessToken: "private-token" }, user: { id: "private-user" },
      states: {
        "sensor.preview": { entity_id: "sensor.preview", state: "21", context: { user_id: "private-user" },
          attributes: { friendly_name: "Preview temperature", unit_of_measurement: "C", access_token: "private-token", latitude: 1 } },
        "sensor.hidden": { entity_id: "sensor.hidden", state: "private-state" },
      }, config: { latitude: 1, longitude: 2, time_zone: "UTC" },
      callService: () => { throw new Error("must not be copied"); },
    }, { views: [{ cards: [{ type: "sensor", entity: "sensor.preview" }] }] });
    expect(Object.keys(snapshot.states)).toEqual(["sensor.preview"]);
    expect(snapshot.states["sensor.preview"].attributes).toEqual({ friendly_name: "Preview temperature", unit_of_measurement: "C" });
    expect(JSON.stringify(snapshot)).not.toMatch(/private-|latitude|longitude|callService|accessToken/);
  });

  it("permits bounded history for the snapshot entity and discards caller-selected extra fields", () => {
    expect(previewReadRequest("ws", { type: "history/history_during_period", entity_ids: ["sensor.preview"],
      start_time: start, no_attributes: false, include_hidden: true }, ids)).toEqual({
      method: "ws", message: { type: "history/history_during_period", entity_ids: ["sensor.preview"],
        start_time: start, end_time: expect.any(String), minimal_response: true, no_attributes: true },
    });
    expect(previewReadRequest("api", ["GET", `history/period/${start}`, { filter_entity_id: "sensor.preview", no_attributes: false }], ids))
      .toMatchObject({ method: "api", params: { filter_entity_id: "sensor.preview", no_attributes: true } });
  });

  it.each([
    ["ws", { type: "call_service", domain: "lock", service: "unlock", target: { entity_id: "lock.front" } }],
    ["ws", { type: "lovelace/config/save", config: { views: [] } }],
    ["ws", { type: "get_states" }],
    ["ws", { type: "history/history_during_period", start_time: start, entity_ids: ["sensor.hidden"] }],
    ["ws", { type: "history/history_during_period", start_time: start }],
    ["ws", { type: "recorder/statistics_metadata" }],
    ["ws", { type: "recorder/statistics_during_period", start_time: "2000-01-01", statistic_ids: ["sensor.preview"], period: "hour" }],
    ["api", ["POST", "services/lock/unlock", {}]],
    ["api", ["GET", "states", {}]],
    ["api", ["GET", `https://outside.invalid/history/period/${start}`, { filter_entity_id: "sensor.preview" }]],
  ])("refuses control, arbitrary endpoints, hidden entities and unbounded reads (%s)", (kind, args) => {
    expect(() => previewReadRequest(kind, args, ids)).toThrow("Preview read refused");
  });

  it("scrubs historical attributes and any unexpected entity returned by HA", () => {
    expect(previewReadResult({
      "sensor.preview": [{ entity_id: "sensor.preview", state: "21", attributes: { friendly_name: "Preview", access_token: "secret" } }],
      "sensor.hidden": [{ entity_id: "sensor.hidden", state: "secret" }],
    }, ids)).toEqual({ "sensor.preview": [{ entity_id: "sensor.preview", state: "21", attributes: { friendly_name: "Preview" } }] });
  });

  it("rewrites static, computed dynamic and meta-URL imports without executing a module", () => {
    const source = sandboxModuleSource('import value from "./dependency.js"; const load = (name) => import("./" + name); const url = import.meta.url;',
      "https://ha.invalid/frontend_latest/example.0123456789.js");
    expect(source.dependencies).toEqual([{ marker: "__PHX_DEPENDENCY_0__", url: "https://ha.invalid/frontend_latest/dependency.js" }]);
    expect(source.source).toContain('import value from "__PHX_DEPENDENCY_0__"');
    expect(source.source).toContain('globalThis.__phxImport("./" + name,"https://ha.invalid/frontend_latest/example.0123456789.js")');
    expect(source.source).toContain('const url = "https://ha.invalid/frontend_latest/example.0123456789.js";');
  });

  it("retains HA's chunk runtime while suppressing application/authentication startup", () => {
    expect(sandboxModuleSource("const require=(id)=>{}; require(123);\n//# sourceMappingURL=app.map", "https://ha.invalid/frontend_latest/app.0123456789.js", true).source)
      .toBe("const require=(id)=>{}; globalThis.__phxRequire=require;");
    expect(() => sandboxModuleSource("unsupportedRuntime()", "https://ha.invalid/app.js", true)).toThrow("Unsupported HA frontend runtime");
  });

  it("confines renderer network, credentials and self-navigation behind two opaque frames", () => {
    const html = previewFrameDocument();
    expect(html).toContain("frame-src 'none'");
    expect(html).toContain("child.srcdoc = documentSource");
    expect(html).toContain("connect-src 'none'");
    expect(html).toContain("worker-src blob:");
    expect(html).toContain('child.sandbox.add("allow-scripts")');
    expect(html).not.toContain("allow-same-origin");
    expect(html).not.toContain("allow-top-navigation");
    expect(html).not.toContain("allow-popups");
  });
});
