"""Proxy and radio diagnostics against Core's real radio_frequency, bluetooth and usb components.

Each case builds state through the installed Core: kitchen_sink's own RF
transmitter entity, a habluetooth remote scanner registered through Core's
public API (which creates the scanner's device and its via_device link), real
advertisements, Core's slot allocation tracking, its serial consumer detection
and real discovery flows. The assertions pin the contract in
docs-internal/spec/proxy-diagnostics.md: scoped keys see only what is attached to
what they can read, pass-through keys see what HA sees except LAN identity, and
nothing returns advertisement payload bytes.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.policy_engine import device_config_entry_ids
from custom_components.phoenix_mcp.version_store import VersionStore

PROXY_SOURCE = "AA:BB:CC:DD:EE:01"
PEER_VISIBLE = "11:22:33:44:55:01"
PEER_HIDDEN = "11:22:33:44:55:02"
STRANGER = "11:22:33:44:55:03"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """HA 2026.7's local Bluetooth scanner keeps its expiry timer after unload; 2026.10 does not."""
    from awesomeversion import AwesomeVersion
    from homeassistant.const import __version__

    return AwesomeVersion(__version__) < AwesomeVersion("2026.10.0")


def _data() -> PhoenixData:
    return PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())


def _key(pass_through: bool = False, **caps: str) -> AccessKeyRecord:
    for cap in ("cap_registry_read", "cap_diagnostics", "cap_config_read", "cap_search"):
        caps.setdefault(cap, "allow")
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        pass_through=pass_through,
        permissions=PermissionTree(domains={"sensor": PermissionNode(state="GREEN"),
                                            "radio_frequency": PermissionNode(state="GREEN")}),
        **caps,
    )


async def _tool(hass: HomeAssistant, name: str, args: dict, key: AccessKeyRecord) -> tuple[dict, str]:
    result = await _call_tool(name, args, key, hass, _data(), "r", None)
    text = result[0]["content"][0]["text"]
    return (json.loads(text) if result[1] == "allowed" else {"error": text}), result[1]


# ---------------------------------------------------------------------------
# Radio frequency
# ---------------------------------------------------------------------------


async def test_describe_entity_reports_a_transmitters_frequencies_and_modulations(hass: HomeAssistant) -> None:
    """kitchen_sink's own transmitter entity reports its range through describe_entity."""
    from homeassistant.components.kitchen_sink.radio_frequency import DemoRadioFrequency
    from homeassistant.components.radio_frequency import DATA_COMPONENT

    assert await async_setup_component(hass, "radio_frequency", {})
    await hass.data[DATA_COMPONENT].async_add_entities(
        [DemoRadioFrequency(unique_id="rf", device_name="RF Blaster", entity_name="Transmitter")])
    await hass.async_block_till_done()
    entity_id = next(iter(hass.states.async_entity_ids("radio_frequency")))
    body, outcome = await _tool(hass, "describe_entity", {"entity_id": entity_id}, _key())
    assert outcome == "allowed"
    assert body["radio_frequency"] == {
        "supported_frequency_ranges": [[300_000_000, 928_000_000]], "supported_modulations": ["OOK"]}


async def test_other_domains_carry_no_radio_frequency_block(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.plain", "1")
    body, _ = await _tool(hass, "describe_entity", {"entity_id": "sensor.plain"}, _key())
    assert "radio_frequency" not in body


# ---------------------------------------------------------------------------
# Bluetooth
# ---------------------------------------------------------------------------


def _remote_scanner_class() -> type:
    from habluetooth import BaseHaRemoteScanner

    class ProxyScanner(BaseHaRemoteScanner):
        """A remote scanner as an ESPHome proxy registers one."""

    return ProxyScanner


def _device_with_sensor(hass: HomeAssistant, entry: MockConfigEntry, name: str, connections: set, domain: str = "sensor") -> Any:
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, name=name, connections=connections, identifiers={(entry.domain, name)})
    entity = er.async_get(hass).async_get_or_create(domain, "test", f"{name}-reading", device_id=device.id,
                                                     config_entry=entry, suggested_object_id=name.lower().replace(" ", "_"))
    hass.states.async_set(entity.entity_id, "1")
    return device


@pytest.fixture
async def proxy(hass: HomeAssistant, enable_bluetooth: None) -> dict[str, Any]:
    """An ESPHome-style proxy with a registered remote scanner, two connections and one advertisement."""
    from homeassistant.components import bluetooth
    from habluetooth import HaBluetoothConnector, get_manager
    from bleak_retry_connector import Allocations

    entry = MockConfigEntry(domain="test", title="Proxies")
    entry.add_to_hass(hass)
    proxy_device = _device_with_sensor(hass, entry, "Garage Proxy", {(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:00")})
    visible = _device_with_sensor(hass, entry, "Kettle", {(dr.CONNECTION_BLUETOOTH, PEER_VISIBLE)})
    hidden = _device_with_sensor(hass, entry, "Front Lock", {(dr.CONNECTION_BLUETOOTH, PEER_HIDDEN)}, domain="lock")
    scanner = _remote_scanner_class()(
        PROXY_SOURCE, "garage-proxy", HaBluetoothConnector(client=MagicMock, source=PROXY_SOURCE, can_connect=lambda: True),
        True)
    unregister = bluetooth.async_register_scanner(
        hass, scanner, connection_slots=3, source_domain="test", source_model="esp32",
        source_config_entry_id=entry.entry_id, source_device_id=proxy_device.id)
    await hass.async_block_till_done()
    get_manager().async_on_allocation_changed(
        Allocations(adapter=PROXY_SOURCE, slots=3, free=1, allocated=[PEER_VISIBLE, PEER_HIDDEN]))
    scanner._async_on_advertisement(
        STRANGER, -61, "Neighbour Speaker", ["0000fe2c-0000-1000-8000-00805f9b34fb"], {"fe2c": b"\x01\x02"},
        {76: b"\x10\x05"}, -12, {}, bluetooth.MONOTONIC_TIME())
    await hass.async_block_till_done()
    yield {"device": proxy_device, "visible": visible, "hidden": hidden, "scanner": scanner}
    unregister()


async def test_core_links_the_scanner_device_to_its_proxy(hass: HomeAssistant, proxy: dict) -> None:
    """The structural link Phoenix reads is the one Core creates, not one a test built."""
    from custom_components.phoenix_mcp.proxy_diagnostics import _scanner_device

    scanner_device = _scanner_device(hass, PROXY_SOURCE)
    assert scanner_device is not None and scanner_device.via_device_id == proxy["device"].id
    owners = {hass.config_entries.async_get_entry(entry_id).domain for entry_id in device_config_entry_ids(scanner_device)}
    assert owners == {"bluetooth"}


async def test_get_device_reports_the_proxy_scanner_without_addresses(hass: HomeAssistant, proxy: dict) -> None:
    """A scoped key that reads the proxy sees its scanner; an unreadable peer is only counted."""
    body, outcome = await _tool(hass, "get_device", {"device_id": proxy["device"].id}, _key())
    assert outcome == "allowed"
    [row] = body["bluetooth_scanners"]
    assert row["scanner_type"] == "remote" and row["connectable"] is True
    assert row["connection_slots"] == {"total": 3, "free": 1}
    assert row["connected_device_ids"] == [proxy["visible"].id]
    assert row["other_connections"] == 1
    text = json.dumps(body)
    for address in (PROXY_SOURCE, PEER_VISIBLE, PEER_HIDDEN, STRANGER):
        assert address not in text and address.lower() not in text


async def test_unrelated_devices_carry_no_scanner_block(hass: HomeAssistant, proxy: dict) -> None:
    body, _ = await _tool(hass, "get_device", {"device_id": proxy["visible"].id}, _key())
    assert "bluetooth_scanners" not in body


async def test_pass_through_radio_network_has_scanners_and_nearby_without_payload_bytes(
    hass: HomeAssistant, proxy: dict,
) -> None:
    body, outcome = await _tool(hass, "get_radio_network", {}, _key(pass_through=True))
    assert outcome == "allowed"
    [network] = [n for n in body["networks"] if n.get("protocol") == "bluetooth"]
    scanner = next(row for row in network["scanners"] if row["source"] == PROXY_SOURCE)
    assert scanner["allocated"] == [PEER_VISIBLE, PEER_HIDDEN] and scanner["device_id"] is not None
    stranger = next(row for row in network["nearby"]["devices"] if row["address"] == STRANGER)
    assert stranger["name"] == "Neighbour Speaker" and stranger["rssi"] == -61
    assert stranger["manufacturer_ids"] == [76] and stranger["scanner_device_id"] == scanner["device_id"]
    assert "0102" not in json.dumps(body) and "1005" not in json.dumps(body)
    assert not {"raw", "manufacturer_data", "service_data"} & set(stranger)


async def test_scoped_and_exposure_filtered_keys_get_no_bluetooth_network(hass: HomeAssistant, proxy: dict) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    for key in (_key(), _key(pass_through=True)):
        if key.pass_through:
            key.use_assist_exposure = True
        body, _ = await _tool(hass, "get_radio_network", {}, key)
        assert not [n for n in body["networks"] if n.get("protocol") == "bluetooth"]
        assert "serial_ports" not in body


async def test_nearby_is_bounded_by_age_and_count(hass: HomeAssistant, proxy: dict, monkeypatch) -> None:
    from homeassistant.components import bluetooth
    from custom_components.phoenix_mcp import proxy_diagnostics

    now = bluetooth.MONOTONIC_TIME()
    for index in range(3):
        proxy["scanner"]._async_on_advertisement(
            f"22:33:44:55:66:0{index}", -70, None, [], {}, {}, None, {}, now - index)
    proxy["scanner"]._async_on_advertisement("22:33:44:55:66:99", -70, None, [], {}, {}, None, {}, now - 400)

    async def nearby() -> dict:
        body, _ = await _tool(hass, "get_radio_network", {}, _key(pass_through=True))
        return next(n for n in body["networks"] if n.get("protocol") == "bluetooth")["nearby"]

    # Age alone: under the default count limit every fresh device fits and the stale one is dropped.
    heard = {row["address"] for row in (await nearby())["devices"]}
    assert {f"22:33:44:55:66:0{index}" for index in range(3)} <= heard and "22:33:44:55:66:99" not in heard
    # Count alone: the newest are kept and the rest counted.
    monkeypatch.setattr(proxy_diagnostics, "PROXY_NEARBY_MAX_DEVICES", 2)
    capped = await nearby()
    assert len(capped["devices"]) == 2 and capped["omitted"] == len(heard) - 2


# ---------------------------------------------------------------------------
# Serial ports
# ---------------------------------------------------------------------------

STICK_PATH = "/dev/ttyUSB0"
STICK_SERIAL = "STICK-SERIAL-42"
NETWORK_PORT = "socket://192.168.1.50:6638"


def _consumers_supported() -> bool:
    """Core detects serial port consumers from HA 2026.9."""
    import importlib.util

    return importlib.util.find_spec("homeassistant.components.usb.consumers") is not None


@pytest.fixture
async def serial(hass: HomeAssistant) -> dict[str, Any]:
    """Core's usb component with one scanned stick and two ZHA entries, one on a network port."""
    from unittest.mock import patch
    from homeassistant.components.usb.models import USBDevice

    stick = USBDevice(device=STICK_PATH, vid="10C4", pid="EA60", serial_number=STICK_SERIAL,
                      manufacturer="Silicon Labs", description="slae.sh cc2652rb stick")
    entries = {}
    for name, path in (("Stick", STICK_PATH), ("Network", NETWORK_PORT)):
        entry = MockConfigEntry(domain="zha", title=f"Zigbee {name}", data={"device": {"path": path}})
        entry.add_to_hass(hass)
        _device_with_sensor(hass, entry, f"Coordinator {name}", set())
        entries[name] = entry
    with patch("homeassistant.components.usb.scan_serial_ports", return_value=[stick]):
        assert await async_setup_component(hass, "usb", {})
        await hass.async_block_till_done()
        yield entries


async def test_list_integrations_reports_port_usage_without_identity(hass: HomeAssistant, serial: dict) -> None:
    if not _consumers_supported():
        pytest.skip("Core reports serial port consumers from HA 2026.9")
    body, outcome = await _tool(hass, "list_integrations", {"domain": "zha"}, _key(cap_integration_write="confirm"))
    assert outcome == "allowed"
    rows = {row["entry_id"]: row for row in body["integrations"]}
    assert rows[serial["Stick"].entry_id]["serial_ports"] == [
        {"transport": "local", "description": "slae.sh cc2652rb stick", "present": True, "active": False}]
    assert rows[serial["Network"].entry_id]["serial_ports"] == [
        {"transport": "network", "description": None, "present": True, "active": False}]
    text = json.dumps(body)
    assert STICK_PATH not in text and STICK_SERIAL not in text and "192.168.1.50" not in text
    assert "discovered" not in body


async def test_pass_through_radio_network_inventories_every_port(hass: HomeAssistant, serial: dict) -> None:
    body, _ = await _tool(hass, "get_radio_network", {}, _key(pass_through=True))
    ports = {row["device"]: row for row in body["serial_ports"]["ports"]}
    stick = ports[STICK_PATH]
    assert stick["serial_number"] == STICK_SERIAL and (stick["vid"], stick["pid"]) == ("10C4", "EA60")
    assert "zha" in stick["matching_integrations"] and stick["discovery_flows"] == 0
    if _consumers_supported():
        assert [c["domain"] for c in stick["consumers"]] == ["zha"]
        assert ports["socket://<redacted>"]["present"] is True
    else:
        assert "consumers" not in stick and len(ports) == 1
    assert "192.168.1.50" not in json.dumps(body)


# ---------------------------------------------------------------------------
# Discovered config flows
# ---------------------------------------------------------------------------


@pytest.fixture
async def discoveries(hass: HomeAssistant, proxy: dict) -> None:
    """Three real discovery flows waiting for confirmation: Bluetooth, USB and zeroconf."""
    from homeassistant import config_entries
    from homeassistant.components import bluetooth
    from homeassistant.helpers.service_info.usb import UsbServiceInfo
    from pytest_homeassistant_custom_component.common import MockModule, mock_config_flow, mock_integration, mock_platform

    class DiscoveryFlow(config_entries.ConfigFlow):
        VERSION = 1

        async def _wait(self, placeholders: dict) -> Any:
            self.context["title_placeholders"] = placeholders
            return self.async_show_form(step_id="confirm")

        async def async_step_confirm(self, user_input: Any = None) -> Any:
            return self.async_abort(reason="not_confirmed")

        async def async_step_bluetooth(self, discovery_info: Any) -> Any:
            return await self._wait({"name": discovery_info.name})

        async def async_step_usb(self, discovery_info: Any) -> Any:
            return await self._wait({"name": "Zigbee stick"})

        async def async_step_zeroconf(self, discovery_info: Any) -> Any:
            return await self._wait({"name": "Printer at 192.168.1.20", "host": "printer.local"})

    mock_integration(hass, MockModule("discoverable"))
    mock_platform(hass, "discoverable.config_flow", None)
    info = bluetooth.async_last_service_info(hass, STRANGER, connectable=False)
    usb = UsbServiceInfo(device=STICK_PATH, vid="10C4", pid="EA60", serial_number=STICK_SERIAL,
                         manufacturer="Silicon Labs", description="slae.sh cc2652rb stick")
    with mock_config_flow("discoverable", DiscoveryFlow):
        for source, data in (("bluetooth", info), ("usb", usb), ("zeroconf", {})):
            result = await hass.config_entries.flow.async_init("discoverable", context={"source": source}, data=data)
            assert result["type"] == "form", result


async def test_pass_through_lists_discovered_devices_without_lan_identity(hass: HomeAssistant, discoveries: None) -> None:
    body, outcome = await _tool(hass, "list_integrations", {}, _key(pass_through=True, cap_integration_write="confirm"))
    assert outcome == "allowed"
    rows = {row["source"]: row for row in body["discovered"] if row["domain"] == "discoverable"}
    assert rows["bluetooth"]["bluetooth"] == {"address": STRANGER, "name": "Neighbour Speaker", "rssi": -61}
    assert rows["usb"]["usb"]["serial_number"] == STICK_SERIAL and rows["usb"]["usb"]["vid"] == "10C4"
    assert "host" not in rows["zeroconf"]["title_placeholders"]
    text = json.dumps(body["discovered"])
    assert "192.168.1.20" not in text and "printer.local" not in text
    assert body["discovered_omitted"] == 0


async def test_discovered_devices_need_a_key_that_sees_what_ha_sees(hass: HomeAssistant, discoveries: None) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    filtered = _key(pass_through=True, cap_integration_write="confirm")
    filtered.use_assist_exposure = True
    for key, args in ((_key(cap_integration_write="confirm"), {}), (filtered, {}),
                      (_key(pass_through=True, cap_integration_write="confirm"), {"offset": 1})):
        body, outcome = await _tool(hass, "list_integrations", args, key)
        assert outcome == "allowed" and "discovered" not in body
