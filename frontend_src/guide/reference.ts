/**
 * The Guide's tool reference, built with the Guide from the tool registry
 * (`tool_reference.json`, written by `tests/tool_reference_build.py`) and the
 * catalogs' `guideTools` section. Each group of tools on the What Agents Can Do
 * page gets a reference page of its own, one section per tool: its summary,
 * what allows it, what Confirm does, its exact description and parameters, and
 * an example call. Which group a tool belongs to is read from that page's
 * `sectionKeywords`, so a tool is named in exactly one group.
 *
 * The build also links tool names in every page: a code span that is exactly a
 * tool's name links to its entry, once per section. And a `{{tools:<cap>}}`
 * line expands into a folded list of the tools that capability allows
 * (`{{tools:none}}`: the tools that need none).
 *
 * Reference pages ship in each language's Guide file with their headings and
 * summaries, which is all Contents and Search need. Each tool's details are the
 * same in every language and load from one file when the reader opens a page.
 */

import { HEADING, lengthProblems, parseTopic, voiceProblems, type GuideBundle } from "./compile";

export interface ToolData {
  name: string;
  /** A native tool's intent name, such as `HassTurnOn`, which the Guide's pages use. */
  shortName?: string;
  description: string;
  inputSchema: JsonSchema;
  annotations: { readOnlyHint: boolean; destructiveHint: boolean; idempotentHint: boolean; openWorldHint: boolean };
  /** Capabilities that must all be on. */
  caps: string[];
  /** Capabilities of which any one is enough. */
  capsAny: string[];
  /** Capabilities a parameter value adds: parameter, value, capabilities. */
  capsWhen: Record<string, Record<string, string[]>>;
  /** Capabilities the tool checks only for some calls, by what a call targets or asks for. */
  capsSometimes: string[];
  /** What Confirm does to a call, where one of its capabilities offers Confirm. */
  onConfirm?: "approval" | "runs";
  requires?: "esphome" | "esphome_builder";
  householdAdminOnly?: boolean;
  companionOnly?: boolean;
  example: Record<string, unknown>;
}

export interface ReferenceData {
  capabilities: string[];
  tools: ToolData[];
}

/** A JSON Schema as the tools declare them; only what the outline shows is named. */
export interface JsonSchema {
  type?: string | string[];
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  enum?: unknown[];
  const?: unknown;
  default?: unknown;
  oneOf?: JsonSchema[];
  anyOf?: JsonSchema[];
  allOf?: JsonSchema[];
  additionalProperties?: boolean | JsonSchema;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  minItems?: number;
  maxItems?: number;
  pattern?: string;
  [key: string]: unknown;
}

/** The reference's words in one language: the catalog's `guideTools` section, flattened, English where it lacks one. */
export type ReferenceText = (key: string) => string;

/** The page holding the list of reference pages. */
export const REFERENCE_INDEX = "topic.toolReference";
/** The overview page whose sections are the groups. */
export const TOOLS_TOPIC = "topic.tools";
/**
 * The file holding each tool's details, `guide/tools.json`: the same in every language, since its
 * words are panel labels the Guide puts in the reader's language and its exact text stays English.
 * A reference page ships with a `{{toolText:<tool>}}` line where each tool's details go.
 */
export const REFERENCE_FILE = "tools";
export const TOOL_TEXT = /^\{\{toolText:([A-Za-z0-9_]+)\}\}$/;

/** A reference page's body with each tool's details put in; null while any is missing. */
export function withToolText(body: string, blocks: Readonly<Record<string, string>>): string | null {
  let missing = false;
  const out = body.split("\n").map((line) => {
    const m = TOOL_TEXT.exec(line);
    if (!m) return line;
    const block = blocks[m[1]];
    if (block === undefined) missing = true;
    return block ?? line;
  }).join("\n");
  return missing ? null : out;
}

/** A reference page's body without the details: its headings and summaries, for Search and until the details load. */
export const withoutToolText = (body: string): string => body.split("\n").filter((line) => !TOOL_TEXT.test(line)).join("\n");
/** A line that expands into the folded list of a capability's tools. */
export const TOOL_LIST = /^\{\{tools:([a-z_]+)\}\}$/;
/** The folded list it becomes: a `+++ <summary>` line, the list, and a closing `+++`. */
export const FOLD_OPEN = /^\+\+\+ (.+)$/;
export const FOLD_CLOSE = "+++";

/** The id of a group's reference page: `topic.tools-<anchor>`. */
export const groupPage = (anchor: string): string => `${TOOLS_TOPIC}-${anchor}`;

/** A tool's anchor: its name, or a native tool's intent, in lower-case words joined by dashes. */
export function toolAnchor(tool: Pick<ToolData, "name" | "shortName">): string {
  return (tool.shortName ?? tool.name).replace(/([a-z0-9])([A-Z])/g, "$1-$2").replace(/_+/g, "-").toLowerCase();
}

/** The names a page may use for a tool in a code span: its announced name and a native tool's intent. */
const toolNames = (tool: ToolData): string[] => [tool.name, ...(tool.shortName ? [tool.shortName] : [])];

export interface ToolGroups {
  /** Group anchors in page order, each with the tools in it, in registry order. */
  groups: Array<{ anchor: string; tools: ToolData[] }>;
  /** The group of each tool, by announced name. */
  byTool: Map<string, string>;
  problems: string[];
}

/**
 * Each tool's group: the section of the overview whose search words name it.
 * A tool named in no section, or in two, is a problem: the reference needs one place for it.
 */
export function toolGroups(toolsTopicSource: string, data: ReferenceData): ToolGroups {
  const parsed = parseTopic(toolsTopicSource);
  if (typeof parsed === "string") return { groups: [], byTool: new Map(), problems: [`${TOOLS_TOPIC}: ${parsed}`] };
  const anchors: string[] = [];
  for (const line of parsed.body.split("\n")) {
    const h = HEADING.exec(line);
    if (h && h[1] === "##") anchors.push(h[3]);
  }
  const words = parsed.sectionKeywords ?? {};
  const byTool = new Map<string, string>();
  const problems: string[] = [];
  for (const tool of data.tools) {
    const found = anchors.filter((anchor) => toolNames(tool).some((name) => (words[anchor] ?? []).includes(name)));
    if (found.length !== 1) problems.push(`${TOOLS_TOPIC}: ${tool.name} must be in exactly one section's sectionKeywords, is in ${found.length ? found.join(", ") : "none"}`);
    else byTool.set(tool.name, found[0]);
  }
  const groups = anchors.map((anchor) => ({ anchor, tools: data.tools.filter((tool) => byTool.get(tool.name) === anchor) }))
    .filter((group) => group.tools.length > 0);
  return { groups, byTool, problems };
}

/** Where each tool name in a code span links: its entry on its group's page. */
export function toolLinks(data: ReferenceData, groups: ToolGroups): Map<string, string> {
  const links = new Map<string, string>();
  for (const tool of data.tools) {
    const group = groups.byTool.get(tool.name);
    if (group) for (const name of toolNames(tool)) links.set(name, `${groupPage(group)}#${toolAnchor(tool)}`);
  }
  return links;
}

/**
 * Link the first code span in each section that is exactly a tool's name. Code
 * blocks, headings and spans already inside a link stay as they are, and a tool's
 * own section does not link to itself.
 */
export function linkTools(body: string, links: ReadonlyMap<string, string>, topicId: string): string {
  let inCode = false;
  let section = "";
  let linked = new Set<string>();
  return body.split("\n").map((line) => {
    if (/^```/.test(line)) { inCode = !inCode; return line; }
    if (inCode) return line;
    const h = HEADING.exec(line);
    if (h || /^#{1,6}\s/.test(line)) { section = h?.[3] ?? ""; linked = new Set(); return line; }
    return line.replace(/(\[[^\]]*)?`([^`\n]+)`/g, (match, inLink: string | undefined, code: string) => {
      const to = links.get(code);
      if (inLink !== undefined || !to || linked.has(code)) return match;
      linked.add(code);
      if (to === `${topicId}#${section}`) return match;
      return `[\`${code}\`](guide:${to})`;
    });
  }).join("\n");
}

/**
 * The tools each capability allows, a tool any of several allow appearing under each, then the
 * tools only some calls of which need it; `none` lists those declaring none.
 */
export function toolsByCapability(data: ReferenceData): Map<string, Array<{ tool: ToolData; sometimes: boolean }>> {
  type Row = { tool: ToolData; sometimes: boolean };
  const by = new Map<string, Row[]>([["none", []], ...data.capabilities.map((cap): [string, Row[]] => [cap, []])]);
  for (const tool of data.tools) {
    const caps = [...new Set([...tool.caps, ...tool.capsAny, ...Object.values(tool.capsWhen).flatMap((v) => Object.values(v).flat())])];
    for (const cap of caps.length ? caps : ["none"]) by.get(cap)?.push({ tool, sometimes: false });
    for (const cap of tool.capsSometimes.filter((c) => !caps.includes(c))) by.get(cap)?.push({ tool, sometimes: true });
  }
  // The tools it always allows first, then those only some calls of which need it.
  for (const rows of by.values()) rows.sort((a, b) => Number(a.sometimes) - Number(b.sometimes));
  return by;
}

/**
 * Expand each `{{tools:<cap>}}` line into the folded list of the tools that
 * capability allows, each linked to its entry. An unknown capability is a problem.
 */
export function expandToolLists(body: string, data: ReferenceData, groups: ToolGroups, text: ReferenceText, problems: string[], file: string): string {
  const by = toolsByCapability(data);
  const links = toolLinks(data, groups);
  return body.split("\n").flatMap((line) => {
    const m = TOOL_LIST.exec(line.trim());
    if (!m) return [line];
    const rows = by.get(m[1]);
    if (!rows) { problems.push(`${file}: {{tools:${m[1]}}} names no capability`); return [line]; }
    return [
      `+++ ${text("allowed").replace("{count}", String(rows.length))}`,
      "",
      ...rows.map(({ tool, sometimes }) => `- [\`${tool.name}\`](guide:${links.get(tool.name)})${sometimes ? ` (${text("someCalls")})` : ""}`),
      "",
      FOLD_CLOSE,
    ];
  }).join("\n");
}

const CODE = "```";

/** A schema's type in one line: `string | array<string>`, an enum's values, a range where one is set. */
function typeOf(schema: JsonSchema): string {
  if (schema.const !== undefined) return JSON.stringify(schema.const);
  if (schema.enum) return schema.enum.map((v) => JSON.stringify(v)).join(" | ");
  const alternatives = schema.oneOf ?? schema.anyOf;
  if (alternatives && !schema.type) return alternatives.map(typeOf).join(" | ");
  const types = Array.isArray(schema.type) ? schema.type : schema.type ? [schema.type] : [];
  const shown = types.map((type) => {
    if (type === "array") {
      const items = schema.items ? typeOf(schema.items) : "any";
      return `array<${items.includes("|") ? `(${items})` : items}>`;
    }
    if (type === "object" && schema.additionalProperties && typeof schema.additionalProperties === "object") {
      return `object<string, ${typeOf(schema.additionalProperties)}>`;
    }
    return type;
  });
  let out = shown.join(" | ") || "any";
  const range = (min: unknown, max: unknown, unit = "") => (min !== undefined || max !== undefined
    ? ` [${min ?? ""}..${max ?? ""}${unit}]` : "");
  out += range(schema.minimum, schema.maximum);
  out += range(schema.minItems, schema.maxItems, " items");
  out += range(schema.minLength, schema.maxLength, " chars");
  if (schema.pattern) out += ` pattern ${schema.pattern}`;
  return out;
}

/** A rule over several parameters (`anyOf` of required sets and the like), in one line. */
function ruleOf(kind: "anyOf" | "oneOf" | "allOf", rules: JsonSchema[], indent: string): string[] {
  const onlyRequired = rules.every((r) => Object.keys(r).length === 1 && Array.isArray(r.required));
  if (onlyRequired && kind !== "allOf") {
    const sets = rules.map((r) => r.required!.join(" + "));
    return [`${indent}${kind === "anyOf" ? "at least one of" : "exactly one of"}: ${sets.join(", ")}`];
  }
  if (kind === "allOf") {
    return rules.flatMap((r) => (["anyOf", "oneOf", "allOf"] as const).flatMap((k) => (r[k] ? ruleOf(k, r[k] as JsonSchema[], indent) : [])));
  }
  return [`${indent}${kind}: ${JSON.stringify(rules)}`];
}

/** The parameters of an object schema as an indented outline: name, type, required or default, then the exact description. */
export function schemaOutline(schema: JsonSchema, indent = ""): string[] {
  const lines: string[] = [];
  const required = new Set(schema.required ?? []);
  for (const [name, prop] of Object.entries(schema.properties ?? {})) {
    const marks = [
      ...(required.has(name) ? ["required"] : []),
      ...(prop.default !== undefined ? [`default ${JSON.stringify(prop.default)}`] : []),
    ];
    lines.push(`${indent}${name}: ${typeOf(prop)}${marks.length ? `  (${marks.join(", ")})` : ""}`);
    if (prop.description) lines.push(`${indent}  ${prop.description}`);
    for (const alt of prop.oneOf ?? prop.anyOf ?? []) {
      if (alt.description) lines.push(`${indent}  - ${typeOf(alt)}: ${alt.description}`);
      if (alt.properties) lines.push(...schemaOutline(alt, `${indent}    `));
      if (alt.items?.properties) lines.push(...schemaOutline(alt.items, `${indent}    `));
    }
    if (prop.properties) lines.push(...schemaOutline(prop, `${indent}  `));
    if (prop.items?.properties) {
      lines.push(`${indent}  each item:`);
      lines.push(...schemaOutline(prop.items, `${indent}    `));
    }
  }
  for (const kind of ["anyOf", "oneOf", "allOf"] as const) {
    if (schema[kind]) lines.push(...ruleOf(kind, schema[kind] as JsonSchema[], indent));
  }
  return lines;
}

/** A capability's name in the reader's language, linked to its section of the Capabilities window's page. */
const capabilityLink = (cap: string): string => `[{{label:caps.${cap}.label}}](guide:window.capabilities#${cap.replace(/^cap_/, "").replace(/_/g, "-")})`;

/**
 * A word of a tool's details: `{{ref:<key>}}`, filled in from the catalog's `guideTools.ref`
 * in the page's language (`refLabels` in its Guide file) when the page is shown.
 */
export const REF_LABEL = /\{\{ref:([A-Za-z]+)\}\}/g;
const label = (key: string): string => `{{ref:${key}}}`;

/** Put each detail word in, from the labels of the page's language. */
export const withRefLabels = (body: string, labels: Readonly<Record<string, string>>): string => body.replace(REF_LABEL, (m, key: string) => labels[key] ?? m);

/** The words a tool's details use; each must be in the catalog's `guideTools.ref`. */
export const DETAIL_LABELS = [
  "needs", "needsOne", "needsWhen", "needsNothing", "needsSometimes", "hints", "readOnly", "changes", "destructive", "idempotent",
  "openWorld", "onConfirm", "confirmApproval", "confirmRuns", "offeredWhen", "requiresEsphome", "requiresBuilder",
  "household", "householdAdmin", "offeredTo", "companionOnly", "description", "parameters", "noParameters", "example",
] as const;

/** One tool's facts, as list items: what allows it, its hints, Confirm, and where it's offered. */
function factsOf(tool: ToolData): string[] {
  const items: string[] = [];
  const fact = (name: string, value: string) => items.push(`- **${name}:** ${value}`);
  if (tool.caps.length) fact(label("needs"), tool.caps.map(capabilityLink).join(", "));
  if (tool.capsAny.length) fact(label("needsOne"), tool.capsAny.map(capabilityLink).join(", "));
  for (const [parameter, byValue] of Object.entries(tool.capsWhen)) {
    for (const [value, caps] of Object.entries(byValue)) fact(`${label("needsWhen")} \`${parameter}: ${value}\``, caps.map(capabilityLink).join(", "));
  }
  if (!tool.caps.length && !tool.capsAny.length && !Object.keys(tool.capsWhen).length) fact(label("needs"), label("needsNothing"));
  if (tool.capsSometimes.length) fact(label("needsSometimes"), tool.capsSometimes.map(capabilityLink).join(", "));
  const a = tool.annotations;
  const hints = [
    label(a.readOnlyHint ? "readOnly" : "changes"),
    ...(!a.readOnlyHint && a.destructiveHint ? [label("destructive")] : []),
    ...(a.idempotentHint ? [label("idempotent")] : []),
    ...(a.openWorldHint ? [label("openWorld")] : []),
  ];
  fact(label("hints"), hints.join(" · "));
  if (tool.onConfirm) fact(label("onConfirm"), label(tool.onConfirm === "approval" ? "confirmApproval" : "confirmRuns"));
  if (tool.requires) fact(label("offeredWhen"), label(tool.requires === "esphome_builder" ? "requiresBuilder" : "requiresEsphome"));
  if (tool.householdAdminOnly) fact(label("household"), label("householdAdmin"));
  if (tool.companionOnly) fact(label("offeredTo"), label("companionOnly"));
  return items;
}

/** One tool's details: its facts, then its exact description, parameters and an example call. */
export function toolDetails(tool: ToolData): string {
  const params = schemaOutline(tool.inputSchema);
  return [
    ...factsOf(tool), "",
    `**${label("description")}**`, "", `${CODE}text`, tool.description, CODE, "",
    `**${label("parameters")}**`, "", ...(params.length ? [`${CODE}text`, ...params, CODE] : [label("noParameters")]), "",
    `**${label("example")}**`, "", `${CODE}json`, JSON.stringify({ name: tool.name, arguments: tool.example }, null, 2), CODE,
  ].join("\n");
}

/** A Markdown header for a generated page. */
const header = (fields: Record<string, unknown>): string => `---\n${JSON.stringify(fields)}\n---\n`;

export interface ReferencePages {
  /** Each page in full, details included, as a topic file the build compiles and checks. */
  files: Array<{ file: string; source: string }>;
  /** Each group page's body as shipped: a `{{toolText:<tool>}}` line where each tool's details go. */
  shipped: Record<string, string>;
  problems: string[];
}

/**
 * The reference pages in one language: the index and one page per group. Summaries
 * are checked for length (and, in English, voice) like any Guide text; the
 * generated details are exempt.
 */
export function referencePages(language: string, data: ReferenceData, groups: ToolGroups, text: ReferenceText, summary: (tool: string) => string | undefined): ReferencePages {
  const files: ReferencePages["files"] = [];
  const shipped: Record<string, string> = {};
  const problems: string[] = [];
  const links = toolLinks(data, groups);
  const indexLines = groups.groups.map((g) => `- [${text(`groups.${g.anchor}`)}](guide:${groupPage(g.anchor)}) (${g.tools.length})`);
  const index = [text("intro"), "", text("exact"), "", ...indexLines].join("\n");
  files.push({
    file: `${language}/${REFERENCE_INDEX}.md`,
    source: header({ id: REFERENCE_INDEX, title: text("title"), parent: "topic.about", order: 35, keywords: ["tool reference", "parameters", "schema", "inputSchema", "example call"] }) + index,
  });
  groups.groups.forEach((group, i) => {
    const id = groupPage(group.anchor);
    const opening = [
      `${text("overview")}: [${text(`groups.${group.anchor}`)}](guide:${TOOLS_TOPIC}#${group.anchor})`,
      "",
      text("exact"),
    ];
    const full = [...opening];
    const ship = [...opening];
    for (const tool of group.tools) {
      const said = summary(tool.name);
      if (!said) { problems.push(`${language}: guideTools.summary.${tool.name} is missing`); continue; }
      for (const p of [...lengthProblems(said), ...(language === "en" ? voiceProblems(said) : [])]) problems.push(`${language}: guideTools.summary.${tool.name}: ${p}`);
      const top = ["", `## ${tool.name} {#${toolAnchor(tool)}}`, "", linkTools(said, links, id), ""];
      full.push(...top, toolDetails(tool));
      ship.push(...top, `{{toolText:${tool.name}}}`);
    }
    const sectionKeywords = Object.fromEntries(group.tools.filter((t) => t.shortName).map((t) => [toolAnchor(t), [t.shortName!]]));
    files.push({
      file: `${language}/${id}.md`,
      source: header({
        id, title: text(`groups.${group.anchor}`), parent: REFERENCE_INDEX, order: (i + 1) * 10,
        ...(Object.keys(sectionKeywords).length ? { sectionKeywords } : {}),
      }) + full.join("\n"),
    });
    shipped[id] = ship.join("\n");
  });
  return { files, shipped, problems };
}

/** Each tool's details, by name, as `guide/tools.json` holds them. */
export const toolTextBlocks = (data: ReferenceData): Record<string, string> => Object.fromEntries(data.tools.map((tool) => [tool.name, toolDetails(tool)]));

/** The `guideTools` catalog section, flattened, in a language with English behind it. */
export function referenceText(english: Record<string, unknown>, own: Record<string, unknown> | undefined): ReferenceText {
  const flat = (node: unknown, prefix = "", out: Record<string, string> = {}): Record<string, string> => {
    if (typeof node === "string") out[prefix] = node;
    else if (node && typeof node === "object") for (const [k, v] of Object.entries(node)) flat(v, prefix ? `${prefix}.${k}` : k, out);
    return out;
  };
  const en = flat(english);
  const mine = flat(own ?? {});
  return (key) => mine[key] ?? en[key] ?? key;
}

/** Where a topic file's body starts, after its `---` header. */
const BODY_START = /^---\n[\s\S]*?\n---\n?/;

export interface GuideSources {
  files: Array<{ file: string; source: string }>;
  /** The generated pages, which the length and voice checks skip (their summaries are checked here). */
  generated: Set<string>;
  /** Each group page's body as shipped. */
  shipped: Record<string, string>;
  problems: string[];
}

/**
 * One language's topic files as the build compiles them: the hand-written pages with
 * tool lists expanded and tool names linked, and, where the language has its own
 * reference, the reference pages added.
 */
export function guideSources(
  language: string,
  files: ReadonlyArray<{ file: string; source: string }>,
  data: ReferenceData,
  toolsTopicSource: string,
  text: ReferenceText,
  summary: (tool: string) => string | undefined,
  /** Whether this language gets reference pages of its own; one without reads English's. */
  ownReference = true,
): GuideSources {
  const groups = toolGroups(toolsTopicSource, data);
  const problems = [...groups.problems];
  const links = toolLinks(data, groups);
  const pages = ownReference ? referencePages(language, data, groups, text, summary) : { files: [], shipped: {}, problems: [] };
  problems.push(...pages.problems);
  const written = files.map(({ file, source }) => {
    const normal = source.replace(/\r\n/g, "\n");
    const head = BODY_START.exec(normal);
    const parsed = parseTopic(normal);
    if (!head || typeof parsed === "string") return { file, source };
    const body = expandToolLists(normal.slice(head[0].length), data, groups, text, problems, file);
    return { file, source: head[0] + linkTools(body, links, parsed.id) };
  });
  const generated = new Set(pages.files.map((f) => parseTopic(f.source)).flatMap((t) => (typeof t === "string" ? [] : [t.id])));
  return { files: [...written, ...pages.files], generated, shipped: pages.shipped, problems };
}

/** The bundle as shipped: each group page with its details left to `guide/tools.json`. */
export function shipReference(bundle: GuideBundle, shipped: Record<string, string>): GuideBundle {
  const topics = { ...bundle.topics };
  for (const [id, body] of Object.entries(shipped)) {
    if (topics[id]) topics[id] = { ...topics[id], body, more: REFERENCE_FILE };
  }
  return { ...bundle, topics };
}
