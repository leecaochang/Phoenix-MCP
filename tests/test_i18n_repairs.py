"""Backend localization repairs: spoken replies, provider reasons and summaries.

Each case pins text that used to reach an operator in English, or in an English
grammatical form, through a backend transport the panel cannot reformat.
"""

from __future__ import annotations

from custom_components.phoenix_mcp.admin import changes
from custom_components.phoenix_mcp.tools import restore as restore_tools
from custom_components.phoenix_mcp.routing.command_display import reading_text, state_label
from custom_components.phoenix_mcp.routing.command_summary import summary_text
from custom_components.phoenix_mcp.routing.decision_provider import decision_error_reason
from custom_components.phoenix_mcp.catalog_text import LocalizedReason, spoken_list, spoken_sentences
from custom_components.phoenix_mcp.tools.radio import _zigbee_group_summary_op

_TRANSLATIONS = {
    "component.light.entity_component._.state.on": "An",
    "component.light.entity_component._.state.off": "Aus",
    "component.binary_sensor.entity_component.door.state.on": "Offen",
    "component.binary_sensor.entity_component._.state.on": "Erkannt",
}


def test_spoken_lists_and_sentences_follow_the_language() -> None:
    assert spoken_list(["a", "b"], "de") == "a, b"
    assert spoken_list(["台灯", "吊灯"], "zh-Hans") == "台灯、吊灯"
    assert spoken_sentences(["Eins.", "Zwei."], "de") == "Eins. Zwei."
    assert spoken_sentences(["一。", "二。"], "ja") == "一。二。"


def test_state_labels_prefer_the_device_class() -> None:
    assert state_label(_TRANSLATIONS, "binary_sensor", "on", "door") == "Offen"
    assert state_label(_TRANSLATIONS, "binary_sensor", "on", "motion") == "Erkannt"
    assert state_label(_TRANSLATIONS, "light", "dim", None) is None


def test_readings_speak_translated_states_and_local_numbers() -> None:
    assert reading_text("on", {}, "light", "de", _TRANSLATIONS) == "An"
    assert reading_text("21.5", {"unit_of_measurement": "°C"}, "sensor", "de", {}) == "21,5 °C"
    # Option values are user data, never a translatable enum.
    assert reading_text("on", {}, "input_select", "de", _TRANSLATIONS) == "on"


def test_state_summary_speaks_translated_states() -> None:
    proposal = {"entity_ids": ["light.a", "light.b", "light.c"], "summary_mode": "breakdown",
                "summary_scope": {"domain": "light", "device_class": "", "area_id": ""}}
    rows = [{"entity_id": "light.a", "state": "on"}, {"entity_id": "light.b", "state": "off"},
            {"entity_id": "light.c", "state": "off"}]
    text = summary_text(proposal, rows, "de", _TRANSLATIONS)
    assert "Aus: 2" in text and "An: 1" in text
    assert "off: 2" not in text
    counted = summary_text({**proposal, "summary_mode": "on"}, rows, "de", _TRANSLATIONS)
    assert "An" in counted and " on" not in counted


def test_localized_reasons_keep_their_english_and_name_a_key() -> None:
    reason = LocalizedReason("Provider returned HTTP 503.", "providerHttpStatus", status=503)
    assert reason == "Provider returned HTTP 503."
    assert (reason.key, reason.params) == ("providerHttpStatus", {"status": 503})


def test_decision_errors_become_operator_sentences() -> None:
    assert decision_error_reason("timeout").key == "decisionProviderTimeout"
    assert decision_error_reason("authentication").key == "providerKeyRejected"
    other = decision_error_reason("typesafe_quota")
    assert (other.key, other.params) == ("decisionProviderCheckFailed", {"detail": "typesafe_quota"})


def test_restore_refusal_keys_match_the_restore_executor() -> None:
    """The admin view maps these exact sentences; a reworded executor would silently drop the key."""
    source = open(restore_tools.__file__, encoding="utf-8").read().replace('"\n            "', "")
    for sentence in (changes._RESTORE_NO_CONFIG_SIDE, changes._RESTORE_YAML_TAGS):
        assert sentence in source, sentence


class _Change:
    def __init__(self, tool_name: str, operation: str) -> None:
        self.tool_name, self.operation = tool_name, operation


def test_zigbee_group_summaries_name_each_operation() -> None:
    assert _zigbee_group_summary_op(_Change("create_zigbee_group", "create")) == "create"
    assert _zigbee_group_summary_op(_Change("set_zigbee_group_members", "add")) == "add_members"
    assert _zigbee_group_summary_op(_Change("set_zigbee_group_members", "remove")) == "remove_members"
    # Removing the whole group is not the same sentence as removing its members.
    assert _zigbee_group_summary_op(_Change("remove_zigbee_group", "remove")) == "delete"
