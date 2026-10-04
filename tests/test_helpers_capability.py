"""Capability evaluation helpers: effective_cap(s) and deferred diff builders.

Covers the deny/allow/confirm formula with its pass-through interaction and
async_evaluate_capability's lazy diff construction.
"""

from __future__ import annotations

from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.const import (
    CAP_ALLOW,
    CAP_CONFIRM,
    CAP_DENY,
    PASS_THROUGH_EXEMPT_CAPS,
)
from custom_components.phoenix_mcp.helpers import effective_cap, effective_caps
from custom_components.phoenix_mcp.access_key_store import PermissionTree, AccessKeyRecord


def _access_key(**overrides) -> AccessKeyRecord:
    defaults = dict(
        id="t",
        name="test",
        access_key_hash="x",
        created_at=utcnow(),
        created_by="admin",
        permissions=PermissionTree(),
    )
    defaults.update(overrides)
    return AccessKeyRecord(**defaults)


# --- non-exempt cap (cap_config_read) -----------------------------------------


class TestNonExemptCap:
    """cap_config_read is NOT pass-through exempt: pass-through bypasses deny, not confirm."""

    def test_scoped_deny(self):
        tok = _access_key(cap_config_read=CAP_DENY)
        assert effective_cap(tok, "cap_config_read") == CAP_DENY

    def test_scoped_allow(self):
        tok = _access_key(cap_config_read=CAP_ALLOW)
        assert effective_cap(tok, "cap_config_read") == CAP_ALLOW

    def test_pass_through_overrides_deny(self):
        tok = _access_key(cap_config_read=CAP_DENY, pass_through=True)
        assert effective_cap(tok, "cap_config_read") == CAP_ALLOW

    def test_pass_through_keeps_allow(self):
        tok = _access_key(cap_config_read=CAP_ALLOW, pass_through=True)
        assert effective_cap(tok, "cap_config_read") == CAP_ALLOW


# --- exempt cap (cap_restart) -------------------------------------------------


class TestExemptCap:
    """cap_restart IS pass-through exempt, so its configured value is used as-is."""

    def test_scoped_deny(self):
        tok = _access_key(cap_restart=CAP_DENY)
        assert effective_cap(tok, "cap_restart") == CAP_DENY

    def test_pass_through_does_not_override_deny(self):
        tok = _access_key(cap_restart=CAP_DENY, pass_through=True)
        assert effective_cap(tok, "cap_restart") == CAP_DENY

    def test_scoped_allow(self):
        tok = _access_key(cap_restart=CAP_ALLOW)
        assert effective_cap(tok, "cap_restart") == CAP_ALLOW

    def test_pass_through_keeps_allow(self):
        tok = _access_key(cap_restart=CAP_ALLOW, pass_through=True)
        assert effective_cap(tok, "cap_restart") == CAP_ALLOW

    def test_confirm_preserved_under_pass_through(self):
        tok = _access_key(cap_restart=CAP_CONFIRM, pass_through=True)
        assert effective_cap(tok, "cap_restart") == CAP_CONFIRM


# --- design rule: confirm honored under pass-through for non-exempt caps ----


class TestConfirmHonoredUnderPassThrough:
    """Confirm survives pass_through on non-exempt caps."""

    def test_non_exempt_confirm_routes_through_gate(self):
        # cap_config_read is non-exempt: pass-through upgrades deny to allow
        # but must leave confirm alone.
        tok = _access_key(cap_config_read=CAP_CONFIRM, pass_through=True)
        assert effective_cap(tok, "cap_config_read") == CAP_CONFIRM


# --- exempt set integrity -----------------------------------------------------


class TestPassThroughExemptCapsContents:
    """Caps that pass-through must never upgrade from deny are in PASS_THROUGH_EXEMPT_CAPS."""

    def test_restart_is_exempt(self):
        assert "cap_restart" in PASS_THROUGH_EXEMPT_CAPS

    def test_physical_control_is_exempt(self):
        assert "cap_physical_control" in PASS_THROUGH_EXEMPT_CAPS

    def test_automation_write_is_exempt(self):
        assert "cap_automation_write" in PASS_THROUGH_EXEMPT_CAPS

    def test_script_write_is_exempt(self):
        assert "cap_script_write" in PASS_THROUGH_EXEMPT_CAPS

    def test_log_read_is_exempt(self):
        assert "cap_log_read" in PASS_THROUGH_EXEMPT_CAPS

    def test_radio_write_is_exempt(self):
        assert "cap_radio_write" in PASS_THROUGH_EXEMPT_CAPS

    def test_config_read_is_not_exempt(self):
        assert "cap_config_read" not in PASS_THROUGH_EXEMPT_CAPS

    def test_template_render_is_not_exempt(self):
        assert "cap_template_render" not in PASS_THROUGH_EXEMPT_CAPS


# --- effective_caps aggregator ------------------------------------------------


class TestEffectiveCaps:
    def test_returns_all_caps(self):
        from custom_components.phoenix_mcp.const import CAPABILITY_NAMES

        tok = _access_key()
        caps = effective_caps(tok)
        assert set(caps.keys()) == set(CAPABILITY_NAMES)

    def test_default_access_key_all_deny(self):
        tok = _access_key()
        for value in effective_caps(tok).values():
            assert value == CAP_DENY

    def test_pass_through_flips_non_exempt_to_allow(self):
        tok = _access_key(pass_through=True)
        caps = effective_caps(tok)
        for cap_name, value in caps.items():
            if cap_name in PASS_THROUGH_EXEMPT_CAPS:
                assert value == CAP_DENY
            else:
                assert value == CAP_ALLOW


# --- deferred diff construction -----------------------------------------------


class TestDeferredDiff:
    """A diff is only read when an approval is created, so builders are deferred.

    Builders passed as callables may read configuration off disk or call the
    ESPHome add-on; an allowed or denied write must not pay for a discarded diff.
    """

    async def test_builder_not_called_on_allow(self):
        from unittest.mock import MagicMock

        from custom_components.phoenix_mcp.helpers import async_evaluate_capability

        calls = []

        def _builder():
            calls.append(1)
            return {"kind": "yaml_diff"}

        result = await async_evaluate_capability(
            "cap_config_read", _access_key(cap_config_read=CAP_ALLOW), MagicMock(), MagicMock(),
            tool_name="t", args={}, request_id="r", diff=_builder,
        )
        assert result.is_allow
        assert calls == []

    async def test_builder_not_called_on_deny(self):
        from unittest.mock import MagicMock

        from custom_components.phoenix_mcp.helpers import async_evaluate_capability

        calls = []

        def _builder():
            calls.append(1)
            return {"kind": "yaml_diff"}

        result = await async_evaluate_capability(
            "cap_config_read", _access_key(cap_config_read=CAP_DENY), MagicMock(), MagicMock(),
            tool_name="t", args={}, request_id="r", diff=_builder,
        )
        assert result.is_deny
        assert calls == []

    async def test_resolve_diff_accepts_sync_async_and_dict(self):
        """A deferred diff may be a dict, a sync callable or an async callable."""
        from custom_components.phoenix_mcp.helpers import _resolve_diff

        async def _async_builder():
            return {"kind": "async"}

        assert await _resolve_diff({"kind": "dict"}) == {"kind": "dict"}
        assert await _resolve_diff(lambda: {"kind": "sync"}) == {"kind": "sync"}
        assert await _resolve_diff(_async_builder) == {"kind": "async"}
        assert await _resolve_diff(None) == {}
        assert await _resolve_diff(lambda: None) == {}
