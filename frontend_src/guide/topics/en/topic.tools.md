---
{ "id": "topic.tools", "title": "What Agents Can Do", "parent": "topic.about", "order": 30, "keywords": ["tools", "tool list", "MCP tools", "what can an agent do", "tools/list"], "sectionKeywords": { "rules": ["deny", "hidden tool", "announce all tools", "result size"], "reading": ["get_state", "get_history", "get_statistics", "get_camera_image", "get_calendar_events", "get_todo_items", "GetLiveContext", "GetDateTime", "history", "statistics", "camera"], "controlling": ["HassTurnOn", "HassTurnOff", "HassLightSet", "HassFanSetSpeed", "HassClimateSetTemperature", "HassSetPosition", "HassStopMoving", "HassMediaPause", "HassMediaSearchAndPlay", "HassSetVolume", "HassVacuumStart", "HassCancelAllTimers", "HassBroadcast", "native tools", "lock", "cover", "HassMediaNext", "HassMediaPrevious", "HassMediaUnpause", "HassMediaPlayerMute", "HassMediaPlayerUnmute", "HassSetVolumeRelative", "HassVacuumCleanArea", "HassVacuumReturnToBase"], "actions": ["call_service", "dry_run_service", "service", "action", "reload", "restart", "response data"], "lists": ["set_todo_item", "create_calendar_event", "update_calendar_event", "delete_calendar_event", "get_calendar_write", "to-do", "calendar"], "waiting": ["watch_entity", "wait for state"], "home-companion": ["home_request", "household request"], "automations": ["list_automations", "get_automation", "create_automation", "edit_automation", "delete_automation", "list_scripts", "get_script", "create_script", "edit_script", "delete_script", "create_blueprint", "edit_blueprint", "delete_blueprint", "blueprint", "indirect control"], "scenes": ["list_scenes", "get_scene", "create_scene", "edit_scene", "delete_scene"], "helpers": ["list_helpers", "create_helper", "edit_helper", "delete_helper", "get_helper_settings", "set_helper_settings", "input_boolean", "counter", "timer", "zone", "Template helper"], "dashboards": ["list_dashboards", "create_dashboard", "edit_dashboard", "delete_dashboard", "get_dashboard_config", "set_dashboard_config", "add_dashboard_card", "edit_dashboard_card", "delete_dashboard_card", "patch_dashboard", "list_dashboard_cards", "list_dashboard_resources", "set_dashboard_resource", "Lovelace", "custom cards"], "restore": ["restore_configuration", "undo", "roll back"], "mesa": ["mesa_query_profiles", "mesa_get_profile", "mesa_explain_profile", "mesa_get_caller_context", "mesa_request_lease", "mesa_release_lease", "lease"], "configuration": ["get_config", "get_energy_config", "get_solar_forecast", "edit_energy_config", "list_blueprints", "get_blueprint", "render_template", "get_logs", "get_log_detail", "get_logbook", "get_notifications", "list_integration_log_levels", "set_integration_log_level", "template", "logbook", "debug logging"], "diagnostics": ["search_entities", "get_overview", "describe_area", "describe_entity", "find_available_actions", "get_relationships", "whatif", "compare_states", "compare_entities", "recent_activity", "recognize_intent", "list_areas", "list_floors", "list_zones", "list_devices", "get_device", "list_labels", "get_automation_traces", "check_ha_config", "validate_automation_or_script", "get_system_health", "get_repairs", "get_radio_network", "get_esphome_overview", "get_phoenix_diagnostics", "traces"], "files": ["list_files", "read_file", "get_yaml_config", "www", "themes", "custom_templates", "secrets.yaml"], "esphome": ["get_esphome_yaml", "search_esphome_yaml", "validate_esphome_yaml", "compile_esphome_firmware", "install_esphome_firmware", "get_esphome_device_logs", "analyze_esphome_memory", "Device Builder", "firmware", "list_esphome_devices", "get_esphome_board", "get_esphome_component", "get_esphome_automations", "get_esphome_history", "check_esphome_connectivity", "clean_esphome_build", "decode_esphome_backtrace", "list_esphome_jobs", "get_esphome_job", "wait_for_esphome_job", "cancel_esphome_job"], "system": ["list_integrations", "set_integration", "set_integration_enabled", "reload_integration", "remove_integration", "reconfigure_integration", "remove_device", "set_entity", "delete_entity", "set_device", "set_area_or_floor", "set_label_or_category", "list_backups", "create_backup", "restart_ha", "send_notification", "Zigbee", "ZHA", "backup", "rename", "get_radio_device", "scan_zigbee_topology", "permit_zigbee_join", "reconfigure_zigbee_device", "remove_zigbee_device", "configure_zigbee_reporting", "set_zigbee_device_options", "set_zigbee_device_property", "set_zigbee_binding", "get_zigbee_groups", "create_zigbee_group", "set_zigbee_group_members", "remove_zigbee_group"], "introspection": ["get_approval_status", "wait_for_approval", "get_capability_summary", "get_audit_summary"], "household": ["family member", "non-admin", "voice", "Assist", "AI Task", "household"] }, "covers": ["custom_components/phoenix_mcp/tool_defs.py", "custom_components/phoenix_mcp/tool_catalog.py", "custom_components/phoenix_mcp/tool_dispatch.py", "custom_components/phoenix_mcp/tool_registration.py", "custom_components/phoenix_mcp/const.py", "custom_components/phoenix_mcp/log_read.py", "custom_components/phoenix_mcp/template_sandbox.py", "custom_components/phoenix_mcp/home_companion.py", "custom_components/phoenix_mcp/home_companion_turn.py", "custom_components/phoenix_mcp/household_authority.py", "custom_components/phoenix_mcp/tools/", "custom_components/phoenix_mcp/mesa_tools.py", "custom_components/phoenix_mcp/authoring_validation.py", "custom_components/phoenix_mcp/card_catalog.py", "custom_components/phoenix_mcp/esphome_builder.py", "custom_components/phoenix_mcp/esphome_yaml.py", "custom_components/phoenix_mcp/history_summary.py", "custom_components/phoenix_mcp/proxy_diagnostics.py", "custom_components/phoenix_mcp/radio.py", "custom_components/phoenix_mcp/recorder_queries.py", "custom_components/phoenix_mcp/service_targets.py", "custom_components/phoenix_mcp/system_log_payload.py", "custom_components/phoenix_mcp/template_diagnostics.py", "custom_components/phoenix_mcp/template_render.py", "custom_components/phoenix_mcp/template_scope.py", "custom_components/phoenix_mcp/template_worker.py", "custom_components/phoenix_mcp/tool_contracts.py", "custom_components/phoenix_mcp/routing/tool_routing.py", "custom_components/phoenix_mcp/yaml_includes.py", "custom_components/phoenix_mcp/logger_control.py", "custom_components/phoenix_mcp/home_companion_response.py", "custom_components/phoenix_mcp/debug_capture.py", "custom_components/phoenix_mcp/fault_log.py", "frontend_src/utils/card_harvest.ts", "custom_components/phoenix_mcp/esphome_constants.py", "custom_components/phoenix_mcp/template_limits.py"] }
---
An agent works through tools. This page explains them by group: what they let an agent do, which capabilities allow them, and the limits worth knowing. Clients read each tool's details from the tool itself.

Two settings decide every action. [{{label:accessKeys.cardCapabilities}}](guide:window.capabilities) decide what kind of thing an agent may do. The [{{label:accessKeys.permissionsTree}}](guide:window.permissions) decides which entities it may do it to.

## How capabilities apply {#rules}

- A tool whose capability is Deny isn't offered to the agent, and a call to it is refused before it runs.
- **{{label:accessKeys.announceAllLabel}}** lists every tool anyway, for troubleshooting. The agent still can't use the hidden ones.
- Confirm holds a change for your approval. Reads that use the same capability run straight away.
- A result that lists many entities stops at about 64 KB.

Pass-through allows most read capabilities without setting them. Every capability that changes something keeps its own setting.

Related: [Deny, Allow and Confirm](guide:window.capabilities#modes), [What pass-through opens](guide:topic.passThrough)

## Reading the home {#reading}

Agents read current states, history, statistics, calendars, to-do lists and a summary of the whole home. They see only the entities the tree lets them read. Sensitive attributes are always removed.

Most reads need no capability; the tree alone decides. Camera stills need one, and so does picking history or statistics entities with a search filter.

- History and statistics come in pages of up to 1000 rows.
- A calendar read covers up to 31 days and 500 events.
- Camera images are at most 2048 pixels on a side.
- A summary of an attribute's values covers at most 7 days.

Allowed by: [{{label:caps.cap_camera_read.label}}](guide:window.capabilities#camera-read), [{{label:caps.cap_search.label}}](guide:window.capabilities#search)

## Controlling devices {#controlling}

Agents turn things on and off, set lights, fans, climate, covers and volume, control media and vacuums, and cancel timers. These are the same commands Home Assistant's own assistant uses. Every target needs Write in the tree.

Locks, alarms, covers and valves also need Physical control. On Confirm, a command that includes one waits for your approval. On Deny, the agent can't control them.

Announcing a message through Assist satellites, or starting a conversation on one, needs Broadcast. MESA checks every command last, after the capability and the tree.

Allowed by: [{{label:caps.cap_physical_control.label}}](guide:window.capabilities#physical-control), [{{label:caps.cap_broadcast.label}}](guide:window.capabilities#broadcast)

Related: [How MESA decides](guide:topic.mesaRules)

## Running any action {#actions}

Agents can also run Home Assistant actions on targets they can write, and preview the result first. The target must be one the action works on, so `light.turn_on` on a switch is refused. `homeassistant.turn_on`, `turn_off`, `toggle` and `update_entity` work on anything. A few actions need more:

- Restarting or stopping Home Assistant needs Restart.
- Reloading automations, scripts, scenes, helpers or the core configuration needs YAML configuration.
- An action's returned data needs Service response data. Without it, the action still runs.
- Notify actions, which send messages, need Notifications.
- Announcements need Broadcast: `assist_satellite.announce`, `start_conversation` and `ask_question`, `music_assistant.play_announcement`, and `media_player.play_media` with `announce`. Ordinary playback needs only Write.
- ESPHome device actions need only Write on the device. MESA doesn't check them, and doesn't check reloads or restarts either.
- Searching or listing the Music Assistant library needs Service response data and Read on a Music Assistant entity, such as a speaker. Nothing plays, so MESA doesn't check these either.

An action that doesn't declare a target, such as `shell_command`, `rest_command` or `python_script`, is refused for every key, pass-through included. That holds whatever target the agent names, even an entity of the action's own domain. Other actions without a target are refused too.

A custom action declares a target when Home Assistant runs it on its entities, or when its registered schema names `entity_id`.

Calendar changes and `homeassistant.reload_config_entry` are refused as well, because they have their own tools. An action still running after 30 seconds is reported as possibly incomplete.

Allowed by: [{{label:caps.cap_restart.label}}](guide:window.capabilities#restart), [{{label:caps.cap_yaml_edit.label}}](guide:window.capabilities#yaml-edit), [{{label:caps.cap_service_response.label}}](guide:window.capabilities#service-response), [{{label:caps.cap_notify.label}}](guide:window.capabilities#notify), [{{label:caps.cap_search.label}}](guide:window.capabilities#search) for previews

## Lists and calendars {#lists}

Agents add, change and remove to-do items, which needs only Write on the list. They create, change and delete calendar events, which also needs Calendar write. MESA checks both. Each calendar change leaves a receipt the agent can check later.

Allowed by: [{{label:caps.cap_calendar_write.label}}](guide:window.capabilities#calendar-write)

## Waiting for a change {#waiting}

An agent can wait up to 30 seconds for an entity to change or reach a state, to confirm that an action worked. The tree and authority are checked again when the wait ends.

Allowed by: [{{label:caps.cap_config_read.label}}](guide:window.capabilities#config-read)

## Home Companion {#home-companion}

A [{{label:personas.home_companion.label}}](guide:window.persona#home-companion) key offers a single tool. The app sends one plain request, such as "turn off the downstairs lights". Phoenix carries it out with its own model, checks the result and reports back.

- It controls lights, fans, switches, climate, covers, valves, media players, vacuums, humidifiers and timers.
- It reads sensors, weather, calendars and the sun, but can't change them. Sensors are limited to temperature, humidity, light, power, energy and battery.
- Locks, alarms and cameras are out of its reach.
- Each key handles one request at a time, with up to 8 actions and 2 minutes per request.
- Pass-through and **{{label:accessKeys.announceAllLabel}}** are always off.

In Agent Chat it uses the chat's model. Everywhere else it uses the Voice Agent's provider and model.

Related: [Voice Agent](guide:topic.homeAssistantFeatures#voice-agent)

## Automations and scripts {#automations}

Agents read, create, edit and delete the automations and scripts that Home Assistant's own editor manages. On Confirm, the approval shows the new configuration, or the before and after of an edit. Every change is kept in [{{label:shell.tabChanges}}](guide:topic.configurationHistory).

An automation runs as Home Assistant, not as the agent. Its actions aren't limited by the key's tree or by MESA. So these capabilities let an agent control devices indirectly. Use Confirm to review each one.

- Only the editor's own files change. Automations and scripts written by hand elsewhere in YAML are refused.
- Listing them needs Registry read, and shows only those whose entity the key can read.
- Blueprints need Manage blueprints plus the capability for the blueprint's kind, automation or script.

Allowed by: [{{label:caps.cap_automation_write.label}}](guide:window.capabilities#automation-write), [{{label:caps.cap_script_write.label}}](guide:window.capabilities#script-write), [{{label:caps.cap_blueprint_write.label}}](guide:window.capabilities#blueprint-write)

## Scenes {#scenes}

Agents read, create, edit and delete scenes in Home Assistant's scene editor. Every entity in a scene needs Write in the tree, even just to read the scene. MESA doesn't check scene changes.

Allowed by: [{{label:caps.cap_scene_write.label}}](guide:window.capabilities#scene-write)

## Helpers {#helpers}

Agents create, edit and delete helpers: toggles, numbers, text, lists, dates, buttons, counters, timers, schedules, zones, tags and people. They also manage helpers set up through an integration, such as Template, and change their settings.

- MESA checks helper changes. A MESA Confirm asks for its own approval, even when Manage helpers is Allow.
- Only zones, tags and people follow the tree. Other helpers are limited by the capability and MESA alone.
- Deleting a Template helper also needs Integration management.
- Helpers defined in YAML can't be changed.

Allowed by: [{{label:caps.cap_helper_write.label}}](guide:window.capabilities#helper-write), [{{label:caps.cap_integration_write.label}}](guide:window.capabilities#integration-write)

## Dashboards {#dashboards}

Agents list, create, edit and delete dashboards, read and search their layouts, change cards and list the installed custom cards. All of it needs Manage dashboards, reads included.

- The tree and MESA don't limit dashboard changes. Entities the key can't read show as `<redacted>`, and a change that still contains one is refused.
- A layout that wouldn't display is refused, and so is a change to a dashboard in YAML mode.
- Deleting a dashboard saves its layout first, so it can be restored.
- The custom card list comes from a browser that opened the panel, refreshed at most every 10 minutes.

Resources, the JavaScript and CSS that every dashboard loads, need Dashboard resources.

Allowed by: [{{label:caps.cap_lovelace_write.label}}](guide:window.capabilities#lovelace-write), [{{label:caps.cap_dashboard_resources.label}}](guide:window.capabilities#dashboard-resources)

## Restoring a change {#restore}

An agent can put back one of its own earlier changes to a Template helper, blueprint, Energy setup, dashboard or dashboard resource. It asks for a receipt first, then sends the restore, which goes through the usual approval for that capability. An agent sees only its own records.

Allowed by: any of [{{label:caps.cap_helper_write.label}}](guide:window.capabilities#helper-write), [{{label:caps.cap_blueprint_write.label}}](guide:window.capabilities#blueprint-write), [{{label:caps.cap_energy_write.label}}](guide:window.capabilities#energy-write), [{{label:caps.cap_lovelace_write.label}}](guide:window.capabilities#lovelace-write), [{{label:caps.cap_dashboard_resources.label}}](guide:window.capabilities#dashboard-resources)

Related: [What a restore can put back](guide:topic.configurationHistory#restore)

## MESA profiles {#mesa}

Agents read the MESA profiles of entities they can read, and see why a profile resolved the way it did. They can also take a short lease, up to 30 seconds, on entities they can write. A lease tells others the agent is working there; it grants nothing.

Allowed by: [{{label:caps.cap_config_read.label}}](guide:window.capabilities#config-read)

Related: [How MESA decides](guide:topic.mesaRules)

## Configuration, templates and logs {#configuration}

- Configuration read covers Home Assistant's basic setup, the Energy setup, solar forecasts and blueprints.
- Manage Energy dashboard changes one part of the Energy setup. It can't add a second source of a type, and it refuses to change a type that already has several.
- Template render renders templates in a separate, limited process. It works on a copy of the states the key can read, for up to 5 seconds.
- System log read covers warnings and errors, the logbook and notifications. The fault log and captured debug records also need Diagnostics.
- Integration log levels changes how much an integration logs, such as turning on debug logging. A change can end after 5 to 120 minutes, last until the next restart, or stay until cleared.

Template functions that need Home Assistant itself, such as area or device lookups, return nothing.

Allowed by: [{{label:caps.cap_config_read.label}}](guide:window.capabilities#config-read), [{{label:caps.cap_energy_write.label}}](guide:window.capabilities#energy-write), [{{label:caps.cap_template_render.label}}](guide:window.capabilities#template-render), [{{label:caps.cap_log_read.label}}](guide:window.capabilities#log-read), [{{label:caps.cap_log_control.label}}](guide:window.capabilities#log-control)

## Discovery and diagnostics {#diagnostics}

These tools only read. They help an agent find its way before it acts.

- Search & discovery searches and describes entities, areas and how things relate, compares them, and finds what actions are available.
- Registry read lists areas, floors, zones, devices, labels, automations, scripts, scenes and helpers.
- Automation traces reads how automations ran.
- Diagnostics checks the configuration and reads system health, repairs, radio networks and the ESPHome overview.

A configuration check names the file and line only when YAML configuration isn't Deny.

Allowed by: [{{label:caps.cap_search.label}}](guide:window.capabilities#search), [{{label:caps.cap_registry_read.label}}](guide:window.capabilities#registry-read), [{{label:caps.cap_traces.label}}](guide:window.capabilities#traces), [{{label:caps.cap_diagnostics.label}}](guide:window.capabilities#diagnostics)

## Files and YAML {#files}

Read files lets an agent read text files in the www, themes and custom_templates folders, up to 1 MiB each. Folder lists come in pages, 500 entries by default and up to 1,000. It can't write these files.

YAML configuration lets an agent read your YAML files, apart from any file with secret in its name, the esphome folder and hidden files. Values under names that suggest a secret show as `<redacted>`, but a password under an ordinary name is visible. No tool writes YAML configuration.

Allowed by: [{{label:caps.cap_filesystem.label}}](guide:window.capabilities#filesystem), [{{label:caps.cap_yaml_edit.label}}](guide:window.capabilities#yaml-edit)

## ESPHome {#esphome}

Agents read and search ESPHome device configuration, with passwords, keys and every request header value masked. With the ESPHome Device Builder add-on, they can also validate, compile, read device logs, analyse memory and check connections. Without the add-on, only reading and searching remain.

Compiling, cleaning and memory analysis need Write on the device. Installing firmware needs ESPHome firmware flashing, ESPHome device YAML and Write on the device, and MESA checks it.

Allowed by: [{{label:caps.cap_esphome_yaml.label}}](guide:window.capabilities#esphome-yaml), [{{label:caps.cap_esphome_flash.label}}](guide:window.capabilities#esphome-flash)

## Integrations, devices and the system {#system}

- Integration management adds, enables, disables, reloads and removes integrations, and removes devices.
- Integration reconfiguration changes an integration's settings.
- Manage entity registry renames entities and devices and changes entity IDs, areas, floors, labels and categories. `recorder.purge_entities` needs it too, because it deletes history.
- Radio management changes Zigbee networks. Groups, bindings and device properties also need Physical control. MESA checks changes to a Zigbee device.
- A Zigbee device with no entities gives MESA nothing to check, so Advisory warns and goes ahead. MESA refuses when it's Enforced, when it failed to start, or when you stored a profile for that device.
- Backup creates and lists backups, but can't restore one.
- Restart or stop Home Assistant restarts it. The agent is told not to retry by itself.
- Notifications sends a notification, whether through its own tool or by running a notify action.

A backup waits up to 45 seconds and never goes to Home Assistant Cloud. Without a backup encryption key it isn't encrypted, and it includes secrets.yaml.

Allowed by: [{{label:caps.cap_integration_write.label}}](guide:window.capabilities#integration-write), [{{label:caps.cap_integration_reconfigure.label}}](guide:window.capabilities#integration-reconfigure), [{{label:caps.cap_registry_write.label}}](guide:window.capabilities#registry-write), [{{label:caps.cap_radio_write.label}}](guide:window.capabilities#radio-write), [{{label:caps.cap_backup.label}}](guide:window.capabilities#backup), [{{label:caps.cap_restart.label}}](guide:window.capabilities#restart), [{{label:caps.cap_notify.label}}](guide:window.capabilities#notify)

## The key's own state {#introspection}

Every key can check its own approval requests, wait up to 30 seconds for up to 50 of them, and read its capability summary and audit entries. These need no capability. Requests from another key look like they don't exist.

## On Voice, Assist and AI Task {#household}

Anyone in the household can speak to Assist or the Voice Agent. For someone who isn't a Home Assistant administrator, the key keeps five capabilities at most. They are Search & discovery, Registry read, Physical control, Broadcast and Service response data. The rest count as Deny.

The approval and audit tools are hidden too, and Home Assistant's admin-only actions are refused. A speaker Home Assistant can't identify is treated the same way. AI Task always runs like this.

Related: [Voice Agent, AI Task or Assist Tool Provider](guide:topic.homeAssistantFeatures), [Assist and the Voice Agent](guide:topic.permissionResolution#household)
