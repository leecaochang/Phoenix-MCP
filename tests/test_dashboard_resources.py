"""Tests for list_dashboard_resources and set_dashboard_resource.

Both run against Home Assistant's real lovelace resource collection. They pin the
capability boundary (cap_dashboard_resources, confirm-only and exempt from
pass-through), the local-path and own-resource limits, the
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

from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord, AccessKeyStore, PermissionTree, _stored_cap_mode,
)
from custom_components.phoenix_mcp.card_catalog import CardCatalogStore, assess_freshness
from custom_components.phoenix_mcp.config_reconciliation import _matches, observe_configuration
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError
from custom_components.phoenix_mcp.const import DASHBOARD_RESOURCE_BINDING, REDACTION_SENTINEL
from custom_components.phoenix_mcp.dashboard_resource_ownership import DashboardResourceOwnership
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.capability import effective_cap
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.request_authority import (
    AuthorityChangedError, RequestAuthority, policy_version, request_authority,
)
from custom_components.phoenix_mcp.request_authority import _approved_exec_ctx
from custom_components.phoenix_mcp.tools import dashboard_resources as resources_tool
from custom_components.phoenix_mcp.tools.recovery import _tool_restore_configuration
from custom_components.phoenix_mcp.ws_dispatch import (
    WsCommandNotDispatchedError, WsDispatchError, async_lovelace_resources,
)
from tests.runtime_fixtures import patch_readers


def _key(**caps) -> AccessKeyRecord:
    base = {"cap_dashboard_resources": "confirm"}
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


async def _approved(hass, data, args, key):
    token = _approved_exec_ctx.set(True)
    try:
        return await resources_tool._execute_set_dashboard_resource(args, key, hass, data)
    finally:
        _approved_exec_ctx.reset(token)


async def _reviewed(hass, data, args, key=None):
    """Run a change as its approval would; a request refused before approval stays refused."""
    key = key or _key()
    try:
        plan = resources_tool._prepare(args, key, await resources_tool._storage_items(hass), hass, data)
    except (resources_tool.ResourceRefusal, ConfigurationRecoveryError):
        return await _call("set_dashboard_resource", args, key, hass, data)
    return await _approved(hass, data, {**args, DASHBOARD_RESOURCE_BINDING: plan.binding}, key)


async def _create(hass, data, key=None, url="/local/new-card.js", type_="module"):
    return await _reviewed(hass, data, {"operation": "create", "url": url, "type": type_}, key)


async def _owned(hass, data, url="/local/theme.css") -> dict:
    """The fixture resource with that URL, recorded as Phoenix's own."""
    item = next(i for i in await _items(hass) if i["url"] == url)
    await data.dashboard_resources.async_add(item["id"])
    return item


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
    with patch("custom_components.phoenix_mcp.tool_common.async_evaluate_capability") as evaluate:
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
    data = _data()
    target = await _owned(lovelace, data)
    result = await _reviewed(lovelace, data, {"operation": "update", "resource_id": target["id"],
                                              "url": "/local/theme2.css"})
    assert _body(result)["status"] == "verified"
    updated = next(i for i in await _items(lovelace) if i["id"] == target["id"])
    assert updated["url"] == "/local/theme2.css" and updated["type"] == "css"


async def test_delete_removes_the_resource(lovelace):
    data = _data()
    target = await _owned(lovelace, data)
    result = await _reviewed(lovelace, data, {"operation": "delete", "resource_id": target["id"]})
    assert _body(result)["status"] == "verified"
    assert target["id"] not in {i["id"] for i in await _items(lovelace)}
    assert not data.dashboard_resources.owns(target["id"])


async def _refusal(hass, args, key=None, data=None):
    before = await _items(hass)
    data = data or _data()
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
    data = _data()
    theme = await _owned(lovelace, data)
    other = next(i for i in await _items(lovelace) if i["id"] != theme["id"])
    cases = [
        ({"operation": "update", "resource_id": theme["id"]}, "url, type, or both"),
        ({"operation": "update", "resource_id": theme["id"], "url": "/local/theme.css"}, "nothing would change"),
        ({"operation": "update", "resource_id": theme["id"], "url": other["url"]}, "already registered"),
        ({"operation": "delete", "resource_id": theme["id"], "type": "css"}, "only resource_id"),
    ]
    for args, needle in cases:
        assert needle in _text(await _refusal(lovelace, args, data=data)), args


@pytest.mark.parametrize("url", [
    "https://cdn.example/card.js", "http://attacker.example/x.js", "//attacker.example/x.js",
    "/local", "/local/", "/hacsfiles/", "/api/states", "/lovelace.js", "local/x.js", "javascript:alert(1)",
    "/local/../api/x.js", "/local/a/../../api/x.js", "/local/%2e%2e/api/x.js", "/local/%252e%252e/api/x.js",
    "/local/..%2fapi/x.js", "/local/.%2e/x.js", "/local//attacker.example/x.js", "/local\\x.js", "/local/x\\..\\y.js",
    "/%6cocal/x.js", "/local/%25252e%25252e%25252fx.js",
])
async def test_only_a_local_path_can_be_registered(lovelace, url):
    result = await _refusal(lovelace, {"operation": "create", "url": url, "type": "module"})
    assert "local path under" in _text(result)


@pytest.mark.parametrize("url", [
    "/local/card.js", "/local/cards/a%20b.js?v=2", "/hacsfiles/mushroom/card.js?hacstag=12", "/local/.well/x.js",
])
def test_a_local_path_is_accepted(url):
    assert resources_tool.is_local_resource_url(url)


async def test_an_update_cannot_point_at_a_remote_file(lovelace):
    data = _data()
    theme = await _owned(lovelace, data)
    result = await _refusal(lovelace, {"operation": "update", "resource_id": theme["id"],
                                       "url": "https://cdn.example/x.css"}, data=data)
    assert "local path under" in _text(result)


@pytest.mark.parametrize("args", [
    {"operation": "update", "url": "/local/replaced.js"},
    {"operation": "update", "type": "css"},
    {"operation": "delete"},
])
async def test_a_resource_phoenix_did_not_create_is_left_alone(lovelace, args):
    """HACS and people register their own resources; those are theirs to change."""
    hacs = next(i for i in await _items(lovelace) if i["url"].startswith("/hacsfiles/"))
    result = await _refusal(lovelace, {**args, "resource_id": hacs["id"]})
    assert result[1] == "denied" and "only the dashboard resources it created" in _text(result)


async def test_an_approval_rechecks_ownership(lovelace):
    """Losing the record after review must not reach a resource Phoenix does not own."""
    key, data = _key(), _data()
    theme = await _owned(lovelace, data)
    args = {"operation": "delete", "resource_id": theme["id"]}
    plan = resources_tool._prepare(args, key, await _items(lovelace), lovelace, data)
    await data.dashboard_resources.async_discard(theme["id"])
    result = await _approved(lovelace, data, {**args, DASHBOARD_RESOURCE_BINDING: plan.binding}, key)
    assert result[1] == "denied"
    assert theme["id"] in {i["id"] for i in await _items(lovelace)}


async def test_a_created_resource_is_recorded_and_listed_as_phoenix_created(lovelace):
    data = _data()
    created = _body(await _create(lovelace, data))
    assert data.dashboard_resources.owns(created["resource"]["id"])
    rows = _body(await _call("list_dashboard_resources", {}, _key(), lovelace, data))["resources"]
    assert {r["url"]: r["phoenix_created"] for r in rows} == {
        "/hacsfiles/mushroom/mushroom.js?hacstag=1": False, "/local/theme.css": False, "/local/new-card.js": True}


async def test_the_ownership_record_survives_a_reload_and_reads_nothing_from_junk():
    class Store:
        def __init__(self, value):
            self.value = value

        async def async_load(self):
            return self.value

        async def async_save(self, value):
            self.value = value

    store = Store(None)
    first = DashboardResourceOwnership(store)
    await first.async_load()
    await first.async_add("abc")
    second = DashboardResourceOwnership(store)
    await second.async_load()
    assert second.owns("abc") and not second.owns("other") and not second.owns(None)
    await second.async_discard("abc")
    assert store.value == {"ids": []}
    for junk in ("text", {"ids": "abc"}, ["abc"]):
        third = DashboardResourceOwnership(Store(junk))
        await third.async_load()
        assert not third.owns("abc")


@pytest.mark.parametrize("pass_through", [False, True])
async def test_allow_never_skips_the_review(lovelace, pass_through):
    """Even a stored Allow on a pass-through key waits for a person."""
    key = _key(cap_dashboard_resources="allow")
    key.pass_through = pass_through
    assert effective_cap(key, "cap_dashboard_resources") == "confirm"
    before = await _items(lovelace)
    result, approval = await _pending(lovelace, _data(), {"operation": "create", "url": "/local/x.js",
                                                          "type": "module"}, key)
    assert result[1] == "pending_approval" and approval["cap_name"] == "cap_dashboard_resources"
    assert await _items(lovelace) == before


async def test_a_direct_executor_call_with_a_stored_allow_is_refused(lovelace):
    result = await resources_tool._execute_set_dashboard_resource(
        {"operation": "create", "url": "/local/x.js", "type": "module"},
        _key(cap_dashboard_resources="allow"), lovelace, _data())
    assert result[1] == "denied" and "requires approval" in _text(result)


def test_a_stored_allow_loads_as_confirm():
    assert _stored_cap_mode("cap_dashboard_resources", "allow") == "confirm"
    assert _stored_cap_mode("cap_lovelace_write", "allow") == "allow"


async def test_the_key_store_refuses_allow(hass):
    store = await AccessKeyStore.async_create(hass)
    record, _raw = await store.async_create_access_key(name="t", created_by="u")
    with pytest.raises(ValueError, match="'allow'"):
        await store.async_patch_access_key(record.id, cap_dashboard_resources="allow")


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
    key, data = _key(), _data()
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
    data = _data()
    theme = await _owned(lovelace, data)
    _, approval = await _pending(lovelace, data, {"operation": "update", "resource_id": theme["id"],
                                                  "type": "module"}, _key())
    diff = approval["diff"]
    assert json.loads(diff["before"])["type"] == "css" and json.loads(diff["after"])["type"] == "module"
    assert diff["target"] == {"type": "dashboard_resource", "id": theme["id"], "label": "/local/theme.css"}


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
        with patch_readers("custom_components.phoenix_mcp.tools.dashboard_resources.read_resource_snapshot", side_effect=bump_policy), \
                patch.object(resources_tool, "async_ws_command") as dispatch, \
                pytest.raises(AuthorityChangedError):
            await _reviewed(lovelace, data, {"operation": "create", "url": "/local/x.js", "type": "module"}, key)
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

    with patch_readers("custom_components.phoenix_mcp.tools.dashboard_resources.read_resource_snapshot", side_effect=stale):
        result = await _create(lovelace, data)
    assert _body(result)["status"] == "partial"
    assert data.config_recovery.records()[0]["phase"] == "partial"
    assert result[0]["isError"] is True and result[1] == "invalid_request"


async def test_an_unreadable_readback_is_unknown(lovelace):
    data = _data()
    original = resources_tool.read_resource_snapshot
    calls = []

    async def fail_second(hass, lookup, key):
        calls.append(key)
        if len(calls) == 2:
            raise ConfigurationRecoveryError("unreadable")
        return await original(hass, lookup, key)

    with patch_readers("custom_components.phoenix_mcp.tools.dashboard_resources.read_resource_snapshot", side_effect=fail_second):
        result = await _create(lovelace, data)
    assert _body(result)["status"] == "unknown"
    assert data.config_recovery.records()[0]["phase"] == "unknown"
    # A dispatched write with no confirmed outcome is an error to the caller and the audit.
    assert result[0]["isError"] is True and result[1] == "invalid_request"


async def test_a_change_while_preparing_dispatches_nothing(lovelace):
    data = _data()
    original = resources_tool.read_resource_snapshot

    async def moved(hass, lookup, key):
        snapshot = await original(hass, lookup, key)
        return {**snapshot, "exists": True}

    with patch_readers("custom_components.phoenix_mcp.tools.dashboard_resources.read_resource_snapshot", side_effect=moved), \
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
    with patch("custom_components.phoenix_mcp.tools.recovery._gate", AsyncMock(return_value=None)):
        return await _tool_restore_configuration(args, key, hass, data)


async def test_restoring_before_a_create_removes_it(lovelace):
    key, data = _key(), _data()
    created = _body(await _create(lovelace, data, key=key))
    restored = await _restore(lovelace, data, key, created["recovery_id"], "before")
    assert _body(restored)["status"] == "verified"
    assert "/local/new-card.js" not in {i["url"] for i in await _items(lovelace)}


async def test_restoring_before_an_update_reverts_it(lovelace):
    key, data = _key(), _data()
    theme = await _owned(lovelace, data)
    changed = _body(await _reviewed(lovelace, data, {"operation": "update", "resource_id": theme["id"],
                                                     "url": "/local/theme2.css", "type": "module"}, key))
    await _restore(lovelace, data, key, changed["recovery_id"], "before")
    current = next(i for i in await _items(lovelace) if i["id"] == theme["id"])
    assert (current["url"], current["type"]) == ("/local/theme.css", "css")


async def test_restoring_before_a_delete_recreates_it_once(lovelace):
    key, data = _key(), _data()
    theme = await _owned(lovelace, data)
    deleted = _body(await _reviewed(lovelace, data, {"operation": "delete", "resource_id": theme["id"]}, key))
    restored = _body(await _restore(lovelace, data, key, deleted["recovery_id"], "before"))
    assert restored["status"] == "verified"
    assert data.dashboard_resources.owns(restored["resource"]["id"])
    again = await _tool_restore_configuration({"recovery_id": deleted["recovery_id"], "side": "before"},
                                              key, lovelace, data)
    assert _body(again)["diff"]["before"] is not None  # the recreated resource is the target now
    assert [i["url"] for i in await _items(lovelace)].count("/local/theme.css") == 1


async def test_a_restore_that_removes_a_resource_forgets_it(lovelace):
    key, data = _key(), _data()
    created = _body(await _create(lovelace, data, key=key))
    await _restore(lovelace, data, key, created["recovery_id"], "before")
    assert not data.dashboard_resources.owns(created["resource"]["id"])


async def test_a_restore_cannot_change_a_resource_phoenix_did_not_create(lovelace):
    key, data = _key(), _data()
    theme = await _owned(lovelace, data)
    changed = _body(await _reviewed(lovelace, data, {"operation": "update", "resource_id": theme["id"],
                                                     "type": "module"}, key))
    await data.dashboard_resources.async_discard(theme["id"])
    result = await _tool_restore_configuration({"recovery_id": changed["recovery_id"], "side": "before"},
                                               key, lovelace, data)
    assert result[1] == "denied" and "only the dashboard resources it created" in _text(result)


async def test_a_restore_cannot_bring_back_a_remote_file(lovelace):
    """A record from before the local-path limit must not reinstate a remote URL."""
    key, data = _key(), _data()
    created = _body(await _create(lovelace, data, key=key))
    record = data.config_recovery._records[created["recovery_id"]]
    record["source"] = {**record["source"], "resource": {**record["source"]["resource"],
                                                         "url": "https://cdn.example/x.js"}}
    result = await _tool_restore_configuration({"recovery_id": created["recovery_id"], "side": "source"},
                                               key, lovelace, data)
    assert result[1] == "denied" and "local path under" in _text(result)


async def test_restore_needs_the_capability(lovelace):
    key, data = _key(), _data()
    created = _body(await _create(lovelace, data, key=key))
    key.cap_dashboard_resources = "deny"
    result = await _tool_restore_configuration({"recovery_id": created["recovery_id"], "side": "before"},
                                               key, lovelace, data)
    assert result[1] == "denied"


async def test_admin_recovery_restores_a_resource(lovelace, hass_admin_user):
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER
    from custom_components.phoenix_mcp.admin.changes import PhoenixAdminConfigurationRecoveryRecordView

    data = _data()
    lovelace.data["phoenix_mcp"] = data
    created = _body(await _create(lovelace, data, key=_key()))
    values = {KEY_HASS_USER: hass_admin_user, KEY_AUTHENTICATED: True, "phoenix_mcp_rid": "test"}
    request = MagicMock()
    request.get.side_effect = lambda k, default=None: values.get(k, default)
    request.__getitem__.side_effect = values.__getitem__
    view = PhoenixAdminConfigurationRecoveryRecordView()
    view.hass = lovelace
    with patch("custom_components.phoenix_mcp.admin.changes._read_body",
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
    data = _data()
    theme = await _owned(lovelace, data)
    args = {
        "create": {"operation": "create", "url": "/local/x.js", "type": "module"},
        "type": {"operation": "update", "resource_id": theme["id"], "type": "module"},
        "url": {"operation": "update", "resource_id": theme["id"], "url": "/local/theme2.css"},
        "delete": {"operation": "delete", "resource_id": theme["id"]},
    }[change]
    assert _body(await _reviewed(lovelace, data, args))["status"] == "verified"
    assert assess_freshness(catalog.catalog, await _items(lovelace)).reasons == ("resources_changed",)
