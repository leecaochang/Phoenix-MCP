"""Token store: permissions, token and settings records, hardened load, patching and presets."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.const import (
    CAP_ALLOW,
    CAP_DENY,
    DEFAULT_RATE_LIMIT_BURST,
    DEFAULT_RATE_LIMIT_REQUESTS,
    STORAGE_VERSION,
    ACCESS_KEY_LENGTH,
    ACCESS_KEY_PREFIX,
    TOOL_ROUTING_SETTINGS,
)
from custom_components.phoenix_mcp.access_key_store import (
    ArchivedAccessKeyRecord,
    GlobalSettings,
    PermissionNode,
    PermissionTree,
    AccessKeyRecord,
    AccessKeyStore,
    access_key_name_slug as _slugify,
    hmac_compare,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------------------------------------------------------
# PermissionNode
# ---------------------------------------------------------------------------


class TestPermissionNode:
    def test_default_state_is_grey(self):
        node = PermissionNode()
        assert node.state == "GREY"
        assert node.hint is None

    def test_round_trip(self):
        node = PermissionNode(state="GREEN", hint="Main light")
        assert PermissionNode.from_dict(node.to_dict()) == node

    def test_from_dict_missing_fields(self):
        node = PermissionNode.from_dict({})
        assert node.state == "GREY"
        assert node.hint is None


# ---------------------------------------------------------------------------
# PermissionTree
# ---------------------------------------------------------------------------


class TestPermissionTree:
    def test_empty_defaults(self):
        tree = PermissionTree()
        assert tree.domains == {}
        assert tree.devices == {}
        assert tree.entities == {}

    def test_round_trip(self):
        tree = PermissionTree(
            domains={"light": PermissionNode(state="GREEN")},
            entities={"sensor.temp": PermissionNode(state="YELLOW", hint="bedroom temp")},
        )
        restored = PermissionTree.from_dict(tree.to_dict())
        assert restored.domains["light"].state == "GREEN"
        assert restored.entities["sensor.temp"].hint == "bedroom temp"

    def test_from_dict_empty(self):
        tree = PermissionTree.from_dict({})
        assert tree.domains == {}


# ---------------------------------------------------------------------------
# AccessKeyRecord
# ---------------------------------------------------------------------------


class TestTokenRecord:
    def _make(self, **kwargs):
        defaults = {
            "id": "test-uuid",
            "name": "test",
            "token_hash": "abc",
            "created_at": utcnow(),
            "created_by": "user1",
        }
        defaults.update(kwargs)
        return AccessKeyRecord(**defaults)

    def test_is_valid_active(self):
        access_key = self._make()
        assert access_key.is_valid() is True

    def test_is_valid_revoked(self):
        access_key = self._make(revoked=True)
        assert access_key.is_valid() is False

    def test_is_expired_no_expiry(self):
        access_key = self._make()
        assert access_key.is_expired() is False

    def test_is_expired_past(self):
        access_key = self._make(expires_at=utcnow() - timedelta(seconds=1))
        assert access_key.is_expired() is True
        assert access_key.is_valid() is False

    def test_is_expired_future(self):
        access_key = self._make(expires_at=utcnow() + timedelta(days=1))
        assert access_key.is_expired() is False

    def test_round_trip(self):
        access_key = self._make(
            expires_at=utcnow() + timedelta(days=7),
            pass_through=True,
            cap_restart="allow",
            permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}),
        )
        restored = AccessKeyRecord.from_dict(access_key.to_storage_dict())
        assert restored.id == access_key.id
        assert restored.name == access_key.name
        assert restored.pass_through is True
        assert restored.cap_restart == "allow"
        assert restored.permissions.domains["light"].state == "GREEN"

    def test_from_dict_defaults(self):
        access_key = AccessKeyRecord.from_dict({
            "id": "x",
            "name": "x",
            "token_hash": "x",
            "created_at": utcnow().isoformat(),
            "created_by": "user1",
        })
        assert access_key.rate_limit_requests == DEFAULT_RATE_LIMIT_REQUESTS
        assert access_key.rate_limit_burst == DEFAULT_RATE_LIMIT_BURST
        assert access_key.revoked is False
        assert access_key.pass_through is False
        assert access_key.announce_all_tools is False

    def test_from_dict_fills_new_caps_to_deny(self):
        # A record persisted before a capability existed carries only the older
        # caps. Loading must default every missing cap to deny (no storage version
        # bump is involved), never raise or leave it unset.
        access_key = AccessKeyRecord.from_dict({
            "id": "x",
            "name": "x",
            "token_hash": "x",
            "created_at": utcnow().isoformat(),
            "created_by": "user1",
            "cap_config_read": CAP_ALLOW,
            "cap_automation_write": CAP_ALLOW,
        })
        assert access_key.cap_config_read == CAP_ALLOW
        assert access_key.cap_automation_write == CAP_ALLOW
        for cap_name in (
            "cap_search", "cap_registry_read", "cap_traces", "cap_diagnostics",
            "cap_scene_write", "cap_helper_write", "cap_integration_write",
            "cap_lovelace_write", "cap_registry_write", "cap_radio_write",
            "cap_backup", "cap_filesystem", "cap_yaml_edit",
        ):
            assert getattr(access_key, cap_name) == CAP_DENY

    def test_announce_all_tools_round_trip(self):
        access_key = self._make()
        access_key.announce_all_tools = True
        d = access_key.to_dict()
        assert d["announce_all_tools"] is True
        restored = AccessKeyRecord.from_dict(access_key.to_storage_dict())
        assert restored.announce_all_tools is True

    def test_from_dict_naive_expires_at_normalized_to_utc(self):
        # A timezone-less expires_at (hand-edited or legacy storage) must load
        # aware: a naive value makes every comparison against aware UTC raise
        # TypeError, and the startup expiry sweep runs during setup, so one bad
        # record would abort the whole integration.
        access_key = AccessKeyRecord.from_dict({
            "id": "x", "name": "x", "token_hash": "x",
            "created_at": utcnow().isoformat(), "created_by": "user1",
            "expires_at": "2030-01-01T12:00:00",
        })
        assert access_key.expires_at is not None
        assert access_key.expires_at.tzinfo is not None
        assert access_key.is_expired() is False

        past = AccessKeyRecord.from_dict({
            "id": "y", "name": "y", "token_hash": "y",
            "created_at": utcnow().isoformat(), "created_by": "user1",
            "expires_at": "2020-01-01T12:00:00",
        })
        assert past.is_expired() is True

    def test_from_dict_rate_limits_sanitized(self):
        def _load(requests, burst):
            return AccessKeyRecord.from_dict({
                "id": "x", "name": "x", "token_hash": "x",
                "created_at": utcnow().isoformat(), "created_by": "user1",
                "rate_limit_requests": requests, "rate_limit_burst": burst,
            })

        # A negative limit would make the rate limiter fail every request with
        # an IndexError on an empty window; non-numeric values would raise at
        # comparison time. Both fall back to the defaults.
        bad = _load(-5, "abc")
        assert bad.rate_limit_requests == DEFAULT_RATE_LIMIT_REQUESTS
        assert bad.rate_limit_burst == DEFAULT_RATE_LIMIT_BURST

        oversized = _load(200_000, True)
        assert oversized.rate_limit_requests == DEFAULT_RATE_LIMIT_REQUESTS
        assert oversized.rate_limit_burst == DEFAULT_RATE_LIMIT_BURST

        # In-range values load verbatim, including a numeric string.
        ok = _load("30", 0)
        assert ok.rate_limit_requests == 30
        assert ok.rate_limit_burst == 0

    def test_from_dict_non_dict_permissions_load_empty(self):
        # Fail closed: a corrupt (non-object) permission tree loads as an empty
        # tree granting nothing, instead of raising AttributeError and killing
        # the record.
        for garbage in (None, "corrupt", [1, 2], 7):
            access_key = AccessKeyRecord.from_dict({
                "id": "x", "name": "x", "token_hash": "x",
                "created_at": utcnow().isoformat(), "created_by": "user1",
                "permissions": garbage,
            })
            assert access_key.permissions.domains == {}
            assert access_key.permissions.devices == {}
            assert access_key.permissions.entities == {}


# ---------------------------------------------------------------------------
# ArchivedAccessKeyRecord
# ---------------------------------------------------------------------------


class TestArchivedTokenRecord:
    def test_round_trip(self):
        now = utcnow()
        record = ArchivedAccessKeyRecord(
            id="a", name="b", token_hash="c",
            created_at=now, created_by="u", revoked_at=now,
            revoked=True,
        )
        restored = ArchivedAccessKeyRecord.from_dict(record.to_storage_dict())
        assert restored.id == "a"
        assert restored.revoked is True


# ---------------------------------------------------------------------------
# GlobalSettings
# ---------------------------------------------------------------------------


class TestGlobalSettings:
    def test_defaults(self):
        s = GlobalSettings()
        assert s.kill_switch is False
        assert s.log_allowed is True
        assert s.notify_on_rate_limit is False
        assert (s.agentcli_conversation_style, s.agentcli_detail_level,
                s.agentcli_home_focused) == ("direct", "concise", False)
        assert (s.voice_agent_conversation_style, s.voice_agent_detail_level,
                s.voice_agent_home_focused) == ("direct", "concise", False)
        assert (s.ai_task_conversation_style, s.ai_task_detail_level) == (
            "direct", "balanced",
        )

    def test_round_trip(self):
        s = GlobalSettings(
            kill_switch=True, log_client_ip=False,
            agentcli_conversation_style="warm", agentcli_detail_level="detailed",
            agentcli_home_focused=True,
            voice_agent_conversation_style="technical", voice_agent_detail_level="balanced",
            voice_agent_home_focused=True,
            ai_task_conversation_style="calm_guide", ai_task_detail_level="concise",
        )
        restored = GlobalSettings.from_dict(s.to_dict())
        assert restored.kill_switch is True
        assert restored.log_client_ip is False
        assert restored.agentcli_conversation_style == "warm"
        assert restored.agentcli_detail_level == "detailed"
        assert restored.agentcli_home_focused is True
        assert restored.voice_agent_conversation_style == "technical"
        assert restored.voice_agent_detail_level == "balanced"
        assert restored.voice_agent_home_focused is True
        assert restored.ai_task_conversation_style == "calm_guide"
        assert restored.ai_task_detail_level == "concise"

    def test_conversation_behavior_invalid_storage_uses_safe_defaults(self):
        s = GlobalSettings.from_dict({
            "agentcli_conversation_style": "invented",
            "agentcli_detail_level": "huge",
            "voice_agent_conversation_style": 3,
            "voice_agent_detail_level": None,
            "ai_task_conversation_style": "",
            "ai_task_detail_level": "verbose",
        })
        assert (s.agentcli_conversation_style, s.agentcli_detail_level) == (
            "direct", "concise",
        )
        assert (s.voice_agent_conversation_style, s.voice_agent_detail_level) == (
            "direct", "concise",
        )
        assert (s.ai_task_conversation_style, s.ai_task_detail_level) == (
            "direct", "balanced",
        )

    def test_agentcli_max_iterations_default_and_clamp(self):
        from custom_components.phoenix_mcp.const import AGENTCLI_MAX_ITERATIONS
        assert GlobalSettings().agentcli_max_iterations == AGENTCLI_MAX_ITERATIONS
        assert GlobalSettings(agentcli_max_iterations=40).to_dict()["agentcli_max_iterations"] == 40
        # Load-side clamp: out-of-range and non-numeric fall to the bounds/default.
        assert GlobalSettings.from_dict({"agentcli_max_iterations": 9999}).agentcli_max_iterations == 100
        assert GlobalSettings.from_dict({"agentcli_max_iterations": 1}).agentcli_max_iterations == 3
        assert GlobalSettings.from_dict({"agentcli_max_iterations": "x"}).agentcli_max_iterations == AGENTCLI_MAX_ITERATIONS

    def test_mesa_inject_default_off(self):
        assert GlobalSettings().mesa_inject_enabled is False

    def test_mesa_inject_round_trip(self):
        s = GlobalSettings(mesa_inject_enabled=True)
        assert GlobalSettings.from_dict(s.to_dict()).mesa_inject_enabled is True

    def test_assist_binding_default_none(self):
        assert GlobalSettings().assist_bound_token_id is None

    def test_assist_binding_round_trip(self):
        s = GlobalSettings(assist_bound_token_id="tok-123")
        assert GlobalSettings.from_dict(s.to_dict()).assist_bound_token_id == "tok-123"

    def test_assist_binding_empty_string_coerces_to_none(self):
        assert GlobalSettings.from_dict({"assist_bound_token_id": ""}).assist_bound_token_id is None
        assert GlobalSettings.from_dict({"assist_bound_token_id": 5}).assist_bound_token_id is None

    def test_voice_agent_defaults(self):
        s = GlobalSettings()
        assert s.voice_agent_enabled is False
        assert s.voice_agent_token_id is None
        assert s.voice_agent_provider_id is None
        assert s.voice_agent_model is None
        assert s.voice_agent_pipeline_id is None

    def test_voice_agent_round_trip(self):
        s = GlobalSettings(
            voice_agent_enabled=True, voice_agent_token_id="tok",
            voice_agent_provider_id="inst", voice_agent_model="claude-opus-4-8",
            voice_agent_pipeline_id="pl1",
        )
        r = GlobalSettings.from_dict(s.to_dict())
        assert r.voice_agent_enabled is True
        assert r.voice_agent_token_id == "tok"
        assert r.voice_agent_provider_id == "inst"
        assert r.voice_agent_model == "claude-opus-4-8"
        assert r.voice_agent_pipeline_id == "pl1"

    def test_voice_agent_empty_strings_coerce_to_none(self):
        r = GlobalSettings.from_dict({"voice_agent_token_id": "", "voice_agent_model": ""})
        assert r.voice_agent_token_id is None
        assert r.voice_agent_model is None

    def test_ai_task_defaults(self):
        s = GlobalSettings()
        assert s.ai_task_enabled is False
        assert s.ai_task_token_id is None
        assert s.ai_task_provider_id is None
        assert s.ai_task_model is None

    def test_ai_task_round_trip(self):
        s = GlobalSettings(
            ai_task_enabled=True, ai_task_token_id="tok",
            ai_task_provider_id="inst", ai_task_model="claude-opus-4-8",
        )
        r = GlobalSettings.from_dict(s.to_dict())
        assert r.ai_task_enabled is True
        assert r.ai_task_token_id == "tok"
        assert r.ai_task_provider_id == "inst"
        assert r.ai_task_model == "claude-opus-4-8"

    def test_ai_task_empty_strings_coerce_to_none(self):
        r = GlobalSettings.from_dict({"ai_task_token_id": "", "ai_task_provider_id": ""})
        assert r.ai_task_token_id is None
        assert r.ai_task_provider_id is None


# ---------------------------------------------------------------------------
# AccessKeyStore - creation and loading
# ---------------------------------------------------------------------------


class TestTokenStoreLoad:
    async def test_empty_load(self, access_key_store):
        assert access_key_store.list_access_keys() == []
        assert access_key_store.list_archived() == []

    async def test_load_with_existing_data(self, hass):
        now = utcnow()
        existing = {
            "version": STORAGE_VERSION,
            "tokens": [{
                "id": "tok1",
                "name": "mytoken",
                "token_hash": "hash1",
                "created_at": now.isoformat(),
                "created_by": "admin",
            }],
            "archived_tokens": [],
            "settings": {"kill_switch": True},
        }
        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=existing)
        mock_store.async_save = AsyncMock()

        with patch("custom_components.phoenix_mcp.access_key_store._PhoenixStore", return_value=mock_store):
            store = await AccessKeyStore.async_create(hass)

        access_keys = store.list_access_keys()
        assert len(access_keys) == 1
        assert access_keys[0].name == "mytoken"
        assert store.get_settings().kill_switch is True

    async def test_load_skips_unparseable_records_and_keeps_siblings(self, hass):
        # The corrupt-record handler must skip ANY record from_dict cannot
        # parse, whatever the exception type. Nested garbage inside the
        # permission tree raises AttributeError, which a narrower
        # (KeyError, TypeError, ValueError) catch would let escape, aborting setup.
        now = utcnow()
        good = {
            "id": "tok-good", "name": "good", "token_hash": "h1",
            "created_at": now.isoformat(), "created_by": "admin",
        }
        nested_corrupt = dict(
            good, id="tok-bad", name="bad", token_hash="h2",
            permissions={"domains": ["not-a-node-map"]},
        )
        existing = {
            "version": STORAGE_VERSION,
            "tokens": [nested_corrupt, "not-even-a-dict", good],
            "archived_tokens": ["garbage"],
            "settings": {},
        }
        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=existing)
        mock_store.async_save = AsyncMock()

        with patch("custom_components.phoenix_mcp.access_key_store._PhoenixStore", return_value=mock_store):
            store = await AccessKeyStore.async_create(hass)

        access_keys = store.list_access_keys()
        assert [t.id for t in access_keys] == ["tok-good"]
        assert store.list_archived() == []

    @pytest.mark.parametrize("bad_created_at", ["not-a-date", "", "2026-13-45T99:99:99"])
    async def test_load_skips_a_record_whose_required_datetime_is_unparseable(
        self, hass, bad_created_at
    ):
        """An unparseable created_at (non-optional) skips the record.

        _parse_dt returns None for anything it cannot parse, which would build a
        AccessKeyRecord with a None created_at: it loads looking valid and crashes
        later at an expiry comparison or .isoformat(). Raising routes it into the
        same skip-and-warn handler as every other corruption.
        """
        now = utcnow()
        good = {
            "id": "tok-good", "name": "good", "token_hash": "h1",
            "created_at": now.isoformat(), "created_by": "admin",
        }
        bad = dict(good, id="tok-bad", name="bad", token_hash="h2",
                   created_at=bad_created_at)
        existing = {"version": STORAGE_VERSION, "tokens": [bad, good], "settings": {}}
        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=existing)
        mock_store.async_save = AsyncMock()

        with patch("custom_components.phoenix_mcp.access_key_store._PhoenixStore", return_value=mock_store):
            store = await AccessKeyStore.async_create(hass)

        assert [t.id for t in store.list_access_keys()] == ["tok-good"]

    async def test_load_skips_an_archived_record_with_an_unparseable_revoked_at(self, hass):
        """Same rule for an archived record's revoked_at, also non-optional."""
        now = utcnow()
        archived_good = {
            "id": "arch-good", "name": "g", "token_hash": "h1",
            "created_at": now.isoformat(), "created_by": "admin",
            "revoked_at": now.isoformat(), "revoked": True,
        }
        archived_bad = dict(archived_good, id="arch-bad", name="b", token_hash="h2",
                            revoked_at="nonsense")
        existing = {
            "version": STORAGE_VERSION, "tokens": [],
            "archived_tokens": [archived_bad, archived_good], "settings": {},
        }
        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=existing)
        mock_store.async_save = AsyncMock()

        with patch("custom_components.phoenix_mcp.access_key_store._PhoenixStore", return_value=mock_store):
            store = await AccessKeyStore.async_create(hass)

        assert [t.id for t in store.list_archived()] == ["arch-good"]

    async def test_load_non_dict_root_ignored(self, hass):
        # A storage root that is not an object (corrupted file) must not crash
        # setup: every collection read assumes a dict root.
        for corrupt_root in ([1, 2, 3], "corrupt", 42):
            mock_store = AsyncMock()
            mock_store.async_load = AsyncMock(return_value=corrupt_root)
            mock_store.async_save = AsyncMock()
            with patch("custom_components.phoenix_mcp.access_key_store._PhoenixStore", return_value=mock_store):
                store = await AccessKeyStore.async_create(hass)
            assert store.list_access_keys() == []
            assert store.list_archived() == []
            assert store.get_pending_approvals() == []
            mock_store.async_save.assert_awaited()  # cleaned form healed to disk

    async def test_load_wrong_typed_collections_reset(self, hass):
        # Each top-level collection of the wrong type is reset to an empty
        # container instead of crashing (an int `tokens` raises TypeError during
        # loading; `settings` or `entity_hints` of the wrong type raise
        # AttributeError; a string `pending_approvals` would silently become a
        # list of characters).
        existing = {
            "version": STORAGE_VERSION,
            "tokens": 7,
            "archived_tokens": {"x": 1},
            "pending_approvals": "abc",
            "settings": [],
            "entity_hints": ["not", "a", "map"],
        }
        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=existing)
        mock_store.async_save = AsyncMock()
        with patch("custom_components.phoenix_mcp.access_key_store._PhoenixStore", return_value=mock_store):
            store = await AccessKeyStore.async_create(hass)
        assert store.list_access_keys() == []
        assert store.list_archived() == []
        assert store.get_pending_approvals() == []
        assert store.get_settings().kill_switch is False
        mock_store.async_save.assert_awaited()

    async def test_load_valid_collections_untouched_no_spurious_save(self, hass):
        # A well-formed current store must load verbatim with no heal-save fired by
        # normalization.
        now = utcnow()
        existing = {
            "version": STORAGE_VERSION,
            "tokens": [{
                "id": "tok1", "name": "mytoken", "token_hash": "h",
                "created_at": now.isoformat(), "created_by": "admin",
                "persona": "custom",
            }],
            "archived_tokens": [],
            "pending_approvals": [],
            "settings": GlobalSettings().to_dict(),
            "entity_hints": {"light.kitchen": "hint"},
        }
        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=existing)
        mock_store.async_save = AsyncMock()
        with patch("custom_components.phoenix_mcp.access_key_store._PhoenixStore", return_value=mock_store):
            store = await AccessKeyStore.async_create(hass)
        assert [t.id for t in store.list_access_keys()] == ["tok1"]
        mock_store.async_save.assert_not_awaited()

    async def test_load_falsy_root_healed_but_fresh_install_not(self, hass):
        # `[]`, `""`, and `0` are corrupt roots and should heal to disk; None
        # (no stored file yet) is a fresh install and must NOT trigger a save.
        for corrupt_root in ([], "", 0):
            mock_store = AsyncMock()
            mock_store.async_load = AsyncMock(return_value=corrupt_root)
            mock_store.async_save = AsyncMock()
            with patch("custom_components.phoenix_mcp.access_key_store._PhoenixStore", return_value=mock_store):
                store = await AccessKeyStore.async_create(hass)
            assert store.list_access_keys() == []
            mock_store.async_save.assert_awaited()

        fresh = AsyncMock()
        fresh.async_load = AsyncMock(return_value=None)
        fresh.async_save = AsyncMock()
        with patch("custom_components.phoenix_mcp.access_key_store._PhoenixStore", return_value=fresh):
            store = await AccessKeyStore.async_create(hass)
        assert store.list_access_keys() == []
        fresh.async_save.assert_not_awaited()

    async def test_load_discards_non_dict_approval_members(self, hass):
        # A correctly-typed pending_approvals LIST can still hold a string/int
        # member; the startup expiry sweep calls entry.get(...) on each, which
        # would abort setup. Non-dict members are discarded (and healed).
        good_approval = {"id": "ap1", "token_id": "t", "status": "pending"}
        existing = {
            "version": STORAGE_VERSION,
            "tokens": [],
            "archived_tokens": [],
            "pending_approvals": [good_approval, "corrupt", 7, None],
            "settings": {},
        }
        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=existing)
        mock_store.async_save = AsyncMock()
        with patch("custom_components.phoenix_mcp.access_key_store._PhoenixStore", return_value=mock_store):
            store = await AccessKeyStore.async_create(hass)
        assert store.get_pending_approvals() == [good_approval]
        mock_store.async_save.assert_awaited()  # cleaned form healed

    async def test_load_non_bool_privilege_flags_fail_closed(self, hass):
        # A stored access-widening flag that is a truthy non-bool (e.g. the
        # string "false") must load False, never silently enable pass-through.
        now = utcnow()
        existing = {
            "version": STORAGE_VERSION,
            "tokens": [{
                "id": "tok1", "name": "t", "token_hash": "h",
                "created_at": now.isoformat(), "created_by": "admin",
                "persona": "custom",
                "pass_through": "false",
                "use_assist_exposure": "true",
                "announce_all_tools": 1,
            }],
            "archived_tokens": [],
            "settings": {},
        }
        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=existing)
        mock_store.async_save = AsyncMock()
        with patch("custom_components.phoenix_mcp.access_key_store._PhoenixStore", return_value=mock_store):
            store = await AccessKeyStore.async_create(hass)
        tok = store.list_access_keys()[0]
        assert tok.pass_through is False
        assert tok.use_assist_exposure is False
        assert tok.announce_all_tools is False


# ---------------------------------------------------------------------------
# AccessKeyStore - token creation
# ---------------------------------------------------------------------------


class TestTokenCreation:
    async def test_create_returns_raw_token_and_record(self, access_key_store):
        record, raw = await access_key_store.async_create_access_key("mytoken", "user1")
        assert raw.startswith(ACCESS_KEY_PREFIX)
        assert len(raw) == ACCESS_KEY_LENGTH
        assert record.name == "mytoken"
        assert record.created_by == "user1"

    async def test_raw_token_not_stored(self, access_key_store):
        record, raw = await access_key_store.async_create_access_key("t1", "u")
        expected_hash = _sha256(raw)
        assert record.token_hash == expected_hash
        assert raw not in record.token_hash

    async def test_create_saves_immediately(self, access_key_store, mock_store):
        await access_key_store.async_create_access_key("t1", "u")
        mock_store.async_save.assert_called()

    async def test_create_with_expiry(self, access_key_store):
        expiry = utcnow() + timedelta(days=30)
        record, _ = await access_key_store.async_create_access_key("t1", "u", expires_at=expiry)
        assert record.expires_at == expiry

    async def test_create_pass_through(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u", pass_through=True)
        assert record.pass_through is True

    async def test_burst_coerced_to_zero_when_requests_zero(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key(
            "t1", "u", rate_limit_requests=0, rate_limit_burst=10
        )
        assert record.rate_limit_burst == 0

    async def test_burst_preserved_when_requests_nonzero(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key(
            "t1", "u", rate_limit_requests=30, rate_limit_burst=5
        )
        assert record.rate_limit_burst == 5

    async def test_default_rate_limits(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        assert record.rate_limit_requests == DEFAULT_RATE_LIMIT_REQUESTS
        assert record.rate_limit_burst == DEFAULT_RATE_LIMIT_BURST

    async def test_default_capability_flags_all_deny(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        assert record.cap_automation_write == "deny"
        assert record.cap_config_read == "deny"
        assert record.cap_template_render == "deny"
        assert record.cap_restart == "deny"
        assert record.persona == "custom"

    async def test_multiple_tokens_have_unique_ids(self, access_key_store):
        r1, _ = await access_key_store.async_create_access_key("t1", "u")
        r2, _ = await access_key_store.async_create_access_key("t2", "u")
        assert r1.id != r2.id

    async def test_multiple_tokens_have_unique_hashes(self, access_key_store):
        r1, _ = await access_key_store.async_create_access_key("t1", "u")
        r2, _ = await access_key_store.async_create_access_key("t2", "u")
        assert r1.token_hash != r2.token_hash


# ---------------------------------------------------------------------------
# AccessKeyStore - lookup
# ---------------------------------------------------------------------------


class TestTokenLookup:
    async def test_get_by_id(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        found = access_key_store.get_access_key_by_id(record.id)
        assert found is not None
        assert found.id == record.id

    async def test_get_by_id_missing(self, access_key_store):
        assert access_key_store.get_access_key_by_id("nonexistent") is None

    async def test_get_by_hash(self, access_key_store):
        record, raw = await access_key_store.async_create_access_key("t1", "u")
        presented_hash = _sha256(raw)
        found = access_key_store.get_access_key_by_hash(presented_hash)
        assert found is not None
        assert found.id == record.id

    async def test_get_by_hash_wrong_value(self, access_key_store):
        await access_key_store.async_create_access_key("t1", "u")
        assert access_key_store.get_access_key_by_hash(_sha256("wrong")) is None

    async def test_get_archived_by_hash(self, access_key_store):
        record, raw = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_archive_access_key(record.id, revoked=True)
        presented_hash = _sha256(raw)
        found = access_key_store.get_archived_by_hash(presented_hash)
        assert found is not None
        assert found.id == record.id


# ---------------------------------------------------------------------------
# AccessKeyStore - listing
# ---------------------------------------------------------------------------


class TestTokenListing:
    async def test_list_tokens_empty(self, access_key_store):
        assert access_key_store.list_access_keys() == []

    async def test_list_tokens_contains_created(self, access_key_store):
        r1, _ = await access_key_store.async_create_access_key("t1", "u")
        r2, _ = await access_key_store.async_create_access_key("t2", "u")
        ids = {t.id for t in access_key_store.list_access_keys()}
        assert r1.id in ids
        assert r2.id in ids

    async def test_active_token_count(self, access_key_store):
        assert access_key_store.active_access_key_count() == 0
        await access_key_store.async_create_access_key("t1", "u")
        assert access_key_store.active_access_key_count() == 1
        await access_key_store.async_create_access_key("t2", "u")
        assert access_key_store.active_access_key_count() == 2

    async def test_list_archived_empty(self, access_key_store):
        assert access_key_store.list_archived() == []


# ---------------------------------------------------------------------------
# AccessKeyStore - archival
# ---------------------------------------------------------------------------


class TestTokenArchival:
    async def test_revoke_moves_to_archived(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_archive_access_key(record.id, revoked=True)
        assert access_key_store.get_access_key_by_id(record.id) is None
        archived = access_key_store.list_archived()
        assert len(archived) == 1
        assert archived[0].id == record.id
        assert archived[0].revoked is True

    async def test_expire_archived_with_revoked_false(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_archive_access_key(record.id, revoked=False)
        archived = access_key_store.list_archived()
        assert archived[0].revoked is False

    async def test_archive_saves_immediately(self, access_key_store, mock_store):
        mock_store.async_save.reset_mock()
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        mock_store.async_save.reset_mock()
        await access_key_store.async_archive_access_key(record.id, revoked=True)
        mock_store.async_save.assert_called()

    async def test_archive_nonexistent_returns_none(self, access_key_store):
        result = await access_key_store.async_archive_access_key("no-such-id", revoked=True)
        assert result is None

    async def test_archive_retains_audit_fields(self, access_key_store):
        expiry = utcnow() + timedelta(days=1)
        record, _ = await access_key_store.async_create_access_key("t1", "u", expires_at=expiry)
        access_key_store.update_last_used(record.id, utcnow())
        archived = await access_key_store.async_archive_access_key(record.id, revoked=True)
        assert archived.name == "t1"
        assert archived.created_by == "u"
        assert archived.expires_at == expiry
        assert archived.last_used_at is not None

    async def test_permission_tree_not_in_archived(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        archived = await access_key_store.async_archive_access_key(record.id, revoked=True)
        assert not hasattr(archived, "permissions")


# ---------------------------------------------------------------------------
# AccessKeyStore - patching
# ---------------------------------------------------------------------------


class TestTokenPatch:
    async def test_patch_pass_through(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        updated = await access_key_store.async_patch_access_key(record.id, pass_through=True)
        assert updated.pass_through is True

    async def test_patch_announce_all_tools(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        assert record.announce_all_tools is False
        updated = await access_key_store.async_patch_access_key(record.id, announce_all_tools=True)
        assert updated.announce_all_tools is True

    async def test_patch_rate_limit(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        updated = await access_key_store.async_patch_access_key(
            record.id, rate_limit_requests=30, rate_limit_burst=5
        )
        assert updated.rate_limit_requests == 30
        assert updated.rate_limit_burst == 5

    async def test_patch_rate_requests_zero_coerces_burst(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u", rate_limit_burst=10)
        updated = await access_key_store.async_patch_access_key(record.id, rate_limit_requests=0)
        assert updated.rate_limit_burst == 0

    async def test_patch_capability_flags(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        updated = await access_key_store.async_patch_access_key(
            record.id,
            cap_restart="allow",
            cap_config_read="allow",
            cap_automation_write="allow",
            cap_template_render="allow",
        )
        assert updated.cap_restart == "allow"
        assert updated.cap_config_read == "allow"
        assert updated.cap_automation_write == "allow"
        assert updated.cap_template_render == "allow"

    async def test_patch_cap_change_after_persona_drops_to_custom(self, access_key_store):
        """Changing a cap after applying a persona switches the persona to custom."""
        from custom_components.phoenix_mcp.personas import get_persona_caps

        record, _ = await access_key_store.async_create_access_key("t1", "u")
        # Seed the voice_assistant persona.
        await access_key_store.async_patch_access_key(
            record.id,
            persona="voice_assistant",
            **get_persona_caps("voice_assistant"),
        )
        assert access_key_store.get_access_key_by_id(record.id).persona == "voice_assistant"

        # Override cap_automation_write (was deny in voice_assistant). Persona should re-derive.
        updated = await access_key_store.async_patch_access_key(
            record.id,
            cap_automation_write="allow",
        )
        assert updated.cap_automation_write == "allow"
        assert updated.persona == "custom"

    async def test_patch_cap_change_can_promote_to_matching_persona(self, access_key_store):
        """If the post-change caps match a different preset exactly, that preset is detected."""
        from custom_components.phoenix_mcp.personas import get_persona_caps

        record, _ = await access_key_store.async_create_access_key("t1", "u")
        # Start from automation_builder.
        await access_key_store.async_patch_access_key(
            record.id,
            persona="automation_builder",
            **get_persona_caps("automation_builder"),
        )
        assert access_key_store.get_access_key_by_id(record.id).persona == "automation_builder"

        # Patch exactly the caps that differ between automation_builder and
        # power_user; the resulting set matches power_user, so the label promotes.
        ab = get_persona_caps("automation_builder")
        pu = get_persona_caps("power_user")
        diff = {cap: mode for cap, mode in pu.items() if ab.get(cap) != mode}
        updated = await access_key_store.async_patch_access_key(record.id, **diff)
        assert updated.persona == "power_user"

    async def test_patch_non_cap_change_does_not_touch_persona(self, access_key_store):
        """Patching only rate-limit fields does not re-derive the persona."""
        from custom_components.phoenix_mcp.personas import get_persona_caps

        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_patch_access_key(
            record.id,
            persona="voice_assistant",
            **get_persona_caps("voice_assistant"),
        )
        updated = await access_key_store.async_patch_access_key(record.id, rate_limit_requests=30)
        assert updated.persona == "voice_assistant"
        assert updated.rate_limit_requests == 30

    async def test_patch_saves_immediately(self, access_key_store, mock_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        mock_store.async_save.reset_mock()
        await access_key_store.async_patch_access_key(record.id, cap_restart="allow")
        mock_store.async_save.assert_called()

    async def test_patch_nonexistent_returns_none(self, access_key_store):
        result = await access_key_store.async_patch_access_key("no-such-id", cap_restart="allow")
        assert result is None

    async def test_patch_applies_name_and_ignores_immutable(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_patch_access_key(record.id, name="renamed-x", created_by="hacker")
        fetched = access_key_store.get_access_key_by_id(record.id)
        assert fetched.name == "renamed-x"   # name is mutable (rename)
        assert fetched.created_by == "u"     # created_by stays immutable (ignored)

    async def test_name_slug_exists_excludes_self(self, access_key_store):
        a, _ = await access_key_store.async_create_access_key("alpha", "u")
        await access_key_store.async_create_access_key("beta", "u")
        assert access_key_store.name_slug_exists("beta") is True
        assert access_key_store.name_slug_exists("beta", exclude_access_key_id=a.id) is True   # beta is a different token
        assert access_key_store.name_slug_exists("alpha", exclude_access_key_id=a.id) is False  # only match is self


# ---------------------------------------------------------------------------
# AccessKeyStore - permissions
# ---------------------------------------------------------------------------


class TestPermissions:
    async def test_set_full_permission_tree(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        tree = PermissionTree(
            domains={"light": PermissionNode(state="GREEN")},
            entities={"sensor.temp": PermissionNode(state="YELLOW", hint="temp sensor")},
        )
        updated = await access_key_store.async_set_permissions(record.id, tree)
        assert updated.permissions.domains["light"].state == "GREEN"
        assert updated.permissions.entities["sensor.temp"].hint == "temp sensor"

    async def test_patch_permission_node_set(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_patch_permission_node(
            record.id, "domains", "light", "GREEN", hint="ceiling lights"
        )
        access_key = access_key_store.get_access_key_by_id(record.id)
        assert access_key.permissions.domains["light"].state == "GREEN"
        assert access_key.permissions.domains["light"].hint == "ceiling lights"

    async def test_patch_permission_node_grey_removes_node(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_patch_permission_node(record.id, "domains", "light", "GREEN")
        await access_key_store.async_patch_permission_node(record.id, "domains", "light", "GREY")
        access_key = access_key_store.get_access_key_by_id(record.id)
        assert "light" not in access_key.permissions.domains

    async def test_patch_permission_node_invalid_type_returns_none(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        result = await access_key_store.async_patch_permission_node(
            record.id, "invalid_type", "light", "GREEN"
        )
        assert result is None

    async def test_permissions_save_immediately(self, access_key_store, mock_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        mock_store.async_save.reset_mock()
        await access_key_store.async_patch_permission_node(record.id, "domains", "light", "GREEN")
        mock_store.async_save.assert_called()

    async def test_set_permissions_nonexistent_returns_none(self, access_key_store):
        result = await access_key_store.async_set_permissions("no-such-id", PermissionTree())
        assert result is None


# ---------------------------------------------------------------------------
# AccessKeyStore - last_used_at
# ---------------------------------------------------------------------------


class TestLastUsed:
    async def test_update_last_used_in_memory(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        ts = utcnow()
        access_key_store.update_last_used(record.id, ts)
        access_key = access_key_store.get_access_key_by_id(record.id)
        assert access_key.last_used_at == ts

    async def test_update_last_used_nonexistent_no_error(self, access_key_store):
        access_key_store.update_last_used("no-such-id", utcnow())

    async def test_flush_last_used_calls_save(self, access_key_store, mock_store):
        record, _ = await access_key_store.async_create_access_key(name="usage-token", created_by="u")
        access_key_store.update_last_used(record.id, utcnow())
        mock_store.async_save.reset_mock()
        await access_key_store.async_flush_last_used()
        mock_store.async_save.assert_called_once()


# ---------------------------------------------------------------------------
# AccessKeyStore - archived deletion
# ---------------------------------------------------------------------------


class TestArchivedDeletion:
    async def test_delete_archived_removes_record(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_archive_access_key(record.id, revoked=True)
        result = await access_key_store.async_delete_archived(record.id)
        assert result is True
        assert access_key_store.list_archived() == []

    async def test_delete_archived_saves_immediately(self, access_key_store, mock_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_archive_access_key(record.id, revoked=True)
        mock_store.async_save.reset_mock()
        await access_key_store.async_delete_archived(record.id)
        mock_store.async_save.assert_called()

    async def test_delete_archived_nonexistent_returns_false(self, access_key_store):
        result = await access_key_store.async_delete_archived("no-such-id")
        assert result is False


# ---------------------------------------------------------------------------
# AccessKeyStore - settings
# ---------------------------------------------------------------------------


class TestSettings:
    async def test_routing_settings_survive_load_without_migration_markers(self, hass):
        settings = GlobalSettings(
            agentcli_tool_routing_mode="observe",
            voice_agent_tool_routing_mode="observe",
            ai_task_tool_routing_mode="advise",
            agentcli_routing_mode="auto", voice_agent_routing_mode="auto",
            decision_provider_id="decision-account", mesa_mode="enforced",
        ).to_dict()
        assert "command_routing_version" not in settings
        assert "tool_routing_version" not in settings
        storage = AsyncMock()
        storage.async_load.return_value = {"version": STORAGE_VERSION, "settings": settings}
        with patch("custom_components.phoenix_mcp.access_key_store._PhoenixStore", return_value=storage):
            store = await AccessKeyStore.async_create(hass)
        assert store.get_settings().to_dict() == settings
        storage.async_save.assert_not_awaited()

    async def test_deliberate_review_opt_in_survives_reload(self, access_key_store, mock_store):
        updates = {key: "observe" for key in TOOL_ROUTING_SETTINGS.values()}
        await access_key_store.async_patch_settings(**updates)
        persisted = mock_store.async_save.call_args.args[0]
        mock_store.async_load.return_value = persisted
        mock_store.async_save.reset_mock()
        await access_key_store.async_load()
        assert all(access_key_store.get_settings().to_dict()[key] == mode for key, mode in updates.items())
        mock_store.async_save.assert_not_awaited()

    async def test_get_settings_defaults(self, access_key_store):
        settings = access_key_store.get_settings()
        assert settings.kill_switch is False
        assert settings.log_allowed is True

    async def test_patch_settings(self, access_key_store):
        updated = await access_key_store.async_patch_settings(kill_switch=True, log_client_ip=False)
        assert updated.kill_switch is True
        assert updated.log_client_ip is False
        assert access_key_store.get_settings().kill_switch is True

    async def test_patch_settings_saves_immediately(self, access_key_store, mock_store):
        mock_store.async_save.reset_mock()
        await access_key_store.async_patch_settings(kill_switch=True)
        mock_store.async_save.assert_called()


# ---------------------------------------------------------------------------
# AccessKeyStore - wipe
# ---------------------------------------------------------------------------


class TestWipe:
    async def test_wipe_clears_all(self, access_key_store):
        await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_create_access_key("t2", "u")
        await access_key_store.async_patch_settings(
            kill_switch=True,
            decision_provider_id="decision-account",
            decision_timeout_ms=12345,
            decision_shadow_enabled=True,
            voice_agent_routing_mode="auto",
            agentcli_routing_mode="hybrid",
            command_routing_languages=["es"],
            command_routing_timeout_ms=750,
            agentcli_routing_shadow_enabled=True,
            agentcli_conversation_style="lively",
            agentcli_detail_level="detailed",
            agentcli_home_focused=True,
            voice_agent_conversation_style="warm",
            voice_agent_detail_level="balanced",
            voice_agent_home_focused=True,
            ai_task_conversation_style="technical",
            ai_task_detail_level="concise",
        )
        await access_key_store.async_wipe()
        assert access_key_store.list_access_keys() == []
        assert access_key_store.list_archived() == []
        settings = access_key_store.get_settings()
        assert settings.kill_switch is False
        assert (settings.agentcli_conversation_style, settings.agentcli_detail_level,
                settings.agentcli_home_focused) == ("direct", "concise", False)
        assert (settings.voice_agent_conversation_style, settings.voice_agent_detail_level,
                settings.voice_agent_home_focused) == ("direct", "concise", False)
        assert (settings.ai_task_conversation_style, settings.ai_task_detail_level) == (
            "direct", "balanced",
        )

        assert settings.decision_provider_id is None
        assert settings.decision_timeout_ms == GlobalSettings().decision_timeout_ms
        assert settings.command_routing_timeout_ms == GlobalSettings().command_routing_timeout_ms
        assert settings.command_routing_languages == GlobalSettings().command_routing_languages
        assert settings.voice_agent_routing_mode == "off"
        assert settings.agentcli_routing_mode == "off"
        assert not settings.decision_shadow_enabled
        assert not settings.agentcli_routing_shadow_enabled

    async def test_wipe_saves_immediately(self, access_key_store, mock_store):
        mock_store.async_save.reset_mock()
        await access_key_store.async_wipe()
        mock_store.async_save.assert_called()

    async def test_wipe_clears_pending_approvals(self, access_key_store):
        # An orphaned pending approval must not survive a wipe (and thus must not
        # be persisted back to disk by the async_save inside async_wipe).
        access_key_store.set_pending_approvals([
            {"id": "ap1", "token_id": "gone", "status": "pending"},
        ])
        await access_key_store.async_wipe()
        assert access_key_store.get_pending_approvals() == []


# ---------------------------------------------------------------------------
# AccessKeyStore - slug uniqueness
# ---------------------------------------------------------------------------


class TestSlugUniqueness:
    async def test_same_name_slug_detected(self, access_key_store):
        await access_key_store.async_create_access_key("my-token", "u")
        assert access_key_store.name_slug_exists("my-token") is True
        assert access_key_store.name_slug_exists("my_token") is True

    async def test_different_name_not_collision(self, access_key_store):
        await access_key_store.async_create_access_key("my-token", "u")
        assert access_key_store.name_slug_exists("other-token") is False

    async def test_slugify(self):
        assert _slugify("my-token") == "my_token"
        assert _slugify("MyToken") == "mytoken"
        assert _slugify("my_token") == "my_token"


# ---------------------------------------------------------------------------
# hmac_compare
# ---------------------------------------------------------------------------


class TestHmacCompare:
    def test_equal_hashes(self):
        h = _sha256("phx_abc123")
        assert hmac_compare(h, h) is True

    def test_unequal_hashes(self):
        h1 = _sha256("phx_abc")
        h2 = _sha256("phx_xyz")
        assert hmac_compare(h1, h2) is False

    def test_empty_strings(self):
        assert hmac_compare("", "") is True

    def test_one_empty(self):
        assert hmac_compare("abc", "") is False


# ---------------------------------------------------------------------------
# Concurrent PATCH (async_lock)
# ---------------------------------------------------------------------------


class TestConcurrentPatch:
    async def test_async_lock_prevents_interleaving(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        results = []

        async def patch_and_record(value):
            async with access_key_store.async_lock:
                await access_key_store.async_patch_access_key(
                    record.id, rate_limit_requests=value
                )
                access_key = access_key_store.get_access_key_by_id(record.id)
                results.append(access_key.rate_limit_requests)

        await asyncio.gather(patch_and_record(10), patch_and_record(20))
        assert len(results) == 2
        assert set(results) == {10, 20}

    async def test_async_lock_is_asyncio_lock(self, access_key_store):
        assert isinstance(access_key_store.async_lock, asyncio.Lock)


# ---------------------------------------------------------------------------
# Token settings presets (workspace model)
# ---------------------------------------------------------------------------


class TestPresets:
    async def test_add_preset_snapshots_current_and_marks_active(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_patch_access_key(record.id, cap_restart=CAP_ALLOW)
        await access_key_store.async_set_permissions(
            record.id,
            PermissionTree(entities={"light.a": PermissionNode(state="GREEN")}),
        )
        updated = await access_key_store.async_add_preset(record.id, "Baseline")
        assert len(updated.presets) == 1
        preset = updated.presets[0]
        assert updated.active_preset_id == preset.id
        assert preset.name == "Baseline"
        assert preset.caps["cap_restart"] == CAP_ALLOW
        assert preset.permissions.entities["light.a"].state == "GREEN"
        # Deep copy: mutating the live tree must not touch the preset.
        updated.permissions.entities["light.a"].state = "RED"
        assert preset.permissions.entities["light.a"].state == "GREEN"

    async def test_preset_round_trip_through_storage(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_add_preset(record.id, "Baseline")
        access_key = access_key_store.get_access_key_by_id(record.id)
        loaded = AccessKeyRecord.from_dict(access_key.to_storage_dict())
        assert len(loaded.presets) == 1
        assert loaded.presets[0].to_dict() == access_key.presets[0].to_dict()
        assert loaded.active_preset_id == access_key.active_preset_id
        assert loaded.settings_version == access_key.settings_version
        assert loaded.tools_list_version == access_key.tools_list_version
        # Persisted deliberately: a deploy is only detectable across the HA
        # restart that applies it, so an in-memory-only fingerprint would lose
        # the very baseline the comparison needs.
        access_key.tools_catalog_fingerprint = "abc123abc123abc1"
        assert AccessKeyRecord.from_dict(
            access_key.to_storage_dict()).tools_catalog_fingerprint == "abc123abc123abc1"

    async def test_corrupt_preset_dropped_token_survives(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_add_preset(record.id, "Baseline")
        raw = access_key_store.get_access_key_by_id(record.id).to_storage_dict()
        del raw["presets"][0]["id"]
        loaded = AccessKeyRecord.from_dict(raw)
        assert loaded.presets == []
        # The dangling active reference is cleared with it.
        assert loaded.active_preset_id is None

    def test_new_field_defaults_on_legacy_records(self):
        base = {
            "id": "tid", "name": "t", "token_hash": "h",
            "created_at": utcnow().isoformat(), "created_by": "u",
        }
        loaded = AccessKeyRecord.from_dict(base)
        assert loaded.presets == [] and loaded.active_preset_id is None
        assert loaded.settings_version == 1
        assert loaded.tools_list_version == 1
        # tools_list_version defaults to the LOADED settings_version so an
        # upgrade never makes an existing token look stale.
        loaded = AccessKeyRecord.from_dict({**base, "settings_version": 5})
        assert loaded.tools_list_version == 5
        # The catalog fingerprint has NO such fallback: access_key_store cannot import
        # mcp_view to learn the current one, so a legacy record has no baseline
        # and empty must read as "never recorded", never as stale.
        assert loaded.tools_catalog_fingerprint == ""
        # confirm_inline_wait_seconds defaults to OFF. Blocking a confirm-gated
        # call is opt-in: holding the request stops the NEXT approval from even
        # being created (tool calls arrive one at a time), so a run of writes
        # reaches the operator's queue one per wait-period instead of at once.
        assert loaded.confirm_inline_wait_seconds == 0

    def test_inline_wait_round_trips_and_clamps(self):
        base = {
            "id": "tid", "name": "t", "token_hash": "h",
            "created_at": utcnow().isoformat(), "created_by": "u",
        }
        assert AccessKeyRecord.from_dict({**base, "confirm_inline_wait_seconds": 30}).confirm_inline_wait_seconds == 30
        # Over the cap clamps to 180; a below-floor positive clamps up to 30;
        # 0/negative/garbage means off (0).
        assert AccessKeyRecord.from_dict({**base, "confirm_inline_wait_seconds": 999}).confirm_inline_wait_seconds == 180
        assert AccessKeyRecord.from_dict({**base, "confirm_inline_wait_seconds": 10}).confirm_inline_wait_seconds == 30
        assert AccessKeyRecord.from_dict({**base, "confirm_inline_wait_seconds": 0}).confirm_inline_wait_seconds == 0
        assert AccessKeyRecord.from_dict({**base, "confirm_inline_wait_seconds": -5}).confirm_inline_wait_seconds == 0
        assert AccessKeyRecord.from_dict({**base, "confirm_inline_wait_seconds": "x"}).confirm_inline_wait_seconds == 0
        # Full serializer round trip preserves a valid value.
        loaded = AccessKeyRecord.from_dict({**base, "confirm_inline_wait_seconds": 90})
        assert AccessKeyRecord.from_dict(loaded.to_storage_dict()).confirm_inline_wait_seconds == 90

    async def test_inline_wait_patch_clamps(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        updated = await access_key_store.async_patch_access_key(record.id, confirm_inline_wait_seconds=999)
        assert updated.confirm_inline_wait_seconds == 180
        # 0 is a valid patch (disable, the unattended-agent mode).
        updated = await access_key_store.async_patch_access_key(record.id, confirm_inline_wait_seconds=0)
        assert updated.confirm_inline_wait_seconds == 0

    async def test_inline_wait_snapshotted_in_preset(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_patch_access_key(record.id, confirm_inline_wait_seconds=60)
        updated = await access_key_store.async_add_preset(record.id, "Baseline")
        assert updated.presets[0].confirm_inline_wait_seconds == 60

    async def test_duplicate_name_case_insensitive_raises(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        await access_key_store.async_add_preset(record.id, "Baseline")
        try:
            await access_key_store.async_add_preset(record.id, "baseline")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    async def test_preset_cap_enforced(self, access_key_store):
        from custom_components.phoenix_mcp.const import MAX_PRESETS_PER_ACCESS_KEY

        record, _ = await access_key_store.async_create_access_key("t1", "u")
        for i in range(MAX_PRESETS_PER_ACCESS_KEY):
            await access_key_store.async_add_preset(record.id, f"p{i}")
        try:
            await access_key_store.async_add_preset(record.id, "one-too-many")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    async def test_rename_preset(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        updated = await access_key_store.async_add_preset(record.id, "Old")
        pid = updated.presets[0].id
        updated = await access_key_store.async_rename_preset(record.id, pid, "New")
        assert updated.presets[0].name == "New"
        try:
            await access_key_store.async_rename_preset(record.id, "ghost", "X")
            raise AssertionError("expected LookupError")
        except LookupError:
            pass

    async def test_delete_active_preset_refused(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        updated = await access_key_store.async_add_preset(record.id, "A")
        pid_a = updated.presets[0].id
        updated = await access_key_store.async_add_preset(record.id, "B")
        pid_b = updated.presets[1].id
        assert updated.active_preset_id == pid_b
        try:
            await access_key_store.async_delete_preset(record.id, pid_b)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
        updated = await access_key_store.async_delete_preset(record.id, pid_a)
        assert [p.id for p in updated.presets] == [pid_b]

    async def test_sync_preset_absorbs_live_state(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        updated = await access_key_store.async_add_preset(record.id, "A")
        preset = updated.presets[0]
        created_at = preset.created_at
        await access_key_store.async_patch_access_key(record.id, cap_restart=CAP_ALLOW)
        updated = await access_key_store.async_sync_preset(record.id, preset.id)
        synced = updated.presets[0]
        assert synced.caps["cap_restart"] == CAP_ALLOW
        assert synced.id == preset.id and synced.name == "A"
        assert synced.created_at == created_at

    async def test_seed_default_presets_idempotent(self, access_key_store):
        from custom_components.phoenix_mcp.const import DEFAULT_PRESET_NAME

        bare, _ = await access_key_store.async_create_access_key("bare", "u")
        preset_owner, _ = await access_key_store.async_create_access_key("owner", "u")
        await access_key_store.async_add_preset(preset_owner.id, "Mine")

        assert await access_key_store.async_seed_default_presets() == 1
        seeded = access_key_store.get_access_key_by_id(bare.id)
        assert seeded.presets[0].name == DEFAULT_PRESET_NAME
        assert seeded.active_preset_id == seeded.presets[0].id
        owner = access_key_store.get_access_key_by_id(preset_owner.id)
        assert [p.name for p in owner.presets] == ["Mine"]
        assert await access_key_store.async_seed_default_presets() == 0

    async def test_create_token_seeds_when_enabled(self, access_key_store):
        from custom_components.phoenix_mcp.const import DEFAULT_PRESET_NAME

        await access_key_store.async_patch_settings(token_presets_enabled=True)
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        assert record.presets[0].name == DEFAULT_PRESET_NAME
        assert record.active_preset_id == record.presets[0].id


class TestSettingsVersion:
    async def test_cap_patch_bumps_and_keeps_active_preset(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        updated = await access_key_store.async_add_preset(record.id, "A")
        active = updated.active_preset_id
        before = updated.settings_version
        updated = await access_key_store.async_patch_access_key(record.id, cap_restart=CAP_ALLOW)
        assert updated.settings_version == before + 1
        # Workspace model: manual edits are the active preset's unsaved state,
        # they never clear the active label.
        assert updated.active_preset_id == active

    async def test_name_only_rename_does_not_bump(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        before = record.settings_version
        updated = await access_key_store.async_patch_access_key(record.id, name="renamed")
        assert updated.settings_version == before

    async def test_inline_wait_patch_does_not_bump(self, access_key_store):
        # confirm_inline_wait_seconds changes neither tools nor caps, so it must
        # not fire the stale-tools advisory (like a bare rename).
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        before = record.settings_version
        updated = await access_key_store.async_patch_access_key(record.id, confirm_inline_wait_seconds=60)
        assert updated.confirm_inline_wait_seconds == 60
        assert updated.settings_version == before

    async def test_permission_writes_bump(self, access_key_store):
        record, _ = await access_key_store.async_create_access_key("t1", "u")
        before = record.settings_version
        await access_key_store.async_set_permissions(record.id, PermissionTree())
        assert access_key_store.get_access_key_by_id(record.id).settings_version == before + 1
        await access_key_store.async_patch_permission_node(
            record.id, "entities", "light.a", "GREEN"
        )
        assert access_key_store.get_access_key_by_id(record.id).settings_version == before + 2

    def test_settings_toggle_round_trip(self):
        assert GlobalSettings.from_dict({"token_presets_enabled": True}).token_presets_enabled is True
        assert GlobalSettings().token_presets_enabled is False
        assert GlobalSettings(token_presets_enabled=True).to_dict()["token_presets_enabled"] is True
