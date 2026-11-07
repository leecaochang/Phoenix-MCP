---
{ "id": "window.capabilities", "title": "{{label:accessKeys.cardCapabilities}}", "parent": "workspace.accessKeys", "order": 3, "keywords": ["tools", "permission", "approval", "deny", "allow", "confirm"], "sectionKeywords": { "modes": ["approval", "queue"], "physical-control": ["lock", "alarm", "cover", "valve", "garage door"], "restart": ["reboot"], "backup": ["snapshot"], "camera-read": ["image", "snapshot"], "filesystem": ["www", "themes", "files"], "lovelace-write": ["Lovelace", "dashboard"], "dashboard-resources": ["custom cards", "JavaScript"], "yaml-edit": ["configuration.yaml", "reload"], "esphome-flash": ["firmware", "OTA"], "registry-write": ["rename entity", "entity ID"], "radio-write": ["Zigbee", "ZHA"], "log-control": ["debug logging"] }, "covers": ["frontend_src/components/CapabilityMatrix.tsx", "custom_components/phoenix_mcp/const.py", "custom_components/phoenix_mcp/tool_defs.py"] }
---
Capabilities decide which tools the key may use. Each one is Deny, Allow or Confirm, and a new key starts with every capability set to Deny.

Capabilities and the [{{label:accessKeys.permissionsTree}}](guide:window.permissions) work together. Capabilities decide what kind of thing the agent may do; the tree decides which devices and entities it can do it to.

## Deny, Allow and Confirm {#modes}

- **{{label:caps.modeDeny}}**: the agent can't use these tools. They aren't offered to it.
- **{{label:caps.modeAllow}}**: the agent uses them freely.
- **{{label:caps.modeConfirm}}**: changes wait in [Approvals](guide:workspace.approvals) until you decide. Reads covered by the same capability run straight away.

Reads and Everyday Actions are Deny or Allow only; their Confirm button is greyed out.

A change applies straight away. Loosening a capability lets the agent carry on. Tightening one refuses the agent's requests already in progress. When you approve a request, Phoenix checks the capability again, and the request fails if it's now Deny.

Related: [Inline approval wait](guide:window.keySettings#inline-wait), [What pass-through opens](guide:topic.passThrough)

## {{label:caps.tierRead}} {#reads}

### {{label:caps.cap_config_read.label}} {#config-read}

Reads Home Assistant's configuration, Energy settings, solar forecast, blueprints and MESA profiles. Pass-through allows it.

### {{label:caps.cap_camera_read.label}} {#camera-read}

Takes still images from cameras the key can reach. No persona allows it, and pass-through doesn't either.

### {{label:caps.cap_template_render.label}} {#template-render}

Renders Home Assistant templates. Pass-through allows it.

### {{label:caps.cap_log_read.label}} {#log-read}

Reads the system log, the logbook, notifications and integration log levels. Pass-through doesn't allow it.

### {{label:caps.cap_search.label}} {#search}

Searches entities and describes areas, entities, relationships and available actions, including dry runs. Every persona and pass-through allow it.

### {{label:caps.cap_registry_read.label}} {#registry-read}

Lists areas, floors, zones, devices, automations, scripts, scenes, helpers and labels. Every persona and pass-through allow it.

### {{label:caps.cap_traces.label}} {#traces}

Reads automation traces. Pass-through allows it.

### {{label:caps.cap_diagnostics.label}} {#diagnostics}

Reads system health, repairs, configuration checks, radio networks and the ESPHome overview, and validates automations and scripts. With {{label:caps.cap_log_read.label}}, it also reads the fault log, captured debug records and Phoenix's own diagnostics. Pass-through allows it.

### {{label:caps.cap_filesystem.label}} {#filesystem}

Reads files in the www, themes and custom_templates folders. Pass-through doesn't allow it.

## {{label:caps.tierEveryday}} {#everyday}

### {{label:caps.cap_broadcast.label}} {#broadcast}

Broadcasts a message through Assist satellites, starts conversations on them and plays announcements and spoken text on speakers, on top of Write on the speaker. Ordinary playback needs only Write. Pass-through allows it.

### {{label:caps.cap_service_response.label}} {#service-response}

Returns the data an action responds with. Without it, actions still run but return no data, and a Music Assistant library search is refused. Pass-through allows it.

### {{label:caps.cap_esphome_yaml.label}} {#esphome-yaml}

Reads and searches ESPHome device configuration. With the ESPHome Device Builder, it also validates, compiles and inspects devices. Pass-through doesn't allow it.

## {{label:caps.tierConfigWrite}} {#config-changes}

Pass-through never changes these; each keeps its own setting.

### {{label:caps.cap_automation_write.label}} {#automation-write}

Reads, creates, edits and deletes automations.

### {{label:caps.cap_script_write.label}} {#script-write}

Reads, creates, edits and deletes scripts.

### {{label:caps.cap_blueprint_write.label}} {#blueprint-write}

Creates, edits and deletes blueprints. Writing a blueprint also needs the matching automations or scripts capability: Deny on either refuses it, and Confirm on either holds it for approval.

### {{label:caps.cap_scene_write.label}} {#scene-write}

Reads, creates, edits and deletes scenes.

### {{label:caps.cap_helper_write.label}} {#helper-write}

Creates, edits and deletes helpers, and changes their settings.

### {{label:caps.cap_calendar_write.label}} {#calendar-write}

Creates, changes and deletes calendar events.

## {{label:caps.tierSystem}} {#system}

### {{label:caps.cap_physical_control.label}} {#physical-control}

Controls locks, alarm panels, covers and valves, through their own actions or any other action that reaches them. Raw device commands such as `zwave_js.set_value` always need it, and so do ESPHome actions on a device with one of these. The agent also needs W on them in the {{label:accessKeys.permissionsTree}}. Radio tools that change Zigbee devices need it too.

Creating or editing a scene with one of these in it needs this capability as well.

### {{label:caps.cap_notify.label}} {#notify}

Sends notifications, through its own tool, a notify action or any other action that sends through a notify entity.

### {{label:caps.cap_restart.label}} {#restart}

Restarts or stops Home Assistant.

### {{label:caps.cap_log_control.label}} {#log-control}

Changes an integration's log level. It also needs {{label:caps.cap_log_read.label}}. No persona allows it.

### {{label:caps.cap_integration_write.label}} {#integration-write}

Adds, changes, enables, disables, reloads and removes integrations, and removes devices.

### {{label:caps.cap_integration_reconfigure.label}} {#integration-reconfigure}

Reconfigures an integration.

### {{label:caps.cap_lovelace_write.label}} {#lovelace-write}

Reads and changes dashboards. Without it, the agent can't read dashboards either.

### {{label:caps.cap_registry_write.label}} {#registry-write}

Renames entities, changes their area, device, labels and categories, and deletes them. An entity's ID can change within its domain. Purging an entity's recorded history with `recorder.purge_entities` needs this capability too.

### {{label:caps.cap_radio_write.label}} {#radio-write}

Manages Zigbee networks. Scanning the network also needs {{label:caps.cap_diagnostics.label}}; changing devices, bindings and groups also needs {{label:caps.cap_physical_control.label}}.

### {{label:caps.cap_energy_write.label}} {#energy-write}

Changes the Energy dashboard's settings.

### {{label:caps.cap_esphome_flash.label}} {#esphome-flash}

Installs firmware on ESPHome devices, through the ESPHome Device Builder. It also needs {{label:caps.cap_esphome_yaml.label}}. Only the ESPHome Devices persona allows it, with your approval. `update.install` on an ESPHome device needs this capability as well.

### {{label:caps.cap_yaml_edit.label}} {#yaml-edit}

Reads Home Assistant's YAML configuration files and runs its reload actions. It also adds file and line numbers to configuration checks.

## {{label:caps.tierIrreversible}} {#high-risk}

### {{label:caps.cap_backup.label}} {#backup}

Lists and creates backups.

### {{label:caps.cap_dashboard_resources.label}} {#dashboard-resources}

Adds and changes dashboard resources, the JavaScript files that custom cards load. Every change needs your approval, so Allow isn't available. Files must come from `/local/` or `/hacsfiles/`, and Phoenix MCP changes only the resources it added itself.
