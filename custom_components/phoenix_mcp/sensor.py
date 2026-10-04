"""Sensor platform for Phoenix MCP per-token telemetry."""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.dt import utcnow

from .const import DOMAIN
from .token_store import token_name_slug

if TYPE_CHECKING:
    from .data import PhoenixData
    from .token_store import TokenRecord

PARALLEL_UPDATES = 0
_LOGGER = logging.getLogger(__name__)

_SENSOR_TYPES = (
    "status",
    "request_count",
    "denied_count",
    "rate_limit_hits",
    "last_access",
    "expires_in",
)


def _unique_id(token_id: str, sensor_type: str) -> str:
    """Key sensor identity on the token UUID so renames preserve entity IDs."""
    return f"phoenix_mcp_{token_id}_{sensor_type}"


def _device_name(token_name: str) -> str:
    """The device's display name. Sensors inherit it via has_entity_name."""
    return f"Phoenix MCP Token: {token_name}"


def _make_sensors(
    token: TokenRecord,
    data: PhoenixData,
) -> list[PhoenixTokenSensor]:
    """Create the full set of sensor entities for one token."""
    return [PhoenixTokenSensor(token, sensor_type, data) for sensor_type in _SENSOR_TYPES]


class PhoenixTokenSensor(SensorEntity):
    """HA sensor entity representing one telemetry dimension for a Phoenix MCP token.

    One sensor is created per entry in _SENSOR_TYPES per active token. Sensors
    are removed immediately when a token is revoked or archived.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    _NUMERIC_TYPES = frozenset({"request_count", "denied_count", "rate_limit_hits", "expires_in"})
    _COUNT_TYPES = frozenset({"request_count", "denied_count", "rate_limit_hits"})

    def __init__(
        self,
        token: TokenRecord,
        sensor_type: str,
        data: PhoenixData,
    ) -> None:
        self._token = token
        self._sensor_type = sensor_type
        self._data = data
        if sensor_type == "last_access":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif sensor_type == "status":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["active", "expired", "revoked"]
        self._attr_unique_id = _unique_id(token.id, sensor_type)
        # HA resolves the display name from translations/<lang>.json's entity
        # section; the English entries are the title-cased sensor type names.
        self._attr_translation_key = sensor_type
        self._device_info = self._build_device_info()

    def _build_device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._token.id)},
            name=_device_name(self._token.name),
            manufacturer="Phoenix MCP",
            model="Token Telemetry",
        )

    @callback
    def async_follow_token_update(self, token: TokenRecord) -> None:
        """Point this sensor at the token's current record, in place.

        A rename produces a NEW TokenRecord, and every reading is taken off the
        one held in _token, so without this the status and expiry sensors would
        keep reporting the pre-rename record. Updating in place, rather than
        rebuilding the sensor, preserves entity identity.
        """
        self._token = token
        self._device_info = self._build_device_info()
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def state_class(self) -> SensorStateClass | None:
        if self._sensor_type in self._COUNT_TYPES:
            # Counters are monotonically increasing totals; TOTAL_INCREASING is the
            # correct state class for HA statistics semantics (not MEASUREMENT).
            return SensorStateClass.TOTAL_INCREASING
        if self._sensor_type == "expires_in":
            return SensorStateClass.MEASUREMENT
        return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        # Unconditional: expires_in is always days. A token with no expiry
        # reports None, which HA shows as unknown with the unit intact.
        if self._sensor_type == "expires_in":
            return UnitOfTime.DAYS
        return None

    @property
    def token_id(self) -> str:
        return self._token.id

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    @property
    def native_value(self) -> str | int | datetime | None:
        token = self._token
        sensor_type = self._sensor_type

        if sensor_type == "status":
            if token.revoked:
                return "revoked"
            if token.is_expired():
                return "expired"
            return "active"

        if sensor_type == "request_count":
            return self._data.token_counters.get(token.id, {}).get("request_count", 0)

        if sensor_type == "denied_count":
            return self._data.token_counters.get(token.id, {}).get("denied_count", 0)

        if sensor_type == "rate_limit_hits":
            return self._data.token_counters.get(token.id, {}).get("rate_limit_hits", 0)

        if sensor_type == "last_access":
            if token.last_used_at is None:
                return None
            return token.last_used_at.replace(second=0, microsecond=0)

        if sensor_type == "expires_in":
            # None, not a sentence, when the token has no expiry. A string would
            # make one sensor alternate between a number with a unit and
            # untranslated prose, breaking its history and statistics and any
            # template comparing the value. None renders as HA's own "unknown" in
            # the viewer's language.
            if token.expires_at is None:
                return None
            delta = token.expires_at - utcnow()
            return max(0, math.ceil(delta.total_seconds() / 86400))

        return None


@callback
def _async_migrate_unique_ids(hass: HomeAssistant, tokens: list[TokenRecord]) -> None:
    """Move name-based sensor unique IDs to token-ID-based ones, keeping entity IDs."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    for token in tokens:
        for sensor_type in _SENSOR_TYPES:
            legacy = f"phoenix_mcp_{token_name_slug(token.name)}_{sensor_type}"
            current = _unique_id(token.id, sensor_type)
            try:
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, legacy)
                if entity_id is not None and registry.async_get_entity_id("sensor", DOMAIN, current) is None:
                    registry.async_update_entity(entity_id, new_unique_id=current)
            except Exception:  # Independent telemetry rows must still initialize.
                _LOGGER.warning("Phoenix MCP: sensor identity migration failed for %s", sensor_type)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialize the sensor platform and create sensors for all existing tokens."""
    data: PhoenixData = hass.data[DOMAIN]
    data.async_add_entities_cb = async_add_entities

    tokens = data.store.list_tokens()
    _async_migrate_unique_ids(hass, tokens)

    sensors: list[PhoenixTokenSensor] = []
    for token in tokens:
        slug = token_name_slug(token.name)
        token_sensors = _make_sensors(token, data)
        data.platform_entities[slug] = token_sensors
        data.token_id_sensors[token.id] = token_sensors
        sensors.extend(token_sensors)

    if sensors:
        async_add_entities(sensors)


async def async_rename_token_sensors(
    hass: HomeAssistant,
    old_name: str,
    token: TokenRecord,
) -> None:
    """Follow a token rename WITHOUT touching entity identity.

    Only the device's display name (inherited through has_entity_name) and the
    TokenRecord each sensor reads from change. The entity registry is untouched,
    and unique_id derives from the token ID, so entity_ids, and any dashboard
    card or automation naming them, survive the rename.
    """
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

    data: PhoenixData = hass.data[DOMAIN]
    old_slug = token_name_slug(old_name)
    new_slug = token_name_slug(token.name)

    # platform_entities stays keyed by name slug because that is the key the
    # revoke/archive path is given. Re-key it so a later removal still finds the
    # list; nothing about the entities themselves moves.
    sensors = data.platform_entities.pop(old_slug, None)
    if sensors is None:
        sensors = data.token_id_sensors.get(token.id, [])
    if sensors:
        data.platform_entities[new_slug] = sensors
        data.token_id_sensors[token.id] = sensors
        for sensor in sensors:
            sensor.async_follow_token_update(token)

    device_registry = dr.async_get(hass)
    # HA 2026.9 deprecated the identifier-only lookup because identifiers are
    # no longer globally unique across config entries. Token sensors have no
    # entry-specific lookup context, so use the collection API and retain the
    # legacy fallback for older HA releases.
    if hasattr(device_registry, "async_get_devices"):
        devices = device_registry.async_get_devices(identifiers={(DOMAIN, token.id)})
        device = devices[0] if devices else None
    else:
        device = device_registry.async_get_device(identifiers={(DOMAIN, token.id)})
    if device is not None:
        device_registry.async_update_device(device.id, name=_device_name(token.name))


async def async_create_token_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry,
    token: TokenRecord,
) -> None:
    """Create and register sensor entities for a newly created token."""
    data: PhoenixData = hass.data[DOMAIN]
    if data.async_add_entities_cb is None:
        return
    slug = token_name_slug(token.name)
    token_sensors = _make_sensors(token, data)
    data.platform_entities[slug] = token_sensors
    data.token_id_sensors[token.id] = token_sensors
    data.async_add_entities_cb(token_sensors)


async def async_remove_token_sensors(
    hass: HomeAssistant,
    token_slug: str,
) -> None:
    """Remove a token's sensor entities, registries, and Recorder statistics.

    Removing from the entity registry prevents 'unavailable' ghost entries after
    the token is gone. The associated device is also removed from the device registry.
    """
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.recorder import DATA_INSTANCE, get_instance
    data: PhoenixData = hass.data[DOMAIN]
    sensors = data.platform_entities.pop(token_slug, [])
    if sensors:
        data.token_id_sensors.pop(sensors[0].token_id, None)
    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)

    # Capture registered IDs before removal, including operator-renamed entities.
    # Statistics are keyed by entity_id, not the token name or sensor unique_id.
    device_id = None
    statistic_ids = []
    for sensor in sensors:
        if sensor.unique_id:
            entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, sensor.unique_id)
            if entity_id:
                statistic_ids.append(entity_id)
                entry = entity_reg.async_get(entity_id)
                if entry and device_id is None:
                    device_id = entry.device_id

    for sensor in sensors:
        await sensor.async_remove()
        if sensor.unique_id:
            entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, sensor.unique_id)
            if entity_id:
                entity_reg.async_remove(entity_id)
    if device_id:
        device_reg.async_remove_device(device_id)

    if statistic_ids and DATA_INSTANCE in hass.data:
        # Recorder owns the queued database work after the entities stop writing.
        # Include all sensors: expires_in can also have measurement statistics.
        get_instance(hass).async_clear_statistics(statistic_ids)
