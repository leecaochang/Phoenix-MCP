"""Audit rows name the provider and model of the Phoenix AI turn that made the request."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import GlobalSettings
from custom_components.phoenix_mcp.approvals import PendingApproval, audit_closed_approvals
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.request_authority import (
    acting_model, acting_model_scope, note_acting_model,
)

CFG = SimpleNamespace(kind="claude", model="claude-sonnet-5-5")
SETTINGS = GlobalSettings(log_allowed=True, log_denied=True)


def _row(audit: AuditLog, request_id: str = "r", **extra) -> None:
    audit.record(request_id=request_id, access_key_id="k", access_key_name="key", method="tools/call",
                 resource="get_state", outcome="allowed", client_ip="agentcli", settings=SETTINGS, **extra)


async def test_rows_inside_a_turn_name_its_model_and_rows_after_it_do_not():
    audit = AuditLog()

    @acting_model_scope
    async def turn() -> None:
        note_acting_model(CFG)
        _row(audit, "inside")

    _row(audit, "before")
    await turn()
    _row(audit, "after")
    rows = {e.request_id: e for e in audit.query()}
    assert (rows["inside"].provider, rows["inside"].model) == ("claude", "claude-sonnet-5-5")
    assert rows["inside"].to_dict()["provider"] == "claude"
    assert rows["inside"].to_dict()["model"] == "claude-sonnet-5-5"
    for outside in ("before", "after"):
        assert (rows[outside].provider, rows[outside].model) == (None, None)
        assert "provider" not in rows[outside].to_dict() and "model" not in rows[outside].to_dict()
    assert acting_model.get() is None


async def test_a_nested_turn_names_its_own_model_and_gives_the_outer_one_back():
    audit = AuditLog()
    inner_cfg = SimpleNamespace(kind="openai_compatible", model="local-model")

    @acting_model_scope
    async def inner() -> None:
        note_acting_model(inner_cfg)
        _row(audit, "inner")

    @acting_model_scope
    async def outer() -> None:
        note_acting_model(CFG)
        await inner()
        _row(audit, "outer")

    await outer()
    rows = {e.request_id: (e.provider, e.model) for e in audit.query()}
    assert rows == {"inner": ("openai_compatible", "local-model"), "outer": ("claude", "claude-sonnet-5-5")}


async def test_a_turn_that_fails_still_gives_the_outer_model_back():
    @acting_model_scope
    async def failing() -> None:
        note_acting_model(CFG)
        raise RuntimeError

    with pytest.raises(RuntimeError):
        await failing()
    assert acting_model.get() is None


@pytest.mark.parametrize("config", [None, SimpleNamespace(kind="claude", model=""), SimpleNamespace(kind="", model="m"),
                                    SimpleNamespace(kind="claude", model=None)])
async def test_an_account_without_a_kind_and_model_names_no_model(config):
    audit = AuditLog()

    @acting_model_scope
    async def turn() -> None:
        note_acting_model(CFG)
        note_acting_model(config)
        _row(audit)

    await turn()
    entry = audit.query()[0]
    assert (entry.provider, entry.model) == (None, None)


def test_a_model_given_with_the_row_wins_over_the_acting_one():
    audit = AuditLog()
    token = acting_model.set(("claude", "claude-sonnet-5-5"))
    try:
        _row(audit, provider_model=("mistral", "mistral-large"))
    finally:
        acting_model.reset(token)
    entry = audit.query()[0]
    assert (entry.provider, entry.model) == ("mistral", "mistral-large")


async def test_the_model_survives_storage_and_a_malformed_one_is_dropped(hass):
    from homeassistant.helpers.storage import Store

    backing = Store(hass, 1, "phoenix_test_acting_model_audit")
    audit = AuditLog(store=backing)
    _row(audit, "named", provider_model=("claude", "claude-sonnet-5-5"))
    _row(audit, "unnamed")
    await audit.async_save()
    saved = await backing.async_load()
    saved["entries"].append({**saved["entries"][0], "request_id": "half", "model": None})
    saved["entries"].append({**saved["entries"][0], "request_id": "wrong", "provider": ["claude"]})
    await backing.async_save(saved)

    loaded = AuditLog(store=backing)
    await loaded.async_load()
    rows = {e.request_id: (e.provider, e.model) for e in loaded.query()}
    assert rows == {"named": ("claude", "claude-sonnet-5-5"), "unnamed": (None, None),
                    "half": (None, None), "wrong": (None, None)}


def _approval(**extra) -> PendingApproval:
    now = utcnow()
    return PendingApproval(
        id="appr_1", access_key_id="k", access_key_name="key", tool_name="restart_ha", cap_name="cap_restart",
        args={}, diff={}, status="expired", created_at=now, expires_at=now + timedelta(minutes=5),
        request_id="queued", **extra)


def test_an_approval_keeps_the_model_that_queued_it_and_its_closing_row_names_it():
    approval = _approval(provider_model=("claude", "claude-sonnet-5-5"))
    stored = approval.to_dict(redact_args=False)
    assert (stored["provider"], stored["model"]) == ("claude", "claude-sonnet-5-5")
    assert PendingApproval.from_dict(stored).provider_model == ("claude", "claude-sonnet-5-5")
    assert PendingApproval.from_dict({**stored, "model": 3}).provider_model is None
    older = {key: value for key, value in stored.items() if key not in ("provider", "model")}
    assert PendingApproval.from_dict(older).provider_model is None

    audit = AuditLog()
    audit_closed_approvals(audit, SETTINGS, [approval, _approval()])
    assert [(e.provider, e.model) for e in audit.query()] == [(None, None), ("claude", "claude-sonnet-5-5")]
