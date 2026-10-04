"""Per-token sensors: construction, state, registry bookkeeping and write debouncing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import secrets
import uuid
from datetime import timedelta
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import UnitOfTime

from custom_components.phoenix_mcp.const import DOMAIN, ACCESS_KEY_PREFIX
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.sensor import (
    PhoenixAccessKeySensor,
    _SENSOR_TYPES,
    _make_sensors,
    async_create_access_key_sensors,
    async_remove_access_key_sensors,
    async_setup_entry,
)
from custom_components.phoenix_mcp.access_key_store import access_key_name_slug as _token_slug
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, AccessKeyStore


def _make_token(
    name: str = "my-token",
    revoked: bool = False,
    expires_at=None,
    last_used_at=None,
) -> AccessKeyRecord:
    from homeassistant.util.dt import utcnow

    raw = ACCESS_KEY_PREFIX + secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return AccessKeyRecord(
        id=str(uuid.uuid4()),
        name=name,
        token_hash=token_hash,
        created_at=utcnow(),
        created_by="user1",
        revoked=revoked,
        expires_at=expires_at,
        last_used_at=last_used_at,
    )


def _make_data(access_keys: list[AccessKeyRecord] | None = None) -> PhoenixData:
    store = MagicMock(spec=AccessKeyStore)
    store.list_access_keys = MagicMock(return_value=access_keys or [])
    store.async_lock = asyncio.Lock()
    rate_limiter = MagicMock(spec=RateLimiter)
    audit = MagicMock(spec=AuditLog)
    return PhoenixData(
        store=store,
        rate_limiter=rate_limiter,
        audit=audit,
    )


def _make_hass(data: PhoenixData) -> MagicMock:
    hass = MagicMock()
    hass.data = {DOMAIN: data}
    return hass


def _make_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test-entry"
    return entry


# --- _token_slug ---

def test_token_slug_lowercases_and_replaces_hyphens():
    access_key = _make_token(name="My-Token")
    assert _token_slug(access_key.name) == "my_token"


def test_token_slug_no_hyphens_unchanged():
    access_key = _make_token(name="mytoken")
    assert _token_slug(access_key.name) == "mytoken"


# --- _make_sensors ---

def test_make_sensors_returns_six():
    access_key = _make_token(name="alpha")
    data = _make_data()
    sensors = _make_sensors(access_key, data)
    assert len(sensors) == 6


def test_make_sensors_unique_ids():
    """A sensor's unique_id is the token's id, never its name.

    The name is mutable: keying on it would delete and recreate every registry row
    (with new entity_ids) on each rename. The device is keyed on the id as well.
    """
    access_key = _make_token(name="alpha")
    data = _make_data()
    sensors = _make_sensors(access_key, data)
    unique_ids = [s._attr_unique_id for s in sensors]
    expected = [
        f"phoenix_mcp_{access_key.id}_status",
        f"phoenix_mcp_{access_key.id}_request_count",
        f"phoenix_mcp_{access_key.id}_denied_count",
        f"phoenix_mcp_{access_key.id}_rate_limit_hits",
        f"phoenix_mcp_{access_key.id}_last_access",
        f"phoenix_mcp_{access_key.id}_expires_in",
    ]
    assert unique_ids == expected
    assert not any("alpha" in uid for uid in unique_ids)


def test_make_sensors_names_come_from_the_translation_catalog():
    """Sensor names come from HA entity translations, not a computed title.

    The English values are asserted as well as the keys: they are the friendly
    names dashboards and automations refer to, so they must stay stable.
    """
    import json
    import pathlib

    access_key = _make_token(name="my-token")
    data = _make_data()
    sensors = _make_sensors(access_key, data)
    keys = [s._attr_translation_key for s in sensors]
    assert keys[0] == "status"
    assert keys[1] == "request_count"
    assert keys[4] == "last_access"

    catalog = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent
         / "custom_components" / "phoenix_mcp" / "translations" / "en.json").read_text()
    )["entity"]["sensor"]
    assert catalog["status"]["name"] == "Status"
    assert catalog["request_count"]["name"] == "Request Count"
    assert catalog["last_access"]["name"] == "Last Access"
    # Every sensor type must have an entry, or HA falls back to the object id.
    assert set(catalog) == {s._sensor_type for s in sensors}


# --- status sensor ---

def test_status_active():
    access_key = _make_token()
    data = _make_data()
    sensor = PhoenixAccessKeySensor(access_key, "status", data)
    assert sensor.native_value == "active"


def test_status_revoked():
    access_key = _make_token(revoked=True)
    data = _make_data()
    sensor = PhoenixAccessKeySensor(access_key, "status", data)
    assert sensor.native_value == "revoked"


def test_status_expired():
    from homeassistant.util.dt import utcnow

    access_key = _make_token(expires_at=utcnow() - timedelta(hours=1))
    data = _make_data()
    sensor = PhoenixAccessKeySensor(access_key, "status", data)
    assert sensor.native_value == "expired"


# --- counter sensors ---

def test_request_count_zero_when_no_counters():
    access_key = _make_token()
    data = _make_data()
    sensor = PhoenixAccessKeySensor(access_key, "request_count", data)
    assert sensor.native_value == 0


def test_request_count_from_token_counters():
    access_key = _make_token()
    data = _make_data()
    data.access_key_counters[access_key.id] = {"request_count": 42, "denied_count": 3, "rate_limit_hits": 1}
    sensor = PhoenixAccessKeySensor(access_key, "request_count", data)
    assert sensor.native_value == 42


def test_denied_count_from_token_counters():
    access_key = _make_token()
    data = _make_data()
    data.access_key_counters[access_key.id] = {"request_count": 10, "denied_count": 5, "rate_limit_hits": 0}
    sensor = PhoenixAccessKeySensor(access_key, "denied_count", data)
    assert sensor.native_value == 5


def test_rate_limit_hits_from_token_counters():
    access_key = _make_token()
    data = _make_data()
    data.access_key_counters[access_key.id] = {"request_count": 10, "denied_count": 0, "rate_limit_hits": 7}
    sensor = PhoenixAccessKeySensor(access_key, "rate_limit_hits", data)
    assert sensor.native_value == 7


def test_denied_count_zero_when_no_counters():
    access_key = _make_token()
    data = _make_data()
    sensor = PhoenixAccessKeySensor(access_key, "denied_count", data)
    assert sensor.native_value == 0


# --- last_access sensor ---

def test_last_access_never_when_none():
    access_key = _make_token(last_used_at=None)
    data = _make_data()
    sensor = PhoenixAccessKeySensor(access_key, "last_access", data)
    assert sensor.native_value is None


def test_last_access_returns_timestamp_at_minute_resolution():
    from homeassistant.util.dt import utcnow

    ts = utcnow()
    access_key = _make_token(last_used_at=ts)
    data = _make_data()
    sensor = PhoenixAccessKeySensor(access_key, "last_access", data)
    assert sensor.native_value == ts.replace(second=0, microsecond=0)


# --- expires_in sensor ---

def test_expires_in_is_unknown_without_an_expiry():
    """No expiry reports None (HA "unknown"), never a sentence.

    The unit and state class stay attached in both cases, so the sensor has one
    type and history and statistics work for it.
    """
    access_key = _make_token(expires_at=None)
    data = _make_data()
    sensor = PhoenixAccessKeySensor(access_key, "expires_in", data)
    assert sensor.native_value is None
    assert sensor.state_class is SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement == UnitOfTime.DAYS


def test_expires_in_carries_the_same_unit_with_and_without_an_expiry():
    """The unit must not depend on the value, or statistics reject the series."""
    from homeassistant.util.dt import utcnow

    data = _make_data()
    with_expiry = PhoenixAccessKeySensor(_make_token(expires_at=utcnow() + timedelta(days=5)), "expires_in", data)
    without = PhoenixAccessKeySensor(_make_token(expires_at=None), "expires_in", data)
    assert with_expiry.native_unit_of_measurement == without.native_unit_of_measurement
    assert with_expiry.state_class is without.state_class


def test_expires_in_returns_days():
    from homeassistant.util.dt import utcnow

    access_key = _make_token(expires_at=utcnow() + timedelta(days=5))
    data = _make_data()
    sensor = PhoenixAccessKeySensor(access_key, "expires_in", data)
    assert sensor.native_value == 5


def test_expires_in_partial_day_rounds_up():
    from homeassistant.util.dt import utcnow

    access_key = _make_token(expires_at=utcnow() + timedelta(hours=2))
    data = _make_data()
    sensor = PhoenixAccessKeySensor(access_key, "expires_in", data)
    assert sensor.native_value == 1


# --- async_setup_entry ---

@pytest.mark.asyncio
async def test_setup_entry_registers_callback_and_adds_sensors(hass):
    access_key = _make_token(name="tok-one")
    data = _make_data(access_keys=[access_key])
    hass.data[DOMAIN] = data
    entry = _make_entry()
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    assert data.async_add_entities_cb is add_entities
    assert "tok_one" in data.platform_entities
    assert len(data.platform_entities["tok_one"]) == 6
    add_entities.assert_called_once()
    added = add_entities.call_args.args[0]
    assert len(added) == 6


@pytest.mark.asyncio
async def test_setup_entry_no_tokens_does_not_call_add_entities():
    data = _make_data(access_keys=[])
    hass = _make_hass(data)
    entry = _make_entry()
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    assert data.async_add_entities_cb is add_entities
    add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_setup_entry_multiple_tokens(hass):
    access_keys = [_make_token(name="alpha"), _make_token(name="beta")]
    data = _make_data(access_keys=access_keys)
    hass.data[DOMAIN] = data
    entry = _make_entry()
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    assert "alpha" in data.platform_entities
    assert "beta" in data.platform_entities
    added = add_entities.call_args.args[0]
    assert len(added) == 12


# --- async_create_access_key_sensors ---

@pytest.mark.asyncio
async def test_create_token_sensors_adds_to_platform_entities():
    access_key = _make_token(name="new-tok")
    data = _make_data()
    data.async_add_entities_cb = MagicMock()
    hass = _make_hass(data)
    entry = _make_entry()

    await async_create_access_key_sensors(hass, entry, access_key)

    assert "new_tok" in data.platform_entities
    assert len(data.platform_entities["new_tok"]) == 6
    data.async_add_entities_cb.assert_called_once()


@pytest.mark.asyncio
async def test_create_token_sensors_noop_when_no_callback():
    access_key = _make_token(name="new-tok")
    data = _make_data()
    data.async_add_entities_cb = None
    hass = _make_hass(data)
    entry = _make_entry()

    await async_create_access_key_sensors(hass, entry, access_key)

    assert "new_tok" not in data.platform_entities


# --- async_remove_access_key_sensors ---

@pytest.mark.asyncio
async def test_remove_token_sensors_calls_async_remove_on_each():
    data = _make_data()
    hass = _make_hass(data)

    sensor1 = MagicMock(spec=PhoenixAccessKeySensor)
    sensor1.async_remove = AsyncMock()
    sensor1.unique_id = None
    sensor2 = MagicMock(spec=PhoenixAccessKeySensor)
    sensor2.async_remove = AsyncMock()
    sensor2.unique_id = None
    data.platform_entities["gone_tok"] = [sensor1, sensor2]

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()), \
         patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()):
        await async_remove_access_key_sensors(hass, "gone_tok")

    sensor1.async_remove.assert_called_once()
    sensor2.async_remove.assert_called_once()
    assert "gone_tok" not in data.platform_entities


@pytest.mark.asyncio
async def test_remove_token_sensors_unknown_slug_is_noop():
    data = _make_data()
    hass = _make_hass(data)

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()), \
         patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()):
        await async_remove_access_key_sensors(hass, "nonexistent")


@pytest.mark.asyncio
async def test_remove_token_sensors_pops_from_platform_entities():
    data = _make_data()
    hass = _make_hass(data)

    sensor = MagicMock(spec=PhoenixAccessKeySensor)
    sensor.async_remove = AsyncMock()
    sensor.unique_id = None
    data.platform_entities["gone_tok"] = [sensor]
    data.platform_entities["other_tok"] = [MagicMock()]

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()), \
         patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()):
        await async_remove_access_key_sensors(hass, "gone_tok")

    assert "gone_tok" not in data.platform_entities
    assert "other_tok" in data.platform_entities


# --- update_access_key_counter sensor-write debounce --------------------------------


class TestCounterWriteDebounce:
    """update_access_key_counter coalesces sensor writes into one per debounce window.

    request_count and last_access change on every request, so immediate writes
    would produce one state_changed event and recorder row per agent call.
    Counters stay immediately current in data.access_key_counters (the admin API reads
    those).
    """

    async def test_writes_coalesce_into_one_flush(self, hass):
        from pytest_homeassistant_custom_component.common import async_fire_time_changed
        from homeassistant.util.dt import utcnow

        from custom_components.phoenix_mcp.const import SENSOR_WRITE_DEBOUNCE_SECONDS
        from custom_components.phoenix_mcp.helpers import update_access_key_counter

        data = _make_data()
        data.hass = hass
        sensor = MagicMock()
        sensor.hass = hass
        data.access_key_id_sensors["t1"] = [sensor]

        update_access_key_counter(data, "t1", "allowed")
        update_access_key_counter(data, "t1", "denied")
        update_access_key_counter(data, "t1", "allowed")

        # Counters are current immediately; sensor writes are pending.
        assert data.access_key_counters["t1"]["request_count"] == 3
        assert data.access_key_counters["t1"]["denied_count"] == 1
        assert sensor.async_write_ha_state.call_count == 0
        assert data.sensor_flush_cancel is not None
        assert data.sensor_write_dirty == {"t1"}

        async_fire_time_changed(hass, utcnow() + timedelta(seconds=SENSOR_WRITE_DEBOUNCE_SECONDS + 1))
        await hass.async_block_till_done()

        # One write for three requests; state cleared for the next window.
        assert sensor.async_write_ha_state.call_count == 1
        assert data.sensor_write_dirty == set()
        assert data.sensor_flush_cancel is None

    async def test_flush_skips_archived_token(self, hass):
        from pytest_homeassistant_custom_component.common import async_fire_time_changed
        from homeassistant.util.dt import utcnow

        from custom_components.phoenix_mcp.const import SENSOR_WRITE_DEBOUNCE_SECONDS
        from custom_components.phoenix_mcp.helpers import update_access_key_counter

        data = _make_data()
        data.hass = hass
        sensor = MagicMock()
        sensor.hass = hass
        data.access_key_id_sensors["t1"] = [sensor]

        update_access_key_counter(data, "t1", "allowed")
        # Token revoked inside the window: its sensor registry entry is removed.
        del data.access_key_id_sensors["t1"]

        async_fire_time_changed(hass, utcnow() + timedelta(seconds=SENSOR_WRITE_DEBOUNCE_SECONDS + 1))
        await hass.async_block_till_done()

        assert sensor.async_write_ha_state.call_count == 0

    def test_writes_immediately_without_hass(self):
        from custom_components.phoenix_mcp.helpers import update_access_key_counter

        data = _make_data()  # data.hass is None (direct construction)
        sensor = MagicMock()
        sensor.hass = MagicMock()
        data.access_key_id_sensors["t1"] = [sensor]

        update_access_key_counter(data, "t1", "allowed")

        assert sensor.async_write_ha_state.call_count == 1
        assert data.sensor_write_dirty == set()

    def test_flush_sensor_writes_is_marked_callback(self):
        # flush_sensor_writes calls sensor.async_write_ha_state(), which must run on
        # the event loop. Without @callback, HA's HassJob type inference sees a
        # plain function passed to async_call_later and dispatches it to the
        # executor pool, violating that. A MagicMock sensor cannot catch a missing
        # decorator (mocks do not care which thread calls them), so this asserts
        # the marker directly.
        from homeassistant.core import is_callback
        from custom_components.phoenix_mcp.helpers import flush_sensor_writes

        assert is_callback(flush_sensor_writes)


# --- token identity survives a rename (real entity registry) ------------------
#
# These use the REAL entity and device registries rather than a MagicMock hass:
# the invariant lives in registry bookkeeping, and a MagicMock records calls
# without asserting anything about identity. The sensors are registered by hand
# rather than through the platform so the tests stay about identity and need no
# full config entry.

class TestTokenRenameKeepsSensorIdentity:
    """A rename must change what is DISPLAYED, never what is ADDRESSED."""

    @staticmethod
    def _register(hass, access_key):
        """Register one token's sensors and device, keyed on the token id."""
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er
        from custom_components.phoenix_mcp.sensor import (
            _SENSOR_TYPES, _device_name, _unique_id,
        )

        entity_reg = er.async_get(hass)
        device_reg = dr.async_get(hass)
        entry = MockConfigEntry(domain=DOMAIN)
        entry.add_to_hass(hass)
        device = device_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, access_key.id)},
            name=_device_name(access_key.name),
        )
        return {
            sensor_type: entity_reg.async_get_or_create(
                "sensor", DOMAIN, _unique_id(access_key.id, sensor_type),
                config_entry=entry, device_id=device.id,
            ).entity_id
            for sensor_type in _SENSOR_TYPES
        }, device.id

    @pytest.mark.asyncio
    async def test_rename_keeps_every_entity_id(self, hass):
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er
        from custom_components.phoenix_mcp.sensor import (
            _device_name, async_rename_access_key_sensors,
        )

        access_key = _make_token(name="alpha")
        data = _make_data(access_keys=[access_key])
        hass.data[DOMAIN] = data
        before, device_id = self._register(hass, access_key)

        renamed = replace(access_key, name="beta")
        await async_rename_access_key_sensors(hass, "alpha", renamed)

        entity_reg = er.async_get(hass)
        after = {
            sensor_type: entity_reg.async_get_entity_id("sensor", DOMAIN, uid)
            for sensor_type, uid in (
                (t, f"phoenix_mcp_{access_key.id}_{t}") for t in before
            )
        }
        # Same rows, same entity_ids: a dashboard card naming one still resolves.
        assert after == before
        assert all(entity_id is not None for entity_id in after.values())
        # Only the display name moved.
        device = dr.async_get(hass).async_get(device_id)
        assert device is not None
        assert device.name == _device_name("beta")
        assert device.id == device_id

    @pytest.mark.asyncio
    async def test_rename_repoints_the_sensors_at_the_new_record(self, hass):
        # The sensors read every value off the AccessKeyRecord they hold, so a rename
        # must repoint them at the new record or the status sensor reports the
        # stale one.
        from custom_components.phoenix_mcp.sensor import async_rename_access_key_sensors

        access_key = _make_token(name="alpha")
        data = _make_data(access_keys=[access_key])
        hass.data[DOMAIN] = data
        sensors = _make_sensors(access_key, data)
        data.platform_entities["alpha"] = sensors
        data.access_key_id_sensors[access_key.id] = sensors

        revoked = replace(access_key, name="beta", revoked=True)
        await async_rename_access_key_sensors(hass, "alpha", revoked)

        status = next(s for s in sensors if s._sensor_type == "status")
        assert status.native_value == "revoked"
        # Re-keyed so the slug-keyed revoke path still finds them.
        assert "alpha" not in data.platform_entities
        assert data.platform_entities["beta"] is sensors

    @pytest.mark.parametrize("recorder_enabled", [True, False])
    async def test_removal_clears_actual_registered_sensor_statistics(self, hass, recorder_enabled):
        """Delete only this token's statistics, using IDs captured before removal."""
        from homeassistant.components.recorder.core import Recorder
        from homeassistant.components.recorder.tasks import ClearStatisticsTask
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.recorder import DATA_INSTANCE, DATA_RECORDER, RecorderData
        from homeassistant.util.dt import utcnow
        from custom_components.phoenix_mcp.sensor import async_rename_access_key_sensors

        access_key = _make_token(name="alpha", expires_at=utcnow() + timedelta(days=1))
        other = _make_token(name="unrelated")
        data = _make_data(access_keys=[access_key, other])
        hass.data[DOMAIN] = data
        ids, device_id = self._register(hass, access_key)
        other_ids, other_device_id = self._register(hass, other)
        registry = er.async_get(hass)
        # The operator can change the entity ID independently of the token name.
        registry.async_update_entity(ids["request_count"], new_entity_id="sensor.custom_token_requests")
        ids["request_count"] = "sensor.custom_token_requests"
        sensors = _make_sensors(access_key, data)
        for sensor in sensors:
            sensor.async_remove = AsyncMock()
        data.platform_entities["alpha"] = sensors
        data.access_key_id_sensors[access_key.id] = sensors

        hass.data[DATA_RECORDER] = RecorderData()
        recorder = Recorder(hass, False, False, 10, 5, "sqlite://", 1, 1, None, set())
        if recorder_enabled:
            hass.data[DATA_INSTANCE] = recorder
        with patch.object(recorder, "queue_task") as queue_task:
            await async_rename_access_key_sensors(hass, "alpha", replace(access_key, name="beta"))
            queue_task.assert_not_called()
            await async_remove_access_key_sensors(hass, "beta")
            if recorder_enabled:
                queue_task.assert_called_once()
                task = queue_task.call_args.args[0]
                assert isinstance(task, ClearStatisticsTask)
                assert set(task.statistic_ids) == set(ids.values())
                assert ids["expires_in"] in task.statistic_ids
            else:
                queue_task.assert_not_called()

            await async_remove_access_key_sensors(hass, "missing_token")
            assert queue_task.call_count == int(recorder_enabled)

        assert all(registry.async_get(entity_id) is None for entity_id in ids.values())
        assert dr.async_get(hass).async_get(device_id) is None
        assert all(registry.async_get(entity_id) is not None for entity_id in other_ids.values())
        assert dr.async_get(hass).async_get(other_device_id) is not None
        assert access_key.id not in data.access_key_id_sensors

# --- the entity IDs Home Assistant actually generates -------------------------


@pytest.mark.asyncio
async def test_generated_entity_ids_match_the_documentation(hass, enable_custom_integrations):
    """Entity IDs HA generates through its real naming path match the documented ones.

    docs/operations.html lists these IDs for operators to use in dashboards and
    automations. The entity platform composes the `has_entity_name` device name and
    entity name into the object id; a hand-built registry row skips that step, so
    the sensors are added through a real EntityPlatform.
    """
    import logging
    from datetime import timedelta as _timedelta

    from homeassistant.helpers.entity_platform import EntityPlatform

    access_key = _make_token(name="my-token")
    data = _make_data(access_keys=[access_key])
    hass.data[DOMAIN] = data
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)

    platform = EntityPlatform(
        hass=hass, logger=logging.getLogger(__name__), domain="sensor",
        platform_name=DOMAIN, platform=None, scan_interval=_timedelta(seconds=60),
        entity_namespace=None,
    )
    platform.config_entry = entry
    # The sensors carry a translation_key rather than a literal name, and the
    # platform resolves it from the translations HA loads during a real setup.
    # A hand-built platform loads none, so every entity would come out unnamed and
    # collide into _2.._6 suffixes (the object id would be the device name alone).
    # Loading the integration's own catalog keeps this exercising the real
    # composition.
    _catalog = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent / "custom_components"
         / "phoenix_mcp" / "translations" / "en.json").read_text(encoding="utf-8")
    )["entity"]["sensor"]
    _translations = {
        f"component.{DOMAIN}.entity.sensor.{key}.name": value["name"]
        for key, value in _catalog.items()
    }

    async def _fake_get_translations(language, category, integration):
        return _translations

    # Patched AT the seam HA itself calls, not next to it: PlatformData owns
    # translation loading and the read-only dictionaries the naming path reads.
    platform.platform_data._async_get_translations = _fake_get_translations
    await platform.platform_data.async_load_translations()

    sensors = _make_sensors(access_key, data)
    await platform.async_add_entities(sensors)
    await hass.async_block_till_done()

    # Each generated id is compared to the id documented for THAT sensor, and the
    # two sets are compared whole. A prefix assertion plus a separate "the docs
    # mention this string" check would bind nothing: HA could generate
    # `..._wrong_status` and every assertion would still pass.
    docs = (
        pathlib.Path(__file__).resolve().parent.parent / "docs" / "operations.html"
    ).read_text(encoding="utf-8")

    generated = {s._sensor_type: s.entity_id for s in sensors}
    documented = {
        sensor_type: f"sensor.phoenix_mcp_token_my_token_{sensor_type}"
        for sensor_type in _SENSOR_TYPES
    }
    assert generated == documented, (
        "Home Assistant generates different entity IDs than docs/operations.html "
        "documents. Operators copy these into dashboards and automations.\n"
        f"  generated: {generated}\n  documented: {documented}"
    )
    # And the table really contains them, so correcting the expectation above
    # without correcting the page cannot pass.
    for sensor_type, entity_id in documented.items():
        assert entity_id in docs, (
            f"docs/operations.html does not list {entity_id} for the "
            f"{sensor_type} sensor."
        )


async def test_released_sensor_identity_migration_preserves_entity_id(hass):
    from homeassistant.helpers import entity_registry as er
    from custom_components.phoenix_mcp.sensor import _async_migrate_unique_ids

    access_key = _make_token(name="legacy-token")
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create("sensor", DOMAIN, "phoenix_mcp_legacy_token_status")
    _async_migrate_unique_ids(hass, [access_key])
    assert registry.async_get_entity_id("sensor", DOMAIN, PhoenixAccessKeySensor(access_key, "status", _make_data()).unique_id) == legacy.entity_id
    assert registry.async_get_entity_id("sensor", DOMAIN, "phoenix_mcp_legacy_token_status") is None


@pytest.mark.parametrize("failure_stage", ["lookup", "update"])
async def test_sensor_setup_continues_after_one_identity_migration_failure(hass, failure_stage, caplog):
    from homeassistant.helpers import entity_registry as er
    from custom_components.phoenix_mcp.sensor import _unique_id

    access_key = _make_token(name="legacy-token")
    data = _make_data(access_keys=[access_key])
    hass.data[DOMAIN] = data
    registry = er.async_get(hass)
    legacy = {
        kind: registry.async_get_or_create("sensor", DOMAIN, f"phoenix_mcp_legacy_token_{kind}")
        for kind in _SENSOR_TYPES
    }
    method = "async_get_entity_id" if failure_stage == "lookup" else "async_update_entity"
    original = getattr(registry, method)

    def failing_row(*args, **kwargs):
        if (failure_stage == "lookup" and args[2] == "phoenix_mcp_legacy_token_status") or (
            failure_stage == "update" and args[0] == legacy["status"].entity_id
        ):
            raise RuntimeError("simulated registry failure")
        return original(*args, **kwargs)

    add_entities = MagicMock()
    with patch.object(registry, method, side_effect=failing_row):
        await async_setup_entry(hass, _make_entry(), add_entities)

    assert "sensor identity migration failed for status" in caplog.text
    assert len(add_entities.call_args.args[0]) == len(_SENSOR_TYPES)
    for kind in _SENSOR_TYPES[1:]:
        assert registry.async_get_entity_id("sensor", DOMAIN, _unique_id(access_key.id, kind)) == legacy[kind].entity_id
    assert registry.async_get_entity_id("sensor", DOMAIN, "phoenix_mcp_legacy_token_status") == legacy["status"].entity_id
