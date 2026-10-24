"""list_labels and set_label_or_category, scoped by what carries each label or category.

A label is listed when something the key can read carries it and changed only
when the key can write everything that carries it; unused ones and new ones need
pass-through. Missing and not-writable answer alike.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool, async_restore_version
from custom_components.phoenix_mcp.version_store import VersionStore
from tests.runtime_fixtures import mesa_off_store


@pytest.fixture
def home(hass):
    entities, labels, categories = er.async_get(hass), lr.async_get(hass), cr.async_get(hass)
    ids = {}
    for domain, name in (("light", "kitchen"), ("light", "secret"), ("automation", "wake")):
        entry = entities.async_get_or_create(domain, "test", name, suggested_object_id=name)
        hass.states.async_set(entry.entity_id, "on")
        ids[name] = entry.entity_id
    made = {name: labels.async_create(name) for name in ("Holiday", "Secret", "Shared", "Unused")}
    entities.async_update_entity(ids["kitchen"], labels={made["Holiday"].label_id, made["Shared"].label_id})
    entities.async_update_entity(ids["secret"], labels={made["Secret"].label_id, made["Shared"].label_id})
    morning = categories.async_create(scope="automation", name="Morning")
    entities.async_update_entity(ids["wake"], categories={"automation": morning.category_id})
    return {**{name: label.label_id for name, label in made.items()}, "morning": morning.category_id, **ids}


def _key(*, pass_through: bool = False) -> AccessKeyRecord:
    tree = PermissionTree(entities={"light.kitchen": PermissionNode(state="GREEN"),
                                    "automation.wake": PermissionNode(state="GREEN")})
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                           permissions=tree, pass_through=pass_through,
                           cap_registry_read="allow", cap_registry_write="allow")


def _data() -> PhoenixData:
    return PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(), mesa=None,
                       versions=VersionStore())


async def _call(hass, tool, args, key, data=None):
    content, outcome, _ = await _call_tool(tool, args, key, hass, data or _data())
    text = content["content"][0]["text"]
    return (json.loads(text) if text.startswith(("{", "[")) else text), outcome


async def test_a_scoped_key_lists_only_what_it_can_see(hass, home):
    body, outcome = await _call(hass, "list_labels", {}, _key())
    assert outcome == "allowed"
    assert {row["name"]: row["entities"] for row in body["labels"]} == {"Holiday": 1, "Shared": 1}
    assert body["categories"] == {"automation": [{"category_id": home["morning"], "name": "Morning", "entities": 1}]}
    body, _ = await _call(hass, "list_labels", {}, _key(pass_through=True))
    assert [row["name"] for row in body["labels"]] == ["Holiday", "Secret", "Shared", "Unused"]


async def test_a_label_the_key_fully_writes_is_renamed_and_restorable(hass, home):
    data = _data()
    body, outcome = await _call(hass, "set_label_or_category", {"kind": "label", "operation": "update", "id": home["Holiday"],
                                                                "changes": {"name": "Festive", "color": "red"}}, _key(), data)
    assert outcome == "allowed" and body["verified"] is True and body["updated"]["name"] == "Festive"
    record = data.versions.list_for("label", home["Holiday"])[0]
    assert record.before["name"] == "Holiday" and record.after["name"] == "Festive"
    result = await async_restore_version(record, "admin", hass, data, side="before")
    assert result[1] == "allowed" and lr.async_get(hass).async_get_label(home["Holiday"]).name == "Holiday"


@pytest.mark.parametrize("label", ["Shared", "Unused", "Secret", "missing"])
async def test_a_label_carried_by_something_hidden_or_by_nothing_reads_as_not_found(hass, home, label):
    text, outcome = await _call(hass, "set_label_or_category", {"kind": "label", "operation": "delete",
                                                                "id": home.get(label, "nope")}, _key())
    assert outcome == "denied" and text == "Label or category not found or not writable."
    assert len(lr.async_get(hass).async_list_labels()) == 4


async def test_creating_and_removing_unused_labels_needs_pass_through(hass, home):
    text, outcome = await _call(hass, "set_label_or_category", {"kind": "label", "operation": "create",
                                                                "changes": {"name": "Garden"}}, _key())
    assert outcome == "denied" and "pass-through" in text
    body, outcome = await _call(hass, "set_label_or_category", {"kind": "label", "operation": "create",
                                                                "changes": {"name": "Garden"}}, _key(pass_through=True))
    assert outcome == "allowed" and lr.async_get(hass).async_get_label(body["id"]).name == "Garden"
    body, outcome = await _call(hass, "set_label_or_category", {"kind": "label", "operation": "delete",
                                                                "id": home["Unused"]}, _key(pass_through=True))
    assert outcome == "allowed" and lr.async_get(hass).async_get_label(home["Unused"]) is None


async def test_a_category_is_renamed_within_its_scope_and_names_must_be_free(hass, home):
    body, outcome = await _call(hass, "set_label_or_category", {"kind": "category", "operation": "update", "scope": "automation",
                                                                "id": home["morning"], "changes": {"name": "Wake up"}}, _key())
    assert outcome == "allowed" and body["id"] == home["morning"]
    assert cr.async_get(hass).async_get_category(scope="automation", category_id=home["morning"]).name == "Wake up"
    text, outcome = await _call(hass, "set_label_or_category", {"kind": "label", "operation": "update", "id": home["Holiday"],
                                                                "changes": {"name": "shared"}}, _key())
    assert outcome == "invalid_request" and "already exists" in text
    text, outcome = await _call(hass, "set_label_or_category", {"kind": "category", "operation": "update",
                                                                "id": home["morning"], "changes": {"name": "x"}}, _key())
    assert outcome == "invalid_request" and "scope" in text
