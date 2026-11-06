"""Every dispatched write whose outcome is unconfirmed answers with the uncertain marker.

The marker is what closes an approval as interrupted rather than failed, so a body that says
partial or unknown and lacks it tells the operator that a change which landed did nothing.
"""

from __future__ import annotations

import ast
import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
import pytest

import custom_components.phoenix_mcp as package
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_common import UncertainOutcome, _tool_error, _tool_uncertain
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.tools import helper, labels, locations
from custom_components.phoenix_mcp.version_store import VersionStore
from custom_components.phoenix_mcp.ws_dispatch import async_ws_command
from tests.runtime_fixtures import mesa_off_store

ROOT = pathlib.Path(package.__file__).parent
UNCERTAIN = frozenset({"partial", "unknown"})


def _uncertain_status(node: ast.expr) -> bool:
    """Whether an expression can yield a status that claims no outcome."""
    if isinstance(node, ast.Constant):
        return node.value in UNCERTAIN
    if isinstance(node, ast.IfExp):
        return _uncertain_status(node.body) or _uncertain_status(node.orelse)
    return False


def _status_bodies(tree: ast.AST) -> list[ast.Dict]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Dict) and any(
        isinstance(key, ast.Constant) and key.value == "status" and _uncertain_status(value)
        for key, value in zip(node.keys, node.values))]


def _body_names(tree: ast.AST) -> dict[str, ast.Dict]:
    """Bodies bound to a name first, as `body = {...}`, by that name."""
    found: dict[str, ast.Dict] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and isinstance(node.targets[0], ast.Name)):
            found[node.targets[0].id] = node.value
    return found


def _sources() -> list[pathlib.Path]:
    return [path for path in ROOT.rglob("*.py") if "mesa_core" not in path.parts]


@pytest.mark.parametrize("path", _sources(), ids=lambda path: str(path.relative_to(ROOT)))
def test_a_body_claiming_no_outcome_never_reaches_a_plain_result(path):
    """A status of partial or unknown goes through _tool_uncertain, never _tool_error or _tool_success."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    uncertain = _status_bodies(tree)
    named = _body_names(tree)
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        name = getattr(call.func, "id", None)
        if name not in ("_tool_error", "_tool_success"):
            continue
        for inner in ast.walk(call):
            if inner in uncertain:
                pytest.fail(f"{path.name}:{inner.lineno}: {name} carries a status of partial or unknown")
            # An uncertain body bound to a name may reach _tool_error only when its status is a
            # conditional that one branch has proved not_applied.
            if (isinstance(inner, ast.Name) and inner.id in named and named[inner.id] in uncertain
                    and not isinstance(_status_value(named[inner.id]), ast.IfExp)):
                pytest.fail(f"{path.name}:{inner.lineno}: {name} carries a status of partial or unknown")


def _status_value(body: ast.Dict) -> ast.expr | None:
    return next((value for key, value in zip(body.keys, body.values)
                 if isinstance(key, ast.Constant) and key.value == "status"), None)


def test_the_enumeration_sees_a_plain_error_with_an_uncertain_status():
    """Positive control: the scan finds the shape it refuses."""
    tree = ast.parse('_tool_error(json.dumps({"status": "unknown" if rid else "not_applied"}))')
    assert len(_status_bodies(tree)) == 1


def test_the_marker_serializes_as_an_ordinary_error():
    assert _tool_uncertain({"status": "unknown"}) == _tool_error('{"status": "unknown"}')
    assert isinstance(_tool_uncertain({"status": "unknown"}), UncertainOutcome)


def _key(**caps) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="uncertain", access_key_hash="x", created_at=utcnow(), created_by="test",
        cap_helper_write="allow", cap_registry_read="allow", cap_registry_write="allow", **caps,
        permissions=PermissionTree(domains={"input_boolean": PermissionNode(state="GREEN")}),
    )


def _data() -> PhoenixData:
    return PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())


def _status(result: dict) -> str:
    return json.loads(result["content"][0]["text"])["status"]


@pytest.fixture
async def flag(hass, hass_admin_user):
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {}})
    item = await async_ws_command(hass, "input_boolean/create", {"name": "Flag"})
    await hass.async_block_till_done()
    return item


async def test_an_edit_whose_row_vanished_is_partial_and_stays_marked(hass, flag):
    """The row reads back as nothing: an unconfirmed edit, not a crash past the recovery marks."""
    real = helper.async_ws_command

    async def dispatch(hass_, command, payload, **kwargs):
        if command == "input_boolean/update":
            return await real(hass_, "input_boolean/delete", {"input_boolean_id": flag["id"]}, **kwargs)
        return await real(hass_, command, payload, **kwargs)

    data = _data()
    with patch.object(helper, "async_ws_command", dispatch):
        result, outcome, _ = await _call_tool("edit_helper", {
            "helper_type": "input_boolean", "helper_id": flag["id"], "config": {"name": "Renamed"},
        }, _key(), hass, data)
    assert isinstance(result, UncertainOutcome) and outcome == "invalid_request"
    assert _status(result) == "partial"
    assert [record["phase"] for record in data.config_recovery.records()] == ["partial"]


async def test_an_edit_that_does_not_read_back_as_sent_is_uncertain(hass, flag):
    real = helper.async_ws_command

    async def dispatch(hass_, command, payload, **kwargs):
        return {} if command == "input_boolean/update" else await real(hass_, command, payload, **kwargs)

    with patch.object(helper, "async_ws_command", dispatch):
        result, _, _ = await _call_tool("edit_helper", {
            "helper_type": "input_boolean", "helper_id": flag["id"], "config": {"name": "Renamed"},
        }, _key(), hass, _data())
    assert isinstance(result, UncertainOutcome) and _status(result) == "partial"


async def test_a_label_that_does_not_read_back_is_uncertain(hass):
    with patch.object(labels, "_entry", return_value=None):
        result, outcome, _ = await _call_tool(
            "set_label_or_category", {"kind": "label", "operation": "create", "changes": {"name": "Holiday"}},
            _key(pass_through=True), hass, _data())
    assert isinstance(result, UncertainOutcome) and outcome == "invalid_request" and _status(result) == "unknown"


async def test_an_area_that_does_not_read_back_is_uncertain(hass):
    with patch.object(locations, "_snapshot", side_effect=[RuntimeError("gone")]):
        result, outcome, _ = await _call_tool(
            "set_area_or_floor", {"kind": "area", "operation": "create", "changes": {"name": "Attic"}},
            _key(pass_through=True), hass, _data())
    assert isinstance(result, UncertainOutcome) and outcome == "invalid_request" and _status(result) == "unknown"


async def test_a_restart_whose_outcome_is_lost_is_uncertain_only_once_it_may_have_run(hass):
    data = _data()
    key = _key(cap_restart="allow")
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock, side_effect=RuntimeError("lost")):
        result, _, _ = await _call_tool("restart_ha", {}, key, hass, data)
    assert isinstance(result, UncertainOutcome) and _status(result) == "unknown"
    with patch.object(data.config_recovery, "begin", AsyncMock(side_effect=ConfigurationRecoveryError("Busy."))):
        result, _, _ = await _call_tool("restart_ha", {}, key, hass, data)
    assert not isinstance(result, UncertainOutcome) and _status(result) == "not_applied"
