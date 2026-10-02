"""Real HA writer faults cannot acknowledge an unwritten execution barrier."""

from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import CoreState
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError

from custom_components.phoenix_mcp.agent_recovery import AgentRecovery, RecoveryRequired
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecovery
from custom_components.phoenix_mcp.durable_storage import DurableStorageError, DurableStore
from custom_components.phoenix_mcp.token_store import TokenStore
from custom_components.phoenix_mcp.tools.calendar import CalendarWriteJournal


# Capture before the HA fixture installs its in-memory storage replacement.
_REAL_WRITE = Store._async_write_data
_REAL_LOAD = Store._async_load


@pytest.fixture(autouse=True)
def real_storage(hass):
    """Qualify the installed HA writer, not the harness's simulated persistence."""
    with patch.object(Store, "_async_write_data", _REAL_WRITE), patch.object(Store, "_async_load", _REAL_LOAD):
        yield


@pytest.mark.parametrize("failure", [WriteError("disk unavailable"), SerializationError("unsupported value")])
@pytest.mark.parametrize("owner", ["configuration", "agent", "calendar", "token"])
async def test_critical_storage_raises_when_real_ha_writer_fails(hass, failure, owner):
    """Patch only HA's final writer, preserving the swallowing handler under test."""
    store = DurableStore(hass, 1, "critical-test", atomic_writes=True)
    with patch.object(Store, "_write_prepared_data", side_effect=failure):
        # Positive reproduction: ordinary HA storage logs this fault and returns.
        ordinary = Store(hass, 1, "ordinary-control")
        await ordinary.async_save({"claim": "not-written"})
        assert not Path(ordinary.path).exists()
        if owner == "configuration":
            recovery = ConfigurationRecovery(store)
            with pytest.raises(DurableStorageError):
                await recovery.begin("resource", "delete", "token", {})
            assert recovery.records()[0]["phase"] == "prepared"
            assert not recovery.records()[0]["active"]
        elif owner == "agent":
            recovery = AgentRecovery(store)
            with pytest.raises(RecoveryRequired, match="nothing was dispatched"):
                await recovery.begin("token", "call_service", "request")
            assert recovery.blocks("token")
        elif owner == "calendar":
            journal = CalendarWriteJournal(hass)
            journal.records = [{"receipt_id": "pending", "status": "outcome_unknown"}]
            with pytest.raises(DurableStorageError):
                await journal.save()
        else:
            tokens = await TokenStore.async_create(hass)
            with pytest.raises(DurableStorageError):
                await tokens.async_create_token("test", "admin")
        assert not Path(store.path).exists()


async def test_critical_store_writes_reloadable_record_and_rejects_deferred_writes(hass):
    store = DurableStore(hass, 1, "durable-success", atomic_writes=True)
    await store.async_save({"claim": "before-dispatch"})
    assert Path(store.path).is_file()
    fresh = DurableStore(hass, 1, "durable-success")
    assert await fresh.async_load() == {"claim": "before-dispatch"}
    readonly = DurableStore(hass, 1, "readonly", read_only=True)
    with pytest.raises(DurableStorageError):
        await readonly.async_save({"claim": "no"})
    hass.set_state(CoreState.stopping)
    with pytest.raises(DurableStorageError):
        await store.async_save({"claim": "deferred"})
    assert await fresh.async_load() == {"claim": "before-dispatch"}
