"""The boundary between Phoenix and mesa-core: host callbacks, storage copies and second-layer guards.

mesa-core calls Phoenix's host callbacks from its own worker threads, so every callback that
reads a registry or the state machine runs on the event loop. The storage backend hands out
deep copies, as mesa-core's own memory backend does. Two guards stand behind another check
today; these tests keep each one meaningful on its own.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

import custom_components.phoenix_mcp.mesa as mesa_module
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.mesa import (
    PhoenixMesaBackend,
    async_setup_mesa,
    evaluate_service_entities,
)
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.mesa_tools import ScopedProfileStore, _build_validity_context


def _key(entities: dict[str, str]) -> AccessKeyRecord:
    return AccessKeyRecord(
        id="k", name="k", access_key_hash="x", created_at=utcnow(), created_by="t",
        permissions=PermissionTree(entities={e: PermissionNode(state=s) for e, s in entities.items()}),
    )


@pytest.fixture
def lamp(hass: HomeAssistant) -> str:
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", "d")})
    entity = er.async_get(hass).async_get_or_create("light", "test", "lamp", config_entry=entry, device_id=device.id)
    hass.states.async_set(entity.entity_id, "on")
    return entity.entity_id


@pytest.mark.parametrize("builder, argument", [
    ("_build_get_entity_device", "entity"),
    ("_build_get_device_parent", "device"),
    ("_build_get_entity_integration", "entity"),
    ("_build_get_state", "entity"),
    ("_build_get_calendar_events", "calendar"),
])
async def test_host_callbacks_read_on_the_event_loop_from_a_worker_thread(hass: HomeAssistant, lamp, monkeypatch, builder, argument):
    marshalled: list[str] = []
    real = mesa_module.run_callback_threadsafe

    def counting(loop, callback, *args):
        marshalled.append(getattr(callback, "__name__", "?"))
        return real(loop, callback, *args)

    monkeypatch.setattr(mesa_module, "run_callback_threadsafe", counting)
    hass.states.async_set("calendar.home", "off")
    device_id = er.async_get(hass).async_get(lamp).device_id
    value = {"entity": lamp, "device": device_id, "calendar": "calendar.home"}[argument]

    callback = getattr(mesa_module, builder)(hass)
    on_loop = callback(value)
    from_thread = await hass.async_add_executor_job(callback, value)
    assert from_thread == on_loop
    assert len(marshalled) == 1, "only the call from a worker thread hops to the loop"


async def test_the_validity_context_reads_on_the_event_loop(hass: HomeAssistant, lamp, monkeypatch):
    seen: list[bool] = []
    real = er.async_get
    context = _build_validity_context(hass)

    def entities(hass_):
        seen.append(threading.get_ident() == hass.loop_thread_id)
        return real(hass_)

    monkeypatch.setattr("custom_components.phoenix_mcp.mesa_tools.er_mod.async_get", entities)
    result = await hass.async_add_executor_job(context, lamp)
    assert lamp in result["known_entity_ids"]
    assert seen and all(seen)


def test_the_backend_never_shares_nested_values():
    backend = PhoenixMesaBackend({"light.a": {"semantic_profile": {"semantic_tags": ["a"]}}})
    read = backend.read("light.a")
    read["semantic_profile"]["semantic_tags"].append("changed")
    assert backend.read("light.a") == {"semantic_profile": {"semantic_tags": ["a"]}}

    written = {"semantic_profile": {"semantic_tags": ["b"]}}
    backend.write("light.b", written)
    written["semantic_profile"]["semantic_tags"].append("changed")
    assert backend.read("light.b")["semantic_profile"]["semantic_tags"] == ["b"]

    snapshot = backend.snapshot()
    backend.read("light.a")
    backend._data["light.a"]["semantic_profile"]["semantic_tags"].append("after")
    assert snapshot["light.a"]["semantic_profile"]["semantic_tags"] == ["a"]
    backend.restore(snapshot)
    snapshot["light.a"]["semantic_profile"]["semantic_tags"].append("later")
    assert backend.read("light.a")["semantic_profile"]["semantic_tags"] == ["a"]


async def test_an_allowed_result_without_a_control_mode_is_refused(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    runtime.enforcer = SimpleNamespace(evaluate=lambda **_: SimpleNamespace(
        allowed=True, evaluated_control_mode=None, rule_applied=None, warnings=[], reason="",
        declared_limits=None, measured_equivalents=None))
    verdict = evaluate_service_entities(
        runtime, "enforced", _key({"light.a": "GREEN"}), ["light.a"],
        domain="light", service="turn_on", service_data={}, session_id="s",
    )
    assert verdict.allowed == []
    assert [(entity, rule) for entity, rule, _reason in verdict.blocked] == [("light.a", "policy_unavailable")]


async def test_the_scoped_store_hides_a_stored_profile_outside_the_key(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "advisory")
    for entity_id in ("light.mine", "lock.vault"):
        hass.states.async_set(entity_id, "on")
        runtime.store.set(entity_id, SemanticProfile.from_dict(
            entity_id, {"semantic_profile": {"semantic_tags": ["lighting.task"]}}, default_origin=MetadataOrigin.USER))
    scoped = ScopedProfileStore(runtime.store, _key({"light.mine": "GREEN"}), hass)
    assert scoped.get("light.mine") is not None
    assert scoped.get("lock.vault") is None
    assert scoped.entity_keys() == ["light.mine"]
