"""const.REDACTION_SENTINEL must stay equal to what the redacting readers emit.

The constant lets a WRITE path refuse to persist a placeholder (see
tool_common.redaction_sentinel_path), but the redacting readers spell the
literal inline. This file makes that duplication safe: if a reader changed its
placeholder, the write guard would silently stop matching and patch_dashboard
would accept redacted values again, and nothing else would notice.

Assertions run against the real filter_service_response rather than a restated
string, because comparing a constant to a copy of itself is vacuous.
"""

from __future__ import annotations

import uuid

from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.const import REDACTION_SENTINEL
from custom_components.phoenix_mcp.helpers import redact_structure
from custom_components.phoenix_mcp.policy_engine import filter_service_response
from custom_components.phoenix_mcp.access_key_store import PermissionTree, AccessKeyRecord


def _access_key() -> AccessKeyRecord:
    """A token with an empty tree, so every entity resolves to NO_ACCESS."""
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", token_hash="x", created_at=utcnow(),
        created_by="u", permissions=PermissionTree(domains={}),
    )


async def test_filter_service_response_emits_the_constant(hass: HomeAssistant) -> None:
    """The entity-id redaction path is what a dashboard read goes through."""
    out = filter_service_response({"entity": "light.not_visible"}, _access_key(), hass)
    assert out["entity"] == REDACTION_SENTINEL


async def test_ghost_entity_redacts_identically(hass: HomeAssistant) -> None:
    """A ghost entity (absent from states and the registry) is NO_ACCESS at once.

    It redacts like any inaccessible entity, so a dashboard carrying dead references
    stays redacted even for a token whose permissions are widened.
    """
    out = filter_service_response({"entity": "lock.deleted_long_ago"}, _access_key(), hass)
    assert out["entity"] == REDACTION_SENTINEL


def test_redact_structure_emits_the_constant() -> None:
    """The sensitive-key path, used for approval records and audit payloads."""
    assert redact_structure({"password": "hunter2"})["password"] == REDACTION_SENTINEL
