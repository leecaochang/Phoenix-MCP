/* eslint-disable @typescript-eslint/no-explicit-any */
/** Isolate dashboard rendering from the HA session and expose only bounded reads. */
import { parse } from "es-module-lexer/js";
import { currentHass } from "../api";

const ENTITY_ID = /^[a-z_][a-z0-9_]*\.[a-z0-9_]+$/;
const FRONTEND_ASSET = /^\/frontend_(?:latest|es5)\/[\w-]+\.[a-f0-9]{8,}\.js$/;
const ATTRIBUTES = new Set([
  "friendly_name", "icon", "unit_of_measurement", "device_class", "state_class",
  "supported_features", "min", "max", "step", "mode", "options", "brightness",
  "color_mode", "supported_color_modes", "rgb_color", "hs_color", "color_temp_kelvin",
  "min_color_temp_kelvin", "max_color_temp_kelvin", "temperature", "current_temperature",
  "target_temp_high", "target_temp_low", "min_temp", "max_temp", "target_temp_step",
  "hvac_modes", "hvac_action", "fan_mode", "fan_modes", "preset_mode", "preset_modes",
  "percentage", "percentage_step", "battery_level", "position", "current_position",
]);

function dict(value: unknown): value is Record<string, any> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

/** Select display data, never session credentials, private attributes, or callable authority. */
export function previewSnapshot(hass: any, config: unknown): Record<string, any> {
  const ids = new Set<string>();
  const walk = (value: unknown, depth: number): void => {
    if (depth > 32 || ids.size >= 64) return;
    if (typeof value === "string") {
      for (const match of value.matchAll(/\b[a-z_][a-z0-9_]*\.[a-z0-9_]+\b/g)) {
        if (hass.states?.[match[0]]) ids.add(match[0]);
        if (ids.size >= 64) break;
      }
    } else if (Array.isArray(value)) value.forEach((v) => walk(v, depth + 1));
    else if (dict(value)) Object.values(value).forEach((v) => walk(v, depth + 1));
  };
  walk(config, 0);
  const states: Record<string, any> = {};
  const names: Record<string, string> = {};
  const formattedStates: Record<string, string> = {};
  for (const id of ids) {
    const state = hass.states[id];
    states[id] = {
      entity_id: id, state: state.state, attributes: displayAttributes(state.attributes),
      last_changed: state.last_changed, last_updated: state.last_updated,
      context: { id: "", parent_id: null, user_id: null },
    };
    names[id] = hass.formatEntityName?.(state) ?? state.attributes?.friendly_name ?? id;
    formattedStates[id] = hass.formatEntityState?.(state) ?? state.state;
  }
  const translations: Record<string, string> = {};
  for (const key of ["state.default.on", "state.default.off", "state.default.unavailable",
    "state.default.unknown", "ui.card.common.entity_not_found", "ui.common.error", "ui.common.loading"]) {
    translations[key] = hass.localize?.(key) ?? key;
  }
  for (const [id, state] of Object.entries(states)) {
    const domain = id.split(".")[0];
    const component = `component.${domain}.entity_component.${state.attributes.device_class ?? "_"}`;
    for (const key of [`${component}.state.${state.state}`, `${component}.name`]) {
      translations[key] = hass.localize?.(key) ?? key;
    }
  }
  return JSON.parse(JSON.stringify({
    states, names, formattedStates, translations, locale: hass.locale,
    config: { unit_system: hass.config?.unit_system, time_zone: hass.config?.time_zone, state: "RUNNING" },
    themes: { themes: {}, darkMode: !!hass.themes?.darkMode },
  }));
}

function displayAttributes(value: unknown): Record<string, unknown> {
  if (!dict(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([key, v]) =>
    ATTRIBUTES.has(key) && (typeof v === "string" || typeof v === "number" || typeof v === "boolean"
      || (Array.isArray(v) && v.every((item) => ["string", "number", "boolean"].includes(typeof item))))));
}

/** Admit history/statistics for the exact snapshot entities; never forward arbitrary APIs. */
export function previewReadRequest(kind: unknown, args: unknown, ids: Set<string>):
  { method: "ws"; message: Record<string, unknown> } | { method: "api"; path: string; params: Record<string, unknown> } {
  const boundedIds = (value: unknown): string[] => {
    if (!Array.isArray(value) || value.length === 0 || value.length > 64
      || !value.every((id) => typeof id === "string" && ids.has(id))) throw new Error("Preview read refused");
    return [...new Set(value as string[])];
  };
  const times = (start: unknown, end: unknown): { start: string; end: string } => {
    if (typeof start !== "string" || (end != null && typeof end !== "string")) throw new Error("Preview read refused");
    const first = Date.parse(start), last = end == null ? Date.now() : Date.parse(end as string);
    if (!Number.isFinite(first) || !Number.isFinite(last) || first > last
      || first < Date.now() - 32 * 86400000 || last > Date.now() + 300000) throw new Error("Preview read refused");
    return { start: new Date(first).toISOString(), end: new Date(last).toISOString() };
  };
  if (kind === "ws" && dict(args)) {
    if (args.type === "history/history_during_period") {
      const range = times(args.start_time, args.end_time);
      return { method: "ws", message: { type: args.type, entity_ids: boundedIds(args.entity_ids),
        start_time: range.start, end_time: range.end, minimal_response: true, no_attributes: true } };
    }
    if (args.type === "recorder/statistics_during_period") {
      const range = times(args.start_time, args.end_time);
      if (!["5minute", "hour", "day", "week", "month"].includes(args.period)) throw new Error("Preview read refused");
      return { method: "ws", message: { type: args.type, statistic_ids: boundedIds(args.statistic_ids),
        start_time: range.start, end_time: range.end, period: args.period,
        types: ["mean", "min", "max", "state", "sum", "change"] } };
    }
    if (args.type === "recorder/statistics_metadata") {
      return { method: "ws", message: { type: args.type, statistic_ids: boundedIds(args.statistic_ids) } };
    }
  }
  if (kind === "api" && Array.isArray(args) && args[0] === "GET" && typeof args[1] === "string") {
    const url = new URL(args[1], "https://preview.invalid/");
    if (url.origin !== "https://preview.invalid" || !url.pathname.startsWith("/history/period/")) throw new Error("Preview read refused");
    const params = dict(args[2]) ? args[2] : {};
    const entities = boundedIds(String(params.filter_entity_id ?? url.searchParams.get("filter_entity_id") ?? "").split(","));
    const range = times(decodeURIComponent(url.pathname.slice("/history/period/".length)), params.end_time ?? url.searchParams.get("end_time"));
    return { method: "api", path: `history/period/${range.start}`, params: {
      filter_entity_id: entities.join(","), end_time: range.end, minimal_response: true, no_attributes: true,
    } };
  }
  throw new Error("Preview read refused");
}

/** Strip private history attributes and unexpected entity rows before sending data into the sandbox. */
export function previewReadResult(value: unknown, ids: Set<string>, depth = 0): unknown {
  if (depth > 32) return null;
  if (Array.isArray(value)) return value.map((v) => previewReadResult(v, ids, depth + 1)).filter((v) => v !== null);
  if (!dict(value)) return value;
  if (typeof value.entity_id === "string" && !ids.has(value.entity_id)) return null;
  return Object.fromEntries(Object.entries(value).filter(([key]) => !ENTITY_ID.test(key) || ids.has(key))
    .map(([key, v]) => [key, key === "attributes" || key === "a" ? displayAttributes(v)
      : key === "context" ? null : previewReadResult(v, ids, depth + 1)]));
}

interface ModuleSource { source: string; dependencies: { marker: string; url: string }[] }

/** Rewrite imports without evaluating source in the authenticated document. */
export function sandboxModuleSource(source: string, url: string, entry = false): ModuleSource {
  const dependencies: ModuleSource["dependencies"] = [];
  const [imports] = parse(source);
  for (const imp of [...imports].reverse()) {
    if (imp.d >= 0) {
      source = source.slice(0, imp.ss) + `globalThis.__phxImport(${source.slice(imp.s, imp.e)},${JSON.stringify(url)})` + source.slice(imp.se);
    } else if (imp.d === -1 && imp.n) {
      const marker = `__PHX_DEPENDENCY_${dependencies.length}__`;
      dependencies.push({ marker, url: new URL(imp.n, url).href });
      source = source.slice(0, imp.s) + marker + source.slice(imp.e);
    } else if (imp.d === -2 && source.slice(imp.e, imp.e + 4) === ".url") {
      source = source.slice(0, imp.s) + JSON.stringify(url) + source.slice(imp.e + 4);
    }
  }
  source = source.replace(/\/\/# sourceMappingURL=.*$/gm, "");
  if (entry) {
    // HA's app entry owns its chunk runtime. Retain that runtime, replacing only
    // the final application startup call: an authenticated HA app never boots here.
    const startup = /([\w$]+)\(\d+\);\s*$/.exec(source);
    if (!startup) throw new Error("Unsupported HA frontend runtime");
    source = source.slice(0, startup.index) + `globalThis.__phxRequire=${startup[1]};`;
  }
  return { source, dependencies };
}

/** The frame has no origin, session, network access, or parent control channel. */
function sandboxBootstrap(): void {
  const blobs: string[] = [];
  const emptyStorage = () => {
    const values = new Map<string, string>();
    return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, String(value)); },
      removeItem: (key: string) => { values.delete(key); }, clear: () => values.clear(),
      key: (index: number) => [...values.keys()][index] ?? null, get length() { return values.size; } };
  };
  for (const key of ["localStorage", "sessionStorage"]) Object.defineProperty(window, key, { value: emptyStorage() });
  let connected = false;
  window.addEventListener("message", async (event) => {
    if (connected || event.source !== parent || event.ports.length !== 1) return;
    connected = true;
    const port = event.ports[0];
    const failed = () => port.postMessage({ kind: "failed", error: "Card renderer failed" });
    window.addEventListener("error", failed);
    window.addEventListener("unhandledrejection", failed);
    let sequence = 0;
    const pending = new Map<number, { resolve: (v: any) => void; reject: (e: Error) => void }>();
    const rpc = (kind: string, args: unknown): Promise<any> => new Promise((resolve, reject) => {
      const id = ++sequence;
      pending.set(id, { resolve, reject });
      port.postMessage({ id, kind, args });
    });
    port.onmessage = ({ data }) => {
      const waiter = pending.get(data.id);
      if (!waiter) return;
      pending.delete(data.id);
      if (data.error) waiter.reject(new Error(data.error)); else waiter.resolve(data.value);
    };
    const urls = new Map<string, Promise<string>>();
    const modules = new Map<string, Promise<any>>();
    // Keep bundler preload helpers out of this serialized, separate realm.
    const importBlob = new Function("url", "return import(url)") as (url: string) => Promise<any>;
    const moduleUrl = (url: string, ancestors: string[] = []): Promise<string> => {
      if (ancestors.includes(url)) return Promise.reject(new Error("Unsupported cyclic module import"));
      if (!urls.has(url)) urls.set(url, (async () => {
        const record = await rpc("asset", url);
        let source: string = record.source;
        for (const dep of record.dependencies) source = source.replaceAll(dep.marker, await moduleUrl(dep.url, [...ancestors, url]));
        const blob = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
        blobs.push(blob);
        return blob;
      })());
      return urls.get(url)!;
    };
    (globalThis as any).__phxImport = (specifier: string, base: string) => {
      const url = new URL(specifier, base).href;
      if (!modules.has(url)) modules.set(url, moduleUrl(url).then(importBlob));
      return modules.get(url)!;
    };
    // Workers use the public asset allowlist and receive no HA session state.
    // The native Markdown parser also supports webviews without opaque workers.
    const RealWorker = window.Worker;
    (window as any).Worker = class extends EventTarget {
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: ErrorEvent) => void) | null = null;
      private worker?: Worker;
      private inlineEndpoint?: EventTarget;
      private stopped = false;
      private queue: [any, Transferable[]][] = [];
      constructor(url: string | URL, options?: WorkerOptions) {
        super();
        const sourceUrl = new URL(String(url), event.data.entry);
        const ready = (async () => {
          // Opaque-origin module workers are unavailable in some HA webviews.
          // Run HA's literal Markdown parser in this same isolated realm instead.
          if (/\/frontend_(?:latest|es5)\/markdown-worker\.[a-f0-9]+\.js$/.test(sourceUrl.pathname)) {
            const record = await rpc("asset", sourceUrl.href);
            if (this.stopped) return;
            this.inlineEndpoint = new EventTarget();
            const scope = new Proxy({} as any, {
              get: (target, key) => key in target ? target[key] : Reflect.get(globalThis, key, globalThis),
            });
            scope.addEventListener = this.inlineEndpoint.addEventListener.bind(this.inlineEndpoint);
            scope.removeEventListener = this.inlineEndpoint.removeEventListener.bind(this.inlineEndpoint);
            scope.postMessage = (value: unknown) => queueMicrotask(() => {
              if (this.stopped) return;
              const message = new MessageEvent("message", { data: value });
              this.onmessage?.(message); this.dispatchEvent(message);
            });
            new Function("globalThis", "self", record.source).call(scope, scope, scope);
          } else {
            const blob = await moduleUrl(sourceUrl.href);
            if (this.stopped) return;
            this.worker = new RealWorker(blob, options);
            this.worker.onmessage = (message) => {
              const forwarded = new MessageEvent("message", { data: message.data, ports: Array.from(message.ports) });
              this.onmessage?.(forwarded);
              this.dispatchEvent(forwarded);
            };
            this.worker.onerror = (error) => {
              const forwarded = new ErrorEvent("error", { message: error.message });
              this.onerror?.(forwarded); this.dispatchEvent(forwarded);
              port.postMessage({ kind: "failed", error: `Preview worker unavailable: ${error.message}` });
            };
          }
          for (const [value, transfer] of this.queue) this.postMessage(value, transfer);
          this.queue = [];
        })();
        ready.catch((error) => {
          this.onerror?.(new ErrorEvent("error", { message: String(error) }));
          port.postMessage({ kind: "failed", error: `Preview worker unavailable: ${String(error?.stack ?? error)}` });
        });
      }
      postMessage(value: any, transfer: Transferable[] = []) {
        if (this.inlineEndpoint) this.inlineEndpoint.dispatchEvent(new MessageEvent("message", { data: value }));
        else if (this.worker) this.worker.postMessage(value, transfer); else this.queue.push([value, transfer]);
      }
      terminate() { this.stopped = true; this.worker?.terminate(); this.queue = []; }
    };
    try {
      const input = event.data;
      const imported = (globalThis as any).__phxImport;
      await imported(input.entry, input.entry);
      const require = (globalThis as any).__phxRequire;
      if (!require?.m) throw new Error("Unsupported HA frontend runtime");
      for (const url of input.chunks) {
        const chunk = await imported(url, input.entry);
        if (chunk.__webpack_modules__) Object.assign(require.m, chunk.__webpack_modules__);
      }
      const helpers = await require(input.helperId);
      if (typeof helpers.createCardElement !== "function") throw new Error("HA card helpers unavailable");
      (window as any).loadCardHelpers = () => Promise.resolve(helpers);
      await Promise.all(input.resources.map((url: string) => Promise.race([
        imported(url, input.entry).catch(() => {}), new Promise((resolve) => setTimeout(resolve, 4000)),
      ])));
      const snapshot = input.snapshot;
      const refuse = () => Promise.reject(new Error("Preview controls are disabled"));
      const hass: any = {
        states: snapshot.states, locale: snapshot.locale, config: snapshot.config, themes: snapshot.themes,
        entities: {}, devices: {}, areas: {}, floors: {}, labels: {}, services: {},
        user: { id: "", name: input.labels.card, is_admin: false },
        localize: (key: string) => snapshot.translations[key] ?? key,
        formatEntityName: (entity: any, name?: string) => name ?? snapshot.names[entity.entity_id] ?? entity.attributes?.friendly_name ?? entity.entity_id,
        formatEntityState: (entity: any) => snapshot.formattedStates[entity.entity_id] ?? entity.state,
        formatEntityAttributeName: (_entity: any, key: string) => key,
        formatEntityAttributeValue: (entity: any, key: string) => String(entity.attributes?.[key] ?? ""),
        callWS: (message: unknown) => rpc("ws", message), callApi: (...args: unknown[]) => rpc("api", args),
        callService: refuse, fetchWithAuth: refuse, sendWS: refuse, refreshTokens: refuse,
        connection: { sendMessagePromise: (message: unknown) => rpc("ws", message),
          sendMessage: refuse, subscribeEvents: async () => () => {},
          subscribeMessage: async (callback: (value: unknown) => void, message: any) => {
            // Native Markdown asks HA to render even static content. Display
            // that literal locally; server templates must not expand read scope.
            if (message?.type !== "render_template" || typeof message.template !== "string"
              || /{[{%#]/.test(message.template)) throw new Error(input.labels.failed);
            callback({ result: message.template, listeners: { all: false, domains: [], entities: [], time: false } });
            return () => {};
          } },
      };
      document.addEventListener("context-request", ((request: any) => {
        const groups: Record<string, unknown> = {
          hassRegistries: hass, hassInternationalization: hass, hassApi: hass,
          hassConnection: hass, hassUi: hass, hassConfig: hass, hassFormatters: hass,
        };
        const value = groups[request.context] ?? hass[request.context];
        if (value !== undefined && request.context !== "auth") {
          request.stopPropagation();
          request.callback(value, () => {});
        }
      }) as EventListener);
      for (const [key, value] of Object.entries(input.theme)) document.documentElement.style.setProperty(key, String(value));
      const root = document.getElementById("cards")!;
      root.inert = true;
      const failures = new Map<HTMLElement, string>();
      const report = () => {
        const errors = [...failures.values()];
        const walk = (node: Element, depth = 0) => {
          if (depth > 10) return;
          if (node.localName === "hui-error-card") {
            const cfg = (node as any)._config;
            errors.push([cfg?.error, cfg?.message].filter((v) => typeof v === "string").join(" - ") || input.labels.failed);
          }
          for (const child of Array.from(node.shadowRoot?.children ?? [])) walk(child, depth + 1);
          for (const child of Array.from(node.children)) walk(child, depth + 1);
        };
        walk(root);
        port.postMessage({ kind: "rendered", height: document.body.scrollHeight, errors });
      };
      const render = (host: HTMLElement, config: Record<string, unknown>) => {
        failures.delete(host);
        let card: any;
        try {
          card = helpers.createCardElement(config);
          card.hass = hass;
          card.preview = true;
          card.addEventListener("ll-rebuild", () => render(host, config), { once: true });
          card.addEventListener("ll-upgrade", () => { card.hass = hass; report(); }, { once: true });
          host.replaceChildren(card);
        } catch (error) {
          const message = `${input.labels.failed} ${String(error)}`.slice(0, 500);
          failures.set(host, message); host.textContent = message;
        }
      };
      for (const section of input.sections) {
        if (section.title) {
          const title = document.createElement("h3"); title.textContent = section.title; root.append(title);
        }
        const grid = document.createElement("div"); grid.className = "grid"; root.append(grid);
        for (const entry of section.cards) {
          const tile = document.createElement("div"); grid.append(tile);
          if (entry.kind === "card") render(tile, entry.config); else tile.textContent = input.labels.badCard;
        }
      }
      new ResizeObserver(report).observe(root);
      setTimeout(report, 250);
      setTimeout(report, 2600);
      window.addEventListener("pagehide", () => { for (const url of blobs) URL.revokeObjectURL(url); port.close(); }, { once: true });
    } catch (error) { port.postMessage({ kind: "failed", error: String(error) }); }
  }, { once: true });
}

/** Never add allow-same-origin: proposed card code must not inherit HA authority. */
export function previewFrameDocument(): string {
  const nonce = [...crypto.getRandomValues(new Uint8Array(16))].map((v) => v.toString(16).padStart(2, "0")).join("");
  const inner = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}' 'unsafe-eval' blob:; style-src 'unsafe-inline'; img-src data: blob:; worker-src blob:; connect-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'"><style>html{color:var(--primary-text-color,#212121);font:14px Roboto,sans-serif}body{margin:0;padding:4px;overflow:hidden}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,240px),1fr));gap:10px;align-items:start}.grid>div{min-width:0}h3{font-size:14px;margin:12px 0 6px}hui-card{display:block}</style></head><body><div id="cards"></div><script nonce="${nonce}">(${sandboxBootstrap.toString().replace(/<\/script/gi, "<\\/script")})()</script></body></html>`;
  // The trusted outer frame also blocks child navigation to network documents.
  // A single sandbox blocks fetch but still allows its own URL to navigate;
  // without this outer frame a template could leak snapshot data in that URL.
  const envelope = (documentSource: string) => {
    const child = document.createElement("iframe");
    child.sandbox.add("allow-scripts");
    child.style.cssText = "display:block;border:0;width:100%;height:100vh";
    child.srcdoc = documentSource;
    let loaded = false;
    let forwarded = false;
    let message: MessageEvent | undefined;
    const channel = new MessageChannel();
    const forward = () => {
      if (!loaded || !message || forwarded) return;
      forwarded = true;
      const port = message.ports[0];
      port.onmessage = ({ data }) => channel.port1.postMessage(data);
      channel.port1.onmessage = ({ data }) => port.postMessage(data);
      child.contentWindow?.postMessage(message.data, "*", [channel.port2]);
    };
    child.addEventListener("load", () => {
      if (forwarded) message?.ports[0].postMessage({ kind: "failed", error: "Preview navigation is disabled" });
      else { loaded = true; forward(); }
    });
    window.addEventListener("message", (event) => {
      if (event.source !== parent || event.ports.length !== 1) return;
      message = event; forward();
    }, { once: true });
    document.body.append(child);
    window.addEventListener("pagehide", () => {
      message?.ports[0].close(); channel.port1.close(); channel.port2.close();
    }, { once: true });
  };
  const script = `(${envelope.toString()})(${JSON.stringify(inner)})`.replace(/<\/script/gi, "<\\/script");
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}' 'unsafe-eval' blob:; style-src 'unsafe-inline'; img-src data: blob:; worker-src blob:; connect-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'"><style>html,body{margin:0;overflow:hidden}</style></head><body><script nonce="${nonce}">${script}</script></body></html>`;
}

const moduleCache = new Map<string, Promise<ModuleSource>>();

/** Attach one opaque renderer, binding every read and response to its HA administrator. */
export async function connectPreviewFrame(frame: HTMLIFrameElement, config: unknown, sections: unknown,
  labels: Record<string, string>, onResult: (result: { kind: "rendered" | "failed"; height?: number; errors?: string[] }) => void,
  signal: AbortSignal): Promise<() => void> {
  const hass = currentHass() as any;
  const userId = hass?.user?.id;
  const valid = () => !signal.aborted && hass?.user?.is_admin === true
    && (currentHass() as any)?.user?.is_admin === true
    && (currentHass() as any)?.user?.id === userId;
  const loader = (window as any).loadCardHelpers;
  if (!valid() || typeof loader !== "function") throw new Error("HA card helpers unavailable");
  const helperId = /\.bind\([\w$]+,\s*(\d+)\)/.exec(Function.prototype.toString.call(loader))?.[1];
  if (!helperId) throw new Error("Unsupported HA frontend runtime");
  await loader();
  if (!valid()) throw new Error("Preview session changed");
  let resources: any[];
  try { resources = await hass.connection.sendMessagePromise({ type: "lovelace/resources/list" }); }
  catch {
    if (!valid()) throw new Error("Preview session changed");
    resources = await hass.connection.sendMessagePromise({ type: "lovelace/resources" });
  }
  if (!valid()) throw new Error("Preview session changed");
  const scripts = Array.from(document.querySelectorAll<HTMLScriptElement>("script"));
  const loaded = [...new Set(performance.getEntriesByType("resource").map((entry) => entry.name)
    .concat(scripts.map((el) => el.src), scripts.flatMap((el) =>
      [...el.textContent?.matchAll(/\/frontend_(?:latest|es5)\/app\.[a-f0-9]{8,}\.js/g) ?? []]
        .map((match) => new URL(match[0], location.href).href))))];
  const frontend = loaded.filter((value) => {
    const url = new URL(value, location.href);
    return url.origin === location.origin && FRONTEND_ASSET.test(url.pathname) && !url.search;
  });
  const entry = frontend.find((value) => /\/app\.[a-f0-9]+\.js$/.test(new URL(value).pathname));
  if (!entry) throw new Error("HA frontend entry unavailable");
  const resourceUrls = (Array.isArray(resources) ? resources : []).filter((r) => r?.type === "module" && typeof r.url === "string")
    .map((r) => new URL(r.url, location.href)).filter((url) => url.origin === location.origin && url.pathname.endsWith(".js"))
    .map((url) => url.href);
  const resourcePaths = resourceUrls.map((value) => new URL(value).pathname.replace(/[^/]+$/, ""));
  const snapshot = previewSnapshot(hass, config);
  const needsCustomCards = JSON.stringify(sections).includes('"type":"custom:');
  const ids = new Set(Object.keys(snapshot.states));
  const channel = new MessageChannel();
  let reads = 0, assets = 0;
  const dispose = () => { channel.port1.close(); channel.port2.close(); };
  signal.addEventListener("abort", dispose, { once: true });
  channel.port1.onmessage = async ({ data }) => {
    if (!valid()) { onResult({ kind: "failed" }); dispose(); return; }
    if (data?.kind === "rendered") {
      onResult({ kind: "rendered", height: typeof data.height === "number" ? Math.max(80, Math.min(2400, data.height)) : 120,
        errors: Array.isArray(data.errors) ? data.errors.filter((v: unknown) => typeof v === "string").slice(0, 50) : [] });
      return;
    }
    if (data?.kind === "failed") {
      console.warn("Phoenix MCP: isolated dashboard renderer unavailable", String(data.error).slice(0, 500));
      onResult({ kind: "failed" }); return;
    }
    if (!Number.isSafeInteger(data?.id) || data.id < 1) return;
    try {
      let value: unknown;
      if (data.kind === "asset" && typeof data.args === "string" && ++assets <= 512) {
        const url = new URL(data.args);
        const knownResource = resourceUrls.includes(url.href);
        const allowed = url.origin === location.origin && !url.username && !url.password && !url.hash
          && ((FRONTEND_ASSET.test(url.pathname) && !url.search) || knownResource
            || (!url.search && url.pathname.endsWith(".js") && resourcePaths.some((path) => path !== "/" && url.pathname.startsWith(path))));
        if (!allowed) throw new Error("Preview asset refused");
        if (!moduleCache.has(url.href)) moduleCache.set(url.href, (async () => {
          const response = await fetch(url.href, { credentials: "omit", referrerPolicy: "no-referrer", signal: AbortSignal.timeout(8000) });
          if (!response.ok || response.redirected) throw new Error("Preview asset unavailable");
          const source = await response.text();
          if (source.length > 4000000) throw new Error("Preview asset too large");
          return sandboxModuleSource(source, url.href, url.href === entry);
        })());
        try { value = await moduleCache.get(url.href)!; } catch (error) { moduleCache.delete(url.href); throw error; }
      } else if ((data.kind === "ws" || data.kind === "api") && ++reads <= 64) {
        const request = previewReadRequest(data.kind, data.args, ids);
        value = request.method === "ws" ? await hass.callWS(request.message) : await hass.callApi("GET", request.path, request.params);
        value = previewReadResult(value, ids);
        if (JSON.stringify(value).length > 2000000) throw new Error("Preview read too large");
      } else throw new Error("Preview request refused");
      if (valid()) channel.port1.postMessage({ id: data.id, value });
    } catch { if (valid()) channel.port1.postMessage({ id: data.id, error: "Preview request unavailable" }); }
  };
  const theme: Record<string, string> = {};
  const computed = getComputedStyle(document.documentElement);
  for (const key of ["--primary-color", "--accent-color", "--primary-text-color", "--secondary-text-color", "--card-background-color",
    "--ha-card-background", "--ha-card-border-radius", "--ha-card-border-color", "--ha-card-border-width", "--state-icon-color", "--divider-color"]) {
    const value = computed.getPropertyValue(key); if (value && !/url\s*\(/i.test(value)) theme[key] = value;
  }
  if (!valid() || !frame.contentWindow) { dispose(); throw new Error("Preview session changed"); }
  frame.contentWindow.postMessage({ entry, chunks: frontend.filter((url) => /\/\d+\.[a-f0-9]+\.js$/.test(new URL(url).pathname)),
    helperId: Number(helperId), resources: needsCustomCards ? resourceUrls : [], sections, snapshot, theme, labels }, "*", [channel.port2]);
  return dispose;
}
