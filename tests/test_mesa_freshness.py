"""MESA state that agents and automations read stays current.

Two host-side gaps: a lease that runs out fires its natural-expiry event only
when Phoenix asks mesa-core to sweep, and the control mode shown to agents
must be the one a write is evaluated against, after an active temporal window
and the restricted privacy level have tightened it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord
from custom_components.phoenix_mcp.const import MESA_LEASE_SWEEP_INTERVAL
from custom_components.phoenix_mcp.mesa import (
    async_setup_mesa,
    async_track_lease_expiry,
    entity_control_mode,
)
from custom_components.phoenix_mcp.mesa_core import ControlMode, MetadataOrigin, SemanticProfile


def _access_key() -> AccessKeyRecord:
    return AccessKeyRecord(id="fresh", name="Fresh", access_key_hash="x", created_at=utcnow(),
                           created_by="admin")


async def test_natural_expiry_fires_without_another_lease_operation(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    events = []
    hass.bus.async_listen("phoenix_mcp_mesa_lease_expired", lambda e: events.append(e.data))
    # Granted a minute ago for ten seconds, so it has already run out.
    granted = runtime.lease_manager.request(
        ["light.a"], 10, session_id="fresh", now=datetime.now(UTC) - timedelta(minutes=1),
    )
    assert granted.granted

    cancel = async_track_lease_expiry(hass, runtime)
    try:
        await hass.async_block_till_done()
        assert events == []  # Nothing sweeps before the interval elapses.
        async_fire_time_changed(hass, utcnow() + MESA_LEASE_SWEEP_INTERVAL + timedelta(seconds=1))
        await hass.async_block_till_done()
    finally:
        cancel()
    assert [(e["reason"], e["lease_id"], e["entities"]) for e in events] == [
        ("natural_expiry", granted.lease_id, ["light.a"]),
    ]
    # Swept once: the next tick finds nothing to report.
    assert runtime.lease_manager.active_leases() == []


async def test_lease_sweep_stops_at_cancel(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    cancel = async_track_lease_expiry(hass, runtime)
    cancel()
    with patch.object(runtime.lease_manager, "expire") as expire:
        async_fire_time_changed(hass, utcnow() + MESA_LEASE_SWEEP_INTERVAL * 3)
        await hass.async_block_till_done()
    expire.assert_not_called()


def _light_profile(boundaries: dict, privacy: dict | None = None) -> SemanticProfile:
    doc: dict = {"semantic_profile": {"operational_boundaries": boundaries}}
    if privacy is not None:
        doc["privacy_classification"] = privacy
    return SemanticProfile.from_dict("light", doc, default_origin=MetadataOrigin.USER)


@pytest.fixture
async def light(hass):
    entity = er.async_get(hass).async_get_or_create(
        "light", "test", "freshness", suggested_object_id="fresh"
    ).entity_id
    hass.states.async_set(entity, "off")
    return entity


async def test_shown_mode_includes_restricted_privacy(hass, light):
    runtime = await async_setup_mesa(hass, "advisory")
    runtime.store.set_domain_profile(
        "light", _light_profile({"control_mode": "autonomous"}, {"level": "restricted"}),
    )
    result = runtime.enforcer.evaluate(light, "light.turn_on", {"entity_id": light})
    # The inherited mode alone, which the display used to read, says Autonomous.
    assert result.effective_profile.operational_boundaries.control_mode is ControlMode.AUTONOMOUS
    assert result.evaluated_control_mode is ControlMode.CONFIRM
    assert entity_control_mode(runtime, _access_key(), light) == "confirm"


@pytest.mark.parametrize("hour,expected", [(12, "read_only"), (20, "autonomous")])
async def test_shown_mode_follows_an_active_temporal_window(hass, light, hour, expected):
    runtime = await async_setup_mesa(hass, "advisory")
    runtime.store.set_domain_profile("light", _light_profile({
        "control_mode": "autonomous",
        "temporal_constraints": [{
            "id": "midday", "condition": {"type": "time_range", "start_time": "11:00", "end_time": "13:00"},
            "effect": {"control_mode": "read_only"},
        }],
    }))
    now = datetime(2026, 10, 2, hour, tzinfo=UTC)
    with patch("custom_components.phoenix_mcp.mesa.dt_util.now", return_value=now):
        assert entity_control_mode(runtime, _access_key(), light) == expected


async def test_shown_mode_unchanged_without_adjustments(hass, light):
    runtime = await async_setup_mesa(hass, "advisory")
    runtime.store.set_domain_profile("light", _light_profile({"control_mode": "prohibited"}))
    assert entity_control_mode(runtime, _access_key(), light) == "prohibited"
    assert entity_control_mode(None, _access_key(), light) is None
