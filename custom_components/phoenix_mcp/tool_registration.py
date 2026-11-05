"""Bind every tool name to its handler and approved executor."""

from __future__ import annotations

from .const import MESA_APPROVED_EXECUTOR
from .tool_defs import MESA_TOOL_NAMES
from .home_companion_turn import run_request
from .tool_catalog import _register_companion_turn, _register_executor, _register_tool
from .tools.approvals import _tool_get_approval_status, _tool_wait_for_approval
from .tools.authoring import (
    _execute_create_automation, _execute_create_scene, _execute_create_script,
    _execute_delete_automation, _execute_delete_scene, _execute_delete_script,
    _execute_edit_automation, _execute_edit_scene, _execute_edit_script,
    _tool_create_automation, _tool_create_scene, _tool_create_script, _tool_delete_automation,
    _tool_delete_scene, _tool_delete_script, _tool_edit_automation, _tool_edit_scene,
    _tool_edit_script, _tool_get_automation, _tool_get_automation_traces, _tool_get_scene,
    _tool_get_script, _tool_list_automations, _tool_list_scenes, _tool_list_scripts,
)
from .tools.backup import _execute_create_backup, _tool_create_backup, _tool_list_backups
from .tools.blueprint import (
    _execute_create_blueprint, _execute_delete_blueprint, _execute_edit_blueprint,
    _tool_create_blueprint, _tool_delete_blueprint, _tool_edit_blueprint, _tool_get_blueprint,
    _tool_list_blueprints,
)
from .tools.calendar import (
    _execute_create_calendar_event, _execute_delete_calendar_event,
    _execute_update_calendar_event, _tool_calendar_write, _tool_get_calendar_write,
)
from .tools.calendar import _tool_get_calendar_events
from .tools.camera import _tool_get_camera_image
from .tools.config_files import _tool_get_yaml_config, _tool_list_files, _tool_read_file
from .tools.dashboard_resources import (
    _execute_set_dashboard_resource, _tool_list_dashboard_resources,
    _tool_set_dashboard_resource,
)
from .tools.discovery import (
    _tool_check_ha_config, _tool_compare_entities, _tool_compare_states, _tool_describe_area,
    _tool_describe_entity, _tool_dry_run_service, _tool_find_available_actions,
    _tool_get_audit_summary, _tool_get_device, _tool_get_overview, _tool_get_relationships,
    _tool_get_repairs, _tool_get_system_health, _tool_list_areas, _tool_list_devices,
    _tool_list_floors, _tool_list_zones, _tool_recent_activity, _tool_recognize_intent,
    _tool_search_entities, _tool_validate_automation_or_script, _tool_whatif,
)
from .tools.energy import (
    _execute_edit_energy_config, _tool_edit_energy_config, _tool_get_energy_config,
    _tool_get_solar_forecast,
)
from .tools.esphome import (
    _execute_install_esphome_firmware, _tool_analyze_esphome_memory, _tool_cancel_esphome_job,
    _tool_check_esphome_connectivity, _tool_clean_esphome_build, _tool_compile_esphome_firmware,
    _tool_decode_esphome_backtrace, _tool_get_esphome_automations, _tool_get_esphome_board,
    _tool_get_esphome_component, _tool_get_esphome_device_logs, _tool_get_esphome_history,
    _tool_get_esphome_job, _tool_get_esphome_overview, _tool_get_esphome_yaml,
    _tool_install_esphome_firmware, _tool_list_esphome_devices, _tool_list_esphome_jobs,
    _tool_search_esphome_yaml, _tool_validate_esphome_yaml, _tool_wait_for_esphome_job,
)
from .tools.helper import (
    _execute_create_helper, _execute_delete_helper, _execute_edit_helper, _execute_set_config_entry_options, _tool_create_helper, _tool_edit_helper, _tool_get_helper_settings, _tool_list_helpers, _tool_set_helper_settings,
)
from .tools.helper_delete import _tool_delete_helper
from .tools.history import _tool_get_history, _tool_get_state, _tool_get_statistics
from .tools.integrations import (
    _execute_reconfigure_integration, _execute_reload_integration, _execute_remove_integration,
    _execute_set_integration, _execute_set_integration_enabled, _tool_list_integrations,
    _tool_reconfigure_integration, _tool_reload_integration, _tool_remove_integration,
    _tool_set_integration, _tool_set_integration_enabled,
)
from .tools.labels import (
    _execute_set_label_or_category, _tool_list_labels, _tool_set_label_or_category,
)
from .tools.locations import _execute_set_area_or_floor, _tool_set_area_or_floor
from .tools.logs import (
    _execute_set_integration_log_level, _tool_get_log_detail, _tool_get_logbook, _tool_get_logs,
    _tool_list_integration_log_levels, _tool_set_integration_log_level,
)
from .tools.lovelace import (
    _execute_add_dashboard_card, _execute_create_dashboard, _execute_delete_dashboard,
    _execute_delete_dashboard_card, _execute_edit_dashboard, _execute_edit_dashboard_card,
    _execute_patch_dashboard, _execute_set_dashboard_config, _tool_create_dashboard,
    _tool_dashboard_card, _tool_delete_dashboard, _tool_edit_dashboard,
    _tool_get_dashboard_config, _tool_list_dashboard_cards, _tool_list_dashboards,
    _tool_patch_dashboard, _tool_set_dashboard_config,
)
from .tools.native import (
    _execute_hass_set_position, _execute_hass_set_volume_relative, _execute_hass_stop_moving,
    _execute_hass_turn_off, _execute_hass_turn_on, _tool_get_date_time, _tool_get_live_context,
    _tool_hass_broadcast, _tool_hass_cancel_all_timers, _tool_hass_climate_set_temperature,
    _tool_hass_fan_set_speed, _tool_hass_light_set, _tool_hass_media_next,
    _tool_hass_media_pause, _tool_hass_media_player_mute, _tool_hass_media_player_unmute,
    _tool_hass_media_previous, _tool_hass_media_search_and_play, _tool_hass_media_unpause,
    _tool_hass_set_position, _tool_hass_set_volume, _tool_hass_set_volume_relative,
    _tool_hass_stop_moving, _tool_hass_turn_off, _tool_hass_turn_on,
    _tool_hass_vacuum_clean_area, _tool_hass_vacuum_return_to_base, _tool_hass_vacuum_start,
)
from .tools.notify import (
    _execute_send_notification, _tool_get_notifications, _tool_send_notification,
)
from .tools.radio import (
    _execute_configure_zigbee_reporting, _execute_create_zigbee_group,
    _execute_permit_zigbee_join, _execute_reconfigure_zigbee_device,
    _execute_remove_zigbee_device, _execute_remove_zigbee_group, _execute_scan_zigbee_topology,
    _execute_set_zigbee_binding, _execute_set_zigbee_device_options,
    _execute_set_zigbee_device_property, _execute_set_zigbee_group_members,
    _tool_configure_zigbee_reporting, _tool_get_radio_device, _tool_get_radio_network,
    _tool_get_zigbee_groups, _tool_permit_zigbee_join, _tool_reconfigure_zigbee_device,
    _tool_remove_zigbee_device, _tool_scan_zigbee_topology, _tool_set_zigbee_binding,
    _tool_set_zigbee_device_options, _tool_set_zigbee_device_property,
    _tool_zigbee_group_change,
)
from .tools.recovery import _execute_restore_configuration, _tool_restore_configuration
from .tools.registry import (
    _execute_delete_entity, _execute_remove_device, _execute_set_device, _execute_set_entity,
    _tool_delete_entity, _tool_remove_device, _tool_set_device, _tool_set_entity,
)
from .tools.service import (
    _execute_call_service, _execute_call_service_mesa_approved, _tool_call_service,
)
from .tools.system import (
    _execute_restart_ha, _tool_get_capability_summary, _tool_get_config,
    _tool_get_phoenix_diagnostics, _tool_mesa, _tool_render_template, _tool_restart_ha,
    _tool_watch_entity,
)
from .tools.todo import _tool_get_todo_items, _tool_set_todo_item


# Register executors for tools that support the admin-approval gate.
# Each entry maps an MCP tool name to its side-effect-only _execute_X function.
# When an admin approves a pending request, async_execute_approved_tool() invokes the
# matching executor with the saved args.
_register_executor("restart_ha", _execute_restart_ha)
_register_executor("send_notification", _execute_send_notification)
_register_executor("call_service", _execute_call_service)
_register_executor("create_automation", _execute_create_automation)
_register_executor("edit_automation", _execute_edit_automation)
_register_executor("delete_automation", _execute_delete_automation)
_register_executor("create_script", _execute_create_script)
_register_executor("edit_script", _execute_edit_script)
_register_executor("delete_script", _execute_delete_script)
_register_executor("create_scene", _execute_create_scene)
_register_executor("edit_scene", _execute_edit_scene)
_register_executor("delete_scene", _execute_delete_scene)
_register_executor("create_helper", _execute_create_helper)
_register_executor("edit_helper", _execute_edit_helper)
_register_executor("delete_helper", _execute_delete_helper)
_register_executor("restore_configuration", _execute_restore_configuration)
_register_executor("create_blueprint", _execute_create_blueprint)
_register_executor("edit_blueprint", _execute_edit_blueprint)
_register_executor("delete_blueprint", _execute_delete_blueprint)
_register_executor("set_helper_settings", _execute_set_config_entry_options)
_register_executor("install_esphome_firmware", _execute_install_esphome_firmware)
_register_executor("set_integration_enabled", _execute_set_integration_enabled)
_register_executor("set_integration", _execute_set_integration)
_register_executor("reconfigure_integration", _execute_reconfigure_integration)
_register_executor("set_integration_log_level", _execute_set_integration_log_level)
_register_executor("reload_integration", _execute_reload_integration)
_register_executor("remove_integration", _execute_remove_integration)
_register_executor("create_backup", _execute_create_backup)
_register_executor("create_dashboard", _execute_create_dashboard)
_register_executor("edit_dashboard", _execute_edit_dashboard)
_register_executor("delete_dashboard", _execute_delete_dashboard)
_register_executor("set_dashboard_resource", _execute_set_dashboard_resource)
_register_executor("set_dashboard_config", _execute_set_dashboard_config)
_register_executor("add_dashboard_card", _execute_add_dashboard_card)
_register_executor("edit_dashboard_card", _execute_edit_dashboard_card)
_register_executor("delete_dashboard_card", _execute_delete_dashboard_card)
_register_executor("patch_dashboard", _execute_patch_dashboard)
_register_executor("edit_energy_config", _execute_edit_energy_config)
_register_executor("set_area_or_floor", _execute_set_area_or_floor)
_register_executor("set_label_or_category", _execute_set_label_or_category)
_register_executor("set_entity", _execute_set_entity)
_register_executor("set_device", _execute_set_device)
_register_executor("remove_device", _execute_remove_device)
_register_executor("delete_entity", _execute_delete_entity)
_register_executor("permit_zigbee_join", _execute_permit_zigbee_join)
_register_executor("scan_zigbee_topology", _execute_scan_zigbee_topology)
_register_executor("reconfigure_zigbee_device", _execute_reconfigure_zigbee_device)
_register_executor("remove_zigbee_device", _execute_remove_zigbee_device)
_register_executor("set_zigbee_device_options", _execute_set_zigbee_device_options)
_register_executor("set_zigbee_device_property", _execute_set_zigbee_device_property)
_register_executor("set_zigbee_binding", _execute_set_zigbee_binding)
_register_executor("configure_zigbee_reporting", _execute_configure_zigbee_reporting)
_register_executor("create_zigbee_group", _execute_create_zigbee_group)
_register_executor("set_zigbee_group_members", _execute_set_zigbee_group_members)
_register_executor("remove_zigbee_group", _execute_remove_zigbee_group)
_register_executor("HassSetPosition", _execute_hass_set_position)
_register_executor("HassSetVolumeRelative", _execute_hass_set_volume_relative)
_register_executor("HassStopMoving", _execute_hass_stop_moving)
_register_executor("HassTurnOn", _execute_hass_turn_on)
_register_executor("HassTurnOff", _execute_hass_turn_off)
# MESA control_mode:confirm re-execution. Registered but intentionally NOT
# dispatchable from _call_tool, so only the admin approve path can reach it.
_register_executor(MESA_APPROVED_EXECUTOR, _execute_call_service_mesa_approved)
# ---------------------------------------------------------------------------
# Tool dispatch registry.
#
# Maps each published tool name to its handler; a catalog test checks the
# registry against the published tool catalog. Handlers differ in which pieces of
# request context they need, so _register_tool reads each one's parameter names
# once at import (from the code object, not inspect.signature) and _call_tool
# passes exactly those by keyword.
# ---------------------------------------------------------------------------

_register_tool("get_state", _tool_get_state)
_register_tool("get_camera_image", _tool_get_camera_image)
_register_tool("get_history", _tool_get_history)
_register_tool("get_statistics", _tool_get_statistics)
_register_tool("get_calendar_events", _tool_get_calendar_events)
_register_tool("get_todo_items", _tool_get_todo_items)
_register_tool("set_todo_item", _tool_set_todo_item)
_register_tool("call_service", _tool_call_service)
_register_tool("get_config", _tool_get_config)
_register_tool("render_template", _tool_render_template)
_register_tool("list_automations", _tool_list_automations)
_register_tool("get_automation", _tool_get_automation)
_register_tool("list_scripts", _tool_list_scripts)
_register_tool("get_script", _tool_get_script)
_register_tool("create_automation", _tool_create_automation)
_register_tool("edit_automation", _tool_edit_automation)
_register_tool("delete_automation", _tool_delete_automation)
_register_tool("restart_ha", _tool_restart_ha)
_register_tool("send_notification", _tool_send_notification)
_register_tool("get_notifications", _tool_get_notifications)
_register_tool("get_approval_status", _tool_get_approval_status)
_register_tool("wait_for_approval", _tool_wait_for_approval)
_register_tool("get_capability_summary", _tool_get_capability_summary)
_register_tool("get_audit_summary", _tool_get_audit_summary)
_register_tool("GetLiveContext", _tool_get_live_context)
_register_tool("GetDateTime", _tool_get_date_time)
_register_tool("HassTurnOn", _tool_hass_turn_on)
_register_tool("HassTurnOff", _tool_hass_turn_off)
_register_tool("HassLightSet", _tool_hass_light_set)
_register_tool("HassFanSetSpeed", _tool_hass_fan_set_speed)
_register_tool("HassClimateSetTemperature", _tool_hass_climate_set_temperature)
_register_tool("HassSetPosition", _tool_hass_set_position)
_register_tool("HassSetVolume", _tool_hass_set_volume)
_register_tool("HassSetVolumeRelative", _tool_hass_set_volume_relative)
_register_tool("HassMediaPause", _tool_hass_media_pause)
_register_tool("HassMediaUnpause", _tool_hass_media_unpause)
_register_tool("HassMediaNext", _tool_hass_media_next)
_register_tool("HassMediaPrevious", _tool_hass_media_previous)
_register_tool("HassMediaSearchAndPlay", _tool_hass_media_search_and_play)
_register_tool("HassMediaPlayerMute", _tool_hass_media_player_mute)
_register_tool("HassMediaPlayerUnmute", _tool_hass_media_player_unmute)
_register_tool("HassCancelAllTimers", _tool_hass_cancel_all_timers)
_register_tool("HassStopMoving", _tool_hass_stop_moving)
_register_tool("HassVacuumStart", _tool_hass_vacuum_start)
_register_tool("HassVacuumReturnToBase", _tool_hass_vacuum_return_to_base)
_register_tool("HassVacuumCleanArea", _tool_hass_vacuum_clean_area)
_register_tool("HassBroadcast", _tool_hass_broadcast)
_register_tool("get_logs", _tool_get_logs)
_register_tool("get_log_detail", _tool_get_log_detail)
_register_tool("get_phoenix_diagnostics", _tool_get_phoenix_diagnostics)
_register_tool("get_logbook", _tool_get_logbook)
_register_tool("list_integration_log_levels", _tool_list_integration_log_levels)
_register_tool("set_integration_log_level", _tool_set_integration_log_level)
_register_tool("list_blueprints", _tool_list_blueprints)
_register_tool("get_blueprint", _tool_get_blueprint)
_register_tool("create_blueprint", _tool_create_blueprint)
_register_tool("edit_blueprint", _tool_edit_blueprint)
_register_tool("delete_blueprint", _tool_delete_blueprint)
_register_tool("set_area_or_floor", _tool_set_area_or_floor)
_register_tool("set_label_or_category", _tool_set_label_or_category)
_register_tool("list_labels", _tool_list_labels)
_register_tool("set_entity", _tool_set_entity)
_register_tool("set_device", _tool_set_device)
_register_tool("remove_device", _tool_remove_device)
_register_tool("delete_entity", _tool_delete_entity)
_register_tool("get_radio_network", _tool_get_radio_network)
_register_tool("get_radio_device", _tool_get_radio_device)
_register_tool("get_zigbee_groups", _tool_get_zigbee_groups)
_register_tool("scan_zigbee_topology", _tool_scan_zigbee_topology)
_register_tool("permit_zigbee_join", _tool_permit_zigbee_join)
_register_tool("reconfigure_zigbee_device", _tool_reconfigure_zigbee_device)
_register_tool("remove_zigbee_device", _tool_remove_zigbee_device)
_register_tool("set_zigbee_device_options", _tool_set_zigbee_device_options)
_register_tool("set_zigbee_device_property", _tool_set_zigbee_device_property)
_register_tool("set_zigbee_binding", _tool_set_zigbee_binding)
_register_tool("configure_zigbee_reporting", _tool_configure_zigbee_reporting)
_register_tool(
    "create_zigbee_group", _tool_zigbee_group_change,
    tool_name="create_zigbee_group",
)
_register_tool(
    "set_zigbee_group_members", _tool_zigbee_group_change,
    tool_name="set_zigbee_group_members",
)
_register_tool(
    "remove_zigbee_group", _tool_zigbee_group_change,
    tool_name="remove_zigbee_group",
)
_register_tool("create_script", _tool_create_script)
_register_tool("edit_script", _tool_edit_script)
_register_tool("delete_script", _tool_delete_script)
_register_tool("list_areas", _tool_list_areas)
_register_tool("list_floors", _tool_list_floors)
_register_tool("list_zones", _tool_list_zones)
_register_tool("list_devices", _tool_list_devices)
_register_tool("get_device", _tool_get_device)
_register_tool("search_entities", _tool_search_entities)
_register_tool("recognize_intent", _tool_recognize_intent)
_register_tool("get_overview", _tool_get_overview)
_register_tool("describe_area", _tool_describe_area)
_register_tool("find_available_actions", _tool_find_available_actions)
_register_tool("get_automation_traces", _tool_get_automation_traces)
_register_tool("get_system_health", _tool_get_system_health)
_register_tool("get_repairs", _tool_get_repairs)
# ESPHome firmware jobs: a build takes minutes and the admin Approve POST runs its
# executor inline, so compile and install ENQUEUE a job in the add-on and return
# its id, and get_esphome_job polls it. The jobs are the add-on's, durable and
# connection-independent, so Phoenix MCP keeps no job state of its own.
_register_tool("get_esphome_overview", _tool_get_esphome_overview)
_register_tool("get_esphome_yaml", _tool_get_esphome_yaml)
_register_tool("search_esphome_yaml", _tool_search_esphome_yaml)
_register_tool("list_esphome_jobs", _tool_list_esphome_jobs)
_register_tool("list_esphome_devices", _tool_list_esphome_devices)
_register_tool("validate_esphome_yaml", _tool_validate_esphome_yaml)
_register_tool("get_esphome_board", _tool_get_esphome_board)
_register_tool("get_esphome_component", _tool_get_esphome_component)
_register_tool("get_esphome_automations", _tool_get_esphome_automations)
_register_tool("clean_esphome_build", _tool_clean_esphome_build)
_register_tool("compile_esphome_firmware", _tool_compile_esphome_firmware)
_register_tool("install_esphome_firmware", _tool_install_esphome_firmware)
_register_tool("get_esphome_job", _tool_get_esphome_job)
_register_tool("wait_for_esphome_job", _tool_wait_for_esphome_job)
_register_tool("cancel_esphome_job", _tool_cancel_esphome_job)
_register_tool("get_esphome_device_logs", _tool_get_esphome_device_logs)
_register_tool("decode_esphome_backtrace", _tool_decode_esphome_backtrace)
_register_tool("get_esphome_history", _tool_get_esphome_history)
_register_tool("check_esphome_connectivity", _tool_check_esphome_connectivity)
_register_tool("analyze_esphome_memory", _tool_analyze_esphome_memory)
_register_tool("check_ha_config", _tool_check_ha_config)
_register_tool("get_relationships", _tool_get_relationships)
_register_tool("describe_entity", _tool_describe_entity)
_register_tool("whatif", _tool_whatif)
_register_tool("compare_states", _tool_compare_states)
_register_tool("compare_entities", _tool_compare_entities)
_register_tool("recent_activity", _tool_recent_activity)
_register_tool("dry_run_service", _tool_dry_run_service)
_register_tool("validate_automation_or_script", _tool_validate_automation_or_script)
_register_tool("list_scenes", _tool_list_scenes)
_register_tool("get_scene", _tool_get_scene)
_register_tool("create_scene", _tool_create_scene)
_register_tool("edit_scene", _tool_edit_scene)
_register_tool("delete_scene", _tool_delete_scene)
_register_tool("list_helpers", _tool_list_helpers)
_register_tool("create_helper", _tool_create_helper)
_register_tool("edit_helper", _tool_edit_helper)
_register_tool("delete_helper", _tool_delete_helper)
_register_tool("restore_configuration", _tool_restore_configuration)
_register_tool("watch_entity", _tool_watch_entity)
_register_tool("list_files", _tool_list_files)
_register_tool("read_file", _tool_read_file)
_register_tool("get_energy_config", _tool_get_energy_config)
_register_tool("get_solar_forecast", _tool_get_solar_forecast)
_register_tool("edit_energy_config", _tool_edit_energy_config)
_register_tool("get_yaml_config", _tool_get_yaml_config)
_register_tool("get_helper_settings", _tool_get_helper_settings)
_register_tool("set_helper_settings", _tool_set_helper_settings)
_register_tool("list_integrations", _tool_list_integrations)
_register_tool("set_integration_enabled", _tool_set_integration_enabled)
_register_tool("set_integration", _tool_set_integration)
_register_tool("reconfigure_integration", _tool_reconfigure_integration)
_register_tool("reload_integration", _tool_reload_integration)
_register_tool("remove_integration", _tool_remove_integration)
_register_tool("list_backups", _tool_list_backups)
_register_tool("create_backup", _tool_create_backup)
_register_tool("list_dashboards", _tool_list_dashboards)
_register_tool("list_dashboard_cards", _tool_list_dashboard_cards)
_register_tool("create_dashboard", _tool_create_dashboard)
_register_tool("edit_dashboard", _tool_edit_dashboard)
_register_tool("delete_dashboard", _tool_delete_dashboard)
_register_tool("list_dashboard_resources", _tool_list_dashboard_resources)
_register_tool("set_dashboard_resource", _tool_set_dashboard_resource)
_register_tool("get_dashboard_config", _tool_get_dashboard_config)
_register_tool("set_dashboard_config", _tool_set_dashboard_config)
_register_tool("add_dashboard_card", _tool_dashboard_card, op="add", tool_name="add_dashboard_card")
_register_tool("edit_dashboard_card", _tool_dashboard_card, op="edit", tool_name="edit_dashboard_card")
_register_tool("delete_dashboard_card", _tool_dashboard_card, op="delete", tool_name="delete_dashboard_card")
_register_tool("patch_dashboard", _tool_patch_dashboard)

# The mesa_* tools take (tool_name, args, ...) and are scoped by mesa_tools.
for _mesa_tool_name in sorted(MESA_TOOL_NAMES):
    _register_tool(_mesa_tool_name, _tool_mesa, mesa_tool_name=_mesa_tool_name)

_register_tool("create_calendar_event", _tool_calendar_write, operation="create", tool_name="create_calendar_event")
_register_tool("update_calendar_event", _tool_calendar_write, operation="update", tool_name="update_calendar_event")
_register_tool("delete_calendar_event", _tool_calendar_write, operation="delete", tool_name="delete_calendar_event")
_register_executor("create_calendar_event", _execute_create_calendar_event)
_register_executor("update_calendar_event", _execute_update_calendar_event)
_register_executor("delete_calendar_event", _execute_delete_calendar_event)
_register_tool("get_calendar_write", _tool_get_calendar_write)

_register_companion_turn(run_request)
