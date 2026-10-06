"""Control, previews and redaction honor the pass-through Assist-exposure filter.

A pass-through access key with use_assist_exposure reaches only entities exposed
to Assist, for actions as for reads, as HA's own Assist does. Each check runs the
same request with the filter off as well, so a refusal is the filter and not a
broken fixture. The exposure lookup writes HA records on first sight and is
loop-only: worker threads take no scope decision, which a static guard pins.
"""

from __future__ import annotations

import ast
import collections
import logging
import pathlib
import threading
from unittest.mock import AsyncMock, patch

from homeassistant.components.homeassistant.exposed_entities import DATA_EXPOSED_ENTITIES, async_expose_entity

from custom_components.phoenix_mcp import policy_engine
from custom_components.phoenix_mcp.helpers import redact_inaccessible_entity_ids_in_text
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.policy_engine import Permission, resolve
from tests import test_assist_exposure_reads as reads
from tests.test_assist_exposure_reads import _body, _data, _key

home = reads.home
related = reads.related


def _turn_on(target: dict) -> dict:
    return {"service": {"domain": "light", "name": "turn_on"}, "targets": [target]}


async def test_call_service_never_acts_on_an_unexposed_entity(home, related):
    _, _, shown, hidden = related
    calls = AsyncMock()
    home.services.async_register("light", "turn_on", calls)
    result, outcome, _ = await _call_tool("call_service", _turn_on({"kind": "entity", "ids": [hidden]}), _key(), home, _data())
    assert outcome == "denied" and calls.await_count == 0, result
    _, outcome, _ = await _call_tool("call_service", _turn_on({"kind": "entity", "ids": [hidden]}), _key(filtered=False), home, _data())
    assert outcome == "allowed" and calls.await_count == 1


async def test_call_service_by_area_acts_and_reports_only_exposed(home, related):
    area, _, shown, hidden = related
    calls = AsyncMock()
    home.services.async_register("light", "turn_on", calls)
    result = await _call_tool("call_service", _turn_on({"kind": "area", "ids": [area.id]}), _key(), home, _data())
    assert result[1] == "allowed", result
    assert calls.await_args.args[0].data["entity_id"] == [shown]
    assert [row["entity_id"] for row in _body(result)["targets"]["states_after_call"]] == [shown]
    result = await _call_tool("call_service", _turn_on({"kind": "area", "ids": [area.id]}), _key(filtered=False), home, _data())
    assert sorted(calls.await_args.args[0].data["entity_id"]) == sorted([shown, hidden])


async def test_dry_run_service_reveals_no_unexposed_target(home, related):
    _, _, shown, hidden = related
    args = {"service": {"domain": "light", "name": "turn_on"}, "targets": [{"kind": "entity", "ids": [shown, hidden]}]}
    body = _body(await _call_tool("dry_run_service", args, _key(), home, _data()))
    assert body["resolved_entities"] == [shown]
    body = _body(await _call_tool("dry_run_service", args, _key(filtered=False), home, _data()))
    assert sorted(body["resolved_entities"]) == sorted([shown, hidden])


async def test_an_unexposed_automation_is_not_a_visible_consumer(home, related):
    _, automation, shown, _ = related
    async_expose_entity(home, "conversation", automation, False)
    scope = {"scope": {"kind": "entity", "id": shown}}
    assert _body(await _call_tool("get_relationships", scope, _key(), home, _data()))["consumers"] == []
    consumers = _body(await _call_tool("get_relationships", scope, _key(filtered=False), home, _data()))["consumers"]
    assert [c["kind"] for c in consumers] == ["automation"]


async def test_text_redaction_hides_unexposed_ids(home, related):
    _, _, shown, hidden = related
    text = f"{shown} and {hidden}"
    assert redact_inaccessible_entity_ids_in_text(text, _key(), home) == f"{shown} and <redacted-entity>"
    assert redact_inaccessible_entity_ids_in_text(text, _key(filtered=False), home) == text


async def test_off_loop_exposure_check_fails_closed_without_recording(home, related, caplog):
    _, _, shown, _ = related
    policy_engine._off_loop_reported = False
    assert resolve(shown, _key(), home) == Permission.WRITE
    before = dict(home.data[DATA_EXPOSED_ENTITIES].entities)
    result: list = []
    worker = threading.Thread(target=lambda: result.append(resolve(shown, _key(), home)))
    with caplog.at_level(logging.ERROR):
        worker.start()
        worker.join()
    assert result == [Permission.NO_ACCESS]
    assert "off the event loop" in caplog.text
    assert dict(home.data[DATA_EXPOSED_ENTITIES].entities) == before


async def test_esphome_listing_decides_scope_on_the_loop(home):
    from custom_components.phoenix_mcp.tools import esphome
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def in_scope(*_args, **_kwargs):
        seen.append(threading.get_ident())
        return True

    with patch.object(esphome, "_esphome_files", return_value=[{"file": "a.yaml", "bytes": 1, "device": None}]), \
            patch.object(esphome, "_esphome_file_in_scope", side_effect=in_scope):
        rows = await esphome._esphome_listing(home, _key())
    assert rows and seen == [loop_thread]


_SCOPE_DECISIONS = {
    "resolve", "resolve_read", "resolve_registry_access", "_resolve_access_key_access", "_assist_exposed",
    "assist_expose_check", "filter_entities_for_access_key", "build_permitted_states", "build_permitted_entity_ids",
    "resolve_service_targets", "resolve_intent_entities", "filter_service_response",
    "redact_inaccessible_entity_ids_in_text", "visible_entity_ids", "_esphome_file_in_scope",
}
_GENERIC = {"get", "read", "record", "result", "done", "event", "text", "start", "items", "values", "keys",
            "update", "pop", "append", "add", "load", "save", "run", "call", "write", "close", "check"}


def test_no_worker_thread_takes_a_scope_decision():
    """Executor jobs must not reach a scope decision; the exposure lookup is loop-only."""
    root = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "phoenix_mcp"
    trees = [ast.parse(path.read_text()) for path in root.rglob("*.py") if "mesa_core" not in path.parts]
    defined = collections.Counter(node.name for tree in trees for node in ast.walk(tree)
                                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))

    def callee(func: ast.expr) -> str | None:
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name in _GENERIC or (isinstance(func, ast.Attribute) and defined.get(name) != 1):
            return None
        return name

    calls: dict[str, set[str]] = collections.defaultdict(set)
    jobs: list[str] = []
    for tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in (sub for sub in ast.walk(node) if isinstance(sub, ast.Call)):
                if name := callee(call.func):
                    calls[node.name].add(name)
                if getattr(call.func, "attr", getattr(call.func, "id", None)) in ("async_add_executor_job", "to_thread"):
                    target = call.args[0] if call.args else None
                    inner = [c.func for c in ast.walk(target) if isinstance(c, ast.Call)] if isinstance(target, (ast.Lambda, ast.Call)) else [target]
                    jobs.extend(n for f in inner if f is not None and (n := callee(f)))

    def path_to_decision(name: str, seen: tuple = ()) -> list[str] | None:
        if name in _SCOPE_DECISIONS:
            return [name]
        if name in seen or not defined.get(name):
            return None
        for child in sorted(calls.get(name, ())):
            if found := path_to_decision(child, (*seen, name)):
                return [name, *found]
        return None

    offenders = [path for job in sorted(set(jobs)) if (path := path_to_decision(job))]
    assert not offenders, offenders
