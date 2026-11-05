---
{ "id": "window.persona", "title": "{{label:accessKeys.cardPersona}}", "parent": "workspace.accessKeys", "order": 2, "keywords": ["role", "preset capabilities", "template"], "sectionKeywords": { "home-companion": ["household", "family"], "esphome": ["firmware"], "maintenance": ["backups"] }, "covers": ["frontend_src/components/PersonaPicker.tsx", "frontend_src/personas.ts", "custom_components/phoenix_mcp/personas.py"] }
---
A persona sets all of the key's capabilities at once, to suit a kind of agent. Choose one, then adjust single capabilities in [{{label:accessKeys.cardCapabilities}}](guide:window.capabilities) if you need to. Your choice applies straight away.

A persona decides which tools the agent may use, not which devices it can reach. That's the [{{label:accessKeys.permissionsTree}}](guide:window.permissions). The one exception is Home Companion.

Personas don't stop an agent from controlling devices. With every persona except Home Companion, the agent can control anything you set to W in the tree. Only locks, alarm panels, covers and valves also need [{{label:caps.cap_physical_control.label}}](guide:window.capabilities#physical-control).

With any [{{label:settings.colorThemeLabel}}](guide:window.settingsGeneral#color-theme) except {{label:settings.colorTheme.spartan}}, three personas have their own color: {{label:personas.home_companion.label}} gold, {{label:personas.new_user.label}} green and {{label:personas.custom.label}} blue.

## {{label:personas.read_only.label}} {#read-only}

Reads only: configuration, templates, system logs, search, the registry, automation traces, diagnostics and service response data. It changes no configuration, but it can still control anything set to W in the tree.

## {{label:personas.voice_assistant.label}} {#voice-assistant}

Reads configuration, templates, search and the registry, gets service response data, and can broadcast. Notifications and physical control need your approval.

## {{label:personas.dashboard_designer.label}} {#dashboard-designer}

Reads configuration, templates, search, the registry and theme and card files, and manages dashboards. Changes to the Energy dashboard and to dashboard resources need your approval.

## {{label:personas.maintenance.label}} {#maintenance}

Reads configuration, templates, logs, search, the registry, traces and diagnostics, and creates backups. Restarts, entity registry changes and radio management need your approval.

## {{label:personas.automation_builder.label}} {#automation-builder}

All the usual reads, and manages automations, scripts, blueprints, scenes, helpers and calendar events. It can also broadcast. Notifications, physical control and restarts need your approval.

## {{label:personas.power_user.label}} {#power-user}

Everything Automation Builder does, and restarts without asking. Integrations, dashboards, the entity registry, radios, the Energy dashboard, dashboard resources and backups need your approval.

## {{label:personas.home_admin.label}} {#home-admin}

Everything Power User does, plus reading theme and card files and ESPHome device YAML. Restarts and YAML configuration need your approval too. It doesn't allow camera images, integration log levels or ESPHome firmware flashing.

## {{label:personas.new_user.label}} {#new-user}

Reads configuration, templates, search and the registry, and gets service response data. Physical control needs your approval. The setup wizard starts with this persona.

## {{label:personas.esphome.label}} {#esphome}

Reads configuration, logs, search, the registry, diagnostics and ESPHome device YAML. Flashing firmware needs your approval; this is the only persona that allows it.

## {{label:personas.home_companion.label}} {#home-companion}

For a household helper. The agent gets a single tool that passes requests to Phoenix's own model, and reaches only household devices, whatever the tree grants. Physical control needs your approval.

In Agent Chat it uses the account chosen there; everywhere else it uses the Voice Agent's account and model. It can't use pass-through or announce all tools: choosing it turns both off, and you're told when it does. Changing a capability doesn't turn it into Custom.

## {{label:personas.custom.label}} {#custom}

Any mix of capabilities. Change a single capability and the persona becomes Custom, or the persona whose settings now match.

Choosing Custom again brings back the key's earlier custom settings, if it was Custom when you switched away and you haven't reloaded the page.
