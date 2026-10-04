"""Normalize released credential fields at known storage boundaries only."""

from __future__ import annotations

from .const import COMMAND_ROUTING_PLAN_KEY, MESA_APPROVED_EXECUTOR

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


def _normalize_approval(record: dict) -> dict:
    """Rename Phoenix-owned proof and preview fields without refreshing authority."""
    result = normalize_access_key_fields(record)
    args = result.get("args")
    if result.get("tool_name") in ("call_service", MESA_APPROVED_EXECUTOR) and isinstance(args, dict):
        plan = args.get(COMMAND_ROUTING_PLAN_KEY)
        if isinstance(plan, dict):
            plan = normalize_access_key_fields(plan)
            if "token_version" in plan:
                plan.setdefault("access_key_version", plan.pop("token_version"))
            result["args"] = {**args, COMMAND_ROUTING_PLAN_KEY: plan}
    diff = result.get("diff")
    if not isinstance(diff, dict):
        return result
    preview = diff.get("preview")
    if not isinstance(preview, dict):
        return result
    preview = dict(preview)

    def references(rows: object) -> object:
        if not isinstance(rows, list):
            return rows
        return [
            {**normalize_access_key_fields(row), "kind": "access_key_permission"}
            if isinstance(row, dict) and row.get("kind") == "token_permission" else row
            for row in rows
        ]

    tool = result.get("tool_name")
    if tool in ("set_entity", "delete_entity") and "phoenix_configuration" in preview:
        preview["phoenix_configuration"] = references(preview["phoenix_configuration"])
    elif tool == "remove_device" and "device_permission_references" in preview:
        preview["device_permission_references"] = references(preview["device_permission_references"])
    elif tool == "remove_integration" and isinstance(preview.get("permission_references"), dict):
        preview["permission_references"] = {
            key: references(value) if key in ("entities", "devices") else value
            for key, value in preview["permission_references"].items()
        }
    result["diff"] = {**diff, "preview": preview}
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
            normalize = _normalize_approval if name == "pending_approvals" else normalize_access_key_fields
            result[name] = [normalize(row) if isinstance(row, dict) else row for row in rows]
    if isinstance(result.get("settings"), dict):
        result["settings"] = normalize_access_key_fields(result["settings"])
    return result, result != raw
