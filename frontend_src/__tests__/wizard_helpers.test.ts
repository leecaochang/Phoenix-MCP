import { describe, it, expect } from "vitest";
import { load } from "js-yaml";
import { buildAgentTabs, buildBearerValue, buildClaudeCommand, buildCodexCommand, buildCodexConfig, buildCursorJson, buildGeminiCommand, buildHarnessConfig, buildMcpJson, buildMcpUrl, buildSkillInstall, buildTestPrompt, codexTokenEnv, firstGreenEntity, firstGreenTarget, harnessServerName, mcpServerName, skillUrlFromMcp } from "../wizard_helpers";
import { PERSONA_CAP_DEFAULTS } from "../personas";
import type { EntityTree, PermissionTree } from "../types";

describe("buildMcpUrl", () => {
  it("appends the Phoenix MCP MCP path to the origin", () => {
    expect(buildMcpUrl("https://x.test")).toBe("https://x.test/api/phoenix-mcp");
  });
  it("does not double a trailing slash", () => {
    expect(buildMcpUrl("https://x.test/")).toBe("https://x.test/api/phoenix-mcp");
  });
});

// Per-token server names: one client entry per token, switch by toggling.
describe("mcpServerName", () => {
  it("derives a lowercase hyphenated name from the token name", () => {
    expect(mcpServerName("Steve_Laptop")).toBe("phx-steve-laptop");
  });
  it("preserves hyphens already in the name", () => {
    expect(mcpServerName("read-only")).toBe("phx-read-only");
  });
  it("distinct token names give distinct server names", () => {
    expect(mcpServerName("voice")).not.toBe(mcpServerName("admin"));
  });
});

describe("codexTokenEnv", () => {
  it("derives a valid uppercase env identifier", () => {
    expect(codexTokenEnv("Steve-Laptop")).toBe("PHOENIX_TOKEN_STEVE_LAPTOP");
    expect(codexTokenEnv("Steve-Laptop")).toMatch(/^[A-Z_][A-Z0-9_]*$/);
  });
  it("distinct token names give distinct env vars", () => {
    expect(codexTokenEnv("voice")).not.toBe(codexTokenEnv("admin"));
  });
});

describe("buildClaudeCommand", () => {
  it("uses --transport http and an Authorization: Bearer header", () => {
    const cmd = buildClaudeCommand("http://192.168.1.11:8123/api/phoenix-mcp", "phx_abc", "phx-voice");
    expect(cmd).toContain("claude mcp add --transport http phx-voice");
    expect(cmd).toContain("http://192.168.1.11:8123/api/phoenix-mcp");
    expect(cmd).toContain("--header 'Authorization: Bearer phx_abc'");
    expect(cmd).not.toContain("X-API-Key");
  });
});

describe("buildMcpJson", () => {
  it("produces an http server config with a Bearer header", () => {
    const cfg = JSON.parse(buildMcpJson("http://ha/api/phoenix-mcp", "phx_xyz", "phx-voice"));
    const server = cfg.mcpServers["phx-voice"];
    expect(server.type).toBe("http");
    expect(server.url).toBe("http://ha/api/phoenix-mcp");
    expect(server.headers.Authorization).toBe("Bearer phx_xyz");
  });
});

describe("buildGeminiCommand", () => {
  it("uses gemini mcp add with --transport http and a Bearer header", () => {
    const cmd = buildGeminiCommand("http://ha/api/phoenix-mcp", "phx_abc", "phx-voice");
    expect(cmd).toContain("gemini mcp add phx-voice http://ha/api/phoenix-mcp");
    expect(cmd).toContain("--transport http");
    expect(cmd).toContain("--header 'Authorization: Bearer phx_abc'");
  });
});

describe("buildCodexConfig", () => {
  it("emits a config.toml table with url and bearer_token_env_var", () => {
    const cfg = buildCodexConfig("http://ha/api/phoenix-mcp", "phx-voice", "PHOENIX_TOKEN_VOICE");
    expect(cfg).toContain("[mcp_servers.phx-voice]");
    expect(cfg).toContain('url = "http://ha/api/phoenix-mcp"');
    expect(cfg).toContain('bearer_token_env_var = "PHOENIX_TOKEN_VOICE"');
  });
});

describe("HTTP client configuration", () => {
  it("adds Codex over HTTP using the configured bearer environment variable", () => {
    expect(buildCodexCommand("http://ha/api/phoenix-mcp", "phx-voice", "PHOENIX_TOKEN_VOICE"))
      .toBe("codex mcp add phx-voice --url http://ha/api/phoenix-mcp --bearer-token-env-var PHOENIX_TOKEN_VOICE");
  });

  it("quotes editable URL metacharacters instead of expanding them in a shell", () => {
    const url = "https://ha.test/api/phoenix-mcp?x=$(touch injected)&y='test'";
    for (const command of [
      buildClaudeCommand(url, "phx_abc", "phx-voice"),
      buildGeminiCommand(url, "phx_abc", "phx-voice"),
      buildCodexCommand(url, "phx-voice", "PHOENIX_TOKEN_VOICE"),
    ]) {
      expect(command).toContain("'https://ha.test/api/phoenix-mcp?x=$(touch injected)&y='\\''test'\\''' ");
    }
  });

  it("keeps Harness names within its schema without truncating distinct tokens", () => {
    const first = "a".repeat(31) + "b";
    const second = "a".repeat(31) + "c";
    expect(harnessServerName(first)).toMatch(/^[A-Za-z0-9_-]{1,32}$/);
    expect(harnessServerName(first)).not.toBe(harnessServerName(second));
    expect(harnessServerName("Voice_Assist")).toBe("voice-assist");
  });

  it("emits a parseable Harness insert overlay with the bearer header intact", () => {
    const url = 'https://ha.test/api/phoenix-mcp?value="quoted"';
    const overlay = load(buildHarnessConfig(url, "phx_secret", "Voice_Assist")) as Array<{
      insert: Array<{ name: string; config: { serverName: string; transport: string; url: string; headers: Record<string, string> } }>;
    }>;
    const entry = overlay[0].insert[0];
    expect(entry.name).toBe("@deepseek-ai/dsh-mcp-client");
    expect(entry.config).toEqual({
      serverName: "voice-assist", transport: "streamable-http", url,
      headers: { Authorization: "Bearer phx_secret" },
    });
  });
});

describe("buildAgentTabs", () => {
  it("returns the supported clients with Harness and keeps the limited hosted beta out", () => {
    const tabs = buildAgentTabs("http://ha/api/phoenix-mcp", "phx_abc", "Voice_Assist");
    expect(tabs.map((t) => t.key)).toEqual(["claude", "gemini", "codex", "cursor", "deepseek-harness", "other"]);
    expect(tabs[0].blocks[0].code).toContain("claude mcp add");
    // Every tab carries a docs link and at least one block.
    for (const t of tabs) {
      expect(t.href).toMatch(/^https:\/\//);
      expect(t.blocks.length).toBeGreaterThan(0);
    }
  });

  it("threads the token-derived server name into every tab", () => {
    const tabs = buildAgentTabs("http://ha/api/phoenix-mcp", "phx_abc", "Voice_Assist");
    expect(tabs.find((t) => t.key === "claude")!.blocks[0].code).toContain("phx-voice-assist");
    expect(tabs.find((t) => t.key === "gemini")!.blocks[0].code).toContain("phx-voice-assist");
    const other = JSON.parse(tabs.find((t) => t.key === "other")!.blocks[0].code!);
    expect(other.mcpServers["phx-voice-assist"]).toBeDefined();
    const cursor = JSON.parse(tabs.find((t) => t.key === "cursor")!.blocks[0].code!);
    expect(cursor.mcpServers["phx-voice-assist"]).toBeDefined();
  });

  it("Codex tab gives the exact Authorization header key/value as fields and a per-token env var", () => {
    const codex = buildAgentTabs("http://ha/api/phoenix-mcp", "phx_abc", "Voice_Assist").find((t) => t.key === "codex")!;
    expect(codex.blocks[0].code).toContain("codex mcp add phx-voice-assist --url");
    expect(codex.blocks[0].code).toContain("export PHOENIX_TOKEN_VOICE_ASSIST=phx_abc");
    const guiFields = codex.blocks[1].fields!;
    expect(guiFields).toContainEqual({ label: "Server name", value: "phx-voice-assist" });
    expect(guiFields).toContainEqual({ label: "Header key", value: "Authorization" });
    expect(guiFields).toContainEqual({ label: "Header value", value: "Bearer phx_abc" });
    expect(codex.blocks[2].title).toContain("~/.codex/config.toml");
    expect(codex.blocks[2].code).toContain("[mcp_servers.phx-voice-assist]");
    expect(codex.blocks[2].code).toContain('bearer_token_env_var = "PHOENIX_TOKEN_VOICE_ASSIST"');
  });
});

describe("buildCursorJson", () => {
  it("uses url + headers without a type field (Cursor format)", () => {
    const cfg = JSON.parse(buildCursorJson("http://ha/api/phoenix-mcp", "phx_abc", "phx-voice"));
    const server = cfg.mcpServers["phx-voice"];
    expect(server.url).toBe("http://ha/api/phoenix-mcp");
    expect(server.headers.Authorization).toBe("Bearer phx_abc");
    expect(server.type).toBeUndefined();
  });
});

describe("buildTestPrompt", () => {
  it("includes a read prompt and an action prompt when not enforced", () => {
    const p = buildTestPrompt("Desk Lamp", false);
    expect(p.read).toBe("List my Home Assistant lights");
    expect(p.action).toBe("Please toggle the Desk Lamp");
  });
  it("suppresses the action prompt under MESA enforced mode", () => {
    const p = buildTestPrompt("Desk Lamp", true);
    expect(p.read).toBe("List my Home Assistant lights");
    expect(p.action).toBeNull();
  });
});

describe("firstGreenEntity", () => {
  const tree = (entities: Record<string, string>): PermissionTree => ({
    domains: {},
    devices: {},
    entities: Object.fromEntries(
      Object.entries(entities).map(([k, v]) => [k, { state: v as never, hint: null }]),
    ),
  });

  it("returns the GREEN entity", () => {
    expect(firstGreenEntity(tree({ "light.a": "GREY", "light.b": "GREEN" }))).toBe("light.b");
  });
  it("returns null when no entity is GREEN", () => {
    expect(firstGreenEntity(tree({ "light.a": "YELLOW" }))).toBeNull();
  });
});

describe("firstGreenTarget", () => {
  const entityTree: EntityTree = {
    light: {
      devices: {},
      deviceless_entities: [],
      entity_details: {
        "light.lamp": { entity_id: "light.lamp", friendly_name: "Lamp", device_id: "dev1", area_id: null, area_name: null, labels: [] },
        "light.ceiling": { entity_id: "light.ceiling", friendly_name: "Ceiling", device_id: null, area_id: null, area_name: null, labels: [] },
      },
    },
  };
  const tree = (over: Partial<PermissionTree>): PermissionTree => ({
    domains: {}, devices: {}, entities: {}, ...over,
  });

  it("prefers a directly granted entity", () => {
    const t = tree({ entities: { "light.ceiling": { state: "GREEN", hint: null } } });
    expect(firstGreenTarget(t, entityTree)).toBe("light.ceiling");
  });
  it("resolves a granted device to an entity under it", () => {
    const t = tree({ devices: { dev1: { state: "GREEN", hint: null } } });
    expect(firstGreenTarget(t, entityTree)).toBe("light.lamp");
  });
  it("resolves a granted domain to an entity in it", () => {
    const t = tree({ domains: { light: { state: "GREEN", hint: null } } });
    expect(firstGreenTarget(t, entityTree)).not.toBeNull();
  });
  it("returns null when nothing is granted", () => {
    expect(firstGreenTarget(tree({}), entityTree)).toBeNull();
  });
});

describe("buildBearerValue", () => {
  it("prefixes the token with Bearer", () => {
    expect(buildBearerValue("phx_x")).toBe("Bearer phx_x");
  });
});

describe("skillUrlFromMcp", () => {
  it("swaps the MCP path for the skill path on the same host", () => {
    expect(skillUrlFromMcp("http://192.168.1.11:8123/api/phoenix-mcp")).toBe(
      "http://192.168.1.11:8123/api/phoenix-mcp/skill",
    );
  });
  it("tolerates a trailing slash on the MCP path", () => {
    expect(skillUrlFromMcp("https://ha.test/api/phoenix-mcp/")).toBe("https://ha.test/api/phoenix-mcp/skill");
  });
  it("appends the skill path when the URL is not the MCP path", () => {
    expect(skillUrlFromMcp("https://ha.test")).toBe("https://ha.test/api/phoenix-mcp/skill");
  });
});

describe("buildSkillInstall", () => {
  const url = "http://ha.test/api/phoenix-mcp/skill";

  it("installs a real Claude Code skill at ~/.claude/skills/phoenix/SKILL.md", () => {
    const blocks = buildSkillInstall(url, "claude");
    expect(blocks).toHaveLength(1);
    expect(blocks[0].code).toContain("~/.claude/skills/phoenix/SKILL.md");
    expect(blocks[0].code).toContain(url);
  });
  it("creates a recognized Cursor rule referencing the downloaded guide", () => {
    const blocks = buildSkillInstall(url, "cursor");
    const command = blocks[0].code!;
    expect(command).toContain("-o .cursor/phoenix.md");
    expect(command).toContain(".cursor/rules/phoenix.mdc");
    const metadata = command.split("\n---\n")[1];
    expect(load(metadata)).toEqual({
      description: "Use when inspecting, controlling, or configuring Home Assistant through Phoenix MCP",
      alwaysApply: false,
    });
    expect(command).toContain("@.cursor/phoenix.md");
  });
  it("gives non-Claude agents the skill URL as a copyable field plus a download", () => {
    for (const key of ["gemini", "codex", "deepseek-harness", "other"]) {
      const blocks = buildSkillInstall(url, key);
      expect(blocks[0].fields).toContainEqual({ label: "Skill guide URL", value: url });
      expect(blocks[0].code).toContain(url);
    }
  });
  it("installs native skills in each client's discovered user directory", () => {
    expect(buildSkillInstall(url, "gemini")[0].code).toContain("~/.gemini/skills/phoenix/SKILL.md");
    expect(buildSkillInstall(url, "codex")[0].code).toContain("~/.agents/skills/phoenix/SKILL.md");
    expect(buildSkillInstall(url, "deepseek-harness")[0].code).toContain("~/.dsh/skills/phoenix/SKILL.md");
  });
});

describe("PERSONA_CAP_DEFAULTS drift guard", () => {
  // Must mirror const.py CAPABILITY_NAMES exactly.
  const CAP_KEYS = [
    "cap_config_read", "cap_camera_read", "cap_template_render", "cap_log_read", "cap_log_control",
    "cap_search", "cap_registry_read", "cap_traces", "cap_diagnostics",
    "cap_broadcast", "cap_service_response",
    "cap_automation_write", "cap_script_write", "cap_blueprint_write",
    "cap_scene_write", "cap_helper_write", "cap_calendar_write",
    "cap_physical_control", "cap_restart",
    "cap_integration_write", "cap_integration_reconfigure", "cap_lovelace_write", "cap_registry_write", "cap_radio_write",
    "cap_energy_write",
    "cap_backup", "cap_filesystem", "cap_yaml_edit", "cap_esphome_yaml",
    "cap_esphome_flash",
  ];
  it("custom has no preset", () => {
    expect(PERSONA_CAP_DEFAULTS.custom).toBeNull();
  });
  it("every non-custom persona defines exactly the full cap set", () => {
    for (const [key, caps] of Object.entries(PERSONA_CAP_DEFAULTS)) {
      if (key === "custom") continue;
      expect(Object.keys(caps!).sort()).toEqual([...CAP_KEYS].sort());
    }
  });
});
