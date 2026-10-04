"""Registry commits stay fresh and complete before asynchronous persistence."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp import mcp_view as view
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.request_authority import (
    AuthorityChangedError, RequestAuthority, policy_version, request_authority,
)
from custom_components.phoenix_mcp.access_key_store import (
    GlobalSettings, PermissionNode, PermissionTree, AccessKeyRecord, AccessKeyStore,
)
from custom_components.phoenix_mcp.version_store import VersionRecord


@pytest.fixture
def commit_env(hass: HomeAssistant):
    owner = MockConfigEntry(domain="test_integration", entry_id="commit-owner")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={(owner.domain, "commit-device")},
    )
    entities = [er.async_get(hass).async_get_or_create(
        "light", owner.domain, name, config_entry=owner,
        device_id=device.id, suggested_object_id=name,
    ).entity_id for name in ("first", "second")]
    for entity_id in entities:
        hass.states.async_set(entity_id, "off")
    labels = [lr.async_get(hass).async_create(name).label_id for name in ("one", "two")]
    access_key = AccessKeyRecord(
        id="commit-token", name="Commit test", access_key_hash="test-hash",
        created_at=utcnow(), created_by="test", cap_registry_write="allow",
        permissions=PermissionTree(
            domains={"light": PermissionNode(state="GREEN")},
            devices={device.id: PermissionNode(state="GREEN")},
        ),
    )
    store = MagicMock(spec=AccessKeyStore)
    store.get_settings.return_value = GlobalSettings(mesa_mode="off")
    store.get_access_key_by_hash.return_value = access_key
    store.list_access_keys.return_value = []
    store.get_entity_hints.return_value = {}
    data = PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), hass=hass)
    hass.data[DOMAIN] = data
    yield access_key, data, device.id, entities, labels
    if data.sensor_flush_cancel is not None:
        data.sensor_flush_cancel()


def _body(result):
    assert result[1] == "allowed", result
    return json.loads(result[0]["content"][0]["text"])


@pytest.mark.parametrize("kind", ["entity", "device"])
@pytest.mark.parametrize("second_operation", ["add", "remove", "restore"])
async def test_overlapping_writes_capture_their_own_results(
    hass, commit_env, kind, second_operation
):
    access_key, data, device_id, entities, labels = commit_env
    target = entities[0] if kind == "entity" else device_id
    execute = getattr(view, f"_execute_set_{kind}")
    entered, release = asyncio.Event(), asyncio.Event()
    captured = []

    async def record(*args, **kwargs):
        captured.append(kwargs)
        if len(captured) == 1:
            entered.set()
            await release.wait()

    with patch.object(view, "_record_version", side_effect=record):
        first = asyncio.create_task(execute(
            {f"{kind}_id": target, "add_labels": [labels[0]]}, access_key, hass, data
        ))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if second_operation == "restore":
                # Exercise the actual admin restore route with a historical snapshot.
                snapshot = VersionRecord(
                    id="restore-test", resource_type=kind, resource_id=target,
                    action="edit", before={"labels": [labels[1]]}, after={"labels": []},
                    timestamp=utcnow().isoformat(), alias=None, access_key_id=None,
                    access_key_name=None, request_id=None, approved_by_user_id=None,
                )
                second = await view.async_restore_version(snapshot, "admin", hass, data, side="before")
            else:
                second = await execute(
                    {f"{kind}_id": target,
                     "add_labels" if second_operation == "add" else "remove_labels":
                         [labels[1] if second_operation == "add" else labels[0]]},
                    access_key, hass, data,
                )
            _body(second)
            expected = sorted(labels) if second_operation == "add" else (
                [labels[1]] if second_operation == "restore" else []
            )
            assert captured[0]["before"]["labels"] == []
            assert captured[0]["after"]["labels"] == [labels[0]]
            assert captured[1]["before"]["labels"] == [labels[0]]
            assert captured[1]["after"]["labels"] == expected
        finally:
            release.set()
            first_result = await first
    assert _body(first_result)["updated"]["labels"] == [labels[0]]
    registry = er.async_get(hass) if kind == "entity" else dr.async_get(hass)
    assert sorted(registry.async_get(target).labels) == expected


async def test_aliases_and_category_scopes_merge_during_history_wait(hass, commit_env):
    access_key, data, _, entities, _ = commit_env
    categories = {
        scope: cr.async_get(hass).async_create(scope=scope, name=scope).category_id
        for scope in ("first", "second")
    }
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def record(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()

    with patch.object(view, "_record_version", side_effect=record):
        task = asyncio.create_task(view._execute_set_entity(
            {"entity_id": entities[0], "add_aliases": ["first alias"],
             "categories": {"first": categories["first"]}}, access_key, hass, data,
        ))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = await view._execute_set_entity(
                {"entity_id": entities[0], "add_aliases": ["second alias"],
                 "categories": {"second": categories["second"]}}, access_key, hass, data,
            )
            _body(second)
        finally:
            release.set()
            first = await task
    assert "second alias" not in _body(first)["aliases"]
    entry = er.async_get(hass).async_get(entities[0])
    assert {"first alias", "second alias"} <= set(entry.aliases)
    assert entry.categories == categories


async def test_rename_then_delete_does_not_reopen_first_commit(hass, commit_env):
    access_key, data, _, entities, _ = commit_env
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def record(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()

    renamed = "light.renamed"
    with patch.object(view, "_record_version", side_effect=record):
        task = asyncio.create_task(view._execute_set_entity(
            {"entity_id": entities[0], "new_entity_id": renamed}, access_key, hass, data
        ))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            _body(await view._execute_delete_entity({"entity_id": renamed}, access_key, hass, data))
            # Independent resources can finish while the first history save is blocked.
            _body(await view._execute_set_entity(
                {"entity_id": entities[1], "name": "Independent"}, access_key, hass, data
            ))
        finally:
            release.set()
            result = await task
    assert _body(result)["entity_id"] == renamed
    assert er.async_get(hass).async_get(renamed) is None


@pytest.mark.parametrize("change", ["label", "category", "scope", "authority"])
async def test_commit_revalidates_after_preparation_wait(hass, commit_env, change):
    access_key, data, _, entities, labels = commit_env
    category = cr.async_get(hass).async_create(scope="test", name="test")
    entered, release = asyncio.Event(), asyncio.Event()

    async def gate(*args, **kwargs):
        entered.set()
        await release.wait()

    args = {"entity_id": entities[0], "changes": {
        "add_labels": [labels[0]], "categories": {"test": category.category_id},
    }}
    authority_ctx = request_authority.set(RequestAuthority(hass, data, access_key.id, access_key.access_key_hash))
    version_ctx = policy_version.set(access_key.settings_version)
    try:
        with patch.object(view, "_gate", side_effect=gate):
            task = asyncio.create_task(view._tool_set_entity(args, access_key, hass, data))
            await asyncio.wait_for(entered.wait(), 1)
            if change == "label":
                lr.async_get(hass).async_delete(labels[0])
            elif change == "category":
                cr.async_get(hass).async_delete(scope="test", category_id=category.category_id)
            elif change == "scope":
                access_key.permissions.domains["light"].state = "RED"
            else:
                data.store.get_access_key_by_hash.return_value = None
            release.set()
            if change == "authority":
                with pytest.raises(AuthorityChangedError):
                    await task
            else:
                result = await task
                assert result[1] in {"denied", "invalid_request"}
    finally:
        release.set()
        policy_version.reset(version_ctx)
        request_authority.reset(authority_ctx)
    assert er.async_get(hass).async_get(entities[0]).labels == set()


async def test_cancellation_after_commit_keeps_write_and_allows_next_write(hass, commit_env):
    access_key, data, _, entities, labels = commit_env
    entered = asyncio.Event()

    async def record(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    with patch.object(view, "_record_version", side_effect=record):
        task = asyncio.create_task(view._execute_set_entity(
            {"entity_id": entities[0], "add_labels": [labels[0]]}, access_key, hass, data
        ))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    _body(await view._execute_set_entity(
        {"entity_id": entities[0], "add_labels": [labels[1]]}, access_key, hass, data
    ))
    assert er.async_get(hass).async_get(entities[0]).labels == set(labels)


async def test_cancelled_preparation_does_not_commit(hass, commit_env):
    access_key, data, _, entities, labels = commit_env
    entered = asyncio.Event()

    async def gate(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    with patch.object(view, "_gate", side_effect=gate), patch.object(view, "_record_version", AsyncMock()) as record:
        task = asyncio.create_task(view._tool_set_entity(
            {"entity_id": entities[0], "changes": {"add_labels": [labels[0]]}}, access_key, hass, data
        ))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        record.assert_not_awaited()
    assert er.async_get(hass).async_get(entities[0]).labels == set()


async def test_batch_keeps_successful_registry_progress_when_later_item_fails(hass, commit_env):
    access_key, data, _, entities, labels = commit_env
    items = [
        {"jsonrpc": "2.0", "id": index, "method": "tools/call", "params": {
            "name": "set_entity", "arguments": {"entity_id": entities[0],
                "changes": {"add_labels": [label]}},
        }}
        for index, label in enumerate([labels[0], "missing-label", labels[1]], 1)
    ]
    results = await view._dispatch_streamable_batch(
        items, access_key, hass, data, "test", base_url="http://test"
    )
    assert [item["id"] for item in results] == [1, 2, 3]
    assert not results[0]["result"].get("isError")
    assert results[1]["result"]["isError"]
    assert not results[2]["result"].get("isError")
    assert er.async_get(hass).async_get(entities[0]).labels == set(labels)
    assert len(data.versions.list_for("entity", entities[0])) == 2
