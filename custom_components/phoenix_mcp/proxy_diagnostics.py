"""Read-only proxy and radio diagnostics for existing tools.

Radio-frequency transmitter capabilities, Bluetooth scanners and nearby devices,
serial ports and discovered config flows, each read as a bounded snapshot of
Core's in-process state through public component APIs. Nothing here transmits,
pairs, opens a port, starts or continues a flow, or subscribes. Scoped
projections answer only for resources the caller can already read; the
pass-through projections add Bluetooth addresses and USB serial identifiers but
withhold LAN identity (IP addresses, hostnames, network MACs and URL hosts), as
every other Phoenix surface does. The contract is spec/proxy-diagnostics.md.
Every function runs on the event loop.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .access_key_store import AccessKeyRecord
from .const import (
    DOMAIN,
    PROXY_DISCOVERED_MAX_FLOWS,
    PROXY_NEARBY_MAX_AGE_SECONDS,
    PROXY_NEARBY_MAX_DEVICES,
    PROXY_NEARBY_MAX_SERVICE_UUIDS,
    PROXY_SERIAL_MAX_PORTS,
    PROXY_TEXT_MAX_CHARS,
)
from .helpers import redact_diagnostics
from .policy_engine import Permission, assist_expose_check, device_config_entry_ids, resolve_device_registry_access

_LOGGER = logging.getLogger(__name__)

_BLUETOOTH = "bluetooth"
_PROXY_PORT_SCHEMES = ("esphome-hass://", "esphome://")
# Discovery title placeholders naming LAN identity are dropped, not scrubbed: the
# scrub catches IP literals but not hostnames.
_LAN_PLACEHOLDER_KEYS = frozenset({"host", "hostname", "ip", "ip_address", "address", "mac", "mac_address", "url", "port"})


def full_pass_through(access_key: AccessKeyRecord, hass: HomeAssistant) -> bool:
    """Whether a key sees what HA sees: pass-through, no Assist-exposure filter, no non-admin household caller."""
    from .household_authority import household_admin_access

    return bool(access_key.pass_through) and household_admin_access() and assist_expose_check(access_key, hass) is None


def _text(value: Any) -> str | None:
    """A bounded, diagnostics-scrubbed string, or None."""
    if not isinstance(value, str) or not value:
        return None
    return str(redact_diagnostics(value[:PROXY_TEXT_MAX_CHARS]))


# ---------------------------------------------------------------------------
# Radio frequency
# ---------------------------------------------------------------------------


def rf_capabilities(hass: HomeAssistant, entity_id: str) -> dict[str, Any] | None:
    """Frequency ranges (hertz) and modulations of one loaded RF transmitter, else None."""
    try:
        from homeassistant.components.radio_frequency import DATA_COMPONENT
        from rf_protocols import ModulationType
    except ImportError:
        return None
    component = hass.data.get(DATA_COMPONENT)
    entity = component.get_entity(entity_id) if component is not None else None
    if entity is None:
        return None
    try:
        return {
            "supported_frequency_ranges": [[int(low), int(high)] for low, high in entity.supported_frequency_ranges],
            "supported_modulations": [m.value for m in ModulationType if entity.supports_modulation(m)],
        }
    except Exception:  # noqa: BLE001 - an integration's property must not fail the read
        _LOGGER.debug("RF capabilities unavailable for %s", entity_id, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Bluetooth
# ---------------------------------------------------------------------------


def _bluetooth_scanners(hass: HomeAssistant) -> list[Any] | None:
    """Every registered scanner, or None when Bluetooth is not loaded."""
    if _BLUETOOTH not in hass.config.components:
        return None
    from homeassistant.components import bluetooth

    return list(bluetooth.async_current_scanners(hass))


def _allocations(source: str) -> Any:
    """The manager's slot allocations for one scanner source, or None."""
    try:
        from habluetooth import get_manager

        rows = get_manager().async_current_allocations(source)
    except Exception:  # noqa: BLE001 - slots are optional detail
        return None
    return rows[0] if rows else None


def _bluetooth_devices(hass: HomeAssistant, address: str) -> list[Any]:
    """Every HA device registered with this Bluetooth address as a connection.

    From HA 2026.10 a connection is unique only within one config entry, so
    async_get_devices lists every holder; earlier releases have one at most.
    """
    registry = dr.async_get(hass)
    connection = (dr.CONNECTION_BLUETOOTH, address)
    finder = getattr(registry, "async_get_devices", None)
    if finder is not None:
        return list(finder(connections={connection}))
    device = registry.async_get_device(connections={connection})
    return [device] if device is not None else []


def _scanner_device(hass: HomeAssistant, source: str) -> Any:
    """The device HA registered for a scanner: the holder of its source owned by a bluetooth entry."""
    candidates = _bluetooth_devices(hass, source)
    for device in candidates:
        entries = (hass.config_entries.async_get_entry(entry_id) for entry_id in device_config_entry_ids(device))
        if any(entry is not None and entry.domain == _BLUETOOTH for entry in entries):
            return device
    return candidates[0] if candidates else None


def _mode(value: Any) -> str | None:
    return getattr(value, "value", None) if value is not None else None


def _scanner_row(scanner: Any, allocations: Any) -> dict[str, Any]:
    """Fields shared by every scanner projection; none of them embeds an address."""
    details = getattr(scanner, "details", None)
    age = scanner.time_since_last_detection()
    row: dict[str, Any] = {
        "scanner_type": _mode(getattr(details, "scanner_type", None)),
        "connectable": bool(scanner.connectable),
        "current_mode": _mode(scanner.current_mode),
        "requested_mode": _mode(scanner.requested_mode),
        "seconds_since_last_advertisement": round(age, 1) if math.isfinite(age) else None,
    }
    # slots == 0 means the scanner has not reported slots, not that they are exhausted.
    if allocations is not None and allocations.slots > 0:
        row["connection_slots"] = {"total": allocations.slots, "free": allocations.free}
    return row


def device_bluetooth_scanners(
    hass: HomeAssistant, device: Any, access_key: AccessKeyRecord,
) -> list[dict[str, Any]] | None:
    """Scanners whose Bluetooth device is this device or names it as via_device.

    The link is structural: HA registers a scanner's device with a bluetooth
    connection equal to the scanner's source, and a proxy's scanner device names
    the proxy as its via_device. Connected devices the access key can read are
    named; every other connection is only counted.
    """
    scanners = _bluetooth_scanners(hass)
    if not scanners:
        return None
    rows = []
    for scanner in scanners:
        owner = _scanner_device(hass, scanner.source)
        if owner is None or device.id not in (owner.id, owner.via_device_id):
            continue
        allocations = _allocations(scanner.source)
        row = _scanner_row(scanner, allocations)
        connected, others = [], 0
        for address in getattr(allocations, "allocated", None) or []:
            readable = [peer.id for peer in _bluetooth_devices(hass, address)
                        if resolve_device_registry_access(peer.id, access_key, hass) in (Permission.READ, Permission.WRITE)]
            if readable:
                connected.extend(readable)
            else:
                others += 1
        row["connected_device_ids"] = sorted(set(connected))
        row["other_connections"] = others
        rows.append(row)
    return rows or None


def bluetooth_network(hass: HomeAssistant) -> dict[str, Any] | None:
    """Pass-through Bluetooth network: every scanner plus a bounded nearby snapshot."""
    scanners = _bluetooth_scanners(hass)
    if scanners is None:
        return None
    from homeassistant.components import bluetooth

    scanner_rows = []
    device_by_source: dict[str, str | None] = {}
    for scanner in scanners:
        owner = _scanner_device(hass, scanner.source)
        device_by_source[scanner.source] = owner.id if owner is not None else None
        allocations = _allocations(scanner.source)
        scanner_rows.append({
            "device_id": device_by_source[scanner.source],
            "source": scanner.source,
            **_scanner_row(scanner, allocations),
            "allocated": list(getattr(allocations, "allocated", None) or []),
        })
    now = bluetooth.MONOTONIC_TIME()
    heard = sorted(
        ((now - info.time, info) for info in bluetooth.async_discovered_service_info(hass, connectable=False)
         if now - info.time <= PROXY_NEARBY_MAX_AGE_SECONDS),
        key=lambda pair: pair[0],
    )
    nearby = []
    for age, info in heard[:PROXY_NEARBY_MAX_DEVICES]:
        name = info.name if info.name and info.name != info.address else None
        nearby.append({
            "address": info.address,
            "name": _text(name),
            "rssi": info.rssi,
            "connectable": bool(info.connectable),
            "tx_power": info.tx_power,
            "manufacturer_ids": sorted(info.manufacturer_data),
            "service_uuids": sorted(info.service_uuids)[:PROXY_NEARBY_MAX_SERVICE_UUIDS],
            "scanner_device_id": device_by_source.get(info.source),
            "seconds_ago": round(age),
        })
    return {
        "protocol": _BLUETOOTH,
        "scanners": sorted(scanner_rows, key=lambda row: row["source"]),
        "nearby": {
            "devices": nearby,
            "omitted": max(0, len(heard) - len(nearby)),
            "max_age_seconds": PROXY_NEARBY_MAX_AGE_SECONDS,
        },
    }


# ---------------------------------------------------------------------------
# Serial ports
# ---------------------------------------------------------------------------


async def _serial_state(hass: HomeAssistant) -> tuple[list[Any], dict[str, list[Any]] | None] | None:
    """Scanned ports and Core's consumers by port (None before HA 2026.9), or None without usb."""
    if "usb" not in hass.config.components:
        return None
    from homeassistant.components import usb

    ports = list(await usb.async_scan_serial_ports(hass))
    try:
        from homeassistant.components.usb.consumers import async_get_serial_port_consumers
    except ImportError:
        return ports, None
    return ports, await async_get_serial_port_consumers(hass, ports)


def _unscannable_schemes() -> tuple[str, ...]:
    try:
        from homeassistant.components.usb.consumers import UNSCANNABLE_PORT_SCHEMES
    except ImportError:
        return ()
    return tuple(UNSCANNABLE_PORT_SCHEMES)


def _transport(device: str) -> str:
    if device.startswith(_PROXY_PORT_SCHEMES):
        return "proxy"
    return "local" if device.startswith("/") else "network"


def _scrubbed_port(device: str) -> str:
    """A local path as is; a URL with its host and everything after it withheld."""
    scheme, separator, _rest = device.partition("://")
    return f"{scheme}://<redacted>" if separator else device


async def integration_serial_ports(hass: HomeAssistant) -> dict[str, list[dict[str, Any]]] | None:
    """Scoped port usage per consuming config entry, or None when Core cannot report usage."""
    try:
        state = await _serial_state(hass)
    except Exception:  # noqa: BLE001 - port usage is optional detail on a listing
        _LOGGER.debug("Serial port usage unavailable", exc_info=True)
        return None
    if state is None or state[1] is None:
        return None
    ports, consumers = state[0], state[1]
    scanned = {port.device: port for port in ports}
    unscannable = _unscannable_schemes()
    usage: dict[str, list[dict[str, Any]]] = {}
    for device, rows in consumers.items():
        port = scanned.get(device)
        for consumer in rows:
            entry_id = getattr(consumer, "config_entry_id", None)
            if not entry_id:
                continue
            usage.setdefault(entry_id, []).append({
                "transport": _transport(device),
                "description": _text(getattr(port, "description", None)),
                "present": port is not None or device.startswith(unscannable),
                "active": bool(consumer.active),
            })
    return usage


async def serial_inventory(hass: HomeAssistant) -> dict[str, Any] | None:
    """Pass-through serial inventory: scanned ports plus configured ports missing from the scan."""
    state = await _serial_state(hass)
    if state is None:
        return None
    ports, consumers = state
    from homeassistant.components import usb
    from homeassistant.components.usb.models import USBDevice
    from homeassistant.helpers.service_info.usb import UsbServiceInfo

    unscannable = _unscannable_schemes()
    scanned = {port.device: port for port in ports}
    devices = list(scanned) + [device for device in (consumers or {}) if device not in scanned]
    rows = []
    for device in devices[:PROXY_SERIAL_MAX_PORTS]:
        port = scanned.get(device)
        row: dict[str, Any] = {
            "device": _scrubbed_port(device),
            "present": port is not None or device.startswith(unscannable),
        }
        for field in ("serial_number", "manufacturer", "description", "interface_description"):
            row[field] = _text(getattr(port, field, None))
        if isinstance(port, USBDevice):
            row["vid"], row["pid"] = port.vid, port.pid
            row["matching_integrations"] = sorted({m["domain"] for m in usb.async_get_usb_matchers_for_device(hass, port)})
        if consumers is not None:
            row["consumers"] = [
                {
                    "kind": consumer.kind,
                    "domain": consumer.domain,
                    "title": _text(consumer.title),
                    "active": bool(consumer.active),
                    "config_entry_id": consumer.config_entry_id,
                }
                for consumer in consumers.get(device, [])
                if consumer.domain != DOMAIN
            ]
        row["discovery_flows"] = len(hass.config_entries.flow.async_progress_by_init_data_type(
            UsbServiceInfo, _usb_device_matcher(device)))
        rows.append(row)
    return {"ports": rows, "omitted": max(0, len(devices) - len(rows))}


def _usb_device_matcher(device: str) -> Any:
    """A flow matcher selecting USB discoveries of one port."""
    def matches(info: Any) -> bool:
        return getattr(info, "device", None) == device
    return matches


# ---------------------------------------------------------------------------
# Discovered config flows
# ---------------------------------------------------------------------------


def _discovery_identities(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Bluetooth and USB identity per flow id, read from each flow's discovery data.

    async_progress_by_init_data_type hands the matcher every flow's init data in
    the order it returns them; with uninitialized flows included the two line up.
    Each backend is imported only when loaded, since its libraries may be absent.
    """
    from homeassistant.helpers.service_info.usb import UsbServiceInfo

    def bluetooth_identity(info: Any) -> dict[str, Any]:
        name = info.name if info.name and info.name != info.address else None
        return {"bluetooth": {"address": info.address, "name": _text(name), "rssi": info.rssi}}

    def usb_identity(info: Any) -> dict[str, Any]:
        return {"usb": {field: _text(getattr(info, field, None))
                        for field in ("vid", "pid", "serial_number", "manufacturer", "description")}}

    kinds: list[tuple[type, Any]] = [(UsbServiceInfo, usb_identity)]
    if _BLUETOOTH in hass.config.components:
        from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

        kinds.append((BluetoothServiceInfoBleak, bluetooth_identity))
    identities: dict[str, dict[str, Any]] = {}
    for info_type, project in kinds:
        seen: list[Any] = []

        def collect(info: Any, seen: list[Any] = seen) -> bool:
            seen.append(info)
            return True

        flows = hass.config_entries.flow.async_progress_by_init_data_type(info_type, collect, include_uninitialized=True)
        if len(flows) != len(seen):
            continue
        for flow, info in zip(flows, seen):
            identities[flow["flow_id"]] = project(info)
    return identities


def discovered_flows(hass: HomeAssistant) -> tuple[list[dict[str, Any]], int]:
    """Pass-through discovered-but-not-added devices: (rows, omitted count)."""
    from homeassistant.config_entries import DISCOVERY_SOURCES

    flows = [
        flow for flow in hass.config_entries.flow.async_progress()
        if flow.get("context", {}).get("source") in DISCOVERY_SOURCES and flow.get("handler") != DOMAIN
    ]
    identities = _discovery_identities(hass)
    rows = []
    for flow in flows:
        context = flow.get("context") or {}
        placeholders = context.get("title_placeholders") or {}
        rows.append({
            "domain": flow.get("handler"),
            "source": context.get("source"),
            "title_placeholders": {
                str(key)[:40]: _text(str(value))
                for key, value in list(placeholders.items())[:8]
                if value is not None and str(key).lower() not in _LAN_PLACEHOLDER_KEYS
            } if isinstance(placeholders, dict) else {},
            **identities.get(flow.get("flow_id", ""), {}),
        })
    rows.sort(key=lambda row: (str(row["domain"]), str(row["source"])))
    return rows[:PROXY_DISCOVERED_MAX_FLOWS], max(0, len(rows) - PROXY_DISCOVERED_MAX_FLOWS)
