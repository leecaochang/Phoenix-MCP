"""Command, Decision Provider and tool routing tables.

Language membership comes from home_assistant_intents, which reaches the install
through the conversation integration, so this module is not standard-library-only
and is deliberately separate from const.
"""

from collections.abc import Mapping
import datetime
from types import MappingProxyType
from typing import Any

from home_assistant_intents import get_languages as _assist_languages

from ..const import DOMAIN_IMPORTANT_ATTRIBUTES


def _sealed_groups(table: dict[str, dict[str, Any]]) -> Mapping[str, Mapping[str, Any]]:
    """Return a read-only view of a two-level table."""
    return MappingProxyType({key: MappingProxyType(group) for key, group in table.items()})


# Auto routing follows the ingress language; legacy diagnostic modes retain their allowlist.
COMMAND_ROUTING_MODES = frozenset({"off", "auto", "local_shadow", "local", "hybrid"})
CHAT_COMMAND_ROUTING_MODES = frozenset({"off", "auto", "local", "hybrid"})
CHAT_ROUTING_HISTORY_LIMIT = 128
COMMAND_CLARIFICATION_LIMIT = 64
COMMAND_CLARIFICATION_TTL_SECONDS = 120
COMMAND_CLARIFICATION_MAX_CHOICES = 5
COMMAND_ROUTING_PROOF_FIELDS = frozenset({'voice_origin', 'clarification'})
DECISION_REQUEST_BUDGET_MAX = 100000
DECISION_BUDGET_WINDOW_SECONDS = 3600
DECISION_BUDGET_STORAGE_KEY = 'phoenix_mcp_decision_budget'
COMMAND_CLARIFICATION_CANCEL = frozenset({'cancel', 'never mind', 'nevermind', '取消', '取消吧',
    'abbrechen', 'cancelar', 'annuler', 'annulla', 'キャンセル', '취소', 'annuleren', 'anuluj', 'отмена'})
CHAT_ROUTING_HISTORY_TTL_SECONDS = 1800
CHAT_ROUTING_HISTORY_MAX_MESSAGES = 128
CHAT_ROUTING_HISTORY_MAX_BYTES = 65536
# Canonical routing tags map to the HA sentence variant; previews use the same set.
_COMMAND_ROUTING_LANGUAGES = {language: language for language in _assist_languages()}
_COMMAND_ROUTING_LANGUAGES.update({"zh-Hans": "zh-CN", "zh-Hant": "zh-TW"})
COMMAND_ROUTING_DEFAULT_LANGUAGES = ("en",)
COMMAND_ROUTING_LANGUAGE_ALIASES = MappingProxyType({
    "zh-hans": "zh-Hans", "zh-cn": "zh-Hans", "zh-sg": "zh-Hans", "zh-my": "zh-Hans",
    "zh-hant": "zh-Hant", "zh-tw": "zh-Hant", "zh-mo": "zh-Hant", "zh": "zh-Hans",
})
COMMAND_ROUTING_PLAN_KEY = "_phoenix_command_route"
COMMAND_ROUTING_NUMERIC_TARGET_LIMIT = 16
COMMAND_ROUTING_NUMERIC_MATCH_LIMIT = 64
COMMAND_ROUTING_TIMEOUT_DEFAULT_MS = 500
COMMAND_ROUTING_TIMEOUT_MIN_MS = 50
COMMAND_ROUTING_TIMEOUT_MAX_MS = 5000
# A short observation window, not a device completion deadline or retry budget.
COMMAND_VERIFICATION_WAIT_SECONDS = 1.0
COMMAND_VERIFICATION_POLL_SECONDS = 0.1
COMMAND_VERIFICATION_TOLERANCES = MappingProxyType({"HassLightSet": 1, "HassSetVolume": 0.005, "HassSetPosition": 0.5})
# Domain, HA slot, proposal field, service, service field, divisor, whole percent, HA matching domains.
_COMMAND_ROUTING_NUMERIC_OPERATIONS = {
    "HassLightSet": ("light", "brightness", "brightness_pct", "turn_on", "brightness_pct", 1, False, ("light",)),
    "HassSetVolume": ("media_player", "volume_level", "volume_pct", "volume_set", "volume_level", 100, True, ("media_player",)),
    "HassSetPosition": ("cover", "position", "position_pct", "set_cover_position", "position", 1, True, ("cover", "valve")),
}
# Additional absolute numeric controls. Synthetic intent names are internal only.
_COMMAND_ROUTING_NUMERIC_OPERATIONS.update({
    "HassFanSetSpeed": ("fan", "percentage", "percentage", "set_percentage", "percentage", 1, True, ("fan",)),
    "PhoenixValvePosition": ("valve", "position", "position_pct", "set_valve_position", "position", 1, True, ("cover", "valve")),
    "PhoenixCoverTilt": ("cover", "tilt", "tilt_pct", "set_cover_tilt_position", "tilt_position", 1, True, ("cover",)),
    "HassClimateSetTemperature": ("climate", "temperature", "temperature", "set_temperature", "temperature", 1, False, ("climate",)),
    "HassHumidifierSetpoint": ("humidifier", "humidity", "humidity", "set_humidity", "humidity", 1, True, ("humidifier",)),
    "PhoenixClimateHumidity": ("climate", "humidity", "humidity", "set_humidity", "humidity", 1, True, ("climate",)),
    "PhoenixWaterTemperature": ("water_heater", "temperature", "temperature", "set_temperature", "temperature", 1, False, ("water_heater",)),
    "PhoenixCounterValue": ("counter", "value", "value", "set_value", "value", 1, True, ("counter",)),
    "PhoenixNumberValue": ("number", "value", "value", "set_value", "value", 1, False, ("number", "input_number")),
    "PhoenixInputNumberValue": ("input_number", "value", "value", "set_value", "value", 1, False, ("number", "input_number")),
    "PhoenixLightTemperature": ("light", "temperature", "temperature", "turn_on", "color_temp_kelvin", 1, True, ("light",)),
})
# Native bounds and precision must be present; missing metadata never invents a range.
COMMAND_ROUTING_NUMERIC_BOUNDS = MappingProxyType({
    "HassClimateSetTemperature": ("min_temp", "max_temp", "target_temp_step"),
    "PhoenixWaterTemperature": ("min_temp", "max_temp", None),
    "HassHumidifierSetpoint": ("min_humidity", "max_humidity", None),
    "PhoenixClimateHumidity": ("min_humidity", "max_humidity", None),
    "PhoenixNumberValue": ("min", "max", "step"),
    "PhoenixInputNumberValue": ("min", "max", "step"),
    "PhoenixLightTemperature": ("min_color_temp_kelvin", "max_color_temp_kelvin", None),
})
COMMAND_ROUTING_NATIVE_VALUES = frozenset({
    "HassClimateSetTemperature", "PhoenixWaterTemperature", "PhoenixNumberValue",
    "PhoenixInputNumberValue", "PhoenixLightTemperature",
})
_COMMAND_ROUTING_NUMERIC_STATE = {
    "HassFanSetSpeed": "percentage", "PhoenixValvePosition": "current_position",
    "PhoenixCoverTilt": "current_tilt_position", "HassClimateSetTemperature": "temperature",
    "PhoenixWaterTemperature": "temperature", "HassHumidifierSetpoint": "humidity",
    "PhoenixClimateHumidity": "humidity", "PhoenixNumberValue": None, "PhoenixInputNumberValue": None,
    "PhoenixLightTemperature": "color_temp_kelvin",
}
COMMAND_ROUTING_NATIVE_VALUES |= {"PhoenixCounterValue"}
_COMMAND_ROUTING_NUMERIC_STATE["PhoenixCounterValue"] = None
COMMAND_ROUTING_SWITCH_SERVICES = MappingProxyType({"HassTurnOn": "turn_on", "HassTurnOff": "turn_off"})
_COMMAND_ROUTING_INTENTS = frozenset({*COMMAND_ROUTING_SWITCH_SERVICES, *_COMMAND_ROUTING_NUMERIC_OPERATIONS, "HassGetState"})
COMMAND_ROUTING_TARGET_SLOTS = frozenset({"name", "area", "domain", "device_class"})

# Fixed entity actions and bounded option/text values. No provider-written service names.
# Domain, service, value field, options attribute, required feature, observed state.
_COMMAND_ROUTING_ENTITY_COMMANDS = {
    "switch_on": ('switch', 'turn_on', '', '', '', 'on'),
    "switch_off": ('switch', 'turn_off', '', '', '', 'off'),
    "input_boolean_on": ('input_boolean', 'turn_on', '', '', '', 'on'),
    "input_boolean_off": ('input_boolean', 'turn_off', '', '', '', 'off'),
    "fan_on": ('fan', 'turn_on', '', '', 'TURN_ON', 'on'),
    "fan_off": ('fan', 'turn_off', '', '', 'TURN_OFF', 'off'),
    "humidifier_on": ('humidifier', 'turn_on', '', '', '', 'on'),
    "humidifier_off": ('humidifier', 'turn_off', '', '', '', 'off'),
    "climate_on": ('climate', 'turn_on', '', '', 'TURN_ON', ''),
    "climate_off": ('climate', 'turn_off', '', '', 'TURN_OFF', 'off'),
    "water_heater_on": ('water_heater', 'turn_on', '', '', 'ON_OFF', ''),
    "water_heater_off": ('water_heater', 'turn_off', '', '', 'ON_OFF', 'off'),
    "media_player_on": ('media_player', 'turn_on', '', '', 'TURN_ON', ''),
    "media_player_off": ('media_player', 'turn_off', '', '', 'TURN_OFF', 'off'),
    "remote_on": ('remote', 'turn_on', '', '', '', 'on'),
    "remote_off": ('remote', 'turn_off', '', '', '', 'off'),
    "siren_on": ('siren', 'turn_on', '', '', 'TURN_ON', 'on'),
    "siren_off": ('siren', 'turn_off', '', '', 'TURN_OFF', 'off'),
    "switch_toggle": ('switch', 'toggle', '', '', '', ''),
    "input_boolean_toggle": ('input_boolean', 'toggle', '', '', '', ''),
    "counter_increment": ('counter', 'increment', '', '', '', ''),
    "counter_decrement": ('counter', 'decrement', '', '', '', ''),
    "counter_reset": ('counter', 'reset', '', '', '', ''),
    "timer_start": ('timer', 'start', '', '', '', ''),
    "timer_pause": ('timer', 'pause', '', '', '', 'paused'),
    "timer_cancel": ('timer', 'cancel', '', '', '', 'idle'),
    "timer_finish": ('timer', 'finish', '', '', '', ''),
    "button_press": ('button', 'press', '', '', '', ''),
    "input_button_press": ('input_button', 'press', '', '', '', ''),
    "cover_open": ('cover', 'open_cover', '', '', 'OPEN', 'open'),
    "cover_close": ('cover', 'close_cover', '', '', 'CLOSE', 'closed'),
    "cover_stop": ('cover', 'stop_cover', '', '', 'STOP', ''),
    "valve_open": ('valve', 'open_valve', '', '', 'OPEN', 'open'),
    "valve_close": ('valve', 'close_valve', '', '', 'CLOSE', 'closed'),
    "valve_stop": ('valve', 'stop_valve', '', '', 'STOP', ''),
    "media_play": ('media_player', 'media_play', '', '', 'PLAY', 'playing'),
    "media_pause": ('media_player', 'media_pause', '', '', 'PAUSE', 'paused'),
    "media_stop": ('media_player', 'media_stop', '', '', 'STOP', 'idle'),
    "media_next_track": ('media_player', 'media_next_track', '', '', 'NEXT_TRACK', ''),
    "media_previous_track": ('media_player', 'media_previous_track', '', '', 'PREVIOUS_TRACK', ''),
    "vacuum_start": ('vacuum', 'start', '', '', 'START', 'cleaning'),
    "vacuum_pause": ('vacuum', 'pause', '', '', 'PAUSE', 'paused'),
    "vacuum_stop": ('vacuum', 'stop', '', '', 'STOP', ''),
    "vacuum_return_to_base": ('vacuum', 'return_to_base', '', '', 'RETURN_HOME', 'returning'),
    "vacuum_locate": ('vacuum', 'locate', '', '', 'LOCATE', ''),
    "lawn_mower_start_mowing": ('lawn_mower', 'start_mowing', '', '', 'START_MOWING', 'mowing'),
    "lawn_mower_dock": ('lawn_mower', 'dock', '', '', 'DOCK', 'returning'),
    "lawn_mower_pause": ('lawn_mower', 'pause', '', '', 'PAUSE', 'paused'),
    "lock_lock": ('lock', 'lock', '', '', '', 'locked'),
    "lock_unlock": ('lock', 'unlock', '', '', '', 'unlocked'),
    "lock_open": ('lock', 'open', '', '', 'OPEN', ''),
    "scene_activate": ('scene', 'turn_on', '', '', '', ''),
    "script_on": ('script', 'turn_on', '', '', '', ''),
    "script_off": ('script', 'turn_off', '', '', '', 'off'),
    "select_option": ('select', 'select_option', 'option', 'options', '', ''),
    "input_select_option": ('input_select', 'select_option', 'option', 'options', '', ''),
    "text_value": ('text', 'set_value', 'value', '', '', ''),
    "input_text_value": ('input_text', 'set_value', 'value', '', '', ''),
    "climate_hvac": ('climate', 'set_hvac_mode', 'hvac_mode', 'hvac_modes', '', ''),
    "climate_preset": ('climate', 'set_preset_mode', 'preset_mode', 'preset_modes', 'PRESET_MODE', ''),
    "climate_fan": ('climate', 'set_fan_mode', 'fan_mode', 'fan_modes', 'FAN_MODE', ''),
    "climate_swing": ('climate', 'set_swing_mode', 'swing_mode', 'swing_modes', 'SWING_MODE', ''),
    "fan_preset": ('fan', 'set_preset_mode', 'preset_mode', 'preset_modes', 'PRESET_MODE', ''),
    "humidifier_mode": ('humidifier', 'set_mode', 'mode', 'available_modes', 'MODES', ''),
    "water_operation": ('water_heater', 'set_operation_mode', 'operation_mode', 'operation_list', 'OPERATION_MODE', ''),
    "media_source": ('media_player', 'select_source', 'source', 'source_list', 'SELECT_SOURCE', ''),
    "media_sound": ('media_player', 'select_sound_mode', 'sound_mode', 'sound_mode_list', 'SELECT_SOUND_MODE', ''),
    "vacuum_speed": ('vacuum', 'set_fan_speed', 'fan_speed', 'fan_speed_list', 'FAN_SPEED', ''),
}
_COMMAND_ROUTING_ENTITY_COMMANDS.update({
    "automation_on": ('automation', 'turn_on', '', '', '', 'on'),
    "automation_off": ('automation', 'turn_off', '', '', '', 'off'),
    "media_mute": ('media_player', 'volume_mute', '', '', 'VOLUME_MUTE', ''),
    "media_unmute": ('media_player', 'volume_mute', '', '', 'VOLUME_MUTE', ''),
    "media_shuffle_on": ('media_player', 'shuffle_set', '', '', 'SHUFFLE_SET', ''),
    "media_shuffle_off": ('media_player', 'shuffle_set', '', '', 'SHUFFLE_SET', ''),
    "media_repeat_all": ('media_player', 'repeat_set', '', '', 'REPEAT_SET', ''),
    "media_repeat_one": ('media_player', 'repeat_set', '', '', 'REPEAT_SET', ''),
    "media_repeat_off": ('media_player', 'repeat_set', '', '', 'REPEAT_SET', ''),
    "media_clear_playlist": ('media_player', 'clear_playlist', '', '', 'CLEAR_PLAYLIST', ''),
    "fan_oscillate_on": ('fan', 'oscillate', '', '', 'OSCILLATE', ''),
    "fan_oscillate_off": ('fan', 'oscillate', '', '', 'OSCILLATE', ''),
    "fan_direction_forward": ('fan', 'set_direction', '', '', 'DIRECTION', ''),
    "fan_direction_reverse": ('fan', 'set_direction', '', '', 'DIRECTION', ''),
    "cover_open_tilt": ('cover', 'open_cover_tilt', '', '', 'OPEN_TILT', ''),
    "cover_close_tilt": ('cover', 'close_cover_tilt', '', '', 'CLOSE_TILT', ''),
    "cover_stop_tilt": ('cover', 'stop_cover_tilt', '', '', 'STOP_TILT', ''),
    "climate_swing_horizontal": ('climate', 'set_swing_horizontal_mode', 'swing_horizontal_mode', 'swing_horizontal_modes', 'SWING_HORIZONTAL_MODE', ''),
})
# Native no-payload actions retain the shared permission and approval executor.
_COMMAND_ROUTING_ENTITY_COMMANDS.update({
    "input_number_increment": ('input_number', 'increment', '', '', '', ''),
    "input_number_decrement": ('input_number', 'decrement', '', '', '', ''),
    "water_away_on": ('water_heater', 'set_away_mode', '', '', 'AWAY_MODE', ''),
    "water_away_off": ('water_heater', 'set_away_mode', '', '', 'AWAY_MODE', ''),
    "vacuum_clean_spot": ('vacuum', 'clean_spot', '', '', 'CLEAN_SPOT', ''),
    "media_volume_up": ('media_player', 'volume_up', '', '', 'VOLUME_STEP', ''),
    "media_volume_down": ('media_player', 'volume_down', '', '', 'VOLUME_STEP', ''),
    "media_play_pause": ('media_player', 'media_play_pause', '', '', 'PAUSE', ''),
    "media_unjoin": ('media_player', 'unjoin', '', '', 'GROUPING', ''),
    "camera_on": ('camera', 'turn_on', '', '', 'ON_OFF', 'idle'),
    "camera_off": ('camera', 'turn_off', '', '', 'ON_OFF', 'off'),
    "camera_motion_on": ('camera', 'enable_motion_detection', '', '', '', ''),
    "camera_motion_off": ('camera', 'disable_motion_detection', '', '', '', ''),
    "light_effect": ('light', 'turn_on', 'effect', 'effect_list', 'EFFECT', ''),
    "light_color": ('light', 'turn_on', 'color_name', '__color_names__', '', ''),
})
for _domain in ('select', 'input_select'):
    for _position in ('first', 'last', 'next', 'previous'):
        _COMMAND_ROUTING_ENTITY_COMMANDS[f"{_domain}_{_position}"] = (_domain, f"select_{_position}", '', '', '', '')
for _domain in ('light', 'fan', 'humidifier', 'climate', 'media_player', 'remote', 'siren', 'automation', 'script'):
    _COMMAND_ROUTING_ENTITY_COMMANDS[f"{_domain}_toggle"] = (_domain, 'toggle', '', '', '', '')
_COMMAND_ROUTING_ENTITY_COMMANDS.update({
    "cover_toggle": ('cover', 'toggle', '', '', 'OPEN', ''),
    "cover_tilt_toggle": ('cover', 'toggle_cover_tilt', '', '', 'OPEN_TILT', ''),
    "valve_toggle": ('valve', 'toggle', '', '', 'OPEN', ''),
})
_COMMAND_ROUTING_ENTITY_COMMANDS.update({
    "fan_increase": ('fan', 'increase_speed', '', '', 'SET_SPEED', ''),
    "fan_decrease": ('fan', 'decrease_speed', '', '', 'SET_SPEED', ''),
    "light_flash_short": ('light', 'turn_on', '', '', 'FLASH', ''),
    "light_flash_long": ('light', 'turn_on', '', '', 'FLASH', ''),
    "alarm_disarm": ('alarm_control_panel', 'alarm_disarm', '', '', '', 'disarmed'),
    "alarm_home": ('alarm_control_panel', 'alarm_arm_home', '', '', 'ARM_HOME', 'armed_home'),
    "alarm_away": ('alarm_control_panel', 'alarm_arm_away', '', '', 'ARM_AWAY', 'armed_away'),
    "alarm_night": ('alarm_control_panel', 'alarm_arm_night', '', '', 'ARM_NIGHT', 'armed_night'),
    "alarm_vacation": ('alarm_control_panel', 'alarm_arm_vacation', '', '', 'ARM_VACATION', 'armed_vacation'),
    "alarm_custom_bypass": ('alarm_control_panel', 'alarm_arm_custom_bypass', '', '', 'ARM_CUSTOM_BYPASS', 'armed_custom_bypass'),
    "alarm_trigger": ('alarm_control_panel', 'alarm_trigger', '', '', 'TRIGGER', 'triggered'),
    "update_skip": ('update', 'skip', '', '', '', ''),
    "update_clear_skipped": ('update', 'clear_skipped', '', '', '', ''),
})
# Read selection reuses known public state projections, never writable service fields.
_COMMAND_ROUTING_ENTITY_COMMANDS["attribute_read"] = ('', '', 'attribute', '__read_attributes__', '', '')
COMMAND_ROUTING_READ_INTENTS = frozenset({"HassGetState", "PhoenixEntityAttributeRead", "PhoenixStateSummary"})
COMMAND_ROUTING_READ_PERCENT_SCALES = MappingProxyType({
    "brightness": (("light",), 255), "volume_level": (("media_player",), 1),
    "current_position": (("cover", "valve"), 100), "current_tilt_position": (("cover",), 100),
    "percentage": (("fan",), 100), "humidity": (("climate", "humidifier", "weather"), 100),
    "current_humidity": (("climate", "humidifier"), 100),
})
COMMAND_ROUTING_READ_ATTRIBUTES = frozenset({
    *(key for fields in DOMAIN_IMPORTANT_ATTRIBUTES.values() for key in fields),
    "unit_of_measurement", "options", "remaining", "finishes_at", "min", "max", "step", "minimum", "maximum",
    "current_tilt_position", "swing_horizontal_mode", "oscillating", "direction", "away_mode", "effect",
}) - {"friendly_name", "supported_features", "supported_color_modes", "device_class", "state_class", "unit_of_measurement"}
# Exact service data is owned by Phoenix; a model selects only the category.
_COMMAND_ROUTING_ENTITY_FIXED_DATA: dict[str, dict[str, bool | str]] = {
    "automation_off": {"stop_actions": True},
    "light_flash_short": {"flash": "short"}, "light_flash_long": {"flash": "long"},
    "water_away_on": {"away_mode": True}, "water_away_off": {"away_mode": False},
    "media_mute": {"is_volume_muted": True}, "media_unmute": {"is_volume_muted": False},
    "media_shuffle_on": {"shuffle": True}, "media_shuffle_off": {"shuffle": False},
    "media_repeat_all": {"repeat": "all"}, "media_repeat_one": {"repeat": "one"}, "media_repeat_off": {"repeat": "off"},
    "fan_oscillate_on": {"oscillating": True}, "fan_oscillate_off": {"oscillating": False},
    "fan_direction_forward": {"direction": "forward"}, "fan_direction_reverse": {"direction": "reverse"},
}
_COMMAND_ROUTING_ENTITY_INTENTS = {"PhoenixEntity" + "".join(part.title() for part in category.split("_")): spec
                                  for category, spec in _COMMAND_ROUTING_ENTITY_COMMANDS.items()}
DECISION_ENTITY_CATEGORIES = MappingProxyType({category: "PhoenixEntity" + "".join(part.title() for part in category.split("_"))
                              for category in _COMMAND_ROUTING_ENTITY_COMMANDS})
_COMMAND_ROUTING_FIXED_INTENT_DATA = {DECISION_ENTITY_CATEGORIES[category]: values
                                     for category, values in _COMMAND_ROUTING_ENTITY_FIXED_DATA.items()}
_COMMAND_ROUTING_FIXED_OBSERVATIONS = {intent: values for intent, values in _COMMAND_ROUTING_FIXED_INTENT_DATA.items()
                                     if intent not in {DECISION_ENTITY_CATEGORIES[key] for key in ("automation_off", "light_flash_short", "light_flash_long")}}
_COMMAND_ROUTING_FIXED_OBSERVATIONS[DECISION_ENTITY_CATEGORIES["water_away_on"]] = {"away_mode": "on"}
_COMMAND_ROUTING_FIXED_OBSERVATIONS[DECISION_ENTITY_CATEGORIES["water_away_off"]] = {"away_mode": "off"}
COMMAND_ROUTING_VALUE_INTENTS = frozenset(intent for intent, spec in _COMMAND_ROUTING_ENTITY_INTENTS.items() if spec[2])
# Native named actions share projections and approval checks with typed decisions.
COMMAND_ROUTING_NATIVE_ACTIONS = MappingProxyType({
    "HassMediaPause": "media_pause", "HassMediaUnpause": "media_play",
    "HassMediaNext": "media_next_track", "HassMediaPrevious": "media_previous_track",
    "HassMediaPlayerMute": "media_mute", "HassMediaPlayerUnmute": "media_unmute",
    "HassVacuumStart": "vacuum_start", "HassVacuumReturnToBase": "vacuum_return_to_base",
})
COMMAND_ROUTING_REQUIRED_STATES = MappingProxyType({
    "HassMediaPause": frozenset({"playing"}), "HassMediaNext": frozenset({"playing"}),
    "HassMediaPrevious": frozenset({"playing"}),
})
for _intent, _category in COMMAND_ROUTING_NATIVE_ACTIONS.items():
    _COMMAND_ROUTING_ENTITY_INTENTS[_intent] = _COMMAND_ROUTING_ENTITY_COMMANDS[_category]
    if _category in _COMMAND_ROUTING_ENTITY_FIXED_DATA:
        _COMMAND_ROUTING_FIXED_INTENT_DATA[_intent] = _COMMAND_ROUTING_ENTITY_FIXED_DATA[_category]
        _COMMAND_ROUTING_FIXED_OBSERVATIONS[_intent] = _COMMAND_ROUTING_ENTITY_FIXED_DATA[_category]
_COMMAND_ROUTING_INTENTS |= _COMMAND_ROUTING_ENTITY_INTENTS.keys()
COMMAND_ROUTING_ENTITY_VALUE_LIMIT = 254
DECISION_ENTITY_VALUE_QUESTIONS = ("value", "attribute", "color")
COMMAND_ROUTING_TIMER_OBSERVATION_TOLERANCE_SECONDS = 2
COMMAND_ROUTING_UNKNOWN_ACTION_DOMAINS = frozenset({"button", "input_button", "scene"})
COMMAND_ROUTING_NUMERIC_READ_DOMAINS = frozenset({"sensor", "number", "input_number", "counter"})
COMMAND_ROUTING_TRANSLATION_TIMEOUT_SECONDS = 2

# Typed helper updates are immediate value writes, never scheduling requests.
COMMAND_ROUTING_TEMPORAL_OPERATIONS = MappingProxyType({
    "PhoenixTimerDuration": ("timer", "start", "duration", "duration"),
    "PhoenixDateValue": ("date", "set_value", "date", "date"),
    "PhoenixTimeValue": ("time", "set_value", "time", "time"),
    "PhoenixDateTimeValue": ("datetime", "set_value", "datetime", "datetime"),
    "PhoenixInputDate": ("input_datetime", "set_datetime", "date", "date"),
    "PhoenixInputTime": ("input_datetime", "set_datetime", "time", "time"),
    "PhoenixInputDateTime": ("input_datetime", "set_datetime", "datetime", "datetime"),
})
DECISION_TEMPORAL_CATEGORIES = MappingProxyType({
    "timer_duration": "PhoenixTimerDuration",
    "date_value": "PhoenixDateValue", "time_value": "PhoenixTimeValue", "datetime_value": "PhoenixDateTimeValue",
    "helper_date": "PhoenixInputDate", "helper_time": "PhoenixInputTime", "helper_datetime": "PhoenixInputDateTime",
})

# Decision-only shadow routing: configurable network time, independent of HA recognition.
DECISION_TYPESAFE_BASE_URL = "https://api.typesafe.ai/v1"
DECISION_TYPESAFE_MODEL = "jev-latest"
DECISION_TIMEOUT_DEFAULT_MS = 5000
DECISION_TIMEOUT_MIN_MS = 250
DECISION_TIMEOUT_MAX_MS = 30000
DECISION_MAX_RESPONSE_BYTES = 65536
DECISION_MAX_ERROR_BYTES = 4096
DECISION_TYPESAFE_HTTP_ERRORS = MappingProxyType({"max_tokens_exceeded": "provider_context_limit"})
# TypeSafe moves this alias to each new JEV revision. An account configured with
# it accepts whichever revision answers, by operator decision, wherever the
# registries below admit the alias; a pinned revision must answer as itself.
DECISION_FOLLOWED_ALIASES = frozenset({("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-latest")})
DECISION_FOLLOWED_REVISION = r"jev-\d+\.\d+\.\d+"
DECISION_METRIC_SAMPLES = 256
DECISION_METRIC_SCOPES = frozenset({"preview", "voice", "chat", "voice_shadow", "chat_shadow", "tool_chat", "tool_voice", "tool_ai_task", "ai_task_evaluate"})
DECISION_METRIC_OPERATIONS = frozenset({"classify", "target", "context", "batch", "reference", "tool", "evaluate"})
# Home Assistant bounds neither, and JEV refuses more than these criteria per question.
DECISION_EVALUATE_MAX_BYTES = 65536
DECISION_EVALUATE_MAX_CHOICES = 255
DECISION_EVALUATE_MAX_SCORE_LEVELS = 10
DECISION_DIAGNOSTIC_QUESTIONS = frozenset({
    "route", "target", "value", "attribute", "color", "percentage", "summary_scope", "summary",
})
DECISION_CONTEXT_MAX_MESSAGES = 32
DECISION_CONTEXT_MAX_BYTES = 16384
DECISION_CONTEXT_MAX_DEPTH = 16
DECISION_CONTEXT_CHECKS = _sealed_groups({
    "request": {
        "immediate": "In this conversation, the latest user message is a real request to carry out the fixed candidate now or read its current state. Its meaning does not depend on unresolved references.",
        "contextual": "The latest message is quoted, hypothetical, a demonstration, text to translate or explain, deferred, conditional, context-dependent or otherwise uncertain.",
    },
    "constraints": {
        "clear": "No earlier user restriction still prevents this fixed candidate now. Either no applicable restriction was given, or a later user message explicitly revoked it.",
        "restricted": "An earlier user restriction still applies, a required condition is unresolved, or it is uncertain whether the fixed candidate is permitted by the user's earlier instructions.",
    },
})
DECISION_CONTEXT_REQUIRED_CHOICES = MappingProxyType({"request": "immediate", "constraints": "clear"})
# Singular references have separate qualification from self-contained commands.
DECISION_REFERENCE_LANGUAGES = frozenset(_COMMAND_ROUTING_LANGUAGES.values())
DECISION_REFERENCE_INTENTS = frozenset({"HassTurnOn", "HassTurnOff", "HassGetState"})
DECISION_REFERENCE_EXECUTION_MODELS = MappingProxyType({
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-latest"): frozenset({"jev-1.13.0"}),
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-1.13.0"): frozenset({"jev-1.13.0"}),
})
DECISION_REFERENCE_CHECKS = _sealed_groups({
    "route": {
        "light_on": "The latest message requests turning the one referenced light on now.",
        "light_off": "The latest message requests turning the one referenced light off now.",
        "state_query": "The latest message requests only the current on/off state of the one referenced light.",
        "provider": "Anything else, including explanation, translation, negation, delay, conditions, numeric or relative adjustments, multiple requests or uncertainty.",
    },
    "reference": {
        "bound": "The latest singular reference refers to the anchored light, which remains the clear conversational subject. This identifies the device, not whether an earlier action succeeded.",
        "unresolved": "The latest message names a target explicitly, has no singular reference, refers elsewhere, has competing referents, or its reference is uncertain.",
    },
    "constraints": {
        "clear": "No earlier user restriction prevents the operation requested in the latest message. A read-only restriction permits a state query, even when earlier commands requested changes.",
        "restricted": "An earlier user restriction prevents the latest requested operation, an applicable condition remains unresolved, or permission under earlier user instructions is uncertain.",
    },
})
# Initial evaluated revisions only; every adapter remains eligible for inert previews.
DECISION_CONTEXT_EXECUTION_MODELS = MappingProxyType({
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-latest"): frozenset({"jev-1.13.0"}),
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-1.13.0"): frozenset({"jev-1.13.0"}),
})
DECISION_CONTEXT_EXECUTION_LANGUAGES = frozenset(_COMMAND_ROUTING_LANGUAGES.values())
# Combining decision-derived candidates with context review is qualified separately.
DECISION_FOLLOWUP_EXECUTION_MODELS = MappingProxyType({
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-latest"): frozenset({"jev-1.13.0"}),
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-1.13.0"): frozenset({"jev-1.13.0"}),
})
# Joint routing has its own qualification, independent of context and numeric decisions.
DECISION_BATCH_EXECUTION_MODELS = MappingProxyType({
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-latest"): frozenset({"jev-1.13.0"}),
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-1.13.0"): frozenset({"jev-1.13.0"}),
})
DECISION_BATCH_EXECUTION_LANGUAGES = frozenset(_COMMAND_ROUTING_LANGUAGES.values())
# Numeric execution is qualified independently from categorical routing.
DECISION_PERCENTAGE_EXECUTION_MODELS = MappingProxyType({
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-latest"): frozenset({"jev-1.13.0"}),
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-1.13.0"): frozenset({"jev-1.13.0"}),
})
DECISION_ROUTE_CHOICES = MappingProxyType({
    "light_on": "One immediate, explicit request to turn on one light, without a brightness setting or adjustment.",
    "light_off": "One immediate, explicit request to turn off one light, without a brightness setting or adjustment.",
    "light_brightness": "One immediate, explicit request to set one light to an absolute whole brightness percentage from 0 through 100, inclusive. Not a relative adjustment.",
    "state_query": "A question about the current primary state of one named entity.",
    "provider": "Anything else, including ambiguity, negation, quoted commands, deferred or conditional actions, multiple actions, comparisons, counts and general conversation. Use the regular agent for history/statistics, forecast/event/list contents, and structured payloads such as calendar/todo authoring, media playback/grouping, remote commands, camera files, temperature ranges, or settings not represented by an exact eligible operation.",
})
_DECISION_PERCENTAGE_PREVIEW_CHOICES = {
    "media_volume": "One immediate, explicit request to set one media player's volume to an absolute whole percentage from 0 through 100. Not a relative adjustment.",
    "cover_position": "One immediate, explicit request to set one cover to an absolute whole percentage open from 0 through 100. Zero is fully closed and 100 fully open. Not a relative adjustment.",
}
# TypeSafe Choice supports 255 options; exact percentages need 101 plus a refusal.
DECISION_EXACT_PERCENTAGE_ENDPOINTS = frozenset({("typesafe", DECISION_TYPESAFE_BASE_URL, "systemone")})
# TypeSafe's direct API rounds probabilities to two decimal places.
DECISION_PROBABILITY_ROUNDING = MappingProxyType({("typesafe", DECISION_TYPESAFE_BASE_URL, "systemone"): 0.005})
DECISION_PROBABILITY_SUM_TOLERANCE = 0.001
DECISION_PROBABILITY_ROUNDING_EPSILON = 1e-9
DECISION_PERCENTAGE_CHOICES = MappingProxyType({
    **{str(value): f"The explicitly requested absolute setting is exactly {value} percent." for value in range(101)},
    "none": "No single explicit absolute whole percentage from 0 through 100 is requested for this operation.",
})

DECISION_PROVIDERS = frozenset({"typesafe"})
_DECISION_TARGET_INTENTS = {
    "light_on": "HassTurnOn", "light_off": "HassTurnOff",
    "light_brightness": "HassLightSet", "state_query": "HassGetState",
    "media_volume": "HassSetVolume", "cover_position": "HassSetPosition",
}
DECISION_ADDITIONAL_NUMERIC = MappingProxyType({
    "fan_speed": ("HassFanSetSpeed", "fan speed percentage"),
    "valve_position": ("PhoenixValvePosition", "valve percentage open, never percentage closed"),
    "cover_tilt": ("PhoenixCoverTilt", "cover tilt percentage, distinct from cover position"),
    "climate_temperature": ("HassClimateSetTemperature", "thermostat target temperature in its native unit"),
    "humidifier_humidity": ("HassHumidifierSetpoint", "humidifier target humidity percentage"),
    "climate_humidity": ("PhoenixClimateHumidity", "climate target humidity percentage"),
    "water_temperature": ("PhoenixWaterTemperature", "water heater target temperature in its native unit"),
    "counter_value": ("PhoenixCounterValue", "counter value as an absolute integer"),
    "number_value": ("PhoenixNumberValue", "numeric entity value in its native unit"),
    "input_number_value": ("PhoenixInputNumberValue", "numeric helper value in its native unit"),
    "light_temperature": ("PhoenixLightTemperature", "light colour temperature in kelvin, not room temperature"),
})
_DECISION_TARGET_INTENTS.update(DECISION_ENTITY_CATEGORIES)
_DECISION_ENTITY_CHOICES = {category: f"One immediate request for {spec[1]} on one named {spec[0]} entity"
                             + (f" using its {spec[2]} value." if spec[2] else ". No additional setting or duration.")
                             for category, spec in _COMMAND_ROUTING_ENTITY_COMMANDS.items()}
for _category, _values in _COMMAND_ROUTING_ENTITY_FIXED_DATA.items():
    _DECISION_ENTITY_CHOICES[_category] += f" Use exactly these fixed service fields: {_values}."
_DECISION_ENTITY_CHOICES["attribute_read"] = "Read one current attribute of one named entity. Select its attribute name from the available options. Brightness, volume, position, speed percentage and humidity are reported as percentages; temperatures use Home Assistant units. No other unit conversions. Not a primary-state query, historical query, comparison or aggregate."
_DECISION_ENTITY_CHOICES["automation_off"] += " Disable future triggers and stop running automation actions, matching Home Assistant turn_off."
_DECISION_TARGET_INTENTS.update(DECISION_TEMPORAL_CATEGORIES)
_DECISION_TARGET_INTENTS.update({category: spec[0] for category, spec in DECISION_ADDITIONAL_NUMERIC.items()})
_DECISION_PERCENTAGE_PREVIEW_CHOICES.update({
    category: f"One immediate explicit request to set one {spec[1]} to an absolute numeric value."
    for category, spec in DECISION_ADDITIONAL_NUMERIC.items()
})
# Leave one of the fallback format's 50 choices for declining.
DECISION_MAX_TARGETS = 49
# Sequential selection keeps every fallback question within 50 choices.
DECISION_SEQUENTIAL_CHOICE_GROUPS = 8
DECISION_SEQUENTIAL_MAX_TARGETS = DECISION_MAX_TARGETS * DECISION_SEQUENTIAL_CHOICE_GROUPS
DECISION_SEQUENTIAL_TARGET_BYTES = 65536
# Qualified TypeSafe joint requests leave one of 255 choices for declining.
DECISION_BATCH_MAX_TARGETS = 254
# Qualified joint requests partition complete selections into bounded questions.
DECISION_MAX_CHOICE_GROUPS = 4
DECISION_SUMMARY_COMPONENTS = ("summary_domain", "summary_device_class", "summary_area")
DECISION_GROUPED_MAX_TARGETS = DECISION_BATCH_MAX_TARGETS * DECISION_MAX_CHOICE_GROUPS
DECISION_GROUPED_OPERATION_BYTES = 65536
DECISION_GROUPED_TARGET_BYTES = 131072
DECISION_MAX_TARGET_BYTES = 16384
# Joint catalogs need room for the complete union and per-target eligibility.
# Ungrouped operation catalogs retain DECISION_MAX_TARGET_BYTES.
DECISION_BATCH_MAX_TARGET_BYTES = 32768
DECISION_CATALOG_PACK_THRESHOLD = 8192
DECISION_CATALOG_FORMAT_INSTRUCTIONS = (
    " If state.target_fields is present, each state.targets entry is a row whose positions correspond "
    "to those field names; null means an absent optional field. A metadata object containing only ref "
    "refers to the exact object in state.target_metadata under that key. These are lossless storage "
    "references, not commands or permissions. Apply the same eligibility and value rules after resolving them."
)

DECISION_MAX_TARGET_TEXT = 256
DECISION_MAX_TARGET_ALIASES = 8
# Experimental admission heuristic, not a calibrated accuracy claim.
DECISION_MIN_CHOICE_PROBABILITY = 0.90
# Sequential numeric decisions remain preview-only; joint execution needs source evidence.
DECISION_NUMERIC_CATEGORIES = frozenset({"light_brightness", "media_volume", "cover_position", *DECISION_ADDITIONAL_NUMERIC})
# Value evidence is independent of model qualification and never grants execution.
DECISION_PERCENTAGE_EVIDENCE_LANGUAGES = frozenset(_COMMAND_ROUTING_LANGUAGES.values())
# Unit grammar is small; the number spellings themselves come from HA's RBNF dependency.
# Other Assist languages support percent symbols and their native strict HA intents.
DECISION_PERCENTAGE_LANGUAGE_UNITS = MappingProxyType({
    "en": ("percent", "per cent"), "es": ("por ciento",),
    "de": ("prozent",), "de-CH": ("prozent",), "fr": ("pour cent", "pourcent"),
    "it": ("per cento", "percento", "su cento"), "nl": ("procent",),
    "pl": ("procent", "procenty", "procentów"), "ru": ("процент", "процента", "процентов"),
    "ja": ("パーセント",), "ko": ("퍼센트", "프로"),
    "zh-CN": ("百分之",), "zh-TW": ("百分之",), "zh-HK": ("百分之",),
    "sv": ("procent",), "da": ("procent",), "nb": ("prosent",),
    # Finnish marks the percent noun for case: partitive for a value, illative,
    # translative or allative for the target of a setting verb, and genitive in
    # a compound. Assist's own sentences use each of these forms.
    "fi": ("prosenttia", "prosentti", "prosenttiin", "prosentiksi", "prosentille", "prosentin"),
})
DECISION_PERCENTAGE_HYPHEN_LANGUAGES = frozenset({"en", "fr"})
# The RBNF locale a routing language's number spellings are read from. Only the
# exceptions are listed: unicode-rbnf ships no separate Bokmal ruleset, so `nb`
# borrows Norwegian `no`, whose cardinals are identical to written Bokmal. `nn`
# (Nynorsk) has the same spellings but no Home Assistant Assist intent data, so
# it is not a routable Phoenix language. Swedish, Danish and Finnish have their
# own rulesets and need no alias.
DECISION_NUMBER_LANGUAGE_ALIASES = MappingProxyType({"zh-CN": "zh", "zh-TW": "zh_Hant", "zh-HK": "zh_Hant", "de-CH": "de", "nb": "no", "pt-BR": "pt", "sr-Latn": "sr_Latn"})

_DECISION_PERCENTAGE_EVIDENCE_MODIFIERS = {
    "en": frozenset({"negative", "minus", "plus", "point", "dot", "half", "halves", "quarter", "quarters",
                     "hundred", "thousand", "million", "billion", "trillion", "dozen", "dozens"}),
    "es": frozenset({"negativo", "negativa", "menos", "más", "coma", "punto", "medio", "media", "mitad",
                     "cuarto", "cuartos", "ciento", "cientos", "mil", "millón", "millones"}),
    "zh-CN": frozenset("负負正点點半几幾多余餘成折分之万萬亿億兆"),
}
_DECISION_PERCENTAGE_EVIDENCE_MODIFIERS.update({
    "de": frozenset({"minus", "plus", "komma", "halb", "halbe", "hälfte", "viertel", "hundert", "tausend", "million", "millionen"}),
    "fr": frozenset({"moins", "plus", "virgule", "demi", "demie", "moitié", "quart"}),
    "it": frozenset({"meno", "più", "virgola", "mezzo", "mezza", "metà", "quarto"}),
    "nl": frozenset({"min", "minus", "plus", "komma", "half", "halve", "kwart"}),
    "pl": frozenset({"minus", "plus", "przecinek", "pół", "połowa", "ćwierć"}),
    "ru": frozenset({"минус", "плюс", "запятая", "половина", "половиной", "четверть"}),
    "ja": frozenset({"マイナス", "プラス", "点", "半", "約"}),
    "ko": frozenset({"마이너스", "플러스", "점", "반"}),
    "sv": frozenset({"minus", "plus", "komma", "halv", "halvt", "halva", "hälft", "hälften", "kvart"}),
    "da": frozenset({"minus", "plus", "komma", "halv", "halvt", "halvdel", "kvart"}),
    "nb": frozenset({"minus", "pluss", "komma", "halv", "halvt", "halvdel", "kvart"}),
    "fi": frozenset({"miinus", "plus", "pilkku", "puoli", "puolikas", "puolikkaan", "neljännes", "neljännestä"}),
})
for _language, _source in (("zh-TW", "zh-CN"), ("zh-HK", "zh-CN"), ("de-CH", "de")):
    _DECISION_PERCENTAGE_EVIDENCE_MODIFIERS[_language] = _DECISION_PERCENTAGE_EVIDENCE_MODIFIERS[_source]
DECISION_PERCENTAGE_EVIDENCE_CONNECTORS = MappingProxyType({
    "en": frozenset({"and"}), "es": frozenset({"y"}), "zh-CN": frozenset(),
    "sv": frozenset({"och"}), "da": frozenset({"og"}),
    "nb": frozenset({"og"}), "fi": frozenset({"ja"}),
})
DECISION_PERCENTAGE_UNITS = (("%",), ("％",), ("percent",), ("per", "cent"), ("por", "ciento"), ("百", "分", "之"))
# Adding a preview category must not automatically authorize an execution path.
DECISION_EXECUTION_CATEGORIES = frozenset({"light_on", "light_off", "state_query", *DECISION_ENTITY_CATEGORIES})
# Bound independent identity checks without assuming whitespace in every language.
DECISION_TARGET_MENTION_LIMIT = 256
DECISION_UNSEGMENTED_TARGET_LANGUAGES = frozenset({'zh', 'ja', 'ko', 'th', 'lo', 'km', 'my'})
DECISION_HYBRID_REASONS = frozenset({
    "plan_ready", "provider_fallback", "category_low_probability", "target_low_probability",
    "target_declined", "target_unverified", "ambiguous_target", "brightness_declined", "no_target", "targets_changed",
    "unsupported_category", "too_many_targets", "target_metadata_too_large", "unsupported_language",
    "invalid_input", "authority_changed", "not_enabled", "not_configured", "local_match", "numeric_unverified",
    "local_unavailable", "busy", "cancelled", "authentication", "rate_limited", "http_error",
    "response_too_large", "provider_context_limit", "malformed", "timeout", "unavailable",
    "request_budget_exhausted", "budget_unavailable",
    "context_unverified", "context_unavailable", "context_changed", "low_probability", "invalid_context",
    "model_unverified", "category_mismatch",
})
DECISION_BRIGHTNESS_CHOICES = _sealed_groups({
    # Enumerate whole values so classification does not require digit arithmetic.
    "brightness_tens": {**{str(value): "The requested brightness is exactly one of: " + ", ".join(
        f"{percent}%" for percent in range(value * 10, min(value * 10 + 10, 101))) + "."
        for value in range(11)}, "none": "No single explicit whole brightness percentage from 0% to 100%."},
    "brightness_units": {**{str(value): "The requested brightness is exactly one of: " + ", ".join(
        f"{percent}%" for percent in range(value, 101, 10)) + "."
        for value in range(10)}, "none": "No single explicit whole brightness percentage from 0% to 100%."},
})

# Summary filters are native primary states, never inferred attributes or thresholds.
COMMAND_SUMMARY_STATES = MappingProxyType({
    **{domain: ('on', 'off') for domain in ('light', 'switch', 'input_boolean', 'binary_sensor', 'fan', 'humidifier', 'automation')},
    **{domain: ('open', 'closed', 'opening', 'closing') for domain in ('cover', 'valve')},
    'lock': ('locked', 'unlocked', 'locking', 'unlocking', 'jammed'),
    'media_player': ('on', 'off', 'playing', 'paused', 'idle', 'standby'),
    'timer': ('active', 'paused', 'idle'),
    'person': ('home', 'not_home'),
    'device_tracker': ('home', 'not_home'),
    'vacuum': ('cleaning', 'docked', 'idle', 'paused', 'returning', 'error'),
})
COMMAND_SUMMARY_UNKNOWN_STATES = ('unknown', 'unavailable')
COMMAND_SUMMARY_MODES = ('total', 'breakdown', *COMMAND_SUMMARY_UNKNOWN_STATES, *sorted({state for states in COMMAND_SUMMARY_STATES.values() for state in states}))
COMMAND_SUMMARY_MAX_STATES = 20
_COMMAND_ROUTING_INTENTS |= {'PhoenixStateSummary'}
_DECISION_TARGET_INTENTS['state_summary'] = 'PhoenixStateSummary'

# Aggregate routing telemetry has fixed dimensions and contains no transcripts.
ROUTING_STATISTICS_STORAGE_KEY = "phoenix_mcp_routing_statistics"
# AI Task counts the Evaluate action only: "decision" is an answer, "refused" a policy refusal.
ROUTING_STATISTICS_SURFACES = ("voice", "chat", "ai_task")
ROUTING_STATISTICS_OUTCOMES = ("local", "decision", "provider", "pending", "failed", "cancelled", "refused")
ROUTING_STATISTICS_FLUSH_INTERVAL = datetime.timedelta(seconds=30)

# Tool advice has separate qualification from direct-command execution.
TOOL_ROUTING_MODES = frozenset({"off", "observe", "advise"})
TOOL_ROUTING_SETTINGS = MappingProxyType({
    "chat": "agentcli_tool_routing_mode", "voice": "voice_agent_tool_routing_mode",
    "ai_task": "ai_task_tool_routing_mode",
})
TOOL_ROUTING_MAX_REVIEWS = 2
TOOL_ROUTING_MAX_REPLANS = 1
TOOL_ROUTING_MAX_BYTES = 262144
TOOL_ROUTING_MAX_GROUPS = 8
# (provider, endpoint, configured model, language): admitted returned models.
# Populate only from independent tool-selection and paired task qualification.
TOOL_ROUTING_QUALIFIED_MODELS: Mapping[tuple[str, str, str, str], tuple[str, ...]] = MappingProxyType({})
TOOL_ROUTING_REASONS = frozenset({
    "agreement", "alternative", "replan", "declined", "low_probability", "unqualified",
    "off", "limit", "last_round", "no_account", "unsupported_language", "context_unavailable",
    "catalog_unavailable", "catalog_changed", "context_changed", "authority_changed", "cancelled",
    "busy", "timeout", "unavailable", "malformed", "request_budget_exhausted", "budget_unavailable",
    "provider_context_limit", "authentication", "rate_limited", "http_error", "response_too_large",
    "model_unverified", "too_many_targets", "category_mismatch",
})

# Several tables above are completed in stages. Each becomes read-only here, once
# nothing can still add to it, and no name for its writable form survives.
COMMAND_ROUTING_ENTITY_FIXED_DATA = _sealed_groups(_COMMAND_ROUTING_ENTITY_FIXED_DATA)
COMMAND_ROUTING_FIXED_INTENT_DATA = _sealed_groups(_COMMAND_ROUTING_FIXED_INTENT_DATA)
COMMAND_ROUTING_FIXED_OBSERVATIONS = _sealed_groups(_COMMAND_ROUTING_FIXED_OBSERVATIONS)
COMMAND_ROUTING_LANGUAGES = MappingProxyType(_COMMAND_ROUTING_LANGUAGES)
COMMAND_ROUTING_NUMERIC_OPERATIONS = MappingProxyType(_COMMAND_ROUTING_NUMERIC_OPERATIONS)
COMMAND_ROUTING_NUMERIC_STATE = MappingProxyType(_COMMAND_ROUTING_NUMERIC_STATE)
COMMAND_ROUTING_ENTITY_COMMANDS = MappingProxyType(_COMMAND_ROUTING_ENTITY_COMMANDS)
COMMAND_ROUTING_ENTITY_INTENTS = MappingProxyType(_COMMAND_ROUTING_ENTITY_INTENTS)
DECISION_TARGET_INTENTS = MappingProxyType(_DECISION_TARGET_INTENTS)
DECISION_ENTITY_CHOICES = MappingProxyType(_DECISION_ENTITY_CHOICES)
DECISION_PERCENTAGE_PREVIEW_CHOICES = MappingProxyType(_DECISION_PERCENTAGE_PREVIEW_CHOICES)
DECISION_PERCENTAGE_EVIDENCE_MODIFIERS = MappingProxyType(_DECISION_PERCENTAGE_EVIDENCE_MODIFIERS)
COMMAND_ROUTING_INTENTS = frozenset(_COMMAND_ROUTING_INTENTS)
del (
    _COMMAND_ROUTING_ENTITY_FIXED_DATA,
    _COMMAND_ROUTING_FIXED_INTENT_DATA,
    _COMMAND_ROUTING_FIXED_OBSERVATIONS,
    _COMMAND_ROUTING_LANGUAGES,
    _COMMAND_ROUTING_NUMERIC_OPERATIONS,
    _COMMAND_ROUTING_NUMERIC_STATE,
    _COMMAND_ROUTING_ENTITY_COMMANDS,
    _COMMAND_ROUTING_ENTITY_INTENTS,
    _DECISION_TARGET_INTENTS,
    _DECISION_ENTITY_CHOICES,
    _DECISION_PERCENTAGE_PREVIEW_CHOICES,
    _DECISION_PERCENTAGE_EVIDENCE_MODIFIERS,
    _COMMAND_ROUTING_INTENTS,
)
del _domain, _position, _intent, _category, _values, _language, _source
