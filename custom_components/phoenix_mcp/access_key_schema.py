"""Normalize released credential fields at known storage boundaries only."""

from __future__ import annotations

# Historical payloads and configuration snapshots are deliberately not traversed.
_FIELDS = {
    "token_id": "access_key_id",
    "token_name": "access_key_name",
    "token_hash": "access_key_hash",
    "owner_token_id": "owner_access_key_id",
    "originating_token_id": "originating_access_key_id",
    "assist_bound_token_id": "assist_bound_access_key_id",
    "voice_agent_token_id": "voice_agent_access_key_id",
    "ai_task_token_id": "ai_task_access_key_id",
    "token_presets_enabled": "access_key_presets_enabled",
}
_REASONS = {
    "token_inactive": "access_key_inactive",
    "token_expired": "access_key_expired",
    "token_revoked": "access_key_revoked",
}


def normalize_access_key_fields(record: dict) -> dict:
    """Return a shallow canonical record; current fields win over legacy fields."""
    result = dict(record)
    for legacy, canonical in _FIELDS.items():
        if legacy in result:
            result.setdefault(canonical, result.pop(legacy))
    reason = result.get("rejected_reason")
    if isinstance(reason, str) and reason in _REASONS:
        result["rejected_reason"] = _REASONS[reason]
    return result


def normalize_access_key_root(raw: dict) -> tuple[dict, bool]:
    """Convert only core collections, settings and approval envelope fields."""
    result = dict(raw)
    for legacy, canonical in (("tokens", "access_keys"), ("archived_tokens", "archived_access_keys")):
        if legacy in result:
            result.setdefault(canonical, result.pop(legacy))
    for name in ("access_keys", "archived_access_keys", "pending_approvals"):
        rows = result.get(name)
        if isinstance(rows, list):
            result[name] = [normalize_access_key_fields(row) if isinstance(row, dict) else row for row in rows]
    if isinstance(result.get("settings"), dict):
        result["settings"] = normalize_access_key_fields(result["settings"])
    return result, result != raw
