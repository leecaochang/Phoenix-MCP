"""get_dashboard_config card search, the deeper layout scrub and YAML-mode withholding.

Search walks the scrubbed layout: nested cards under card container keys match
on their own, typed features and rows stay with their card, hidden values never
match, and every returned path addresses the stored layout for patch_dashboard.
Ordinary reads keep their default target, no longer empty deep containers, and
never return a YAML-mode layout whose !secret values Home Assistant resolved.
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import DASHBOARD_SCRUB_MAX_DEPTH, DASHBOARD_SEARCH_MAX_MATCHES
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.tool_common import resolve_json_path
from custom_components.phoenix_mcp.tools.dashboard_search import Criteria, parse_criteria, search_layout
from custom_components.phoenix_mcp.version_store import VersionStore
from custom_components.phoenix_mcp.ws_dispatch import (
    async_get_lovelace_config, async_save_lovelace_config, async_ws_command,
)
from tests.runtime_fixtures import mesa_off_store

LAYOUT = {"views": [
    {"title": "Home", "badges": ["light.porch", {"type": "entity", "entity": "sensor.outside"}],
     "header": {"card": {"type": "markdown", "content": "Welcome home"}},
     "sections": [{"type": "grid", "cards": [
         {"type": "heading", "heading": "Kitchen"},
         {"type": "tile", "entity": "light.kitchen", "features": [{"type": "light-brightness"}]},
         {"type": "vertical-stack", "cards": [{"type": "horizontal-stack", "cards": [
             {"type": "button", "entity": "light.island", "tap_action": {"action": "toggle"}}]}]},
         {"type": "heading", "heading": "Hall"},
         {"type": "entities", "entities": [{"type": "attribute", "entity": "light.hall", "attribute": "brightness"}]},
     ]}]},
    {"cards": [
        {"type": "custom:group-card", "groups": [{"cards": [
            {"card": {"type": "tile", "entity": "switch.fan"}},
            {"type": "custom:mushroom-light-card", "entity": "light.desk"}]}]},
        {"type": "custom:tabbed-card", "tabs": [{"attributes": {"label": "Climate"},
                                                 "card": {"type": "thermostat", "entity": "climate.hall"}}]},
        {"type": "custom:button-card", "name": "Lamp", "custom_fields": {
            "stack": {"card": {"type": "glance", "entities": ["light.lamp"]}},
            "chip": {"type": "custom:mushroom-chips-card", "chips": [{"type": "entity", "entity": "sensor.co2"}]},
            "label": "[[[ return 'Lamp' ]]]"}},
        {"type": "custom:state-switch", "entity": "input_select.mode",
         "states": {"day": {"type": "markdown", "content": "Daytime {{ states('sensor.lux') }} 1.5"}}},
        {"type": "conditional", "conditions": [], "card": {"type": "picture-elements", "image": "/local/a.png",
                                                            "elements": [{"type": "state-icon", "entity": "light.attic"}]}},
    ]},
]}


def _search(layout=LAYOUT, **criteria):
    parsed, error = parse_criteria(criteria)
    assert error is None and parsed is not None
    return search_layout(layout, parsed)


def _paths(matches):
    return [tuple(match["path"]) for match in matches]


class TestCriteria:
    def test_absent_and_blank_criteria_mean_an_ordinary_read(self):
        assert parse_criteria({}) == (None, None)
        assert parse_criteria({"query": "  ", "heading": ""}) == (None, None)

    def test_values_are_trimmed_and_entity_patterns_lowered(self):
        assert parse_criteria({"query": " Lamp ", "entity_id": "Light.*"})[0] == Criteria(query="Lamp", entity_id="light.*")

    @pytest.mark.parametrize("args", [
        {"query": 5}, {"entity_id": "kitchen"}, {"entity_id": "light.kitchen lamp"}, {"heading": "x" * 201},
        {"card_type": "badge", "heading": "Kitchen"},
    ])
    def test_malformed_criteria_are_refused(self, args):
        criteria, error = parse_criteria(args)
        assert criteria is None and error


class TestNesting:
    def test_custom_containers_reach_nested_cards_and_paths_address_the_stored_layout(self):
        matches, problems = _search(card_type="tile")
        assert problems == []
        assert _paths(matches) == [
            ("views", 0, "sections", 0, "cards", 1),
            ("views", 1, "cards", 0, "groups", 0, "cards", 0, "card"),
        ]
        for name, expected in [("mushroom-light-card", ("views", 1, "cards", 0, "groups", 0, "cards", 1)),
                               ("thermostat", ("views", 1, "cards", 1, "tabs", 0, "card")),
                               ("glance", ("views", 1, "cards", 2, "custom_fields", "stack", "card")),
                               ("mushroom-chips-card", ("views", 1, "cards", 2, "custom_fields", "chip")),
                               ("markdown", None),
                               ("button", ("views", 0, "sections", 0, "cards", 2, "cards", 0, "cards", 0)),
                               ("picture-elements", ("views", 1, "cards", 4, "card"))]:
            found = _paths(_search(card_type=name)[0])
            if expected is not None:
                assert found == [expected], name
        markdown = _paths(_search(card_type="markdown")[0])
        assert ("views", 0, "header", "card") in markdown
        assert ("views", 1, "cards", 3, "states", "day") in markdown
        for match in _search(entity_id="*")[0]:
            container, key, reason = resolve_json_path(LAYOUT, match["path"])
            assert reason is None and container[key] == match["config"]

    def test_matches_name_their_view_and_enclosing_cards(self):
        matches, _ = _search(card_type="tile")
        assert [m.get("view") for m in matches] == ["Home", None]
        assert [m.get("within") for m in matches] == [None, ["custom:group-card"]]
        button, = _search(card_type="button")[0]
        assert button["within"] == ["vertical-stack", "horizontal-stack"]
        assert {m["view"] for m in _search(card_type="badge")[0]} == {"Home"}

    def test_features_rows_and_elements_stay_with_their_card(self):
        assert _search(card_type="light-brightness")[0] == []
        assert _search(card_type="attribute")[0] == []
        assert _search(card_type="state-icon")[0] == []
        assert _paths(_search(entity_id="light.hall")[0]) == [("views", 0, "sections", 0, "cards", 4)]
        assert _paths(_search(entity_id="light.attic")[0]) == [("views", 1, "cards", 4, "card")]
        assert _paths(_search(entity_id="sensor.co2")[0]) == [("views", 1, "cards", 2, "custom_fields", "chip")]

    def test_a_parent_matches_on_its_own_content_only(self):
        matches, _ = _search(entity_id="light.island")
        assert _paths(matches) == [("views", 0, "sections", 0, "cards", 2, "cards", 0, "cards", 0)]
        matches, _ = _search(query="Lamp")
        assert _paths(matches) == [("views", 1, "cards", 2), ("views", 1, "cards", 2, "custom_fields", "stack", "card")]
        assert [m["path"] for m in matches[0]["matched"]] == [["name"], ["custom_fields", "label"]]
        assert [m["path"] for m in matches[1]["matched"]] == [["entities", 0]]

    def test_headings_govern_the_cards_after_them(self):
        matches, _ = _search(heading="kitchen")
        assert [m["type"] for m in matches] == ["heading", "tile", "vertical-stack", "horizontal-stack", "button"]
        assert {m["heading"] for m in matches} == {"Kitchen"}
        assert [m["type"] for m in _search(heading="hall", entity_id="light.*")[0]] == ["entities"]

    def test_badges_are_listed_and_matched_by_reference(self):
        badges, _ = _search(card_type="badge")
        assert [(m["kind"], m["path"]) for m in badges] == [
            ("badge", ["views", 0, "badges", 0]), ("badge", ["views", 0, "badges", 1])]
        assert _paths(_search(entity_id="sensor.outside")[0]) == [("views", 0, "badges", 1)]
        assert all(m["kind"] == "card" for m in _search(card_type="entity")[0])

    def test_entity_wildcards_match_whole_ids_and_skip_decimals(self):
        assert {m["config"].get("entity") for m in _search(entity_id="light.*", card_type="tile")[0]} == {"light.kitchen"}
        assert _search(entity_id="light.kit")[0] == []
        assert _paths(_search(entity_id="sensor.lux")[0]) == [("views", 1, "cards", 3, "states", "day")]
        decimals = {"views": [{"cards": [{"type": "markdown", "content": "Version 1.5 of 2.0"}]}]}
        assert _search(decimals, entity_id="*")[0] == [] and _search(decimals, entity_id="1.*")[0] == []

    def test_query_reads_underscores_as_spaces(self):
        layout = {"views": [{"cards": [{"type": "tile", "entity": "light.studio_desk_lamp"},
                                       {"type": "tile", "entity": "light.desk"}]}]}
        matches, _ = _search(layout, query="Studio Desk Lamp")
        assert _paths(matches) == [("views", 0, "cards", 0)]
        assert matches[0]["matched"] == [{"path": ["entity"], "value": "light.studio_desk_lamp"}]
        assert _paths(_search(layout, query="studio_desk")[0]) == [("views", 0, "cards", 0)]

    def test_custom_prefix_is_optional_for_card_type(self):
        assert _paths(_search(card_type="button-card")[0]) == _paths(_search(card_type="custom:button-card")[0])


class TestShapes:
    @pytest.mark.parametrize("layout,where", [
        ({"views": {"a": 1}}, ["views"]),
        ({"views": ["x"]}, ["views", 0]),
        ({"views": [{"sections": {"a": 1}}]}, ["views", 0, "sections"]),
        ({"views": [{"sections": ["x"]}]}, ["views", 0, "sections", 0]),
        ({"views": [{"cards": {"a": 1}}]}, ["views", 0, "cards"]),
        ({"views": [{"cards": ["x", {"no": "type"}]}]}, ["views", 0, "cards", 0]),
        ({"views": [{"badges": "light.a"}]}, ["views", 0, "badges"]),
        ({"views": [{"header": ["x"]}]}, ["views", 0, "header"]),
        ({"views": [{"header": {"card": "x"}}]}, ["views", 0, "header", "card"]),
        ({"strategy": {"type": "original-states"}}, []),
        ({"views": [{"strategy": {"type": "x"}}]}, ["views", 0]),
        ("not a layout", []),
    ])
    def test_wrong_shapes_are_reported_not_searched_silently(self, layout, where):
        matches, problems = _search(layout, query="x")
        assert matches == []
        assert problems and problems[0]["path"] == where

    def test_an_untyped_view_with_sections_and_cards_is_searched_whole(self):
        layout = {"views": [{"cards": [{"type": "tile", "entity": "light.a"}],
                             "sections": [{"cards": [{"type": "tile", "entity": "light.b"}]}]}]}
        assert len(_search(layout, card_type="tile")[0]) == 2


def _key(cap="allow", domains=("light", "switch", "climate")):
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="search", access_key_hash="x", created_at=utcnow(), created_by="u",
        cap_lovelace_write=cap, permissions=PermissionTree(domains={d: PermissionNode(state="GREEN") for d in domains}),
    )


def _data():
    return PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())


def _body(content):
    return json.loads(content["content"][0]["text"])


async def _call(hass, name, args, key=None, data=None):
    return await _call_tool(name, args, key or _key(), hass, data or _data())


@pytest.fixture
async def boards(hass, hass_admin_user):
    for entity_id in ("light.kitchen", "light.island", "light.hall", "switch.fan", "climate.hall", "sensor.secret"):
        hass.states.async_set(entity_id, "on")
    with open(os.path.join(hass.config.config_dir, "secrets.yaml"), "w") as handle:
        handle.write("grafana: https://user:hunter2@grafana.example/d/1\n")
    with open(os.path.join(hass.config.config_dir, "yaml_board.yaml"), "w") as handle:
        handle.write("views:\n  - cards:\n      - type: iframe\n        url: !secret grafana\n")
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"dashboards": {
        "yaml-board": {"mode": "yaml", "filename": "yaml_board.yaml", "title": "Yaml board"}}}})
    for url_path in ("board-a", "board-empty"):
        await async_ws_command(hass, "lovelace/dashboards/create", {"url_path": url_path, "title": url_path})
    await async_save_lovelace_config(hass, None, {"views": [{"cards": [
        {"type": "markdown", "content": "Door {{ states('sensor.secret') }} and light.kitchen"},
        {"type": "entities", "entities": ["sensor.secret", "light.hall"]}]}]})
    await async_save_lovelace_config(hass, "board-a", LAYOUT)
    return hass


class TestToolSearch:
    async def test_default_target_is_unchanged_and_one_dashboard_carries_its_hash(self, boards):
        content, outcome, _ = await _call(boards, "get_dashboard_config", {"entity_id": "light.kitchen"})
        assert outcome == "allowed", content
        body = _body(content)
        assert body["url_path"] is None and [d["url_path"] for d in body["dashboards"]] == [None]
        assert body["content_hash"] == body["dashboards"][0]["content_hash"]
        assert body["match_count"] == 1 and body["truncated"] is False

    async def test_hidden_and_nonexistent_entities_are_indistinguishable(self, boards):
        hidden = _body((await _call(boards, "get_dashboard_config", {"entity_id": "sensor.secret"}))[0])
        missing = _body((await _call(boards, "get_dashboard_config", {"entity_id": "sensor.nowhere"}))[0])
        assert hidden["match_count"] == missing["match_count"] == 0
        assert _body((await _call(boards, "get_dashboard_config", {"query": "secret"}))[0])["match_count"] == 0
        assert _body((await _call(boards, "get_dashboard_config", {"query": "redacted"}))[0])["match_count"] == 0
        body = _body((await _call(boards, "get_dashboard_config", {"query": "door"}))[0])
        config = json.dumps(body["dashboards"][0]["matches"][0]["config"])
        assert "sensor.secret" not in config and "light.kitchen" in config and "<redacted>" in config

    async def test_template_property_access_is_not_an_entity_id(self, boards):
        await async_save_lovelace_config(boards, None, {"views": [{"title": "Main", "cards": [
            {"type": "custom:button-card", "entity": "light.kitchen", "custom_fields": {
                "status": "[[[ return entity.state === 'on' && states['sensor.secret'].state ]]]"}},
            {"type": "markdown", "content": "{{ hass.user }} and sensor.secret"}]}]})
        body = _body((await _call(boards, "get_dashboard_config", {"query": "entity.state"}))[0])
        match = body["dashboards"][0]["matches"][0]
        status = match["config"]["custom_fields"]["status"]
        assert body["match_count"] == 1 and match["view"] == "Main"
        assert "entity.state" in status and "sensor.secret" not in status and "<redacted>" in status
        stars = _body((await _call(boards, "get_dashboard_config", {"entity_id": "*"}))[0])
        assert [m["type"] for m in stars["dashboards"][0]["matches"]] == ["custom:button-card"]
        for hidden in ("sensor.secret", "sensor.*", "entity.state"):
            assert _body((await _call(boards, "get_dashboard_config", {"entity_id": hidden}))[0])["match_count"] == 0

    async def test_all_dashboards_reports_what_it_could_not_search(self, boards):
        content, outcome, _ = await _call(boards, "get_dashboard_config", {"card_type": "tile", "all_dashboards": True})
        assert outcome == "allowed", content
        body = _body(content)
        assert [(d["url_path"], d["match_count"]) for d in body["dashboards"]] == [(None, 0), ("board-a", 2)]
        reasons = {row["url_path"]: row["reason"] for row in body["not_searched"]}
        assert set(reasons) == {"yaml-board", "board-empty"}
        assert "yaml_board.yaml" in reasons["yaml-board"] and "auto-generated" in reasons["board-empty"]
        assert "hunter2" not in content["content"][0]["text"]
        raw = await async_get_lovelace_config(boards, "board-a")
        for match in body["dashboards"][1]["matches"]:
            container, key, reason = resolve_json_path(raw, match["path"])
            assert reason is None and container[key]["type"] == "tile"

    async def test_search_to_patch_and_a_stale_hash(self, boards):
        body = _body((await _call(boards, "get_dashboard_config", {"entity_id": "light.island", "url_path": "board-a"}))[0])
        match = body["dashboards"][0]["matches"][0]
        change = {"target": {"url_path": "board-a"}, "expected_hash": body["content_hash"],
                  "changes": [{"path": [*match["path"], "tap_action", "action"], "kind": "set", "value": "more-info"}]}
        content, outcome, _ = await _call(boards, "patch_dashboard", change)
        assert outcome == "allowed", content
        raw = await async_get_lovelace_config(boards, "board-a")
        container, key, _ = resolve_json_path(raw, match["path"])
        assert container[key]["tap_action"] == {"action": "more-info"}
        content, outcome, _ = await _call(boards, "patch_dashboard", change)
        assert outcome == "invalid_request" and "changed" in content["content"][0]["text"]

    async def test_results_are_bounded_across_dashboards(self, boards):
        many = {"views": [{"cards": [{"type": "tile", "entity": "light.kitchen"}] * (DASHBOARD_SEARCH_MAX_MATCHES - 10)}]}
        await async_save_lovelace_config(boards, None, many)
        await async_save_lovelace_config(boards, "board-a", many)
        body = _body((await _call(boards, "get_dashboard_config", {"card_type": "tile", "all_dashboards": True}))[0])
        assert body["match_count"] == 2 * (DASHBOARD_SEARCH_MAX_MATCHES - 10) and body["truncated"] is True
        assert sum(len(d["matches"]) for d in body["dashboards"]) == DASHBOARD_SEARCH_MAX_MATCHES

    @pytest.mark.parametrize("args,needle", [
        ({"all_dashboards": True}, "at least one"),
        ({"all_dashboards": True, "query": "x", "url_path": "board-a"}, "not both"),
        ({"all_dashboards": "yes", "query": "x"}, "boolean"),
        ({"querry": "x"}, "query"),
    ])
    async def test_argument_refusals(self, boards, args, needle):
        content, outcome, _ = await _call(boards, "get_dashboard_config", args)
        assert outcome == "invalid_request" and needle in content["content"][0]["text"]

    async def test_capability_denial_precedes_argument_diagnostics(self, boards):
        content, outcome, _ = await _call(boards, "get_dashboard_config", {"all_dashboards": "yes"}, _key("deny"))
        assert outcome == "denied" and "boolean" not in content["content"][0]["text"]


class TestYamlMode:
    @pytest.mark.parametrize("args", [{"url_path": "yaml-board"}, {"url_path": "yaml-board", "query": "grafana"}])
    async def test_yaml_layouts_are_withheld(self, boards, args):
        content, outcome, _ = await _call(boards, "get_dashboard_config", args)
        text = content["content"][0]["text"]
        assert outcome == "invalid_request" and "hunter2" not in text
        assert "YAML-mode" in text and "yaml_board.yaml" in text and "get_yaml_config" in text


class TestDepth:
    async def test_common_nesting_is_no_longer_emptied(self, boards):
        body = _body((await _call(boards, "get_dashboard_config", {"url_path": "board-a"}))[0])
        button = body["config"]["views"][0]["sections"][0]["cards"][2]["cards"][0]["cards"][0]
        assert button["tap_action"] == {"action": "toggle"} and "warnings" not in body

    async def test_beyond_the_bound_is_a_reported_placeholder_writes_refuse(self, boards):
        deep: dict = {"type": "markdown", "content": "bottom"}
        for _ in range(DASHBOARD_SCRUB_MAX_DEPTH):
            deep = {"type": "vertical-stack", "cards": [deep]}
        stored = {"views": [{"cards": [deep]}]}
        await async_save_lovelace_config(boards, "board-a", stored)
        body = _body((await _call(boards, "get_dashboard_config", {"url_path": "board-a"}))[0])
        assert body["warnings"] and "<redacted>" in json.dumps(body["config"])
        content, outcome, _ = await _call(boards, "set_dashboard_config", {"url_path": "board-a", "config": body["config"]})
        assert outcome == "invalid_request", content
        assert await async_get_lovelace_config(boards, "board-a") == stored
        search = _body((await _call(boards, "get_dashboard_config", {"url_path": "board-a", "query": "bottom"}))[0])
        assert search["match_count"] == 0 and search["warnings"]

    async def test_card_tools_refuse_a_scrubbed_card(self, boards):
        before = await async_get_lovelace_config(boards, None)
        body = _body((await _call(boards, "get_dashboard_config", {"query": "Door"}))[0])
        card = body["dashboards"][0]["matches"][0]["config"]
        assert "<redacted>" in json.dumps(card)
        for tool, args in [("edit_dashboard_card", {"card_index": 0}), ("add_dashboard_card", {})]:
            content, outcome, _ = await _call(boards, tool, {"view_index": 0, "card": card, **args})
            assert outcome == "invalid_request" and "placeholder" in content["content"][0]["text"], tool
        assert await async_get_lovelace_config(boards, None) == before


def test_dashboard_tools_steer_to_search_and_trust_a_saved_write():
    """Live runs read whole layouts because these words said to; pin the steering."""
    from custom_components.phoenix_mcp.skill_view import PHOENIX_SKILL_MARKDOWN
    from custom_components.phoenix_mcp.tool_defs import _SYSTEM_TOOL_DEFS
    descriptions = {d["name"]: d["description"] for d in _SYSTEM_TOOL_DEFS}
    assert "so do not also read the whole layout" in descriptions["get_dashboard_config"]
    for name in ("add_dashboard_card", "edit_dashboard_card", "delete_dashboard_card", "patch_dashboard"):
        text = descriptions[name]
        assert "get_dashboard_config search" in text and "not a full read" in text, name
        assert "do not re-read to check it" in text, name
        assert "Call get_dashboard_config first" not in text, name
    assert "so do\n  not also read the whole layout" in PHOENIX_SKILL_MARKDOWN
    assert "do not read or search again to check\n  it" in PHOENIX_SKILL_MARKDOWN
