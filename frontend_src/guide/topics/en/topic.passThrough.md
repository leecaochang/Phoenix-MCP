---
{ "id": "topic.passThrough", "title": "What Pass-Through Opens", "parent": "window.accessKeys", "order": 2, "keywords": ["pass-through", "unrestricted", "full access", "scoped"], "covers": ["custom_components/phoenix_mcp/helpers.py", "custom_components/phoenix_mcp/const.py", "custom_components/phoenix_mcp/policy_engine.py"] }
---
A pass-through key skips the Permissions Tree: it can read and control every entity, except Phoenix's own. Use it only for an agent you trust with your whole home.

## What it turns on {#allows}

Pass-through also allows these capabilities, unless you set them to Confirm:

- {{label:caps.cap_config_read.label}}, {{label:caps.cap_template_render.label}}, {{label:caps.cap_search.label}} and {{label:caps.cap_registry_read.label}}.
- {{label:caps.cap_traces.label}}, {{label:caps.cap_diagnostics.label}}, {{label:caps.cap_broadcast.label}} and {{label:caps.cap_service_response.label}}.

{{label:caps.cap_diagnostics.label}} and {{label:caps.cap_service_response.label}} can expose sensitive details about your system.

## What stays in your control {#keeps}

Every other capability keeps its own setting. That includes physical control, restarts, notifications, camera images, system logs, files, ESPHome YAML and every configuration change. Confirm stays Confirm.

Locks, alarm panels, covers and valves still need {{label:caps.cap_physical_control.label}}. Sensitive attributes, such as camera stream addresses, are still removed.

## Narrowing it {#limit}

**{{label:accessKeys.assistExposureLabel}}** in [{{label:workspace.keySettings}}](guide:window.keySettings#assist-exposure) limits the key to the entities exposed to Assist. Home Companion keys can't use pass-through at all.

## Turning it off {#off}

**{{label:accessKeys.switchToScoped}}** in the [{{label:accessKeys.permissionsTree}}](guide:window.permissions#pass-through) window. The key uses its saved tree again.
