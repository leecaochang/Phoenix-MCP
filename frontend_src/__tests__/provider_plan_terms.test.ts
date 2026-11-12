/**
 * Plan-terms caveat in the provider add form.
 *
 * Z.ai's Coding Plan, MiMo's Token Plan and Alibaba Cloud's Coding and Token Plan
 * hosts forbid use from application backends such as Home Assistant; the form
 * names that while such a route is chosen, and stays quiet for the others.
 */
import { describe, expect, it } from "vitest";

import { planTermsNote } from "../components/ProviderAddForm";
import type { AgentCliProviderType } from "../types";

const choice = (value: string, note_key?: string) => ({ value, label: value, ...(note_key ? { note_key } : {}) });

const zai: AgentCliProviderType = {
  kind: "zai", label: "Z.ai",
  fields: [{ id: "endpoint_id", type: "choice", label_key: "x",
    choices: [choice("standard"), choice("coding", "settings.agentcliZaiCodingTerms"), choice("china")] }],
} as AgentCliProviderType;
const qwen: AgentCliProviderType = {
  kind: "qwen", label: "Qwen",
  fields: [{ id: "api_key", type: "secret", label_key: "x" }, { id: "base_url", type: "url", label_key: "x" }],
} as AgentCliProviderType;

describe("planTermsNote", () => {
  it("names the caveat of the chosen route only", () => {
    expect(planTermsNote(zai, { endpoint_id: "coding" })).toBe("settings.agentcliZaiCodingTerms");
    expect(planTermsNote(zai, { endpoint_id: "standard" })).toBeNull();
    expect(planTermsNote(zai, { endpoint_id: "china" })).toBeNull();
  });

  it("recognises Alibaba Cloud's plan hosts in a typed Qwen address", () => {
    for (const url of [
      "https://coding-intl.dashscope.aliyuncs.com/v1",
      " https://coding.dashscope.aliyuncs.com/v1 ",
      "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    ]) expect(planTermsNote(qwen, { api_key: "k", base_url: url }), url).toBe("settings.agentcliQwenPlanTerms");
    for (const url of [
      "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
      "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "https://example.com/coding-intl.dashscope.aliyuncs.com",
    ]) expect(planTermsNote(qwen, { api_key: "k", base_url: url }), url).toBeNull();
  });

  it("never warns for another provider with a matching address", () => {
    const other = { ...qwen, kind: "openrouter" } as AgentCliProviderType;
    expect(planTermsNote(other, { base_url: "https://coding-intl.dashscope.aliyuncs.com/v1" })).toBeNull();
  });
});
