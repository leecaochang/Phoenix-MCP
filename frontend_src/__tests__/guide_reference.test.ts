/**
 * The tool reference the Guide's build generates (guide/reference.ts): anchors,
 * the parameter outline, linking tool names once per section, the capability
 * tool lists, and the shipped split between each page and the shared details
 * file. Against the real build: every capability's list equals what its tools
 * declare, and every page's details are in the shared file.
 */

import { describe, expect, it } from "vitest";
import { resolve } from "path";
import { buildGuide } from "../guide/build";
import {
  DETAIL_LABELS, REFERENCE_FILE, REF_LABEL, TOOL_TEXT, withRefLabels, groupPage, linkTools, schemaOutline, toolAnchor, toolsByCapability,
  withToolText, withoutToolText, type ReferenceData,
} from "../guide/reference";
import { renderTopic } from "../shell/guide";
import data from "../guide/tool_reference.json";
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";

const REPO = resolve(__dirname, "../..");
const guide = buildGuide(REPO);
const english = guide.compiled.find((b) => b.language === "en")!;
const reference = data as unknown as ReferenceData;
const shippedFile = (name: string) => JSON.parse(guide.files.find((f) => f.fileName === `guide/${name}.json`)!.source);

describe("the tool reference", () => {
  it("builds cleanly in every language", () => {
    expect(guide.problems).toEqual([]);
  });

  it("anchors each tool by its name, or a native tool by its intent", () => {
    expect(toolAnchor({ name: "get_state" })).toBe("get-state");
    expect(toolAnchor({ name: "intent__HassTurnOn", shortName: "HassTurnOn" })).toBe("hass-turn-on");
    expect(toolAnchor({ name: "homeassistant__GetLiveContext", shortName: "GetLiveContext" })).toBe("get-live-context");
    const anchors = reference.tools.map(toolAnchor);
    expect(new Set(anchors).size).toBe(anchors.length);
  });

  it("outlines parameters with their types, marks, exact descriptions and rules", () => {
    expect(schemaOutline({
      type: "object",
      properties: {
        id: { type: "string", description: "The id." },
        limit: { type: "integer", minimum: 1, maximum: 50, default: 20 },
        mode: { type: "string", enum: ["a", "b"] },
        rows: { type: "array", items: { type: "object", properties: { kind: { type: "string" } }, required: ["kind"] } },
      },
      required: ["id"],
      anyOf: [{ required: ["id"] }, { required: ["mode"] }],
    })).toEqual([
      "id: string  (required)",
      "  The id.",
      "limit: integer [1..50]  (default 20)",
      'mode: "a" | "b"',
      "rows: array<object>",
      "  each item:",
      "    kind: string  (required)",
      "at least one of: id, mode",
    ]);
  });

  it("links the first code span naming a tool in each section, never in code, links or its own section", () => {
    const links = new Map([["get_state", "topic.tools-reading#get-state"], ["HassTurnOn", "topic.tools-controlling#hass-turn-on"]]);
    const body = [
      "Use `get_state`, then `get_state` again, and `HassTurnOn`.",
      "```",
      "`get_state`",
      "```",
      "Already [`get_state`](guide:topic.tools) linked.",
      "## Next {#get-state}",
      "`get_state` and `unknown_tool`.",
    ].join("\n");
    expect(linkTools(body, links, "topic.tools-reading")).toBe([
      "Use [`get_state`](guide:topic.tools-reading#get-state), then `get_state` again, and [`HassTurnOn`](guide:topic.tools-controlling#hass-turn-on).",
      "```",
      "`get_state`",
      "```",
      "Already [`get_state`](guide:topic.tools) linked.",
      "## Next {#get-state}",
      "`get_state` and `unknown_tool`.",
    ].join("\n"));
  });

  it("lists under each capability the tools that declare it, then those some calls of which need it, and the rest under none", () => {
    const by = toolsByCapability(reference);
    const page = english.topics["window.capabilities"].body;
    const listed = (anchor: string) => {
      const at = page.indexOf(`{#${anchor}}`);
      const fold = page.slice(page.indexOf("+++ ", at), page.indexOf("\n+++", page.indexOf("+++ ", at) + 4));
      return [...fold.matchAll(/\[`([^`]+)`\]\(guide:[^)]+\)( \(some calls\))?/g)].map((m) => `${m[1]}${m[2] ? "*" : ""}`);
    };
    for (const cap of reference.capabilities) {
      const declared = reference.tools.filter((t) => [...t.caps, ...t.capsAny, ...Object.values(t.capsWhen).flatMap((v) => Object.values(v).flat())].includes(cap));
      const some = reference.tools.filter((t) => t.capsSometimes.includes(cap));
      const expected = [...declared.map((t) => t.name), ...some.map((t) => `${t.name}*`)];
      expect(listed(cap.replace(/^cap_/, "").replace(/_/g, "-")), cap).toEqual(expected);
      expect(by.get(cap)!.map((r) => `${r.tool.name}${r.sometimes ? "*" : ""}`)).toEqual(expected);
      // No capability's list is empty: a tool always reaches it, if only for some calls.
      expect(expected.length, cap).toBeGreaterThan(0);
    }
    expect(listed("no-capability")).toEqual(by.get("none")!.map((r) => r.tool.name));
    expect(listed("service-response")).toEqual(["call_service*"]);
  });

  it("ships each group page with a line where each tool's details go, the details in one shared file", () => {
    const shipped = shippedFile("en");
    const blocks = shippedFile(REFERENCE_FILE).blocks as Record<string, string>;
    expect(Object.keys(blocks).sort()).toEqual(reference.tools.map((t) => t.name).sort());
    const page = shipped.topics[groupPage("reading")];
    expect(page.more).toBe(REFERENCE_FILE);
    expect(page.body).toContain("{{toolText:get_state}}");
    // In full, the page is what the build compiled and checked.
    expect(withToolText(page.body, blocks)).toBe(english.topics[groupPage("reading")].body);
    expect(withToolText(page.body, {})).toBeNull();
    expect(withoutToolText(page.body).split("\n").some((l) => TOOL_TEXT.test(l))).toBe(false);
    // A translated Guide's page shares the same details, with its own words for them.
    expect(shippedFile("zh-Hans").topics[groupPage("reading")].body).toContain("{{toolText:get_state}}");
    expect(shippedFile("zh-Hans").refLabels.needs).not.toBe(shipped.refLabels.needs);
    expect(withRefLabels(blocks.get_state, shipped.refLabels)).not.toMatch(REF_LABEL);
    // A language without its own tools page reads the English reference.
    expect(shippedFile("de").topics[groupPage("reading")]).toBeUndefined();
    expect(shippedFile("de").refLabels).toBeUndefined();
  });

  it("names every detail word in the English catalog, and uses no other", () => {
    const labels = (en as unknown as { guideTools: { ref: Record<string, string> } }).guideTools.ref;
    expect(DETAIL_LABELS.filter((key) => typeof labels[key] !== "string")).toEqual([]);
    const used = new Set(Object.values(shippedFile(REFERENCE_FILE).blocks as Record<string, string>).flatMap((b) => [...b.matchAll(REF_LABEL)].map((m) => m[1])));
    expect([...used].filter((key) => !(DETAIL_LABELS as readonly string[]).includes(key))).toEqual([]);
  });

  it("renders a folded list as a disclosure", () => {
    const html = renderTopic("Intro.\n\n+++ Tools it allows (2)\n\n- [`a`](guide:topic.tools)\n\n+++\n\nAfter.");
    expect(html).toMatch(/<details class="phx-guide-fold"><summary>Tools it allows \(2\)<\/summary>/);
    expect(html).toContain("</details>");
    expect(html.indexOf("After.")).toBeGreaterThan(html.indexOf("</details>"));
  });
});
