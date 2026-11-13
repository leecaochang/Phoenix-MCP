"""Jinja an agent adds to a dashboard passes the template worker before any approval exists.

Drawing a pending card, as the approval Preview does, sends its Jinja to Core's render_template,
so every agent layout write and layout restore checks the strings it adds in the worker first and
refuses the call when the worker refuses, dies or runs slow. Strings the stored layout already
holds are never re-checked. Worker requests run in-process here; one real-worker case lives in
test_template_worker_checks.py.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import config_reconciliation, template_render
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.tools import lovelace, recovery
from custom_components.phoenix_mcp.version_store import VersionStore
from custom_components.phoenix_mcp.ws_dispatch import async_get_lovelace_config, async_save_lovelace_config

# A Jinja escape for a lone surrogate: tool arguments refuse the raw character, not this text.
SURROGATE = "{{ as_timestamp('\\ud800', 0) }}"
PLAIN = {"type": "markdown", "content": "weather"}
JINJA = {"type": "markdown", "content": "{{ states('light.kitchen') }}"}


class ApprovalStore:
    """The slice of the integration store the Confirm gate keeps pending approvals in."""

    def __init__(self) -> None:
        self.pending: list = []
        self.async_lock = asyncio.Lock()
        self.async_save = AsyncMock()

    def get_pending_approvals(self) -> list:
        return self.pending

    def set_pending_approvals(self, approvals: list) -> None:
        self.pending = approvals

    def get_settings(self):
        return SimpleNamespace(mesa_mode="off", notify_on_approval=False)


def _data() -> tuple[PhoenixData, ApprovalStore]:
    store = ApprovalStore()
    return PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore(), mesa=None), store


def _key(mode: str = "confirm") -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}),
        cap_registry_read="allow", cap_lovelace_write=mode,
    )


def _text(content: dict) -> str:
    return content["content"][0]["text"]


async def _seed(hass: HomeAssistant, cards: list) -> dict:
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    layout = {"views": [{"title": "V", "cards": cards}]}
    await async_save_lovelace_config(hass, None, layout)
    return layout


@pytest.fixture
def gate_calls(monkeypatch) -> list[str]:
    """Record every _gate call the dashboard tools make; the real gate still runs."""
    calls: list[str] = []
    for module in (lovelace, recovery):
        real = module._gate

        async def spy(*args, _real=real, **kwargs):
            calls.append(kwargs.get("tool_name", ""))
            return await _real(*args, **kwargs)

        monkeypatch.setattr(module, "_gate", spy)
    return calls


@pytest.fixture
def worker_calls(monkeypatch) -> list[list[str]]:
    """Record the templates of each worker check; the in-process worker still runs them."""
    calls: list[list[str]] = []
    run = template_render._run_worker

    async def spy(payload, hass, data):
        calls.append(list(payload.get("templates", [])))
        return await run(payload, hass, data)

    monkeypatch.setattr(template_render, "_run_worker", spy)
    return calls


def _writes(card: dict) -> list[tuple[str, dict]]:
    """One call per agent layout write, each adding ``card`` to a dashboard holding PLAIN."""
    return [
        ("add_dashboard_card", {"view_index": 0, "card": card}),
        ("edit_dashboard_card", {"view_index": 0, "card_index": 0, "card": card}),
        ("set_dashboard_config", {"config": {"views": [{"title": "V", "cards": [dict(PLAIN), card]}]}}),
        ("patch_dashboard", {"changes": [{"path": ["views", 0, "cards", 0, "content"], "kind": "set",
                                          "value": card["content"]}]}),
    ]


@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize("mode", ["confirm", "allow"])
async def test_a_surrogate_template_is_refused_before_any_approval(hass, gate_calls, index, mode):
    layout = await _seed(hass, [dict(PLAIN)])
    data, store = _data()
    tool, args = _writes({"type": "markdown", "content": SURROGATE})[index]
    content, outcome, _ = await _call_tool(tool, args, _key(mode), hass, data)
    assert outcome == "invalid_request", content
    assert "lone surrogate" in _text(content) and "Nothing was written" in _text(content)
    assert gate_calls == [] and store.pending == []
    assert await async_get_lovelace_config(hass, None) == layout


@pytest.mark.parametrize(("worker", "expected"), [
    ((-9, b""), "exited unexpectedly"),
    ((0, b'{"compile_seconds": 0.3}'), "too long to compile"),
])
async def test_a_dead_worker_or_slow_compile_refuses_the_card(hass, monkeypatch, gate_calls, worker, expected):
    await _seed(hass, [dict(PLAIN)])
    monkeypatch.setattr(template_render, "_run_worker", AsyncMock(return_value=worker))
    data, store = _data()
    content, outcome, _ = await _call_tool(
        "add_dashboard_card", {"view_index": 0, "card": dict(JINJA)}, _key(), hass, data)
    assert outcome == "invalid_request" and expected in _text(content)
    assert gate_calls == [] and store.pending == []


async def test_only_added_jinja_goes_to_the_worker(hass, worker_calls):
    """Existing templates are not re-checked and do not count against the per-request cap."""
    existing = [{"type": "markdown", "content": "{{ %d }}" % n} for n in range(300)]
    await _seed(hass, [dict(PLAIN), *existing])
    data, store = _data()
    key = _key("allow")
    _, outcome, _ = await _call_tool("add_dashboard_card", {"view_index": 0, "card": {"type": "markdown", "content": "new"}},
                                     key, hass, data)
    assert outcome == "allowed" and worker_calls == []
    _, outcome, _ = await _call_tool("add_dashboard_card", {"view_index": 0, "card": dict(JINJA)}, key, hass, data)
    assert outcome == "pending_approval" and worker_calls == [[JINJA["content"]]]


async def test_a_write_adding_more_templates_than_one_check_takes_is_refused(hass, worker_calls, gate_calls):
    await _seed(hass, [dict(PLAIN)])
    data, store = _data()
    card = {"type": "vertical-stack", "cards": [{"type": "markdown", "content": "{{ %d }}" % n} for n in range(300)]}
    content, outcome, _ = await _call_tool("add_dashboard_card", {"view_index": 0, "card": card}, _key(), hass, data)
    assert outcome == "invalid_request" and "Too many templates" in _text(content)
    assert worker_calls == [] and gate_calls == [] and store.pending == []


@pytest.mark.parametrize(("card", "outcome"), [
    (dict(PLAIN, content="plain text"), "allowed"),
    ({"type": "custom:button-card", "name": "[[[ return hass.user.name ]]]"}, "pending_approval"),
])
async def test_plain_cards_and_javascript_templates_skip_the_worker(hass, worker_calls, card, outcome):
    await _seed(hass, [dict(PLAIN)])
    data, _ = _data()
    _, got, _ = await _call_tool("add_dashboard_card", {"view_index": 0, "card": card}, _key("allow"), hass, data)
    assert got == outcome and worker_calls == []


async def test_a_template_below_the_argument_depth_limit_is_still_checked(hass, gate_calls):
    """A patch can address a value deeper in a stored layout than any tool argument may nest."""
    nested: dict = {"content": "plain"}
    for _ in range(80):
        nested = {"x": nested}
    layout = await _seed(hass, [dict(PLAIN, deep=nested)])
    data, store = _data()
    path = ["views", 0, "cards", 0, "deep", *["x"] * 80, "content"]
    content, outcome, _ = await _call_tool(
        "patch_dashboard", {"changes": [{"path": path, "kind": "set", "value": SURROGATE}]}, _key(), hass, data)
    assert outcome == "invalid_request" and "lone surrogate" in _text(content), content
    assert gate_calls == [] and store.pending == []
    assert await async_get_lovelace_config(hass, None) == layout


async def test_an_agent_restore_bringing_back_a_failing_template_is_refused(hass, monkeypatch, gate_calls):
    await _seed(hass, [dict(PLAIN), dict(JINJA)])
    data, store = _data()
    key = _key("allow")
    content, outcome, _ = await _call_tool(
        "set_dashboard_config", {"config": {"views": [{"title": "V", "cards": [dict(PLAIN)]}]}}, key, hass, data)
    assert outcome == "allowed"
    recovery_id = json.loads(_text(content))["recovery_id"]
    gate_calls.clear()
    monkeypatch.setattr(template_render, "_run_worker", AsyncMock(return_value=(-9, b"")))
    args = {"recovery_id": recovery_id, "side": "before"}
    content, outcome, _ = await _call_tool("restore_configuration", args, key, hass, data)
    assert outcome == "invalid_request" and "exited unexpectedly" in _text(content)
    assert gate_calls == [] and store.pending == []
    # An administrator's restore is its own review: its receipt never runs the agent check.
    template_render._run_worker.reset_mock()
    _, diff = await config_reconciliation._restore_receipt(hass, data, data.config_recovery.get(recovery_id), "before")
    assert diff["reviewed_actions"] == ["{{ }}"]
    template_render._run_worker.assert_not_called()
