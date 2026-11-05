"""Structural-validation and pagination-bound contracts on the admin API.

Both are reachable with syntactically valid JSON or query strings:

* A permission-tree section that is not an object is a 400, never an
  AttributeError surfacing as a 500.
* The paged endpoints agree on bounds. A negative limit is refused (it would
  slice as "everything but the last N", and mean "return everything" in the
  version store), and malformed approvals paging input is reported rather than
  silently replaced with defaults.
"""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from custom_components.phoenix_mcp.admin_view import (
    PhoenixAdminApprovalsView,
    PhoenixAdminAuditView,
    PhoenixAdminPermissionBulkSelectView,
    PhoenixAdminPermissionDomainView,
    PhoenixAdminPermissionsView,
    PhoenixAdminAccessKeysView,
    PhoenixAdminAccessKeyView,
    PhoenixAdminSettingsView,
    _parse_pagination,
    _request_time,
    _validate_permission_tree_body,
)
from custom_components.phoenix_mcp.access_key_store import GlobalSettings

from tests.runtime_fixtures import make_active_access_key as _make_active_access_key, make_admin_request as _make_admin_request, make_admin_data as _make_data, make_admin_hass as _make_hass


# --------------------------------------------------------------------------- #
# Permission tree: a non-object section must be a 400, never a 500
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "body",
    [
        {"domains": []},
        {"devices": "not-an-object"},
        {"entities": 42},
        {"domains": None},
    ],
)
def test_non_object_permission_section_is_rejected(body):
    """A non-object section is rejected, not an AttributeError on .items()."""
    resp = _validate_permission_tree_body(body, "rid-1")
    assert resp is not None, f"{body!r} should be refused"
    assert resp.status == 400


def test_valid_permission_sections_still_pass():
    """The guard must not reject well-formed trees."""
    body = {
        "domains": {"light": {"state": "GREEN"}},
        "devices": {},
        "entities": {"light.kitchen": {"state": "YELLOW", "hint": "ok"}},
    }
    assert _validate_permission_tree_body(body, "rid-1") is None


@pytest.mark.asyncio
async def test_permissions_put_returns_400_not_500_for_bad_section():
    """End-to-end: the handler reports a client error, not an internal one."""
    data = _make_data()
    access_key = _make_active_access_key()
    data.store.get_access_key_by_id = MagicMock(return_value=access_key)
    hass = _make_hass(data)

    view = PhoenixAdminPermissionsView()
    view.hass = hass

    body = json.dumps({"domains": []}).encode()
    request = _make_admin_request(body=body)
    resp = await view.put(request, access_key_id=access_key.id)

    assert resp.status == 400


# --------------------------------------------------------------------------- #
# Pagination bounds
# --------------------------------------------------------------------------- #

def _req(**query):
    r = _make_admin_request()
    r.query = {k: str(v) for k, v in query.items()}
    return r


def test_negative_limit_is_rejected():
    """A negative limit slices as 'everything but the last N'; refuse it."""
    out = _parse_pagination(_req(limit=-1), "rid", default_limit=50, max_limit=500)
    assert getattr(out, "status", None) == 400


def test_negative_offset_is_rejected():
    out = _parse_pagination(_req(offset=-5), "rid", default_limit=50, max_limit=500)
    assert getattr(out, "status", None) == 400


def test_malformed_limit_is_reported_not_silently_defaulted():
    out = _parse_pagination(_req(limit="abc"), "rid", default_limit=50, max_limit=500)
    assert getattr(out, "status", None) == 400


def test_limit_is_clamped_to_the_maximum():
    limit, offset = _parse_pagination(_req(limit=10_000), "rid", default_limit=50, max_limit=500)
    assert (limit, offset) == (500, 0)


def test_defaults_apply_when_absent():
    assert _parse_pagination(_req(), "rid", default_limit=50, max_limit=500) == (50, 0)


def test_zero_limit_is_allowed():
    """Zero is a legitimate 'count only' request, distinct from negative."""
    assert _parse_pagination(_req(limit=0), "rid", default_limit=50, max_limit=500) == (0, 0)


@pytest.mark.asyncio
async def test_audit_endpoint_rejects_negative_limit():
    data = _make_data()
    hass = _make_hass(data)
    view = PhoenixAdminAuditView()
    view.hass = hass

    request = _make_admin_request()
    request.query = {"limit": "-1"}
    resp = await view.get(request)

    assert resp.status == 400


@pytest.mark.asyncio
async def test_approvals_endpoint_rejects_negative_limit():
    data = _make_data()
    hass = _make_hass(data)
    view = PhoenixAdminApprovalsView()
    view.hass = hass

    request = _make_admin_request()
    request.query = {"limit": "-1"}
    resp = await view.get(request)

    assert resp.status == 400


# --------------------------------------------------------------------------- #
# Rate-limit fields must be real integers
#
# int() accepts bool (True becomes 1), float (1.75 becomes 1), and numeric
# strings ("12" becomes 12), so a caller could store a value it never sent
# while the API advertised "must be integers". bool needs an explicit
# exclusion because it is a subclass of int.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "payload",
    [
        {"rate_limit_requests": True, "rate_limit_burst": 1},
        {"rate_limit_requests": 60, "rate_limit_burst": False},
        {"rate_limit_requests": 1.75, "rate_limit_burst": 1},
        {"rate_limit_requests": 60, "rate_limit_burst": 1.5},
        {"rate_limit_requests": "12", "rate_limit_burst": 1},
        {"rate_limit_requests": 60, "rate_limit_burst": "12"},
        {"rate_limit_requests": None, "rate_limit_burst": 1},
    ],
)
@pytest.mark.asyncio
async def test_create_rejects_non_integer_rate_limits(payload):
    data = _make_data()
    hass = _make_hass(data)
    access_key = _make_active_access_key()
    data.store.name_slug_exists.return_value = False
    data.store.async_create_access_key = AsyncMock(return_value=(access_key, "phx_rawaccesskey123"))

    view = PhoenixAdminAccessKeysView()
    view.hass = hass

    body = json.dumps({"name": "my-access-key", **payload}).encode()
    resp = await view.post(_make_admin_request(body=body))

    assert resp.status == 400, f"{payload!r} should be refused"
    data.store.async_create_access_key.assert_not_called()


@pytest.mark.asyncio
async def test_create_accepts_real_integer_rate_limits():
    data = _make_data()
    hass = _make_hass(data)
    access_key = _make_active_access_key()
    data.store.name_slug_exists.return_value = False
    data.store.async_create_access_key = AsyncMock(return_value=(access_key, "phx_rawaccesskey123"))

    view = PhoenixAdminAccessKeysView()
    view.hass = hass

    body = json.dumps({"name": "my-access-key", "rate_limit_requests": 30, "rate_limit_burst": 5}).encode()
    resp = await view.post(_make_admin_request(body=body))

    assert resp.status == 201
    kwargs = data.store.async_create_access_key.call_args.kwargs
    assert kwargs["rate_limit_requests"] == 30 and kwargs["rate_limit_burst"] == 5


@pytest.mark.parametrize(
    "payload",
    [
        {"rate_limit_requests": True},
        {"rate_limit_burst": False},
        {"rate_limit_requests": 1.75},
        {"rate_limit_burst": "12"},
    ],
)
@pytest.mark.asyncio
async def test_patch_rejects_non_integer_rate_limits(payload):
    data = _make_data()
    hass = _make_hass(data)
    access_key = _make_active_access_key()
    data.store.get_access_key_by_id = MagicMock(return_value=access_key)
    data.store.async_patch_access_key = AsyncMock(return_value=access_key)

    view = PhoenixAdminAccessKeyView()
    view.hass = hass

    resp = await view.patch(_make_admin_request(body=json.dumps(payload).encode()), access_key_id=access_key.id)

    assert resp.status == 400, f"{payload!r} should be refused"
    data.store.async_patch_access_key.assert_not_called()


# --------------------------------------------------------------------------- #
# Well-formed JSON with an unusable value is a 400, never a 500
# --------------------------------------------------------------------------- #


async def _patch_key(body: bytes):
    access_key = _make_active_access_key()
    data = _make_data([access_key])
    view = PhoenixAdminAccessKeyView()
    view.hass = _make_hass(data)
    return await view.patch(_make_admin_request(body=body), access_key_id=access_key.id)


async def _patch_settings(body: bytes):
    data = _make_data([])
    data.store.get_settings.return_value = GlobalSettings()
    view = PhoenixAdminSettingsView()
    view.hass = _make_hass(data)
    return await view.patch(_make_admin_request(body=body))


@pytest.mark.parametrize("body", [
    b"\xff\xfe{", b"[" * 500000, b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e999}', b'{"a":' + b"9" * 5000 + b"}",
], ids=["encoding", "depth", "nan", "infinity", "exponent", "digits"])
async def test_a_body_that_cannot_be_read_as_finite_json_is_a_400(body):
    assert (await _patch_key(body)).status == 400


@pytest.mark.parametrize("value", [["GREEN"], {"a": 1}, 1])
async def test_an_unhashable_permission_state_is_a_400(value):
    resp = _validate_permission_tree_body({"domains": {"light": {"state": value}}}, "rid-1")
    assert resp is not None and resp.status == 400


@pytest.mark.parametrize("value", [["GREEN"], {"a": 1}])
async def test_an_unhashable_state_on_a_node_or_bulk_request_is_a_400(value):
    access_key = _make_active_access_key()
    data = _make_data([access_key])
    body = json.dumps({"state": value}).encode()
    node = PhoenixAdminPermissionDomainView()
    node.hass = _make_hass(data)
    assert (await node.patch(_make_admin_request(body=body), access_key_id=access_key.id, node_id="light")).status == 400
    bulk = PhoenixAdminPermissionBulkSelectView()
    bulk.hass = _make_hass(data)
    body = json.dumps({"selector_type": "area", "selector_id": "kitchen", "state": value}).encode()
    assert (await bulk.post(_make_admin_request(body=body), access_key_id=access_key.id)).status == 400


@pytest.mark.parametrize("field", ["persona", "cap_restart"])
@pytest.mark.parametrize("value", [["allow"], {"a": 1}])
async def test_an_unhashable_menu_value_on_a_key_is_a_400(field, value):
    assert (await _patch_key(json.dumps({field: value}).encode())).status == 400


@pytest.mark.parametrize("body", [{"mesa_mode": ["advisory"]}, {"audit_log_maxlen": 10 ** 400}, {"audit_log_maxlen": [1]}])
async def test_an_unusable_setting_is_a_400(body):
    assert (await _patch_settings(json.dumps(body).encode())).status == 400


@pytest.mark.parametrize("since", ["2026-13-45T00:00:00", "0001-01-01T00:00:00+14:00", "not-a-time"])
async def test_a_timestamp_that_names_no_moment_is_refused_by_the_audit_filter(since):
    data = _make_data([])
    view = PhoenixAdminAuditView()
    view.hass = _make_hass(data)
    request = _make_admin_request()
    request.query = {"since": since}
    assert (await view.get(request)).status == 400


@pytest.mark.parametrize("raw,valid", [
    ("2026-13-45T00:00:00", False),
    ("9999-12-31T23:59:59-14:00", False),
    ("0001-01-01T00:00:00+14:00", False),
    ("nonsense", False),
    ("2030-06-01T12:00:00+00:00", True),
])
def test_request_time_is_utc_or_none(raw, valid):
    parsed = _request_time(raw)
    assert (parsed is not None) is valid
    assert parsed is None or parsed.tzinfo is not None
