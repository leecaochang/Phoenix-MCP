"""What the Guide's tool reference says about each tool beyond its definition.

Data only, never read at runtime: tests/tool_reference_build.py copies it beside each
tool's schema into the reference the Guide builds. Two things live here:

- TOOL_EXAMPLES, one example call per published tool. A test checks every example
  against its tool's own inputSchema, so an example cannot drift from the schema it
  illustrates. Names, IDs and values are made up for illustration.
- TOOL_CALL_CAPS, the capabilities a tool checks only for some calls, by what a call
  targets or asks for, beyond those its definition declares. Only checks that refuse
  the call belong here, not ones that merely add detail to a result. A test finds each
  capability named in the code of the tool's handler or a module it imports.
"""

from __future__ import annotations

# Tool name as announced: capabilities some of its calls need, in the order the reference lists them.
TOOL_CALL_CAPS: dict[str, tuple[str, ...]] = {
    # An action's name, data and targets decide (policy_engine.required_service_cap,
    # capability.service_capabilities, tools.camera.media_read_refusal).
    "call_service": (
        "cap_physical_control", "cap_notify", "cap_broadcast", "cap_service_response", "cap_camera_read",
        "cap_restart", "cap_yaml_edit", "cap_scene_write", "cap_registry_write", "cap_esphome_flash",
    ),
    # A lock among the targets.
    "intent__HassTurnOn": ("cap_physical_control",),
    "intent__HassTurnOff": ("cap_physical_control",),
    # Picking entities with a search filter (select).
    "get_history": ("cap_search",),
    "get_statistics": ("cap_search",),
    # Searching every configuration by trigger or text.
    "list_automations": ("cap_automation_write",),
    # A trigger with templates.
    "test_trigger": ("cap_automation_write",),
    # A detail_ref from Phoenix's own diagnostics.
    "get_log_detail": ("cap_diagnostics",),
    # The blueprint's domain.
    "create_blueprint": ("cap_automation_write", "cap_script_write"),
    "edit_blueprint": ("cap_automation_write", "cap_script_write"),
    "delete_blueprint": ("cap_automation_write", "cap_script_write"),
    # A lock, cover, valve or alarm panel among the scene's entities.
    "create_scene": ("cap_physical_control",),
    "edit_scene": ("cap_physical_control",),
    # A Template helper, removed through its config entry.
    "delete_helper": ("cap_integration_write",),
}

# Tool name as announced: the arguments of one typical call.
TOOL_EXAMPLES: dict[str, dict] = {
    # Reading the home
    "get_state": {"entity_id": ["light.living_room", "sensor.outdoor_temperature"]},
    "get_camera_image": {"entity_id": "camera.front_door", "width": 640, "height": 360},
    "get_history": {"entity_id": "binary_sensor.front_door", "start_time": "7d", "mode": "summary"},
    "get_statistics": {"entity_id": "sensor.outdoor_temperature", "start_time": "7d", "period": "day"},
    "get_todo_items": {"entity_id": "todo.shopping_list", "status": "needs_action"},
    "get_calendar_events": {"entity_id": ["calendar.family", "calendar.work"], "end_time": "+14d"},
    "homeassistant__GetLiveContext": {},
    "llm__GetDateTime": {},
    # Controlling devices
    "assist_satellite__HassBroadcast": {"message": "Dinner is ready."},
    "intent__HassTurnOn": {"name": "Kitchen Light"},
    "intent__HassTurnOff": {"area": "Living Room", "domain": ["light"]},
    "light__HassLightSet": {"name": "Desk Lamp", "brightness": 40},
    "fan__HassFanSetSpeed": {"name": "Bedroom Fan", "percentage": 50},
    "climate__HassClimateSetTemperature": {"area": "Living Room", "temperature": 21},
    "intent__HassSetPosition": {"name": "Office Blinds", "position": 30},
    "media_player__HassSetVolume": {"name": "Kitchen Speaker", "volume_level": 25},
    "media_player__HassSetVolumeRelative": {"name": "Kitchen Speaker", "volume_step": "up"},
    "media_player__HassMediaPause": {"name": "Living Room TV"},
    "media_player__HassMediaUnpause": {"name": "Living Room TV"},
    "media_player__HassMediaNext": {"name": "Kitchen Speaker"},
    "media_player__HassMediaPrevious": {"name": "Kitchen Speaker"},
    "media_player__HassMediaSearchAndPlay": {"name": "Kitchen Speaker", "search_query": "Miles Davis - So What", "media_class": "track"},
    "media_player__HassMediaPlayerMute": {"name": "Living Room TV"},
    "media_player__HassMediaPlayerUnmute": {"name": "Living Room TV"},
    "intent__HassCancelAllTimers": {"area": "Kitchen"},
    "vacuum__HassVacuumStart": {"name": "Robot Vacuum"},
    "vacuum__HassVacuumReturnToBase": {"name": "Robot Vacuum"},
    "vacuum__HassVacuumCleanArea": {"area": "Kitchen"},
    "intent__HassStopMoving": {"name": "Garage Door"},
    # Running any action
    "call_service": {
        "service": {"domain": "light", "name": "turn_on", "data": {"brightness_pct": 60}},
        "targets": [{"kind": "area", "ids": ["living_room"]}],
    },
    "dry_run_service": {
        "service": {"domain": "lock", "name": "unlock"},
        "targets": [{"kind": "entity", "ids": ["lock.front_door"]}],
    },
    # Lists and calendars
    "create_calendar_event": {
        "entity_id": "calendar.family",
        "event": {"summary": "Bin day", "start": "2026-11-23", "end": "2026-11-24", "rrule": "FREQ=WEEKLY;BYDAY=MO"},
    },
    "update_calendar_event": {
        "entity_id": "calendar.family", "uid": "4f1c2a90-6d1e-4b7a-9a52-0c3d7e1b2f44",
        "start_time": "2026-11-23", "end_time": "2026-11-24",
        "expected_hash": "9b2e5c1a7d3f4e60", "mode": "patch", "event": {"location": "Kerb outside"},
    },
    "delete_calendar_event": {
        "entity_id": "calendar.family", "uid": "4f1c2a90-6d1e-4b7a-9a52-0c3d7e1b2f44",
        "start_time": "2026-11-23", "end_time": "2026-11-24", "expected_hash": "9b2e5c1a7d3f4e60",
    },
    "get_calendar_write": {"entity_id": "calendar.family"},
    "set_todo_item": {"entity_id": "todo.shopping_list", "action": "add", "summary": "Milk"},
    # Waiting for a change
    "watch_entity": {"entity_id": "cover.garage_door", "timeout": 20, "until_state": "closed"},
    # Testing triggers and conditions
    "test_trigger": {"trigger": {"trigger": "state", "entity_id": "binary_sensor.hallway_motion", "to": "on"}, "timeout": 30},
    "test_condition": {"condition": {"condition": "numeric_state", "entity_id": "sensor.outdoor_temperature", "below": 5}},
    # Home Companion
    "home_request": {"request": "Turn off every light downstairs except the hallway.", "language": "en"},
    # Automations and scripts
    "list_automations": {"trigger": "sun"},
    "get_automation": {"automation_id": "automation.porch_light_at_sunset"},
    "create_automation": {
        "config": {
            "alias": "Porch light at sunset",
            "triggers": [{"trigger": "sun", "event": "sunset"}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
            "mode": "single",
        },
    },
    "edit_automation": {
        "automation_id": "phoenix_mcp_1732000000000",
        "config": {
            "alias": "Porch light at sunset",
            "triggers": [{"trigger": "sun", "event": "sunset", "offset": "-00:15:00"}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
            "mode": "single",
        },
        "expected_hash": "c41d9e07a2b85f13",
    },
    "delete_automation": {"automation_id": "automation.porch_light_at_sunset"},
    "list_scripts": {},
    "get_script": {"script_id": "morning_routine"},
    "create_script": {
        "script_id": "movie_time",
        "config": {
            "alias": "Movie time",
            "sequence": [{"action": "light.turn_off", "target": {"area_id": "living_room"}}],
            "mode": "single",
        },
    },
    "edit_script": {
        "script_id": "movie_time",
        "config": {
            "alias": "Movie time",
            "sequence": [{"action": "light.turn_on", "target": {"area_id": "living_room"}, "data": {"brightness_pct": 10}}],
            "mode": "single",
        },
        "expected_hash": "1f0e6b2d9c8a7354",
    },
    "delete_script": {"script_id": "movie_time"},
    "create_blueprint": {
        "domain": "automation",
        "path": "my_author/door_left_open.yaml",
        "content": (
            "blueprint:\n  name: Door left open\n  domain: automation\n  input:\n    door:\n      selector:\n"
            "        entity:\n          domain: binary_sensor\n"
            "triggers:\n  - trigger: state\n    entity_id: !input door\n    to: \"on\"\n    for: \"00:10:00\"\n"
            "actions:\n  - action: persistent_notification.create\n    data:\n      message: The door is still open.\n"
        ),
    },
    "edit_blueprint": {
        "domain": "automation",
        "path": "my_author/door_left_open.yaml",
        "content": (
            "blueprint:\n  name: Door left open\n  domain: automation\n  input:\n    door:\n      selector:\n"
            "        entity:\n          domain: binary_sensor\n"
            "triggers:\n  - trigger: state\n    entity_id: !input door\n    to: \"on\"\n    for: \"00:05:00\"\n"
            "actions:\n  - action: persistent_notification.create\n    data:\n      message: The door is still open.\n"
        ),
    },
    "delete_blueprint": {"domain": "automation", "path": "my_author/door_left_open.yaml"},
    # Scenes
    "list_scenes": {"entity_id": "light.living_room"},
    "get_scene": {"scene_id": "1731998765432"},
    "create_scene": {
        "config": {"name": "Reading", "entities": {"light.desk_lamp": {"state": "on", "brightness": 200}, "light.ceiling": "off"}},
    },
    "edit_scene": {
        "scene_id": "1731998765432",
        "config": {"name": "Reading", "entities": {"light.desk_lamp": {"state": "on", "brightness": 230}, "light.ceiling": "off"}},
        "expected_hash": "7a6b5c4d3e2f1a09",
    },
    "delete_scene": {"scene_id": "1731998765432"},
    # Helpers
    "list_helpers": {"helper_type": "input_boolean"},
    "create_helper": {"helper_type": "input_boolean", "config": {"name": "Guest mode", "icon": "mdi:account-multiple"}},
    "edit_helper": {"helper_type": "input_boolean", "helper_id": "guest_mode", "config": {"icon": "mdi:sofa"}},
    "delete_helper": {"helper_type": "input_boolean", "helper_id": "guest_mode"},
    "get_helper_settings": {"entry_id": "01JD4K7Z8Q2W3E4R5T6Y7U8I9O"},
    "set_helper_settings": {
        "entry_id": "01JD4K7Z8Q2W3E4R5T6Y7U8I9O",
        "settings": {"entity_id": "sensor.new_power_meter"},
        "expected_hash": "5e4d3c2b1a098f7e",
    },
    # Restoring a change
    "restore_configuration": {"recovery_id": "rcv_01JD4M2N3B4V5C6X7Z8A9S0D1F", "side": "before"},
    # MESA profiles
    "mesa_query_profiles": {"domains": ["lock"], "limit": 20},
    "mesa_get_profile": {"entity_id": "lock.front_door"},
    "mesa_explain_profile": {"entity_id": "lock.front_door"},
    "mesa_get_caller_context": {},
    "mesa_request_lease": {"entities": ["climate.living_room"], "duration_seconds": 20, "intent": "Adjust the evening setpoint"},
    "mesa_release_lease": {"lease_id": "lease_01JD4N5M6K7J8H9G0F1D2S3A4P"},
    # Dashboards
    "list_dashboards": {},
    "list_dashboard_cards": {"type": "custom:mushroom-light-card"},
    "create_dashboard": {"config": {"url_path": "dashboard-energy", "title": "Energy", "icon": "mdi:flash"}},
    "edit_dashboard": {"dashboard_id": "dashboard_energy", "config": {"title": "Energy Use"}},
    "delete_dashboard": {"dashboard_id": "dashboard_energy"},
    "list_dashboard_resources": {},
    "set_dashboard_resource": {"operation": "create", "url": "/local/my-card.js", "type": "module"},
    "get_dashboard_config": {"entity_id": "light.living_room"},
    "set_dashboard_config": {
        "url_path": "dashboard-energy",
        "config": {"views": [{"title": "Overview", "cards": [{"type": "energy-distribution"}]}]},
        "expected_hash": "0a1b2c3d4e5f6071",
    },
    "add_dashboard_card": {
        "view_index": 0, "section_index": 1,
        "card": {"type": "tile", "entity": "light.living_room"},
        "expected_hash": "0a1b2c3d4e5f6071",
    },
    "edit_dashboard_card": {
        "view_index": 0, "section_index": 1, "card_index": 2,
        "card": {"type": "tile", "entity": "light.living_room", "name": "Sofa light"},
        "expected_hash": "0a1b2c3d4e5f6071",
    },
    "delete_dashboard_card": {"view_index": 0, "section_index": 1, "card_index": 2, "expected_hash": "0a1b2c3d4e5f6071"},
    "patch_dashboard": {
        "changes": [{"path": ["views", 0, "sections", 1, "cards", 2, "entity"], "kind": "set", "value": "light.sofa"}],
        "expected_hash": "0a1b2c3d4e5f6071",
    },
    # Configuration, templates and logs
    "get_config": {},
    "get_energy_config": {"start_time": "24h"},
    "get_solar_forecast": {},
    "edit_energy_config": {
        "operation": "replace_statistic",
        "target": {"kind": "statistic", "id": "sensor.old_power_meter_energy"},
        "changes": {"new_statistic": "sensor.new_power_meter_energy"},
    },
    "render_template": {"template": "{{ states('sensor.outdoor_temperature') }} degrees"},
    "get_logs": {"level": "ERROR", "integration": "mqtt", "since": "24h"},
    "get_log_detail": {"detail_ref": "5d0c9a7e3b1f42c6a8e4d2b07f9c13a5e6b8d0f2c4a6e8b0d2f4a6c8e0b2d4f6", "detail_field": "exception"},
    "get_logbook": {"start_time": "24h", "target": {"kind": "resources", "entity_ids": ["lock.front_door"]}},
    "list_integration_log_levels": {},
    "set_integration_log_level": {"integration": "mqtt", "level": "DEBUG", "persistence": "none", "duration_minutes": 30},
    "list_blueprints": {"domain": "automation"},
    "get_blueprint": {"domain": "automation", "path": "homeassistant/motion_light.yaml"},
    "get_notifications": {},
    # Discovery and diagnostics
    "get_phoenix_diagnostics": {"level": "ERROR", "since": "24h"},
    "get_radio_network": {},
    "list_labels": {},
    "list_areas": {},
    "list_floors": {},
    "list_zones": {},
    "list_devices": {"area": "Kitchen", "integration": "zha"},
    "get_device": {"device_id": "8f3c1a2b4d5e6f708192a3b4c5d6e7f8"},
    "search_entities": {"domain": "binary_sensor", "device_class": ["door", "window"], "state": "on"},
    "recognize_intent": {"sentence": "turn on the kitchen light"},
    "get_overview": {},
    "describe_area": {"area": "Kitchen"},
    "find_available_actions": {"entity_id": "cover.garage_door"},
    "get_automation_traces": {"automation_id": "automation.porch_light_at_sunset", "summary": True},
    "get_system_health": {},
    "get_repairs": {"severity": "error"},
    "get_esphome_overview": {},
    "check_ha_config": {},
    "get_relationships": {"scope": {"kind": "entity", "id": "sensor.old_power_meter"}},
    "describe_entity": {"entity_id": "climate.living_room"},
    "whatif": {"entity_id": "binary_sensor.front_door", "hypothetical_state": "on"},
    "compare_states": {"entity_ids": ["climate.living_room", "climate.bedroom"], "t1": "24h"},
    "compare_entities": {"entity_id": "sensor.old_power_meter", "compare_to": "sensor.new_power_meter"},
    "recent_activity": {"minutes": 60},
    "validate_automation_or_script": {
        "type": "automation",
        "config": {
            "alias": "Porch light at sunset",
            "triggers": [{"trigger": "sun", "event": "sunset"}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
        },
    },
    # Files and YAML
    "list_files": {"path": "www/"},
    "read_file": {"path": "themes/dark.yaml"},
    "get_yaml_config": {"file": "configuration.yaml", "key": "homeassistant.packages"},
    # ESPHome
    "get_esphome_yaml": {"file": "living-room-sensor.yaml"},
    "search_esphome_yaml": {"query": "deep_sleep"},
    "list_esphome_devices": {"state": "offline"},
    "list_esphome_jobs": {"status": "failed"},
    "validate_esphome_yaml": {"file": "living-room-sensor.yaml"},
    "get_esphome_board": {"board_id": "esp32dev"},
    "get_esphome_component": {"component_ids": ["bme280_i2c"]},
    "get_esphome_automations": {"file": "living-room-sensor.yaml"},
    "compile_esphome_firmware": {"file": "living-room-sensor.yaml"},
    "clean_esphome_build": {"file": "living-room-sensor.yaml"},
    "install_esphome_firmware": {"file": "living-room-sensor.yaml"},
    "get_esphome_job": {"lookup": {"kind": "file", "id": "living-room-sensor.yaml"}},
    "wait_for_esphome_job": {"job_id": "job_01JD4P6Q7R8S9T0U1V2W3X4Y5Z", "timeout": 120},
    "cancel_esphome_job": {"job_id": "job_01JD4P6Q7R8S9T0U1V2W3X4Y5Z"},
    "get_esphome_device_logs": {"file": "living-room-sensor.yaml", "seconds": 20},
    "decode_esphome_backtrace": {"file": "living-room-sensor.yaml", "lines": ["Backtrace: 0x400d1234:0x3ffb1230 0x400d5678:0x3ffb1260"]},
    "get_esphome_history": {"file": "living-room-sensor.yaml"},
    "check_esphome_connectivity": {"file": "living-room-sensor.yaml"},
    "analyze_esphome_memory": {"file": "living-room-sensor.yaml"},
    # Integrations, devices and the system
    "set_entity": {"entity_id": "sensor.power_meter", "changes": {"name": "Power", "add_aliases": ["mains power"]}},
    "set_area_or_floor": {"kind": "area", "operation": "update", "location_id": "workshop", "changes": {"floor_id": "ground_floor"}},
    "set_device": {"device_id": "8f3c1a2b4d5e6f708192a3b4c5d6e7f8", "changes": {"area_id": "kitchen"}},
    "remove_device": {"device_id": "8f3c1a2b4d5e6f708192a3b4c5d6e7f8"},
    "delete_entity": {"entity_id": "sensor.old_power_meter"},
    "get_radio_device": {"device_id": "8f3c1a2b4d5e6f708192a3b4c5d6e7f8"},
    "scan_zigbee_topology": {"backend": "zha"},
    "permit_zigbee_join": {"duration": 120},
    "reconfigure_zigbee_device": {"device_id": "8f3c1a2b4d5e6f708192a3b4c5d6e7f8"},
    "remove_zigbee_device": {"device_id": "8f3c1a2b4d5e6f708192a3b4c5d6e7f8"},
    "set_zigbee_device_options": {
        "device_id": "8f3c1a2b4d5e6f708192a3b4c5d6e7f8",
        "options": {"temperature_precision": 1},
        "expected_hash": "3c2b1a0f9e8d7c6b",
    },
    "set_zigbee_device_property": {
        "device_id": "8f3c1a2b4d5e6f708192a3b4c5d6e7f8",
        "property": "led_indicator", "value": False, "expected_hash": "6b5a4f3e2d1c0b9a",
    },
    "set_zigbee_binding": {
        "operation": "bind",
        "source_device_id": "8f3c1a2b4d5e6f708192a3b4c5d6e7f8",
        "target_device_id": "1a2b3c4d5e6f708192a3b4c5d6e7f8a9b",
    },
    "configure_zigbee_reporting": {
        "device_id": "8f3c1a2b4d5e6f708192a3b4c5d6e7f8",
        "endpoint": 1, "cluster": "msTemperatureMeasurement", "attribute": "measuredValue",
        "minimum_report_interval": 30, "maximum_report_interval": 600, "reportable_change": 20,
        "expected_hash": "9a8b7c6d5e4f3a2b",
    },
    "get_zigbee_groups": {},
    "create_zigbee_group": {
        "name": "Kitchen ceiling",
        "members": [
            {"device_id": "8f3c1a2b4d5e6f708192a3b4c5d6e7f8", "endpoint": 11},
            {"device_id": "1a2b3c4d5e6f708192a3b4c5d6e7f8a9b", "endpoint": 11},
        ],
    },
    "set_zigbee_group_members": {
        "group_entity_id": "light.kitchen_ceiling", "operation": "add",
        "members": [{"device_id": "2b3c4d5e6f708192a3b4c5d6e7f8a9b0", "endpoint": 11}],
        "expected_hash": "4d3c2b1a0f9e8d7c",
    },
    "remove_zigbee_group": {"group_entity_id": "light.kitchen_ceiling", "expected_hash": "4d3c2b1a0f9e8d7c"},
    "send_notification": {"target": "persistent_notification", "title": "Laundry", "message": "The washing machine has finished."},
    "restart_ha": {},
    "set_label_or_category": {"kind": "label", "operation": "create", "changes": {"name": "Holiday", "color": "amber", "icon": "mdi:palm-tree"}},
    "list_integrations": {"domain": "zha"},
    "set_integration": {"entry_id": "01JD4Q1W2E3R4T5Y6U7I8O9P0A", "changes": {"pref_disable_new_entities": True}},
    "reconfigure_integration": {"entry_id": "01JD4Q1W2E3R4T5Y6U7I8O9P0A", "inspect": True},
    "set_integration_subentry": {"entry_id": "01JD4Q1W2E3R4T5Y6U7I8O9P0A", "subentry_type": "conversation", "inspect": True},
    "fix_repair": {"repair_ref": "rr_5c4b3a29", "inspect": True},
    "set_integration_enabled": {"entry_id": "01JD4Q1W2E3R4T5Y6U7I8O9P0A", "enabled": False},
    "reload_integration": {"entry_id": "01JD4Q1W2E3R4T5Y6U7I8O9P0A"},
    "remove_integration": {"entry_id": "01JD4Q1W2E3R4T5Y6U7I8O9P0A"},
    "list_backups": {"limit": 5},
    "create_backup": {"name": "Before the heating rework"},
    # The key's own state
    "get_approval_status": {},
    "wait_for_approval": {"approval_ids": ["apr_01JD4R7T8Y9U0I1O2P3A4S5D6F"], "timeout": 120},
    "get_capability_summary": {},
    "get_audit_summary": {"limit": 10, "outcome": "denied"},
}
