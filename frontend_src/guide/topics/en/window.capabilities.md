---
{ "id": "window.capabilities", "title": "{{label:accessKeys.cardCapabilities}}", "parent": "workspace.accessKeys", "order": 3, "keywords": ["tools", "permission", "approval", "deny", "allow", "confirm"], "sectionKeywords": { "modes": ["approval", "queue"], "physical-control": ["lock", "alarm", "cover", "valve", "garage door"], "restart": ["reboot"], "backup": ["snapshot"], "camera-read": ["image", "snapshot"], "filesystem": ["www", "themes", "files"], "lovelace-write": ["Lovelace", "dashboard"], "dashboard-resources": ["custom cards", "JavaScript"], "yaml-edit": ["configuration.yaml", "reload"], "esphome-flash": ["firmware", "OTA"], "registry-write": ["rename entity", "entity ID"], "radio-write": ["Zigbee", "ZHA"], "log-control": ["debug logging"] }, "covers": ["frontend_src/components/CapabilityMatrix.tsx", "custom_components/phoenix_mcp/const.py", "custom_components/phoenix_mcp/tool_defs.py"] }
---
Capabilities decide which tools the key can use. Each one is {{label:caps.modeDeny}}, {{label:caps.modeAllow}}, or {{label:caps.modeConfirm}}. A new key starts with every capability set to {{label:caps.modeDeny}}.

Capabilities decide what kind of thing the agent can do. The [{{label:accessKeys.permissionsTree}}](guide:window.permissions) decides which devices and entities it can do it to.

## Deny, Allow, and Confirm {#modes}

- **{{label:caps.modeDeny}}**: the agent can't use these tools. They aren't offered to it, unless {{label:accessKeys.announceAllLabel}} is on.
- **{{label:caps.modeAllow}}**: the agent uses them freely.
- **{{label:caps.modeConfirm}}**: changes wait in [Approvals](guide:workspace.approvals) until you decide. Reads covered by the same capability run straight away.

{{label:caps.tierRead}} and {{label:caps.tierEveryday}} are {{label:caps.modeDeny}} or {{label:caps.modeAllow}} only. Their **{{label:caps.modeConfirm}}** button is greyed out.

A change applies straight away. Loosening a capability lets the agent carry on. Tightening one refuses the agent's requests already in progress. When you approve a request, Phoenix checks the capability again. If it's now {{label:caps.modeDeny}}, the request fails.

Related: [Inline approval wait](guide:window.keySettings#inline-wait), [What pass-through opens](guide:topic.passThrough)

## {{label:caps.tierRead}} {#reads}

### {{label:caps.cap_config_read.label}} {#config-read}

Reads Home Assistant's configuration, Energy settings, solar forecast, blueprints, and MESA profiles. Pass-through allows it.

{{tools:cap_config_read}}

### {{label:caps.cap_camera_read.label}} {#camera-read}

Takes still images from cameras the key can reach. An action that names a camera, an image, or a camera recording as a media source, such as an AI Task attachment, needs it too. So does one that names a private photo, file, or capture library. No persona allows it, and pass-through doesn't either.

{{tools:cap_camera_read}}

### {{label:caps.cap_template_render.label}} {#template-render}

Renders Home Assistant templates. Pass-through allows it.

{{tools:cap_template_render}}

### {{label:caps.cap_log_read.label}} {#log-read}

Reads the system log, the logbook, notifications, and integration log levels. Pass-through doesn't allow it.

{{tools:cap_log_read}}

### {{label:caps.cap_search.label}} {#search}

Searches entities and describes areas, entities, relationships, and available actions, including dry runs. Every persona and pass-through allow it.

{{tools:cap_search}}

### {{label:caps.cap_registry_read.label}} {#registry-read}

Lists areas, floors, zones, devices, automations, scripts, scenes, helpers, and labels. Every persona and pass-through allow it.

{{tools:cap_registry_read}}

### {{label:caps.cap_traces.label}} {#traces}

Reads automation traces. Pass-through allows it.

{{tools:cap_traces}}

### {{label:caps.cap_diagnostics.label}} {#diagnostics}

Reads system health, repairs, configuration checks, radio networks, and the ESPHome overview, and validates automations and scripts. With {{label:caps.cap_log_read.label}}, it also reads the fault log, captured debug records, and Phoenix's own diagnostics. Pass-through allows it.

{{tools:cap_diagnostics}}

### {{label:caps.cap_filesystem.label}} {#filesystem}

Reads files in the www, themes, and custom_templates folders. Pass-through doesn't allow it.

{{tools:cap_filesystem}}

## {{label:caps.tierEveryday}} {#everyday}

### {{label:caps.cap_broadcast.label}} {#broadcast}

Broadcasts a message through Assist satellites, starts conversations on them, and plays announcements and spoken text on speakers. The key also needs W on the speaker. Ordinary playback needs only W. Pass-through allows it.

{{tools:cap_broadcast}}

### {{label:caps.cap_service_response.label}} {#service-response}

Returns the data an action responds with. Without it, most actions still run but return no data. Phoenix refuses actions that only return data, such as weather forecasts and Music Assistant library searches. Pass-through allows it.

{{tools:cap_service_response}}

### {{label:caps.cap_esphome_yaml.label}} {#esphome-yaml}

Reads and searches ESPHome device configuration. With the ESPHome Device Builder, it also validates, compiles, and inspects devices. Pass-through doesn't allow it.

{{tools:cap_esphome_yaml}}

## {{label:caps.tierConfigWrite}} {#config-changes}

Pass-through never changes these. Each keeps its own setting.

### {{label:caps.cap_automation_write.label}} {#automation-write}

Reads, creates, edits, and deletes automations. Adding a call that runs code or reaches the host, a restart or update, a templated action name, or a blueprint always needs your approval, even on {{label:caps.modeAllow}}.

{{tools:cap_automation_write}}

### {{label:caps.cap_script_write.label}} {#script-write}

Reads, creates, edits, and deletes scripts. The approval rule for automations applies here too.

{{tools:cap_script_write}}

### {{label:caps.cap_blueprint_write.label}} {#blueprint-write}

Creates, edits, and deletes blueprints. Writing a blueprint also needs the matching automations or scripts capability. If either is {{label:caps.modeDeny}}, Phoenix refuses it. If either is {{label:caps.modeConfirm}}, the write waits for your approval. The approval rule for automations applies here too.

{{tools:cap_blueprint_write}}

### {{label:caps.cap_scene_write.label}} {#scene-write}

Reads, creates, edits, and deletes scenes. The key needs W on every entity in the scene.

{{tools:cap_scene_write}}

### {{label:caps.cap_helper_write.label}} {#helper-write}

Creates, edits, and deletes helpers, and changes their settings.

{{tools:cap_helper_write}}

### {{label:caps.cap_calendar_write.label}} {#calendar-write}

Creates, changes, and deletes calendar events.

{{tools:cap_calendar_write}}

## {{label:caps.tierSystem}} {#system}

### {{label:caps.cap_physical_control.label}} {#physical-control}

Controls locks, alarm panels, covers, and valves, through their own actions or any other action that reaches them. Raw device commands such as `zwave_js.set_value` always need it, and so do ESPHome actions on a device with one of these.

The agent also needs W on them in the {{label:accessKeys.permissionsTree}}. A raw command or an ESPHome action needs W on everything its device can operate, buttons included. Radio tools that change Zigbee devices also need {{label:caps.cap_physical_control.label}}.

Creating or editing a scene with one of these in it needs this capability as well.

{{tools:cap_physical_control}}

### {{label:caps.cap_notify.label}} {#notify}

Sends notifications, through its own tool, a notify action, or any other action that sends through a notify entity. A legacy notify action other than a phone's always needs your approval, even on {{label:caps.modeAllow}}.

{{tools:cap_notify}}

### {{label:caps.cap_restart.label}} {#restart}

Restarts or stops Home Assistant and installs updates. Every update needs your approval, even on {{label:caps.modeAllow}}. ESPHome firmware uses its own capability instead.

{{tools:cap_restart}}

### {{label:caps.cap_log_control.label}} {#log-control}

Changes an integration's log level. It also needs {{label:caps.cap_log_read.label}}. No persona allows it.

{{tools:cap_log_control}}

### {{label:caps.cap_integration_write.label}} {#integration-write}

Adds, changes, enables, disables, reloads, and removes integrations, and removes devices.

{{tools:cap_integration_write}}

### {{label:caps.cap_integration_reconfigure.label}} {#integration-reconfigure}

Reconfigures an integration, adds or changes its subentries, such as an AI agent, and fixes its repair issues. Subentries and repair fixes always need your approval, even on {{label:caps.modeAllow}}.

{{tools:cap_integration_reconfigure}}

### {{label:caps.cap_lovelace_write.label}} {#lovelace-write}

Reads and changes dashboards. Without it, the agent can't read dashboards either.

Cards run with the access of whoever opens the dashboard. So some changes wait for your approval, even on {{label:caps.modeAllow}}. These add a template, add or change any custom card, or add a picture or link from another site. An agent's restore of an earlier layout that brings such a card back waits too.

A custom card already on the dashboard and left unchanged doesn't count. Built-in cards without any of these follow the mode you set.

Phoenix first tries any new templates in a dashboard change. If one crashes, takes too long, or goes over a limit, Phoenix refuses the change.

{{tools:cap_lovelace_write}}

### {{label:caps.cap_registry_write.label}} {#registry-write}

Renames entities, changes their area, device, labels, and categories, and deletes them. An entity's ID can change within its domain. Purging an entity's recorded history with `recorder.purge_entities` needs this capability too.

{{tools:cap_registry_write}}

### {{label:caps.cap_radio_write.label}} {#radio-write}

Manages Zigbee networks. Scanning the network also needs {{label:caps.cap_diagnostics.label}}. Changing devices, bindings, and groups also needs {{label:caps.cap_physical_control.label}}.

{{tools:cap_radio_write}}

### {{label:caps.cap_energy_write.label}} {#energy-write}

Changes the Energy dashboard's settings.

{{tools:cap_energy_write}}

### {{label:caps.cap_esphome_flash.label}} {#esphome-flash}

Installs firmware on ESPHome devices, through the ESPHome Device Builder. It also needs {{label:caps.cap_esphome_yaml.label}}. Only the {{label:personas.esphome.label}} persona allows it, with your approval. `update.install` on an ESPHome device needs this capability as well.

{{tools:cap_esphome_flash}}

### {{label:caps.cap_yaml_edit.label}} {#yaml-edit}

Reads Home Assistant's YAML configuration files and runs its reload actions. It also adds file and line numbers to configuration checks.

{{tools:cap_yaml_edit}}

## {{label:caps.tierIrreversible}} {#high-risk}

### {{label:caps.cap_backup.label}} {#backup}

Lists and creates backups.

{{tools:cap_backup}}

### {{label:caps.cap_dashboard_resources.label}} {#dashboard-resources}

Adds and changes dashboard resources, the JavaScript files that custom cards load. Every change needs your approval, so {{label:caps.modeAllow}} isn't available. Files must come from `/local/` or `/hacsfiles/`. Phoenix changes only the resources it added itself.

Every browser that opens a dashboard runs these files with that person's Home Assistant access. An administrator's browser also runs them when it opens the Phoenix MCP panel. The panel reads the custom cards they add.

{{tools:cap_dashboard_resources}}

## Tools that need no capability {#no-capability}

The [{{label:accessKeys.permissionsTree}}](guide:window.permissions) decides which entities these tools reach. The ones that change something are offered only when the tree gives the key Write somewhere.

Some calls still need a capability, such as locking a door, which needs {{label:caps.cap_physical_control.label}}.

{{tools:none}}
