"""Tests for list_dashboard_resources and set_dashboard_resource.

Both run against Home Assistant's real lovelace resource collection. They pin the
capability boundary (cap_dashboard_resources, exempt from pass-through), the
unreadable/empty/YAML distinctions, every mutation's refusals before approval,
the private approval binding, recoverable and verified writes with their
uncertain outcomes, restoration, and card-catalog freshness after a change.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionTree
from custom_components.phoenix_mcp.card_catalog import CardCatalogStore, assess_freshness
from custom_components.phoenix_mcp.config_reconciliation import _matches, observe_configuration
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError
from custom_components.phoenix_mcp.const import DASHBOARD_RESOURCE_BINDING, REDACTION_SENTINEL
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.request_authority import (
    AuthorityChangedError, RequestAuthority, policy_version, request_authority,
)
from custom_components.phoenix_mcp.tool_common import _approved_exec_ctx
from custom_components.phoenix_mcp.tools import dashboard_resources as resources_tool
from custom_components.phoenix_mcp.tools.recovery import _tool_restore_configuration
from custom_components.phoenix_mcp.ws_dispatch import (
    WsCommandNotDispatchedError, WsDispatchError, async_lovelace_resources,
)


def _key(**caps) -> AccessKeyRecord:
    base = {"cap_dashboard_resources": "allow"}
    base.update(caps)
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(),
        created_by="u", permissions=PermissionTree(domains={}), **base,
    )


def _data(catalog: CardCatalogStore | None = None) -> PhoenixData:
    return PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(),
                       card_catalog=catalog or CardCatalogStore())


def _body(result) -> dict:
    return json.loads(result[0]["content"][0]["text"])


def _text(result) -> str:
    return result[0]["content"][0]["text"]


async def _call(name, args, key, hass, data):
    return await _call_tool(name, args, key, hass, data)


async def _items(hass) -> list[dict]:
    listing = await async_lovelace_resources(hass)
    assert listing is not None
    return listing.items


@pytest.fixture
async def lovelace(hass: HomeAssistant, hass_admin_user):
    """Real storage-mode lovelace holding two resources; the admin user runs HA's commands."""
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    collection = hass.data[LOVELACE_DATA].resources
    await collection.async_create_item({"res_type": "module", "url": "/hacsfiles/mushroom/mushroom.js?hacstag=1"})
    await collection.async_create_item({"res_type": "css", "url": "/local/theme.css"})
    return hass


async def _create(hass, data, key=None, url="/local/new-card.js", type_="module"):
    return await _call("set_dashboard_resource", {"operation": "create", "url": url, "type": type_},
                       key or _key(), hass, data)


# --- listing -----------------------------------------------------------------


async def test_list_returns_ids_types_and_urls(lovelace):
    result = await _call("list_dashboard_resources", {}, _key(), lovelace, _data())
    assert result[1] == "allowed"
    body = _body(result)
    assert body["resource_mode"] == "storage" and body["editable"] is True and body["count"] == 2
    assert {(r["type"], r["url"]) for r in body["resources"]} == {
        ("module", "/hacsfiles/mushroom/mushroom.js?hacstag=1"), ("css", "/local/theme.css")}
    assert all(isinstance(r["id"], str) and r["id"] for r in body["resources"])


async def test_an_empty_registry_is_an_empty_list(hass, hass_admin_user):
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    body = _body(await _call("list_dashboard_resources", {}, _key(), hass, _data()))
    assert body["resources"] == [] and body["count"] == 0


async def test_an_unreadable_list_is_an_error_not_an_empty_one(hass):
    """Without lovelace the resources are unknown, never 'none registered'."""
    result = await _call("list_dashboard_resources", {}, _key(), hass, _data())
    assert result[1] == "invalid_request" and result[0]["isError"]
    assert "could not be read" in _text(result)


async def test_safe_mode_is_unreadable_rather_than_empty(lovelace):
    lovelace.config.safe_mode = True
    try:
        result = await _call("list_dashboard_resources", {}, _key(), lovelace, _data())
    finally:
        lovelace.config.safe_mode = False
    assert result[1] == "invalid_request"


async def test_yaml_mode_counts_but_never_lists_resolved_values(hass, hass_admin_user):
    """HA resolves !secret in YAML resources, so their URLs are withheld."""
    assert await async_setup_component(hass, "lovelace", {"lovelace": {
        "resource_mode": "yaml", "resources": [{"url": "/local/a.js?token-from-secret", "type": "module"}],
    }})
    result = await _call("list_dashboard_resources", {}, _key(), hass, _data())
    body = _body(result)
    assert body == {**body, "resource_mode": "yaml", "editable": False, "count": 1}
    assert "resources" not in body and "token-from-secret" not in _text(result)
    assert "get_yaml_config" in body["message"]


async def test_listing_redacts_url_credentials(lovelace):
    await lovelace.data[LOVELACE_DATA].resources.async_create_item(
        {"res_type": "module", "url": "https://cdn.example/card.js?access_token=abcdef123456"})
    result = await _call("list_dashboard_resources", {}, _key(), lovelace, _data())
    assert "abcdef123456" not in _text(result)


async def test_listing_marks_a_resource_that_failed_to_load(lovelace):
    catalog = CardCatalogStore()
    await catalog.async_replace(entries=[], resource_count=2,
                                failed_imports=[{"url": "/local/theme.css", "error": "404 <script>"}],
                                resources=await _items(lovelace))
    body = _body(await _call("list_dashboard_resources", {}, _key(), lovelace, _data(catalog)))
    flagged = {r["url"]: r.get("load_failed", False) for r in body["resources"]}
    assert flagged == {"/hacsfiles/mushroom/mushroom.js?hacstag=1": False, "/local/theme.css": True}
    assert body["load_checked_at"] == catalog.catalog.harvested_at
    assert "<script>" not in json.dumps(body)


@pytest.mark.parametrize("tool,args", [
    ("list_dashboard_resources", {}),
    ("set_dashboard_resource", {"operation": "create", "url": "/local/x.js", "type": "module"}),
])
async def test_denied_without_the_capability(lovelace, tool, args):
    before = await _items(lovelace)
    result = await _call(tool, args, _key(cap_dashboard_resources="deny", cap_lovelace_write="allow"),
                         lovelace, _data())
    assert result[1] == "denied" and _text(result).startswith("Forbidden")
    assert await _items(lovelace) == before


async def test_pass_through_cannot_grant_the_capability(lovelace):
    key = _key(cap_dashboard_resources="deny")
    key.pass_through = True
    for tool, args in (("list_dashboard_resources", {}),
                       ("set_dashboard_resource", {"operation": "delete", "resource_id": "x"})):
        assert (await _call(tool, args, key, lovelace, _data()))[1] == "denied"


async def test_confirm_lists_without_an_approval(lovelace):
    data = _data()
    with patch("custom_components.phoenix_mcp.helpers.async_evaluate_capability") as evaluate:
        result = await _call("list_dashboard_resources", {}, _key(cap_dashboard_resources="confirm"),
                             lovelace, data)
    assert result[1] == "allowed"
    evaluate.assert_not_called()


# --- mutations ---------------------------------------------------------------


async def test_create_registers_a_verified_recoverable_resource(lovelace):
    data = _data()
    result = await _create(lovelace, data)
    assert result[1] == "allowed", result
    body = _body(result)
    assert body["status"] == "verified"
    assert body["resource"]["url"] == "/local/new-card.js" and body["resource"]["type"] == "module"
    assert "next page reload" in body["note"]
    assert any(item["url"] == "/local/new-card.js" for item in await _items(lovelace))
    record = data.config_recovery.get(body["recovery_id"])
    assert record["phase"] == "verified" and record["before"]["exists"] is False
    assert record["source"]["resource"]["id"] == body["resource"]["id"]


async def test_update_changes_only_the_given_fields(lovelace):
    target = next(i for i in await _items(lovelace) if i["url"] == "/local/theme.css")
    result = await _call("set_dashboard_resource", {"operation": "update", "resource_id": target["id"],
                                                    "url": "/local/theme2.css"}, _key(), lovelace, _data())
    assert _body(result)["status"] == "verified"
    updated = next(i for i in await _items(lovelace) if i["id"] == target["id"])
    assert updated["url"] == "/local/theme2.css" and updated["type"] == "css"


async def test_delete_removes_the_resource(lovelace):
    target = (await _items(lovelace))[0]
    result = await _call("set_dashboard_resource", {"operation": "delete", "resource_id": target["id"]},
                         _key(), lovelace, _data())
    assert _body(result)["status"] == "verified"
    assert target["id"] not in {i["id"] for i in await _items(lovelace)}


async def _refusal(hass, args, key=None):
    before = await _items(hass)
    data = _data()
    result = await _call("set_dashboard_resource", args, key or _key(), hass, data)
    assert result[0].get("isError"), result
    assert await _items(hass) == before
    assert data.config_recovery.records() == []
    return result


@pytest.mark.parametrize("args,needle", [
    ({"operation": "rename"}, "operation must be"),
    ({"operation": "create", "url": "/local/x.js"}, "create requires url and type"),
    ({"operation": "create", "url": "/local/x.js", "type": "html"}, "type must be one of"),
    ({"operation": "create", "url": "/local/x.js", "type": "module", "resource_id": "x"}, "omit resource_id"),
    ({"operation": "create", "url": "  ", "type": "module"}, "non-empty"),
    ({"operation": "create", "url": "/local/my card.js", "type": "module"}, "spaces or control"),
    ({"operation": "create", "url": "/local/x" + "y" * 2100 + ".js", "type": "module"}, "at most"),
    ({"operation": "create", "url": f"/local/{REDACTION_SENTINEL}.js", "type": "module"}, "redacted read"),
    ({"operation": "create", "url": "https://u:p@cdn.example/x.js", "type": "module"}, "credential"),
    ({"operation": "create", "url": "/local/x.js", "type": "module", "verify": True}, "Unknown argument"),
    ({"operation": "create", "url": "/local/x.js", "type": "module", DASHBOARD_RESOURCE_BINDING: "x"},
     "Unknown argument"),
    ({"operation": "create", "url": "/local/theme.css", "type": "css"}, "already registered"),
    ({"operation": "update"}, "requires the resource_id"),
    ({"operation": "delete", "resource_id": "x", "url": "/local/a.js"}, "No dashboard resource"),
])
async def test_create_and_shape_refusals_change_nothing(lovelace, args, needle):
    assert needle in _text(await _refusal(lovelace, args))


async def test_unknown_resource_id_is_not_found(lovelace):
    result = await _refusal(lovelace, {"operation": "delete", "resource_id": "0" * 32})
    assert result[1] == "not_found"


async def test_update_and_delete_refusals_change_nothing(lovelace):
    items = await _items(lovelace)
    theme = next(i for i in items if i["url"] == "/local/theme.css")
    other = next(i for i in items if i["id"] != theme["id"])
    cases = [
        ({"operation": "update", "resource_id": theme["id"]}, "url, type, or both"),
        ({"operation": "update", "resource_id": theme["id"], "url": "/local/theme.css"}, "nothing would change"),
        ({"operation": "update", "resource_id": theme["id"], "url": other["url"]}, "already registered"),
        ({"operation": "delete", "resource_id": theme["id"], "type": "css"}, "only resource_id"),
    ]
    for args, needle in cases:
        assert needle in _text(await _refusal(lovelace, args)), args


async def test_the_same_file_under_another_tag_is_a_warning(lovelace):
    result = await _create(lovelace, _data(), url="/hacsfiles/mushroom/mushroom.js?hacstag=2")
    body = _body(result)
    assert body["status"] == "verified"
    assert "already loads the same file" in body["warnings"][0]


async def test_yaml_mode_cannot_be_changed(hass, hass_admin_user):
    assert await async_setup_component(hass, "lovelace", {"lovelace": {
        "resource_mode": "yaml", "resources": [{"url": "/local/a.js", "type": "module"}]}})
    result = await _create(hass, _data())
    assert result[1] == "invalid_request" and "defined in YAML" in _text(result)


async def test_home_assistant_validator_refusal_is_reported_before_approval(lovelace):
    data = _data()
    with patch.object(resources_tool, "validate_ws_payload",
                      side_effect=WsCommandNotDispatchedError("Invalid arguments: bad url")):
        result = await _create(lovelace, data, key=_key(cap_dashboard_resources="confirm"))
    assert result[1] == "invalid_request" and "Home Assistant refused the resource" in _text(result)
    assert data.config_recovery.records() == []


# --- approval ------------------------------------------------------------------


async def _pending(hass, data, args, key):
    captured = {}

    async def fake_create(store, **kwargs):
        captured.update(kwargs)
        return MagicMock(id="appr", tool_name=kwargs["tool_name"], args=kwargs["args"], diff=kwargs["diff"])

    with patch("custom_components.phoenix_mcp.approvals.async_create_pending_approval", side_effect=fake_create), \
            patch("custom_components.phoenix_mcp.approvals.create_approval_notification"), \
            patch("custom_components.phoenix_mcp.approvals.fire_approval_requested_event"), \
            patch("custom_components.phoenix_mcp.tool_common._pending_or_inline",
                  AsyncMock(return_value=({"content": []}, "pending_approval", "approval:appr"))):
        data.store.async_lock = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
        result = await _call("set_dashboard_resource", args, key, hass, data)
    return result, captured


async def test_confirm_creates_a_bound_approval_and_changes_nothing(lovelace):
    key, data = _key(cap_dashboard_resources="confirm"), _data()
    before = await _items(lovelace)
    result, approval = await _pending(lovelace, data, {"operation": "create", "url": "/local/x.js",
                                                       "type": "module"}, key)
    assert result[1] == "pending_approval"
    assert await _items(lovelace) == before
    assert approval["cap_name"] == "cap_dashboard_resources"
    assert approval["args"][DASHBOARD_RESOURCE_BINDING]
    diff = approval["diff"]
    assert diff["summary_key"] == "diff.dashboard_resource.create"
    assert diff["summary_params"] == {"url": "/local/x.js"}
    assert json.loads(diff["after"]) == {"type": "module", "url": "/local/x.js"} and diff["before"] is None
    assert "JavaScript file runs in every browser" in diff["preview"]["warning"]


async def test_a_stylesheet_carries_no_javascript_warning(lovelace):
    _, approval = await _pending(lovelace, _data(), {"operation": "create", "url": "/local/extra.css",
                                                     "type": "css"}, _key(cap_dashboard_resources="confirm"))
    assert "warning" not in approval["diff"]["preview"]


async def test_update_preview_shows_current_and_proposed(lovelace):
    theme = next(i for i in await _items(lovelace) if i["url"] == "/local/theme.css")
    _, approval = await _pending(lovelace, _data(), {"operation": "update", "resource_id": theme["id"],
                                                     "type": "module"}, _key(cap_dashboard_resources="confirm"))
    diff = approval["diff"]
    assert json.loads(diff["before"])["type"] == "css" and json.loads(diff["after"])["type"] == "module"
    assert diff["target"] == {"type": "dashboard_resource", "id": theme["id"], "label": "/local/theme.css"}


async def _approved(hass, data, args, key):
    token = _approved_exec_ctx.set(True)
    try:
        return await resources_tool._execute_set_dashboard_resource(args, key, hass, data)
    finally:
        _approved_exec_ctx.reset(token)


async def test_the_approved_change_runs_once_reviewed(lovelace):
    key, data = _key(cap_dashboard_resources="confirm"), _data()
    _, approval = await _pending(lovelace, data, {"operation": "create", "url": "/local/x.js",
                                                  "type": "module"}, key)
    result = await _approved(lovelace, data, approval["args"], key)
    assert _body(result)["status"] == "verified"


async def test_a_list_changed_after_review_refuses_the_approval(lovelace):
    key, data = _key(cap_dashboard_resources="confirm"), _data()
    _, approval = await _pending(lovelace, data, {"operation": "create", "url": "/local/x.js",
                                                  "type": "module"}, key)
    await lovelace.data[LOVELACE_DATA].resources.async_create_item({"res_type": "module", "url": "/local/y.js"})
    before = await _items(lovelace)
    result = await _approved(lovelace, data, approval["args"], key)
    assert "changed after this was reviewed" in _text(result)
    assert await _items(lovelace) == before and data.config_recovery.records() == []


async def test_a_revoked_capability_refuses_the_approval(lovelace):
    key, data = _key(cap_dashboard_resources="confirm"), _data()
    _, approval = await _pending(lovelace, data, {"operation": "create", "url": "/local/x.js",
                                                  "type": "module"}, key)
    key.cap_dashboard_resources = "deny"
    result = await _approved(lovelace, data, approval["args"], key)
    assert result[1] == "denied"
    assert "/local/x.js" not in {i["url"] for i in await _items(lovelace)}


async def test_a_confirm_executor_never_runs_unapproved(lovelace):
    result = await resources_tool._execute_set_dashboard_resource(
        {"operation": "create", "url": "/local/x.js", "type": "module"},
        _key(cap_dashboard_resources="confirm"), lovelace, _data())
    assert result[1] == "denied" and "requires approval" in _text(result)
    assert "/local/x.js" not in {i["url"] for i in await _items(lovelace)}


async def test_authority_lost_before_dispatch_sends_nothing(lovelace):
    """A policy change during the wait is caught at the last check before HA is called."""
    key, data = _key(), _data()
    data.ready = True
    data.store.get_access_key_by_hash.return_value = key
    data.store.get_settings.return_value.kill_switch = False
    lovelace.data["phoenix_mcp"] = data
    original = resources_tool.read_resource_snapshot

    async def bump_policy(*args):
        key.settings_version += 1
        return await original(*args)

    authority = request_authority.set(RequestAuthority(lovelace, data, key.id, key.access_key_hash))
    epoch = policy_version.set(key.settings_version)
    try:
        with patch.object(resources_tool, "read_resource_snapshot", side_effect=bump_policy), \
                patch.object(resources_tool, "async_ws_command") as dispatch, \
                pytest.raises(AuthorityChangedError):
            await resources_tool._execute_set_dashboard_resource(
                {"operation": "create", "url": "/local/x.js", "type": "module"}, key, lovelace, data)
    finally:
        request_authority.reset(authority)
        policy_version.reset(epoch)
    dispatch.assert_not_called()
    assert data.config_recovery.records()[0]["phase"] == "not_applied"


# --- uncertain and failed writes ----------------------------------------------


async def test_a_refused_command_is_not_applied_and_leaves_no_barrier(lovelace):
    data = _data()
    with patch.object(resources_tool, "async_ws_command",
                      AsyncMock(side_effect=WsCommandNotDispatchedError("refused"))):
        result = await _create(lovelace, data)
    assert result[1] == "invalid_request"
    assert data.config_recovery.records()[0]["phase"] == "not_applied"
    assert _body(await _create(lovelace, data))["status"] == "verified"


async def test_an_uncertain_command_keeps_the_barrier(lovelace):
    data = _data()
    with patch.object(resources_tool, "async_ws_command",
                      AsyncMock(side_effect=WsDispatchError("timed out after dispatch"))):
        result = await _create(lovelace, data)
    assert result[1] == "invalid_request"
    assert data.config_recovery.records()[0]["phase"] == "unknown"
    blocked = await _create(lovelace, data, url="/local/other.js")
    assert "Resolve the configuration recovery request" in _text(blocked)


async def test_a_readback_that_differs_is_partial(lovelace):
    data = _data()
    original = resources_tool.read_resource_snapshot
    calls = []

    async def stale(hass, lookup, key):
        snapshot = await original(hass, lookup, key)
        calls.append(snapshot)
        if len(calls) == 2 and snapshot.get("resource"):
            snapshot = {**snapshot, "resource": {**snapshot["resource"], "type": "js"}}
        return snapshot

    with patch.object(resources_tool, "read_resource_snapshot", side_effect=stale):
        result = await _create(lovelace, data)
    assert _body(result)["status"] == "partial"
    assert data.config_recovery.records()[0]["phase"] == "partial"


async def test_an_unreadable_readback_is_unknown(lovelace):
    data = _data()
    original = resources_tool.read_resource_snapshot
    calls = []

    async def fail_second(hass, lookup, key):
        calls.append(key)
        if len(calls) == 2:
            raise ConfigurationRecoveryError("unreadable")
        return await original(hass, lookup, key)

    with patch.object(resources_tool, "read_resource_snapshot", side_effect=fail_second):
        result = await _create(lovelace, data)
    assert _body(result)["status"] == "unknown"
    assert data.config_recovery.records()[0]["phase"] == "unknown"


async def test_a_change_while_preparing_dispatches_nothing(lovelace):
    data = _data()
    original = resources_tool.read_resource_snapshot

    async def moved(hass, lookup, key):
        snapshot = await original(hass, lookup, key)
        return {**snapshot, "exists": True}

    with patch.object(resources_tool, "read_resource_snapshot", side_effect=moved), \
            patch.object(resources_tool, "async_ws_command") as dispatch:
        result = await _create(lovelace, data)
    assert "changed while preparing" in _text(result)
    dispatch.assert_not_called()
    assert data.config_recovery.records()[0]["phase"] == "not_applied"


async def test_reconciliation_observes_an_uncertain_create(lovelace):
    """A create whose result was lost is found by its URL and matched without its ID."""
    data = _data()

    async def lost_result(hass, command, payload):
        await lovelace.data[LOVELACE_DATA].resources.async_create_item(dict(payload))
        raise WsDispatchError("timed out after dispatch")

    with patch.object(resources_tool, "async_ws_command", side_effect=lost_result):
        await _create(lovelace, data)
    record = data.config_recovery.records()[0]
    observed = await observe_configuration(lovelace, record)
    assert observed["exists"] is True and _matches(record, observed) is True


# --- restoration ---------------------------------------------------------------


async def _restore(hass, data, key, rid, side):
    args = {"recovery_id": rid, "side": side}
    receipt = await _tool_restore_configuration(args, key, hass, data)
    assert receipt[1] == "allowed", receipt
    info = _body(receipt)
    args.update({k: info[k] for k in ("snapshot_digest", "target_fingerprint")})
    return await _tool_restore_configuration(args, key, hass, data)


async def test_restoring_before_a_create_removes_it(lovelace):
    key, data = _key(), _data()
    created = _body(await _create(lovelace, data, key=key))
    restored = await _restore(lovelace, data, key, created["recovery_id"], "before")
    assert _body(restored)["status"] == "verified"
    assert "/local/new-card.js" not in {i["url"] for i in await _items(lovelace)}


async def test_restoring_before_an_update_reverts_it(lovelace):
    key, data = _key(), _data()
    theme = next(i for i in await _items(lovelace) if i["url"] == "/local/theme.css")
    changed = _body(await _call("set_dashboard_resource", {"operation": "update", "resource_id": theme["id"],
                                                           "url": "/local/theme2.css", "type": "module"},
                                key, lovelace, data))
    await _restore(lovelace, data, key, changed["recovery_id"], "before")
    current = next(i for i in await _items(lovelace) if i["id"] == theme["id"])
    assert (current["url"], current["type"]) == ("/local/theme.css", "css")


async def test_restoring_before_a_delete_recreates_it_once(lovelace):
    key, data = _key(), _data()
    theme = next(i for i in await _items(lovelace) if i["url"] == "/local/theme.css")
    deleted = _body(await _call("set_dashboard_resource", {"operation": "delete", "resource_id": theme["id"]},
                                key, lovelace, data))
    restored = _body(await _restore(lovelace, data, key, deleted["recovery_id"], "before"))
    assert restored["status"] == "verified"
    again = await _tool_restore_configuration({"recovery_id": deleted["recovery_id"], "side": "before"},
                                              key, lovelace, data)
    assert _body(again)["diff"]["before"] is not None  # the recreated resource is the target now
    assert [i["url"] for i in await _items(lovelace)].count("/local/theme.css") == 1


async def test_restore_needs_the_capability(lovelace):
    key, data = _key(), _data()
    created = _body(await _create(lovelace, data, key=key))
    key.cap_dashboard_resources = "deny"
    result = await _tool_restore_configuration({"recovery_id": created["recovery_id"], "side": "before"},
                                               key, lovelace, data)
    assert result[1] == "denied"


async def test_admin_recovery_restores_a_resource(lovelace, hass_admin_user):
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER
    from custom_components.phoenix_mcp.admin_view import PhoenixAdminConfigurationRecoveryRecordView

    data = _data()
    lovelace.data["phoenix_mcp"] = data
    created = _body(await _create(lovelace, data, key=_key()))
    values = {KEY_HASS_USER: hass_admin_user, KEY_AUTHENTICATED: True, "phoenix_mcp_rid": "test"}
    request = MagicMock()
    request.get.side_effect = lambda k, default=None: values.get(k, default)
    request.__getitem__.side_effect = values.__getitem__
    view = PhoenixAdminConfigurationRecoveryRecordView()
    view.hass = lovelace
    with patch("custom_components.phoenix_mcp.admin_view._read_body",
               AsyncMock(return_value={"action": "restore", "side": "before"})):
        response = await view.post(request, recovery_id=created["recovery_id"])
    assert response.status == 200, response.text
    assert json.loads(response.text)["outcome"] == "allowed"
    assert "/local/new-card.js" not in {i["url"] for i in await _items(lovelace)}


# --- card catalog freshness ----------------------------------------------------


@pytest.mark.parametrize("change", ["create", "type", "url", "delete"])
async def test_every_change_makes_the_card_catalog_stale(lovelace, change):
    catalog = CardCatalogStore()
    await catalog.async_replace(entries=[], resource_count=2, failed_imports=[], resources=await _items(lovelace))
    assert assess_freshness(catalog.catalog, await _items(lovelace)).complete is True
    theme = next(i for i in await _items(lovelace) if i["url"] == "/local/theme.css")
    args = {
        "create": {"operation": "create", "url": "/local/x.js", "type": "module"},
        "type": {"operation": "update", "resource_id": theme["id"], "type": "module"},
        "url": {"operation": "update", "resource_id": theme["id"], "url": "/local/theme2.css"},
        "delete": {"operation": "delete", "resource_id": theme["id"]},
    }[change]
    assert _body(await _call("set_dashboard_resource", args, _key(), lovelace, _data()))["status"] == "verified"
    assert assess_freshness(catalog.catalog, await _items(lovelace)).reasons == ("resources_changed",)
