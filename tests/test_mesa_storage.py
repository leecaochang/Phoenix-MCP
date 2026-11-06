"""MESA's stored policy: corrupt storage refuses, and a failed save is not success.

The corrupt-file tests write a real file and let Home Assistant's own Store find
it, so they depend on what the Store actually does with an undecodable file rather
than on a model of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage
from homeassistant.util.dt import utcnow
from homeassistant.util.file import WriteError

from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord,
    AccessKeyStore,
    GlobalSettings,
    PermissionTree,
)
from custom_components.phoenix_mcp.admin.mesa import PhoenixAdminMesaProfileView
from custom_components.phoenix_mcp.const import DOMAIN, MESA_STORAGE_KEY
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.durable_storage import MesaSaveError
from custom_components.phoenix_mcp.mesa import (
    MesaStorageCorruptError,
    async_setup_mesa,
    evaluate_service_entities,
)
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from tests.runtime_fixtures import mesa_admin_request as _admin_request

_WRITE = "homeassistant.helpers.storage.Store._async_write_data"


@pytest.fixture
def real_store_files(hass: HomeAssistant):
    """Put back the file layer the test harness swaps for a dictionary."""
    harness_layer = getattr(storage, "_FileStoreIO", None)
    if harness_layer is None:
        pytest.skip("this Home Assistant keeps no separate Store file layer")
    with patch.object(storage, "_FileStoreIO", harness_layer.__mro__[1]):
        yield


def _profile(key: str, mode: str) -> SemanticProfile:
    return SemanticProfile.from_dict(
        key,
        {"semantic_profile": {"operational_boundaries": {"control_mode": mode}}},
        default_origin=MetadataOrigin.USER,
    )


def _storage_file(hass: HomeAssistant) -> Path:
    path = Path(hass.config.path(".storage", MESA_STORAGE_KEY))
    path.parent.mkdir(exist_ok=True)
    return path


def _valid_file(profiles: dict) -> str:
    return json.dumps({"version": 1, "minor_version": 1, "key": MESA_STORAGE_KEY,
                       "data": {"profiles": profiles, "dismissed_suggestions": []}})


async def test_a_corrupt_file_refuses_and_keeps_refusing_after_a_reload(hass: HomeAssistant, real_store_files):
    """Home Assistant moves the file aside, so the second start finds nothing there."""
    path = _storage_file(hass)
    path.write_text('{"version": 1, "data": {"profi')

    with pytest.raises(MesaStorageCorruptError, match="corrupt"):
        await async_setup_mesa(hass, "enforced")
    assert not path.exists()
    assert list(path.parent.glob(f"{MESA_STORAGE_KEY}.corrupt.*"))

    with pytest.raises(MesaStorageCorruptError):
        await async_setup_mesa(hass, "enforced")


async def test_restoring_the_corrupt_copy_or_deleting_it_recovers(hass: HomeAssistant, real_store_files):
    path = _storage_file(hass)
    path.write_text("not json")
    with pytest.raises(MesaStorageCorruptError):
        await async_setup_mesa(hass, "enforced")

    path.write_text(_valid_file({"light.vault": _profile("light.vault", "prohibited").to_dict()}))
    runtime = await async_setup_mesa(hass, "enforced")
    assert runtime.store.get("light.vault") is not None

    path.unlink()
    with pytest.raises(MesaStorageCorruptError):
        await async_setup_mesa(hass, "enforced")
    for copy in path.parent.glob(f"{MESA_STORAGE_KEY}.corrupt.*"):
        copy.unlink()
    runtime = await async_setup_mesa(hass, "enforced")
    assert runtime.backend.list_keys() == []


async def test_a_copy_left_by_the_store_refuses_with_nothing_in_its_place(hass: HomeAssistant):
    path = _storage_file(hass)
    path.with_name(f"{MESA_STORAGE_KEY}.corrupt.2026-01-01T00:00:00+00:00").write_text("not json")
    with pytest.raises(MesaStorageCorruptError, match="corrupt"):
        await async_setup_mesa(hass, "enforced")


async def test_no_file_at_all_is_an_empty_store(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    assert runtime.backend.list_keys() == []


@pytest.mark.parametrize("profiles", [[], "", 0, None, "text", ["light.a"], {"light.a": "text"}, {"light.a": None}])
async def test_an_invalid_shape_refuses(hass: HomeAssistant, hass_storage, profiles):
    hass_storage[MESA_STORAGE_KEY] = {"version": 1, "minor_version": 1, "key": MESA_STORAGE_KEY,
                                      "data": {"profiles": profiles}}
    with pytest.raises(MesaStorageCorruptError):
        await async_setup_mesa(hass, "enforced")


def _document(data) -> str:
    return json.dumps({"version": 1, "minor_version": 1, "key": MESA_STORAGE_KEY, "data": data})


@pytest.mark.parametrize("text", [
    "{}",
    _document(None),
    _document({}),
    _document({"dismissed_suggestions": []}),
    _document({"profiles": {}, "registry_ids": []}),
], ids=["empty_object", "null_data", "empty_data", "no_profiles_key", "bad_registry_ids"])
async def test_a_valid_file_that_holds_no_profile_set_refuses(hass: HomeAssistant, real_store_files, text):
    """Phoenix always writes the set, so no profiles is a conclusion only an absent file supports."""
    _storage_file(hass).write_text(text)
    with pytest.raises(MesaStorageCorruptError):
        await async_setup_mesa(hass, "enforced")


async def test_a_saved_empty_set_is_not_a_damaged_file(hass: HomeAssistant, real_store_files):
    _storage_file(hass).write_text(_document({"profiles": {}, "dismissed_suggestions": []}))
    assert (await async_setup_mesa(hass, "enforced")).backend.list_keys() == []


async def test_a_failed_save_raises_and_withdraws_the_change(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    runtime.store.set("light.vault", _profile("light.vault", "prohibited"))
    await runtime.async_save()

    runtime.store.delete("light.vault")
    runtime.dismissed_suggestions.add("suggestion")
    with patch(_WRITE, side_effect=WriteError("disk full")), pytest.raises(MesaSaveError):
        await runtime.async_save()

    # A reported failure must not leave a loosened policy in force.
    assert runtime.store.get("light.vault") is not None
    assert runtime.dismissed_suggestions == set()
    key = AccessKeyRecord(id="k", name="k", access_key_hash="x", created_at=utcnow(), created_by="t",
                          permissions=PermissionTree())
    verdict = evaluate_service_entities(
        runtime, "enforced", key, ["light.vault"], domain="light", service="turn_on", service_data={}, session_id="s",
    )
    assert verdict.blocked


async def test_a_failed_admin_save_is_reported(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "advisory")
    store = MagicMock(spec=AccessKeyStore)
    store.get_settings.return_value = GlobalSettings(mesa_mode="advisory")
    hass.data[DOMAIN] = PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), mesa=runtime)
    view = PhoenixAdminMesaProfileView()
    view.hass = hass
    body = json.dumps({"semantic_profile": {"operational_boundaries": {"control_mode": "prohibited"}}}).encode()

    with patch(_WRITE, side_effect=WriteError("disk full")):
        response = await view.put(_admin_request(body=body), entity_id="light.kitchen")

    assert response.status == 503
    payload = json.loads(response.text)
    assert payload["error"] == "service_unavailable"
    assert payload["message_key"] == "adminError.mesaSaveFailed"
    assert runtime.store.get("light.kitchen") is None
