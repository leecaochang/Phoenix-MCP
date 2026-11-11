/**
 * The review of a system action in the Change window: why it needs approval, what happens,
 * what uses the item and which Phoenix settings name it, with the whole preview still
 * available as YAML in a fold. The previews below follow the backend's real shapes.
 */
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { ApprovalChange } from "../components/ApprovalChange";
import type { ApprovalDiff, ApprovalRecord } from "../types";

function record(diff: ApprovalDiff, tool = "delete_entity"): ApprovalRecord {
  return {
    id: "a", access_key_id: "k", access_key_name: "key", tool_name: tool, cap_name: "cap_registry_write", args: {},
    diff, status: "pending", created_at: "", expires_at: "",
    resolved_at: null, approved_by_user_id: null, closure_reason: null, result: null, request_id: "r", client_ip: null,
  };
}

const automation = (n: number) => ({
  kind: "automation", id: `auto_${n}`, name: `Automation ${n}`, roles: ["action", "condition"],
  entities: [{ entity_id: "light.living_room_light", roles: ["action", "condition"] }],
});

/** A delete_entity preview as `_build_diff_delete_entity` builds it. */
function deleteEntityPreview(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    approval_reason: "This access key's Manage entity registry capability is set to Confirm.",
    name: "Living Room Light",
    area_id: "living_room",
    warning: "Removes the entity's registry entry. A live entity's integration may re-create it; an orphan stays gone.",
    relationships: {
      consumers: [
        automation(1), automation(2),
        { kind: "script", id: "movie_time", name: "Movie time", roles: ["sequence"], entities: [] },
        { kind: "scene", id: "evening", name: "Evening living room", roles: ["member"], entities: [] },
        { kind: "person", id: "pat", entity_id: "person.pat", name: null, roles: ["reference"], entities: [] },
        { kind: "dashboard", id: "lovelace", name: "Home", roles: ["card"], entities: [],
          paths: [["views", 0, "cards", 2, "entity"], ["views", 1, "sections", 0, "cards", 1, "entities", 0]] },
        { kind: "config_entry", id: "entry1", name: "Living room template", roles: ["template"], entities: [] },
      ],
      consumer_count: 7,
      searched: ["automation", "script", "scene", "group", "person", "dashboard", "config_entry"],
      not_searched: [
        { kind: "consumer_identity", reason: "YAML consumers without a verified Home Assistant identity are omitted." },
      ],
    },
    phoenix_configuration: [
      { kind: "access_key_permission", access_key_id: "k1", access_key_name: "kitchen_key" },
      { kind: "preset_permission", access_key_id: "k2", access_key_name: "hall_key", preset_id: "p1", preset_name: "Night" },
      { kind: "global_hint", entity_id: "light.living_room_light" },
      { kind: "pending_approval", approval_id: "ap_123", tool_name: "set_entity" },
    ],
    mesa: {
      decision: "confirm", rule: "control_mode", reason: "Lights here need a person to agree.",
      effective_rule: { control_mode: "confirm" }, warnings: ["The profile is inherited from the area."],
    },
    ...extra,
  };
}

function renderSystemAction(preview: Record<string, unknown>, summary = "Delete Living Room Light (light.living_room_light)") {
  return render(<ApprovalChange record={record({ kind: "system_action", summary, preview })} />);
}

describe("system action review", () => {
  it("keeps the summary line and says why the request needs approval", () => {
    renderSystemAction(deleteEntityPreview());
    expect(screen.getByText("Delete Living Room Light (light.living_room_light)")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Why This Needs Your Approval" })).toBeTruthy();
    expect(screen.getByText("This access key's Manage entity registry capability is set to Confirm.")).toBeTruthy();
  });

  it("shows the warning, the plain fields and the MESA verdict under What Happens", () => {
    const { container } = renderSystemAction(deleteEntityPreview());
    expect(screen.getByRole("heading", { name: "What Happens" })).toBeTruthy();
    expect(screen.getByRole("note").textContent).toContain("Removes the entity's registry entry.");
    expect(screen.getByText("Name").nextElementSibling?.textContent).toBe("Living Room Light");
    expect(screen.getByText("Area").nextElementSibling?.textContent).toBe("living_room");
    expect(screen.getByText("MESA").nextElementSibling?.textContent).toBe("Confirm: Lights here need a person to agree.");
    expect(screen.getByText("The profile is inherited from the area.")).toBeTruthy();
    // Nested values stay in the fold rather than being flattened into rows.
    expect(container.querySelector(".approval-detail-meta")?.textContent).not.toContain("control_mode");
    expect(container.textContent).not.toContain("[object Object]");
  });

  it("groups what uses the item by kind, with translated roles and counts", () => {
    renderSystemAction(deleteEntityPreview());
    expect(screen.getByRole("heading", { name: "Used By (7)" })).toBeTruthy();
    const automations = screen.getByRole("heading", { name: "Automations (2)" }).parentElement!;
    expect(within(automations).getByText("Automation 1").nextElementSibling?.textContent).toBe("action, condition");
    expect(screen.getByRole("heading", { name: "Scripts (1)" })).toBeTruthy();
    expect(screen.getByText("Movie time").nextElementSibling?.textContent).toBe("sequence");
    expect(screen.getByText("Evening living room").nextElementSibling?.textContent).toBe("member");
    // A consumer without a name falls back to its entity ID; a graph reference reads as "mentioned".
    expect(screen.getByText("person.pat").nextElementSibling?.textContent).toBe("mentioned");
    expect(screen.getByRole("heading", { name: "People (1)" })).toBeTruthy();
    // Unknown roles, such as a config entry's integration domain, show as they are.
    expect(screen.getByText("Living room template").nextElementSibling?.textContent).toBe("template");
    expect(screen.getByRole("heading", { name: "Integrations (1)" })).toBeTruthy();
  });

  it("names the places on a dashboard instead of its card role", () => {
    renderSystemAction(deleteEntityPreview());
    expect(screen.getByRole("heading", { name: "Dashboards (1)" })).toBeTruthy();
    expect(screen.getByText("Home").nextElementSibling?.textContent).toBe("view 1 > card 3, view 2 > section 1 > card 2");
  });

  it("lists at most eight consumers of one kind and counts the rest", () => {
    const consumers = Array.from({ length: 11 }, (_, i) => automation(i + 1));
    renderSystemAction(deleteEntityPreview({ relationships: { consumers, consumer_count: 11, searched: ["automation"] } }));
    const group = screen.getByRole("heading", { name: "Automations (11)" }).parentElement!;
    expect(within(group).getAllByRole("listitem")).toHaveLength(9);
    expect(within(group).getByText("Automation 8")).toBeTruthy();
    expect(within(group).queryByText("Automation 9")).toBeNull();
    expect(within(group).getByText("and 3 more")).toBeTruthy();
  });

  it("says what was not checked, and when nothing searched uses the item", () => {
    renderSystemAction(deleteEntityPreview({
      relationships: {
        consumers: [], consumer_count: 0, searched: ["automation", "script"],
        not_searched: [{ kind: "reference_graph", reason: "Home Assistant's reference graph is unavailable." }],
      },
    }));
    expect(screen.getByRole("heading", { name: "Used By" })).toBeTruthy();
    expect(screen.getByText("Nothing uses it in the places Phoenix checked.")).toBeTruthy();
    expect(screen.getByText("Not checked: Home Assistant's reference graph is unavailable.")).toBeTruthy();
  });

  it("lists the Phoenix settings that name the item, one line each", () => {
    renderSystemAction(deleteEntityPreview());
    const section = screen.getByRole("heading", { name: "Phoenix Settings That Name It" }).parentElement!;
    const lines = within(section).getAllByRole("listitem").map((li) => li.textContent);
    expect(lines).toEqual([
      "Entity permission in access key kitchen_key",
      "Entity permission in preset Night of access key hall_key",
      "Entity hint for all access keys",
      "Pending request for set_entity (ap_123)",
    ]);
  });

  it("leaves out the Phoenix settings section when nothing names the item", () => {
    renderSystemAction(deleteEntityPreview({ phoenix_configuration: [] }));
    expect(screen.queryByRole("heading", { name: "Phoenix Settings That Name It" })).toBeNull();
  });

  it("keeps the whole preview as YAML in a fold at the bottom", () => {
    const { container } = renderSystemAction(deleteEntityPreview());
    const fold = container.querySelector("details.action-preview-details")!;
    expect(fold).toBeTruthy();
    expect(fold.hasAttribute("open")).toBe(false);
    expect(fold.querySelector("summary")?.textContent).toBe("All Details (YAML)");
    expect(fold.querySelector("summary")?.getAttribute("data-help")).toBe("help.previewAllDetails");
    const yaml = fold.textContent ?? "";
    for (const key of ["approval_reason:", "relationships:", "phoenix_configuration:", "mesa:", "effective_rule:", "not_searched:"]) {
      expect(yaml).toContain(key);
    }
    expect(fold).toBe(container.querySelector(".action-preview")?.lastElementChild);
  });

  it("draws a service-shaped MESA verdict with the existing MESA block", () => {
    renderSystemAction({ mesa: { confirm_entities: ["light.a"], allowed_entities: [], warnings: [] } });
    expect(screen.getByText("MESA confirm").nextElementSibling?.textContent).toBe("light.a");
  });

  it("shows each entity's verdict for a location's list of MESA decisions", () => {
    renderSystemAction({ mesa: [{ entity_id: "light.a", decision: "confirm", rule: "r", reason: "Needs a person." }] });
    expect(screen.getByText("MESA").nextElementSibling?.textContent).toBe("light.a: Confirm: Needs a person.");
  });

  it("renders a preview of unknown keys as rows and YAML without crashing", () => {
    const { container } = renderSystemAction({
      backend: "zha", visible_device_count: 4, routes: false, mystery: null, nested: { deep: [1, 2] },
      warnings: ["First warning.", "Second warning."],
    }, "Scan Zigbee topology");
    expect(screen.getByText("backend").nextElementSibling?.textContent).toBe("zha");
    expect(screen.getByText("visible_device_count").nextElementSibling?.textContent).toBe("4");
    expect(screen.getByText("routes").nextElementSibling?.textContent).toBe("No");
    expect(screen.getByText("mystery").nextElementSibling?.textContent).toBe("(none)");
    expect(screen.queryByText("nested")).toBeNull();
    expect(within(screen.getByRole("note")).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("heading", { name: "Why This Needs Your Approval" })).toBeNull();
    expect(container.querySelector("details.action-preview-details")?.textContent).toContain("deep:");
  });

  it("shows each changed field as from and to, not only in the fold", () => {
    const diff = {
      kind: "system_action", summary_key: "diff.set_entity", target: { type: "entity", id: "light.kitchen", label: "Kitchen" },
      preview: {
        approval_reason: "This access key's Registry write capability is set to Confirm.",
        name: { before: "Kitchen Light", after: "Lounge Lamp" },
        enabled: { before: true, after: false },
        aliases: { before: ["Kitchen"], after: ["Kitchen", "Lounge"] },
        labels: { before: [], after: ["evening"] },
      },
    } as ApprovalDiff;
    const { container } = render(<ApprovalChange record={record(diff, "set_entity")} />);
    const meta = container.querySelector(".action-preview .approval-detail-meta") as HTMLElement;
    expect(within(meta).getByText("from Kitchen Light to Lounge Lamp")).toBeTruthy();
    expect(within(meta).getByText("from Yes to No")).toBeTruthy();
    expect(within(meta).getByText("from Kitchen to Kitchen, Lounge")).toBeTruthy();
    expect(within(meta).getByText(/^from .+ to evening$/)).toBeTruthy();
  });

  it("shows only the summary for an empty preview", () => {
    const { container } = renderSystemAction({}, "Open the Zigbee network");
    expect(screen.getByText("Open the Zigbee network")).toBeTruthy();
    expect(container.querySelector(".action-preview")).toBeNull();
  });

  it("keeps the Removing pane of a delete that carries its definition", () => {
    render(<ApprovalChange record={record({
      kind: "system_action", summary: "Delete script", before: "{\"alias\": \"Movie time\"}",
      preview: { warning: "This script will be removed permanently." },
    }, "delete_script")} />);
    expect(screen.getByText("Removing")).toBeTruthy();
    expect(screen.getByRole("note").textContent).toBe("This script will be removed permanently.");
  });
});

describe("other change kinds", () => {
  it("leaves a configuration diff without the action layout", () => {
    const { container } = render(<ApprovalChange record={record({
      kind: "config_diff", before: "a: 1", after: "a: 2", preview: { alias: "x" },
    }, "edit_script")} />);
    expect(container.querySelector(".action-preview")).toBeNull();
    expect(screen.queryByText("All Details (YAML)")).toBeNull();
  });

  it("leaves a service preview as its raw rows", () => {
    const { container } = render(<ApprovalChange record={record({
      kind: "service_preview", preview: { domain: "light", service: "turn_on", approval_reason: "Because." },
    }, "call_service")} />);
    expect(container.querySelector(".action-preview")).toBeNull();
    expect(screen.getByText("domain")).toBeTruthy();
    expect(screen.getByText("approval_reason")).toBeTruthy();
  });
});
