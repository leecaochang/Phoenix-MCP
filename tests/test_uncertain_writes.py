"""Every dispatched write whose outcome is unconfirmed answers with the uncertain marker.

The marker is what closes an approval as interrupted rather than failed, so a body that says
partial or unknown and lacks it tells the operator that a change which landed did nothing.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
import pytest

import custom_components.phoenix_mcp as package
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.request_authority import AuthorityChangedError
from custom_components.phoenix_mcp.tool_common import UncertainOutcome, _recovery_failure, _tool_error, _tool_uncertain
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.tools import authoring, helper, labels, locations, lovelace
from custom_components.phoenix_mcp.version_store import VersionStore
from custom_components.phoenix_mcp.ws_dispatch import WsCommandNotDispatchedError, WsDispatchError, async_ws_command
from tests.runtime_fixtures import mesa_off_store

ROOT = pathlib.Path(package.__file__).parent
UNCERTAIN = frozenset({"partial", "unknown"})
_NO_OUTCOME_TEXT = re.compile(r"outcome is (uncertain|unknown)|readback is incomplete")


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


def _plain_error_text(call: ast.Call) -> str:
    return " ".join(str(node.value) for node in ast.walk(call)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str))


def _plain_uncertain_errors(tree: ast.AST) -> list[int]:
    """Lines of a plain error whose text claims no outcome."""
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_tool_error"
            and _NO_OUTCOME_TEXT.search(_plain_error_text(node))]


def _unknown_marks_then_plain_error(tree: ast.AST) -> list[int]:
    """Lines where a recovery record is marked unknown and the next statement returns a plain error."""
    found = []
    for node in ast.walk(tree):
        for body in (getattr(node, "body", None), getattr(node, "orelse", None)):
            if not isinstance(body, list):
                continue
            for first, second in zip(body, body[1:]):
                call = first.value.value if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Await)) else None
                if (isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "mark"
                        and any(isinstance(item, ast.Constant) and item.value == "unknown" for item in ast.walk(call))
                        and isinstance(second, ast.Return) and second.value is not None
                        and any(getattr(item, "id", None) == "_tool_error" for item in ast.walk(second.value))):
                    found.append(first.lineno)
    return found


@pytest.mark.parametrize("path", _sources(), ids=lambda path: str(path.relative_to(ROOT)))
def test_no_plain_error_says_its_outcome_is_unknown(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert _plain_uncertain_errors(tree) == []
    assert _unknown_marks_then_plain_error(tree) == []


def test_the_text_scans_see_what_they_refuse():
    """Positive controls for both scans."""
    assert _plain_uncertain_errors(ast.parse('_tool_error("Save outcome is uncertain; inspect.")'))
    assert _unknown_marks_then_plain_error(ast.parse("""
async def write():
    try:
        pass
    except Exception:
        await recovery.mark(rid, "not_applied" if refused else "unknown")
        return _tool_error("failed"), "invalid_request", tool
"""))


def test_a_failure_before_dispatch_is_plain_and_after_it_uncertain():
    lost, unsent = "It may have landed.", "Nothing was changed."
    before = _recovery_failure(RuntimeError("x"), None, lost=lost, unsent=unsent)
    assert not isinstance(before, UncertainOutcome) and before == _tool_error(unsent)
    after = _recovery_failure(RuntimeError("x"), "r1", lost=lost, unsent=unsent)
    assert isinstance(after, UncertainOutcome)
    assert json.loads(after["content"][0]["text"]) == {
        "status": "unknown", "recovery_id": "r1", "retry_safe": False, "message": lost}


def test_a_recovery_error_keeps_its_own_text_on_either_side_of_dispatch():
    exc = ConfigurationRecoveryError("Busy.")
    assert _recovery_failure(exc, None, lost="l", unsent="u") == _tool_error("Busy.")
    assert json.loads(_recovery_failure(exc, "r1", lost="l", unsent="u")["content"][0]["text"])["message"] == "Busy."


async def _board_write(hass, data, error):
    """One dashboard registry create whose command fails with `error`."""
    created: list[dict] = []

    async def dispatch(_hass, command, payload=None, **kwargs):
        if command == "lovelace/dashboards/list":
            return list(created)
        if error is not None:
            raise error
        created.append({"id": "d1", **payload})
        return created[0]

    with patch.object(lovelace, "async_ws_command", AsyncMock(side_effect=dispatch)):
        before = await lovelace._dashboard_registry_snapshot(hass, "d1")
        return await lovelace._recoverable_dashboard_registry_write(
            dashboard_id="d1", before=before, operation="create_dashboard",
            command="lovelace/dashboards/create", payload={"url_path": "d1", "title": "D"},
            access_key=_key(), hass=hass, data=data,
        )


async def _layout_write(hass, data, error):
    """One dashboard layout save whose command fails with `error`."""
    with (patch.object(lovelace, "async_get_lovelace_config", AsyncMock(return_value={"views": []})),
          patch.object(lovelace, "async_save_lovelace_config", AsyncMock(side_effect=error))):
        return await lovelace._recoverable_dashboard_save(
            url_path="d1", before={"views": []}, after={"views": [{"title": "A"}]},
            access_key=_key(), hass=hass, data=data,
        )


@pytest.mark.parametrize("write", [_board_write, _layout_write], ids=["registry", "layout"])
async def test_a_dashboard_command_with_a_lost_result_is_uncertain(hass, write):
    data = _data()
    result, outcome, _ = await write(hass, data, WsDispatchError("timed out after dispatch"))
    assert isinstance(result, UncertainOutcome) and outcome == "invalid_request" and _status(result) == "unknown"
    assert [record["phase"] for record in data.config_recovery.records()] == ["unknown"]


@pytest.mark.parametrize("write", [_board_write, _layout_write], ids=["registry", "layout"])
async def test_a_dashboard_command_that_never_ran_is_a_plain_failure(hass, write):
    data = _data()
    result, outcome, _ = await write(hass, data, WsCommandNotDispatchedError("not loaded"))
    assert not isinstance(result, UncertainOutcome) and result.get("isError") and outcome == "invalid_request"
    assert [record["phase"] for record in data.config_recovery.records()] == ["not_applied"]


@pytest.mark.parametrize("write", [_board_write, _layout_write], ids=["registry", "layout"])
async def test_a_record_lost_after_dispatch_is_uncertain(hass, write):
    """The command returned, then the recovery record could not be updated."""
    data = _data()
    real = data.config_recovery.mark

    async def mark(rid, phase, **observed):
        if phase != "dispatching":
            raise RuntimeError("storage full")
        await real(rid, phase, **observed)

    with patch.object(data.config_recovery, "mark", mark):
        result, _, _ = await write(hass, data, None)
    assert isinstance(result, UncertainOutcome) and _status(result) == "unknown"


@pytest.mark.parametrize("write", [_board_write, _layout_write], ids=["registry", "layout"])
async def test_a_failure_before_dispatch_says_nothing_changed(hass, write):
    data = _data()
    with patch.object(data.config_recovery, "begin", AsyncMock(side_effect=RuntimeError("storage full"))):
        result, _, _ = await write(hass, data, None)
    assert not isinstance(result, UncertainOutcome) and "uncertain" not in result["content"][0]["text"]


@pytest.mark.parametrize("write", [_board_write, _layout_write], ids=["registry", "layout"])
async def test_authority_lost_before_dispatch_records_that_nothing_was_sent(hass, write):
    data = _data()
    with (patch("custom_components.phoenix_mcp.tool_common.check_request_authority",
                side_effect=AuthorityChangedError("gone")),
          pytest.raises(AuthorityChangedError)):
        await write(hass, data, None)
    assert [record["phase"] for record in data.config_recovery.records()] == ["not_applied"]


@pytest.mark.parametrize(("stored", "remains", "deleted"), [
    ({"id": "a1"}, False, False), (None, True, True), ({"id": "a1"}, True, False),
], ids=["reads_back", "entity_left", "both"])
async def test_a_deletion_that_does_not_read_back_gone_is_uncertain(hass, stored, remains, deleted):
    with (patch.object(authoring, "_read_config_entry", AsyncMock(return_value=stored)),
          patch.object(authoring, "_authoring_entity_id", return_value="automation.a1" if remains else None)):
        result, outcome, _ = await authoring._deletion_receipt(hass, "automation", "a1", "delete_automation")
    assert isinstance(result, UncertainOutcome) and outcome == "invalid_request" and _status(result) == "partial"
    body = json.loads(result["content"][0]["text"])
    assert body["verified"] is False and body["retry_safe"] is False and body["deleted"] is deleted
    assert "do not repeat the delete" in body["message"]


async def test_a_deletion_that_reads_back_gone_succeeds(hass):
    with (patch.object(authoring, "_read_config_entry", AsyncMock(return_value=None)),
          patch.object(authoring, "_authoring_entity_id", return_value=None)):
        result, outcome, _ = await authoring._deletion_receipt(hass, "automation", "a1", "delete_automation")
    assert outcome == "allowed" and not result.get("isError")
    assert json.loads(result["content"][0]["text"])["verified"] is True


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
