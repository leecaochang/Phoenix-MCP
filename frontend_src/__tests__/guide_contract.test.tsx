/**
 * The shipped Guide against the code it describes (user, 2026-11-01): it
 * compiles cleanly with every covered file present; every window of a
 * converted workspace has its page; every persona and capability has its
 * section; every Settings index entry has its section on its window's page, and selecting the entry's control in a
 * rendered window points the Guide there; every `<topic id>#<anchor>` the
 * source names exists; and only workspace overviews sit at the top of
 * Contents. The rules are in docs-internal/agents/guide.md.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { existsSync, readFileSync, readdirSync, statSync } from "fs";
import { join, resolve } from "path";
import { compileGuide, type GuideBundle } from "../guide/compile";
import { flattenCatalog } from "../i18n";
import { WORKSPACES } from "../shell/workspaces";
import { CHAT_WINDOW } from "../shell/chatDock";
import type { WorkspaceId } from "../shell/storage";
import { SETTING_INDEX } from "../shell/settingsIndex";
import { PERSONAS, PERSONA_CAP_DEFAULTS } from "../personas";
import { capabilityGuide, personaGuide } from "../guide/targets";
import { parseGuideRef } from "../shell/guideTarget";
import { findControl, settingAnchor, settingsGuideAnchor } from "../shell/settings";
import { api } from "../api";
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";
import { BASE_SETTINGS, mockSettingsApi, renderSettings, toolsFor } from "./settings_harness";
import type { GlobalSettings } from "../types";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  const { SETTINGS_API } = await import("./settings_api");
  return { ...actual, api: Object.fromEntries(SETTINGS_API.map((n) => [n, vi.fn()])) };
});

const REPO = resolve(__dirname, "../..");
const TOPICS = resolve(REPO, "frontend_src/guide/topics/en");
const LABELS = flattenCatalog((en as { panel: unknown }).panel);

/** Workspaces whose website pages have been turned into Guide pages; each gains this check as it is converted. */
const CONVERTED: readonly WorkspaceId[] = ["settings", "accessKeys", "approvals", "changes", "mesa", "logs", "my"];
/** Windows every workspace shares, documented once rather than per workspace. */
const SHARED = new Set(["attention", "help", "notifications"]);

function shipped(): { bundle: GuideBundle; problems: string[] } {
  const files = readdirSync(TOPICS).filter((f) => f.endsWith(".md")).map((f) => ({ file: f, source: readFileSync(join(TOPICS, f), "utf8") }));
  return compileGuide("en", files, (key) => key in LABELS, (path) => existsSync(resolve(REPO, path)));
}

/** Every source file of the panel, tests and topics left out. */
function sources(dir = resolve(REPO, "frontend_src")): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (name === "__tests__" || name === "topics") return [];
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

describe("the shipped Guide", () => {
  const { bundle, problems } = shipped();

  it("compiles cleanly, with every covered file present", () => {
    expect(problems).toEqual([]);
  });

  it("has a page for every window of every converted workspace", () => {
    for (const ws of CONVERTED) {
      for (const id of WORKSPACES[ws].windows.filter((w) => !SHARED.has(w))) {
        expect(bundle.topics[`window.${id}`], `window.${id}`).toBeDefined();
      }
    }
    for (const id of [...SHARED, CHAT_WINDOW]) expect(bundle.topics[`window.${id}`], `window.${id}`).toBeDefined();
  });

  it("has a section for every setting Find Setting knows, on its window's page", () => {
    const missing = SETTING_INDEX.filter((entry) => !bundle.topics[`window.${entry.window}`]?.anchors.includes(settingAnchor(entry.id)))
      .map((entry) => `window.${entry.window}#${settingAnchor(entry.id)}`);
    expect(missing).toEqual([]);
  });

  it("has a section for every persona and every capability, which their controls point at", () => {
    const refs = [...PERSONAS.map((p) => personaGuide(p.key)), ...Object.keys(PERSONA_CAP_DEFAULTS.read_only!).map(capabilityGuide)];
    expect(refs.length).toBeGreaterThan(40);
    const missing = refs.filter((ref) => {
      const { topic, anchor } = parseGuideRef(ref);
      return !bundle.topics[topic!]?.anchors.includes(anchor!);
    });
    expect(missing).toEqual([]);
  });

  it("resolves every page and section the panel's source points at", () => {
    const ref = /["'`]((?:window|workspace|wizard|topic)\.[A-Za-z0-9_-]+)#([a-z0-9]+(?:-[a-z0-9]+)*)["'`]/g;
    const found: string[] = [];
    const broken: string[] = [];
    for (const file of sources()) {
      for (const [, topic, anchor] of readFileSync(file, "utf8").matchAll(ref)) {
        found.push(`${topic}#${anchor}`);
        if (!bundle.topics[topic]?.anchors.includes(anchor)) broken.push(`${file.slice(REPO.length + 1)}: ${topic}#${anchor}`);
      }
    }
    expect(found.length).toBeGreaterThan(10);
    expect(broken).toEqual([]);
  });

  it("puts only workspace overviews at the top of Contents", () => {
    const top = Object.entries(bundle.topics).filter(([, t]) => !t.parent).map(([id]) => id);
    expect(top.filter((id) => !id.startsWith("workspace."))).toEqual([]);
  });
});

describe("selecting a setting", () => {
  beforeEach(() => mockSettingsApi());

  it("points the Guide at that setting's section", async () => {
    // Every optional setting shown: a decision account in use, a voice assistant set up, AI Task supported.
    vi.mocked(api.getDecisionProviders).mockResolvedValue({ instances: [{ id: "d1", kind: "jev", name: "JEV", model: "jev-latest" }], provider_types: [] } as never);
    vi.mocked(api.getAiTaskPreferred).mockResolvedValue({ supported: true, entity_id: "ai_task.phoenix_mcp_ai_task", gen_data_entity_id: null, gen_data_name: null, is_preferred: false });
    const { container, registry } = renderSettings({ settings: {
      ...BASE_SETTINGS, decision_provider_id: "d1", decision_provider_default_id: "d1", voice_agent_pipeline_id: "pl1",
    } as GlobalSettings });
    await screen.findByRole("radio", { name: "JEV" });
    await screen.findByRole("checkbox", { name: "Default AI Task", hidden: true });
    // Adding an account shows on request, from the toolbar.
    const add = await waitFor(() => toolsFor(registry).flatMap((g) => g.tools).find((tool) => tool.id === "addAccount")!);
    act(() => add.onSelect!());
    await screen.findAllByLabelText("Provider type");
    const wrong: string[] = [];
    let checked = 0;
    for (const entry of SETTING_INDEX) {
      const windowEl = container.querySelector(`[data-phx-window="${entry.window}"]`);
      const control = windowEl ? findControl(windowEl, entry) : null;
      if (!control) continue;
      checked++;
      const got = settingsGuideAnchor(control, entry.window);
      if (got !== settingAnchor(entry.id)) wrong.push(`${entry.id}: ${got}`);
    }
    expect(checked).toBe(SETTING_INDEX.length);
    expect(wrong).toEqual([]);
  });
});
