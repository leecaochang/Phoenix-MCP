"""Config-file tools: the scoped filesystem jail and the raw YAML edit.

`cap_filesystem` covers only www/, themes/ and custom_templates/; `_resolve_fs_path`
realpaths and requires containment (const.FILESYSTEM_ALLOWED_DIRS), so symlinks and
traversal cannot escape and configuration.yaml is unreachable. `cap_yaml_edit` covers
configuration.yaml and the YAML files it !includes, behind three refusals:
- Mount gate (`_yaml_mount_refusal`): an include target's top level IS the value
  of the key it loads at (http.yaml holds trusted_proxies as a plain key), which a
  content check cannot see. Refused: files reached from homeassistant:, http:,
  frontend: or lovelace:, files that cannot be ruled out, and files nothing loads.
- Protected subtrees (`_yaml_protected_check`): adding, removing or changing a path
  in const.YAML_PROTECTED_SUBTREES is hard-refused, pre-gate and at apply time,
  failing closed on unparseable content. They redefine HA's OWN trust boundary
  (auth, trusted proxies, brute-force defences, packages, dashboard JavaScript);
  command_line and shell_command stay writable.
- Removal guard (`_yaml_removal_check`): dropping a top-level key present in the
  file is refused unless `remove_keys` names it, so the claim shows in the approval
  diff. It abstains on absent or unparseable prior content (a broken file stays
  repairable) and on a version RESTORE.
"""

from __future__ import annotations

from ..request_authority import check_request_authority
from ..config_recovery import ConfigurationRecoveryError, safe_snapshot

from collections.abc import Sequence
from typing import Any
import asyncio
import json
import logging
import os

from homeassistant.util.file import WriteError
from homeassistant.core import HomeAssistant

from ..const import CAP_DENY, DOMAIN, FILESYSTEM_ALLOWED_DIRS, MAX_FILE_BYTES, REDACTION_SENTINEL, YAML_PROTECTED_SUBTREES
from ..data import PhoenixData
from ..tool_contracts import normalize_tool_args, unknown_argument_error
from ..helpers import content_hash, diff_summary_fields as _summary, effective_cap, redact_secrets_in_text as _redact_secrets_in_text, str_arg, str_list_arg, version_summary_fields as _version_summary
from ..tool_common import _CAP_FORBIDDEN_MESSAGE, _cas_conflict, _gate, _read_text_capped, _record_version, _restore_ctx, _text_file_cas_conflict, _tool_error, _tool_success, _truncate, _usable_path_arg, _version_content_payload, _write_text_atomic, redaction_sentinel_path
from ..token_store import TokenRecord
from .. import yaml_includes, yaml_patch

_LOGGER = logging.getLogger(__name__)


_CONFIG_YAML = "configuration.yaml"
_YAML_LOCKS_KEY = f"{DOMAIN}_yaml_file_locks"


def _authored_file_snapshot(rel: str, existed: bool, content: str | None,
                            snapshot_type: str = "authored_file") -> dict:
    """Serialize one authored file without persisting secret-shaped source text."""
    if existed != (content is not None):
        raise ConfigurationRecoveryError("A complete file recovery snapshot is unavailable; nothing was dispatched.")
    redacted = _redact_secrets_in_text(content)
    if content is not None and redacted != content:
        redacted = redacted or ""
        line = next(i for i, (before, after) in enumerate(
            zip(content.split("\n"), redacted.split("\n")), 1) if before != after)
        raise ConfigurationRecoveryError(
            f"The file contains suspected sensitive content on line {line} and cannot be "
            "snapshotted safely. Remove inline credentials or use references to securely "
            "stored secrets. Approval cannot override this check; nothing was dispatched."
        )
    return safe_snapshot({
        "snapshot_type": snapshot_type, "schema": 1,
        "resource_id": rel, "exists": existed, "content": content,
    }) or {}


async def _file_recovery_precheck(
    hass: HomeAssistant, path: str, rel: str, content: str, tool: str,
    snapshot_type: str = "authored_file",
) -> tuple[dict, str, str] | None:
    """Admit both recovery images before asking an administrator to approve."""
    existed = await hass.async_add_executor_job(os.path.isfile, path)
    before = None
    if existed:
        try:
            before = await hass.async_add_executor_job(_read_text_capped, path)
        except (OSError, ValueError):
            return _tool_error(
                "The existing file cannot be read completely for recovery; nothing was dispatched."
            ), "invalid_request", tool
    for label, exists, text in (("Existing file", existed, before), ("Proposed file", True, content)):
        try:
            await hass.async_add_executor_job(_authored_file_snapshot, rel, exists, text, snapshot_type)
        except ConfigurationRecoveryError as err:
            return _tool_error(f"{label}: {err}"), "invalid_request", tool
    return None


async def _recoverable_file_write(
    *, path: str, rel: str, before_exists: bool, before_content: str | None,
    content: str, token: TokenRecord, data: PhoenixData, operation: str,
    display_path: str, hass: HomeAssistant, snapshot_type: str = "authored_file",
    return_result: bool = False, recovery_id_out: dict[str, str] | None = None,
) -> tuple[dict, str, str] | None:
    """Persist the file-family before image, dispatch once, and verify readback."""
    recovery = data.config_recovery
    resource = f"authored_file:{rel}"
    try:
        before = await hass.async_add_executor_job(_authored_file_snapshot, rel, before_exists, before_content, snapshot_type)
        expected = await hass.async_add_executor_job(_authored_file_snapshot, rel, True, content, snapshot_type)
        async with recovery.resource(resource):
            rid = await recovery.begin(resource, operation, token.id, before,
                expected=expected)
            if recovery_id_out is not None:
                recovery_id_out["recovery_id"] = rid
            try:
                await recovery.mark(rid, "dispatching")
                current_exists = await hass.async_add_executor_job(os.path.isfile, path)
                current_content = None
                if current_exists:
                    try:
                        current_content = await hass.async_add_executor_job(_read_text_capped, path)
                    except (OSError, ValueError):
                        current_content = None
                if current_exists != before_exists or current_content != before_content:
                    await recovery.mark(rid, "not_applied")
                    return _tool_error("The file changed while preparing its recovery snapshot; nothing was dispatched."), "invalid_request", operation
                check_request_authority()
                try:
                    await hass.async_add_executor_job(_write_text_atomic, path, content)
                except (WriteError, OSError):
                    # Atomic-writer failure can follow replacement (directory fsync).
                    # Only verified unchanged state proves that no write landed.
                    unchanged = False
                    try:
                        exists = await hass.async_add_executor_job(os.path.isfile, path)
                        actual = await hass.async_add_executor_job(_read_text_capped, path) if exists else None
                        unchanged = exists == before_exists and actual == before_content
                    except (OSError, ValueError):
                        pass
                    await recovery.mark(rid, "not_applied" if unchanged else "unknown", error="file_write_failed")
                    return _tool_error("The file write failed; nothing changed." if unchanged else
                        "The file write outcome is uncertain; inspect configuration recovery before retrying."), "invalid_request", operation
                observed = await hass.async_add_executor_job(_read_text_capped, path)
                if observed != content:
                    await recovery.mark(rid, "partial", observed_hash=content_hash(observed))
                    return _tool_success(json.dumps({
                        "status": "partial", "recovery_id": rid,
                        "message": "The file write landed but readback differs; do not retry automatically.",
                    })), "allowed", f"file:{display_path}"
                await recovery.mark(rid, "verified", observed_hash=content_hash(observed))
                source = await hass.async_add_executor_job(_authored_file_snapshot, rel, True, observed, snapshot_type)
                await recovery.set_source(rid, source)
                if return_result:
                    return _tool_success(json.dumps({
                        "status": "verified", "recovery_id": rid,
                        "path": rel, "content_hash": content_hash(observed),
                    })), "allowed", f"file:{display_path}"
            finally:
                recovery.leave(rid)
    except ConfigurationRecoveryError as err:
        return _tool_error(str(err)), "invalid_request", operation
    except (OSError, WriteError):
        _LOGGER.exception("%s recovery write failed for %s", operation, rel)
        return _tool_error("The file write or its recovery readback failed; inspect configuration recovery before retrying."), "invalid_request", operation
    return None


async def _recoverable_file_delete(
    *, path: str, rel: str, before_content: str, token: TokenRecord,
    data: PhoenixData, operation: str, hass: HomeAssistant,
) -> tuple[dict, str, str]:
    """Delete one scoped authored file only after persisting and rechecking it."""
    recovery = data.config_recovery
    resource = f"authored_file:{rel}"
    try:
        before = await hass.async_add_executor_job(_authored_file_snapshot, rel, True, before_content)
        async with recovery.resource(resource):
            rid = await recovery.begin(resource, operation, token.id, before,
                expected=_authored_file_snapshot(rel, False, None))
            try:
                await recovery.mark(rid, "dispatching")
                exists = await hass.async_add_executor_job(os.path.isfile, path)
                current = await hass.async_add_executor_job(_read_text_capped, path) if exists else None
                if not exists or current != before_content:
                    await recovery.mark(rid, "not_applied")
                    return _tool_error("The file changed while preparing its recovery snapshot; nothing was deleted."), "invalid_request", operation
                check_request_authority()
                await hass.async_add_executor_job(os.remove, path)
                if await hass.async_add_executor_job(os.path.exists, path):
                    await recovery.mark(rid, "partial")
                    return _tool_success(json.dumps({
                        "status": "partial", "recovery_id": rid,
                        "message": "The file delete landed but the path still exists; do not retry automatically.",
                    })), "allowed", f"file:{rel}"
                absent = _authored_file_snapshot(rel, False, None)
                await recovery.mark(rid, "verified")
                await recovery.set_source(rid, absent)
                return _tool_success(json.dumps({
                    "status": "verified", "recovery_id": rid, "path": rel,
                })), "allowed", f"file:{rel}"
            finally:
                recovery.leave(rid)
    except ConfigurationRecoveryError as err:
        return _tool_error(str(err)), "invalid_request", operation
    except OSError:
        _LOGGER.exception("%s recovery delete failed for %s", operation, rel)
        return _tool_error("The file delete or its recovery readback failed; inspect configuration recovery before retrying."), "invalid_request", operation


def _get_yaml_file_lock(hass: HomeAssistant, rel: str) -> asyncio.Lock:
    """Per-file lock for whole-file writes: set_yaml_config, patch_yaml_config, write_file.

    A full-file replace loses an edit when two callers interleave, and the
    protected-subtree re-check reads prior content a few statements before the write
    lands. expected_hash narrows that window but is optional, so it cannot be the
    floor. Keyed by config-relative path (bounded by the write jail) so edits to
    unrelated files do not serialize.

    The automation, script and scene executors take their own domain locks, never
    these: yaml_includes refuses a domain defined inline in configuration.yaml, so
    they only splice !include leaf files. The one remaining overlap, an entry-level
    edit racing a whole-file write on the SAME leaf, is guarded by expected_hash.
    """
    locks: dict[str, asyncio.Lock] = hass.data.setdefault(_YAML_LOCKS_KEY, {})
    if rel not in locks:
        locks[rel] = asyncio.Lock()
    return locks[rel]


# ---------------------------------------------------------------------------
# Scoped filesystem (cap_filesystem): www/ themes/ custom_templates/
# ---------------------------------------------------------------------------




def _resolve_fs_path(hass: HomeAssistant, path: Any) -> str | None:
    """Resolve a path to a realpath strictly inside an allowed config dir, or None.

    realpath collapses '..' before the containment check, so traversal out of the
    allowlist is refused (returns None).
    """
    if _usable_path_arg(path) is None:
        return None
    config_dir = os.path.realpath(hass.config.config_dir)
    candidate = os.path.realpath(os.path.join(config_dir, path))
    for allowed in FILESYSTEM_ALLOWED_DIRS:
        base = os.path.realpath(os.path.join(config_dir, allowed))
        if candidate == base or candidate.startswith(base + os.sep):
            return candidate
    return None


def _listdir(target: str) -> list[dict]:
    return [
        {"name": name, "is_dir": os.path.isdir(os.path.join(target, name))}
        for name in sorted(os.listdir(target))
    ]






async def _tool_list_files(
    args: dict, token: TokenRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list files in an allowed config directory."""
    if effective_cap(token, "cap_filesystem") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "list_files"
    error = unknown_argument_error("list_files", args)
    if error:
        return _tool_error(error), "invalid_request", "list_files"
    path = str_arg(args.get("path"))
    if not path:
        return _tool_success(json.dumps({"directories": list(FILESYSTEM_ALLOWED_DIRS)})), "allowed", "list_files"
    target = _resolve_fs_path(hass, path)
    if target is None or not await hass.async_add_executor_job(os.path.isdir, target):
        return _tool_error("Directory not found."), "not_found", path
    entries = await hass.async_add_executor_job(_listdir, target)
    return _tool_success(json.dumps({"path": path, "entries": entries}, default=str)), "allowed", path






async def _tool_read_file(
    args: dict, token: TokenRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read a UTF-8 text file from an allowed config directory."""
    if effective_cap(token, "cap_filesystem") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "read_file"
    error = unknown_argument_error("read_file", args)
    if error:
        return _tool_error(error), "invalid_request", "read_file"
    path = str_arg(args.get("path"))
    target = _resolve_fs_path(hass, path)
    if target is None or not await hass.async_add_executor_job(os.path.isfile, target):
        return _tool_error("File not found."), "not_found", path
    try:
        content = await hass.async_add_executor_job(_read_text_capped, target)
    except ValueError:
        return _tool_error("File exceeds the maximum readable size."), "invalid_request", path
    except OSError:
        _LOGGER.exception("read_file failed for %s", path)
        return _tool_error("Failed to read file."), "invalid_request", path
    return _tool_success(json.dumps({
        "path": path, "content": content, "content_hash": content_hash(content),
    }, default=str)), "allowed", path


def _file_write_precheck(args: dict, hass: HomeAssistant, tool_name: str) -> tuple[dict, str, str] | None:
    """Pre-gate validation for filesystem writes; None means OK to proceed.

    Checks the path is inside an allowed directory and content is a size-bounded
    string, so a doomed request never becomes a pending approval. The executor
    re-validates at apply time.
    """
    if _resolve_fs_path(hass, args.get("path", "")) is None:
        return _tool_error("Path is outside the allowed directories (www/, themes/, custom_templates/)."), "denied", tool_name
    content = args.get("content")
    if not isinstance(content, str):
        return _tool_error("content must be a string."), "invalid_request", tool_name
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return _tool_error("Content exceeds the maximum file size."), "invalid_request", tool_name
    return None


async def _tool_write_file(
    args: dict, token: TokenRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: write a file under an allowed config directory (Confirm-gated)."""
    if effective_cap(token, "cap_filesystem") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "write_file"
    error = unknown_argument_error("write_file", args)
    if error:
        return _tool_error(error), "invalid_request", "write_file"
    pre = _file_write_precheck(args, hass, "write_file")
    if pre is not None:
        return pre
    conflict = await _text_file_cas_conflict(
        args.get("expected_hash"), _resolve_fs_path(hass, args.get("path", "")), hass, "write_file",
    )
    if conflict is not None:
        return conflict
    target = _resolve_fs_path(hass, args["path"])
    assert target is not None  # The side-effect-free path precheck succeeded.
    pre = await _file_recovery_precheck(hass, target, args["path"], args["content"], "write_file")
    if pre is not None:
        return pre
    blocked = await _gate(
        "cap_filesystem", token, hass, data,
        tool_name="write_file", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_write_file(args, token, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_write_file(args, token, hass, data)


async def _execute_write_file(
    args: dict, token: TokenRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    path = args.get("path", "")
    content = args.get("content")
    target = _resolve_fs_path(hass, path)
    if target is None:
        return _tool_error("Path is outside the allowed directories (www/, themes/, custom_templates/)."), "denied", "write_file"
    if not isinstance(content, str):
        return _tool_error("content must be a string."), "invalid_request", "write_file"
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return _tool_error("Content exceeds the maximum file size."), "invalid_request", "write_file"
    existed = await hass.async_add_executor_job(os.path.isfile, target)
    before_content: str | None = None
    if existed:
        try:
            before_content = await hass.async_add_executor_job(_read_text_capped, target)
        except (OSError, ValueError):
            before_content = None  # unreadable/too large: capture as no prior content
    # Optimistic-concurrency guard at apply time: catches a change made during the
    # read/approve window (an absent/unreadable file hashes as "").
    conflict = _cas_conflict(args.get("expected_hash"), before_content or "", "write_file")
    if conflict is not None:
        return conflict
    # realpath touches the filesystem, so it goes to the executor like the
    # isfile probe above; relpath is pure string work and stays here.
    config_root = await hass.async_add_executor_job(
        os.path.realpath, hass.config.config_dir,
    )
    rel = os.path.relpath(target, config_root)
    async with _get_yaml_file_lock(hass, rel):
        recovery_receipt: dict[str, str] = {}
        recovery_result = await _recoverable_file_write(
            path=target, rel=rel, before_exists=existed,
            before_content=before_content, content=content,
            token=token, data=data, operation="write_file",
            display_path=path, hass=hass, recovery_id_out=recovery_receipt,
        )
    if recovery_result is not None:
        return recovery_result
    await _record_version(
        data, token, resource_type="file", resource_id=rel,
        action="edit" if existed else "create",
        before=_version_content_payload(before_content, path=rel),
        after=_version_content_payload(content, path=rel),
        alias=rel,
    )
    return _tool_success(json.dumps({
        "path": path, "bytes_written": len(content.encode("utf-8")),
        "recovery_id": recovery_receipt.get("recovery_id"),
    })), "allowed", f"file:{path}"


# ---------------------------------------------------------------------------
# Raw configuration.yaml edit (cap_yaml_edit)
# ---------------------------------------------------------------------------


_YAML_READ_REFUSED = (
    "Only .yaml and .yml files inside the Home Assistant configuration directory can "
    "be read; secrets.yaml, the esphome directory, and hidden directories are excluded. "
    "Use get_esphome_yaml for ESPHome device configuration."
)


def _yaml_read_path_ok(rel: str) -> bool:
    """Structural rules for a readable YAML path, applied to a config-relative path.

    Run against BOTH the caller's argument and the realpath-resolved result, so a
    symlink cannot rename its way past the extension or secrets.yaml rule.
    """
    parts = [p for p in rel.replace(os.sep, "/").split("/") if p not in ("", ".")]
    if not parts:
        return False
    if any(p.startswith(".") for p in parts):
        return False  # .storage, .cloud, and any other hidden directory
    if parts[0].lower() == "esphome":
        return False  # dedicated tool applies entity scope and credential masking
    name = parts[-1].lower()
    return name != yaml_includes.SECRETS_YAML and name.endswith((".yaml", ".yml"))


def _resolve_yaml_read_path(hass: HomeAssistant, file_arg: Any) -> tuple[str, str] | None:
    """Resolve a get_yaml_config file argument to (realpath, config-relative path).

    Returns None when the path is refused. Containment is checked on the realpath so
    '..' and symlinks cannot escape the configuration directory.
    """
    # A blank value means "not set" (models routinely send "" for an omitted
    # optional string), so it reads configuration.yaml rather than erroring.
    if file_arg is None or (isinstance(file_arg, str) and not file_arg.strip()):
        rel_arg = _CONFIG_YAML
    elif _usable_path_arg(file_arg) is not None:
        rel_arg = file_arg.strip()
    else:
        return None
    if os.path.isabs(rel_arg) or not _yaml_read_path_ok(rel_arg):
        return None
    config_dir = os.path.realpath(hass.config.config_dir)
    candidate = os.path.realpath(os.path.join(config_dir, rel_arg))
    if candidate != config_dir and not candidate.startswith(config_dir + os.sep):
        return None
    rel = os.path.relpath(candidate, config_dir)
    if not _yaml_read_path_ok(rel):
        return None
    return candidate, rel


def _fragment_text(fragment: Any) -> str:
    """Dump one YAML fragment for display, without the document-end marker.

    PyYAML appends "...\\n" after a bare scalar document; it is valid YAML but
    noise in a one-key answer.
    """
    text = yaml_includes.dump_tagged(fragment)
    if text.endswith("\n...\n"):
        text = text[: -len("...\n")]
    return text


def _yaml_fragment(text: str, key: str) -> tuple[bool, Any]:
    """Walk a dotted key path through mappings. Returns (found, value).

    Raises yaml_includes.YamlParseError when the document does not parse.
    """
    node = yaml_includes.load_tagged_lenient(text)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


async def _tool_get_yaml_config(
    args: dict, token: TokenRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read a YAML config file, or one key inside it."""
    if effective_cap(token, "cap_yaml_edit") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_yaml_config"
    error = unknown_argument_error("get_yaml_config", args)
    if error:
        return _tool_error(error), "invalid_request", "get_yaml_config"
    resolved = _resolve_yaml_read_path(hass, args.get("file"))
    if resolved is None:
        return _tool_error(_YAML_READ_REFUSED), "invalid_request", "get_yaml_config"
    path, rel = resolved
    key = args.get("key")
    if key is not None and (not isinstance(key, str) or not key.strip()):
        return _tool_error("key must be a non-empty string."), "invalid_request", "get_yaml_config"
    if not await hass.async_add_executor_job(os.path.isfile, path):
        # An absent file reads as empty content rather than an error, so a
        # get-then-set round trip can create configuration.yaml and the CAS hash
        # of "" still matches. Shape is identical to the present-file case.
        body: dict[str, Any] = {
            "path": rel, "exists": False, "content": "", "content_hash": content_hash(""),
        }
        if key is not None:
            body["key_found"] = False
        return _tool_success(json.dumps(body)), "allowed", "get_yaml_config"
    try:
        content = await hass.async_add_executor_job(_read_text_capped, path)
    except ValueError:
        return _tool_error(f"{rel} exceeds the maximum readable size."), "invalid_request", "get_yaml_config"
    except OSError:
        _LOGGER.exception("get_yaml_config failed to read %s", rel)
        return _tool_error(f"Failed to read {rel}."), "invalid_request", "get_yaml_config"
    # content_hash is over the WHOLE file even when a fragment is returned: it is
    # the optimistic-lock token for a full-file write, not a checksum of the reply.
    body = {"path": rel, "exists": True, "content": content, "content_hash": content_hash(content)}
    if key is not None:
        try:
            found, fragment = await hass.async_add_executor_job(
                _yaml_fragment, content, key.strip())
        except yaml_includes.YamlParseError as err:
            return _tool_error(f"{rel} is not valid YAML: {err}"), "invalid_request", "get_yaml_config"
        body["key_found"] = found
        body["content"] = _fragment_text(fragment) if found else ""
        if (
            isinstance(fragment, yaml_includes.TaggedValue)
            and fragment.tag in yaml_includes.INCLUDE_TAGS
        ):
            # The key's real body lives in another file; name it so the agent can
            # read that file rather than concluding the key is empty.
            body["defined_via_include"] = fragment.value
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_yaml_config"


def _yaml_write_precheck(args: dict, tool_name: str) -> tuple[dict, str, str] | None:
    """Pre-gate validation for YAML writes; None means OK to proceed.

    Checks content is a size-bounded string, so a doomed request never becomes a
    pending approval. The executor re-validates at apply time.
    """
    content = args.get("content")
    if not isinstance(content, str):
        return _tool_error("content must be a string."), "invalid_request", tool_name
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return _tool_error("Content exceeds the maximum file size."), "invalid_request", tool_name
    return None


_MISSING = object()


def _protected_subtree_diff(old_text: str | None, new_text: str, *, allow_aliases: bool = True) -> list[str]:
    """Dotted paths under YAML_PROTECTED_SUBTREES whose value the write changes.

    Both sides parse with the lenient loader so an unknown third-party tag cannot
    make the file unwritable. A value identical on both sides passes, however
    sensitive the key: this refuses CHANGES, not the presence of a key.

    Raises yaml_includes.YamlParseError when the NEW content does not parse. Old
    content that does not parse is treated as empty (fail closed): every protected
    key in the new content then counts as changed.
    """
    new_doc = yaml_includes.load_tagged_lenient(new_text, allow_aliases=allow_aliases)
    try:
        old_doc = yaml_includes.load_tagged_lenient(old_text) if old_text else None
    except yaml_includes.YamlParseError:
        old_doc = None
    if not isinstance(new_doc, dict):
        new_doc = {}
    if not isinstance(old_doc, dict):
        old_doc = {}

    changed: list[str] = []
    for top, children in YAML_PROTECTED_SUBTREES.items():
        new_top = new_doc.get(top)
        old_top = old_doc.get(top)
        for child in children:
            new_val = new_top.get(child, _MISSING) if isinstance(new_top, dict) else _MISSING
            old_val = old_top.get(child, _MISSING) if isinstance(old_top, dict) else _MISSING
            if new_val is _MISSING and old_val is _MISSING:
                continue
            if new_val is _MISSING or old_val is _MISSING or new_val != old_val:
                changed.append(f"{top}.{child}")
    return changed


def _yaml_protected_check(
    old_text: str | None, new_text: str, tool_name: str
) -> tuple[dict, str, str] | None:
    """Refuse a write that changes a protected subtree; None means OK to proceed.

    Runs pre-gate AND at apply time, so a change that appears during the approval
    window is caught too. Unparseable new content is refused rather than written:
    the check cannot be evaluated against it, and a configuration.yaml that does
    not parse stops Home Assistant from starting anyway.
    """
    try:
        changed = _protected_subtree_diff(old_text, new_text, allow_aliases=tool_name != "set_yaml_config")
    except yaml_includes.YamlParseError as err:
        return (
            _tool_error(f"content is not valid YAML: {err}"),
            "invalid_request", tool_name,
        )
    if changed:
        return (
            _tool_error(
                "Refused: this write changes protected configuration keys: "
                f"{', '.join(changed)}. These keys define Home Assistant's own "
                "authentication, proxy trust, and dashboard code-loading, so they "
                "cannot be added, removed, or modified through this tool. Leave them "
                "exactly as they are; an administrator edits them by hand."
            ),
            "invalid_request", tool_name,
        )
    return None


def _removed_top_level_keys(
    old_text: str | None, new_text: str, declared: Sequence[str]
) -> list[str]:
    """Top-level keys the write drops without having declared them in remove_keys.

    A whole-file replace can silently delete configuration the caller never
    mentioned, and nothing else catches it: the content is valid YAML, no protected
    subtree moved, and expected_hash matches because the caller DID read first. The
    worst shape is a tidy file, not a mangled one: dropping
    `automation: !include automations.yaml` disables every automation while
    check_config passes.

    Naming the keys, rather than a bare force flag, puts the intent in the approval
    diff where a wrong claim is visible. Both sides parse with the lenient loader.
    Old content that is absent or does not parse yields NO removals (the check
    abstains, not fail closed) so a configuration.yaml that does not parse can still
    be repaired; the protected-subtree refusal still runs there. New content that
    does not parse also yields none, because _yaml_protected_check refuses it first
    at the gating call sites.
    """
    try:
        old_doc = yaml_includes.load_tagged_lenient(old_text) if old_text else None
    except yaml_includes.YamlParseError:
        return []
    if not isinstance(old_doc, dict):
        return []
    try:
        new_doc = yaml_includes.load_tagged_lenient(new_text)
    except yaml_includes.YamlParseError:
        return []
    if not isinstance(new_doc, dict):
        new_doc = {}

    allowed = set(declared)
    return [key for key in old_doc if key not in new_doc and key not in allowed]


def _removed_list_entries(old_text: str | None, new_text: str) -> int:
    """How many top-level entries a write to a LIST-shaped file drops.

    The mapping-key guard cannot see this: templates.yaml and sensors.yaml are
    top-level lists, so there are no keys to compare and it abstains.

    A count rather than a set difference, because list entries have no stable
    identity: an EDIT reads as one removal plus one addition either way. The gap
    that leaves (drop one entry, add another, in the same write) is an accepted
    limit; patch_yaml_config's index addressing is the answer for a single-entry
    edit, since it resends nothing and cannot lose anything.

    Abstains on absent or unparseable prior content, like the mapping form.
    """
    try:
        old_doc = yaml_includes.load_tagged_lenient(old_text) if old_text else None
        new_doc = yaml_includes.load_tagged_lenient(new_text)
    except yaml_includes.YamlParseError:
        return 0
    if not isinstance(old_doc, list) or not isinstance(new_doc, list):
        return 0
    return max(0, len(old_doc) - len(new_doc))


def _yaml_removal_check(
    old_text: str | None, new_text: str, args: dict, tool_name: str
) -> tuple[dict, str, str] | None:
    """Refuse an undeclared key or list-entry removal; None means OK to proceed.

    Runs pre-gate AND at apply time like the protected-subtree check: the file can
    change while an approval waits, so a key present at gate time may be one this
    write drops. A wrong-shaped remove_keys or remove_entries degrades to
    no declaration, so the write is refused rather than waved through.
    """
    dropped = _removed_list_entries(old_text, new_text)
    if dropped and dropped != _declared_entry_removals(args):
        return (
            _tool_error(
                f"Refused: this write removes {dropped} top-level entries that were not "
                f"declared. If you meant to remove them, pass remove_entries: {dropped}. "
                "If you did not, this write was about to drop configuration you never "
                "intended to touch: re-read the file with get_yaml_config and preserve "
                "every entry you are not changing, or change the one entry in place with "
                "patch_yaml_config, which resends nothing else."
            ),
            "invalid_request", tool_name,
        )
    declared = str_list_arg(args.get("remove_keys"))
    removed = _removed_top_level_keys(old_text, new_text, declared)
    if removed:
        return (
            _tool_error(
                "Refused: this write removes top-level configuration keys that were "
                f"not declared: {', '.join(sorted(removed))}. If you meant to remove "
                "them, pass remove_keys naming exactly those keys. If you did not, "
                "this write was about to drop configuration you never intended to "
                "touch: re-read the file with get_yaml_config and preserve every key "
                "you are not changing."
            ),
            "invalid_request", tool_name,
        )
    return None


_YAML_WRITE_REFUSED = (
    "Only .yaml and .yml files inside the Home Assistant configuration directory can "
    "be written; secrets.yaml and hidden directories are excluded."
)


def _yaml_mount_refusal(
    config_dir: str, path: str, rel: str, tool_name: str
) -> tuple[dict, str, str] | None:
    """Refuse a write whose place in the configuration tree is unsafe or unknown.

    The protected subtrees (const.YAML_PROTECTED_SUBTREES) are defined on
    configuration.yaml's own top-level keys, and an include target inherits none of
    that shape: a file loaded at `http:` puts trusted_proxies at ITS top level, and
    a packages file carries whole integration configs including `frontend:` and
    `lovelace:`. So the check here is where the content LANDS, and anything not
    provably harmless is refused.

    Four refusals, each pointing the reader elsewhere: the file is loaded under a
    protected key; a protected key's branch could not be read, so that cannot be
    ruled out; configuration.yaml itself is unreadable, so nothing is known; or
    nothing loads the file, which is a likely typo and an inert write anyway.

    configuration.yaml is never routed here: it is the root of the scan, and the
    protected-subtree check governs it directly.

    Safe to run in an executor job: filesystem reads only, no hass.
    """
    scan = yaml_includes.scan_mount_points(config_dir, path)
    protected = frozenset(YAML_PROTECTED_SUBTREES)
    if not scan.readable:
        return (
            _tool_error(
                f"Refused: {_CONFIG_YAML} is missing or does not parse, so Phoenix MCP "
                f"cannot tell where {rel} is loaded into the configuration. Writing a "
                "file whose place in the configuration is unknown is refused. Fix "
                f"{_CONFIG_YAML} first."
            ),
            "invalid_request", tool_name,
        )
    loaded_under = sorted(scan.keys & protected)
    if loaded_under:
        return (
            _tool_error(
                f"Refused: {_CONFIG_YAML} loads {rel} under "
                f"{', '.join(loaded_under)}, which holds keys that define Home "
                "Assistant's own authentication, proxy trust, and dashboard "
                "code-loading. Those cannot be written through this tool wherever "
                "they live; an administrator edits them by hand."
            ),
            "invalid_request", tool_name,
        )
    unproven = sorted(scan.opaque & protected)
    if unproven:
        return (
            _tool_error(
                f"Refused: the {', '.join(unproven)} configuration includes a file "
                f"that could not be read, so Phoenix MCP cannot rule out that {rel} "
                "is loaded there. Protected configuration keys cannot be written "
                "through this tool, so a write it cannot place is refused."
            ),
            "invalid_request", tool_name,
        )
    if not scan.keys:
        return (
            _tool_error(
                f"Refused: {_CONFIG_YAML} does not load {rel}, so writing it would "
                "change nothing. Check the path, or read configuration.yaml with "
                "get_yaml_config to see which files it includes."
            ),
            "invalid_request", tool_name,
        )
    return None


async def _read_yaml_text(hass: HomeAssistant, path: str) -> str | None:
    """Current text of a YAML file, or None when absent or unreadable."""
    if not await hass.async_add_executor_job(os.path.isfile, path):
        return None
    try:
        return await hass.async_add_executor_job(_read_text_capped, path)
    except (OSError, ValueError):
        return None


def _yaml_write_target(hass: HomeAssistant, args: dict) -> tuple[str, str] | None:
    """The (realpath, config-relative path) a YAML write targets, or None if refused.

    The jail is the read side's, so both tools agree on what a path is. The mount
    gate is not included: it does filesystem work, so the caller runs it in an
    executor job.
    """
    return _resolve_yaml_read_path(hass, args.get("file"))


async def _tool_set_yaml_config(
    args: dict, token: TokenRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: replace a YAML configuration file whole (Confirm-gated)."""
    if effective_cap(token, "cap_yaml_edit") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_yaml_config"
    error = unknown_argument_error("set_yaml_config", args)
    if error:
        return _tool_error(error), "invalid_request", "set_yaml_config"
    pre = _yaml_write_precheck(args, "set_yaml_config")
    if pre is not None:
        return pre
    target = _yaml_write_target(hass, args)
    if target is None:
        return _tool_error(_YAML_WRITE_REFUSED), "invalid_request", "set_yaml_config"
    path, rel = target
    if rel != _CONFIG_YAML:
        mount = await hass.async_add_executor_job(
            _yaml_mount_refusal, hass.config.config_dir, path, rel, "set_yaml_config",
        )
        if mount is not None:
            return mount
    current = await _read_yaml_text(hass, path)
    protected = await hass.async_add_executor_job(
        _yaml_protected_check, current, args["content"], "set_yaml_config",
    )
    if protected is not None:
        return protected
    removal = await hass.async_add_executor_job(
        _yaml_removal_check, current, args["content"], args, "set_yaml_config",
    )
    if removal is not None:
        return removal
    conflict = await _text_file_cas_conflict(
        args.get("expected_hash"), path, hass, "set_yaml_config",
    )
    if conflict is not None:
        return conflict
    pre = await _file_recovery_precheck(hass, path, rel, args["content"], "set_yaml_config", "yaml_file")
    if pre is not None:
        return pre
    blocked = await _gate(
        "cap_yaml_edit", token, hass, data,
        tool_name="set_yaml_config", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_set_yaml_config(args, token, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_set_yaml_config(args, token, hass, data)


async def _execute_set_yaml_config(
    args: dict, token: TokenRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    content = args.get("content")
    if not isinstance(content, str):
        return _tool_error("content must be a string."), "invalid_request", "set_yaml_config"
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return _tool_error("Content exceeds the maximum file size."), "invalid_request", "set_yaml_config"
    target = _yaml_write_target(hass, args)
    if target is None:
        return _tool_error(_YAML_WRITE_REFUSED), "invalid_request", "set_yaml_config"
    path, rel = target
    # Read, protected-subtree re-check, CAS and write are one critical section:
    # the write is a whole-file replace, so an interleaved second writer would
    # both lose an edit and invalidate the prior content the checks ran on.
    async with _get_yaml_file_lock(hass, rel):
        # The mount is re-checked inside the lock, like the two content guards
        # below: configuration.yaml can be re-routed while an approval waits, so an
        # ordinary include target at gate time may be loaded under a protected key
        # by the time it applies. A RESTORE is not exempt: reproducing a snapshot
        # is only safe while the file still lands where it did when it was taken.
        if rel != _CONFIG_YAML:
            mount = await hass.async_add_executor_job(
                _yaml_mount_refusal, hass.config.config_dir, path, rel, "set_yaml_config",
            )
            if mount is not None:
                return mount
        existed = await hass.async_add_executor_job(os.path.isfile, path)
        before_content: str | None = None
        if existed:
            try:
                before_content = await hass.async_add_executor_job(_read_text_capped, path)
            except (OSError, ValueError):
                before_content = None  # unreadable/too large: capture as no prior content
        # Re-check the protected subtrees against what is on disk at apply time:
        # the file can change between the gate and an admin approving the write.
        protected = await hass.async_add_executor_job(
            _yaml_protected_check, before_content, content, "set_yaml_config",
        )
        if protected is not None:
            return protected
        # Same approval-window reasoning as the protected re-check above.
        #
        # A version RESTORE is exempt: the admin picked that snapshot from a panel
        # showing its content, reproducing it byte-for-byte is the entire operation,
        # and it legitimately drops keys added after it was taken. This guard exists
        # to catch an AGENT dropping a key it never mentioned. The ContextVar is
        # read HERE, on the event loop, because it does not cross into an executor
        # job.
        if _restore_ctx.get() is None:
            removal = await hass.async_add_executor_job(
                _yaml_removal_check, before_content, content, args, "set_yaml_config",
            )
            if removal is not None:
                return removal
        # Optimistic-concurrency guard at apply time: catches a change made during the
        # read/approve window (an absent/unreadable file hashes as "").
        conflict = _cas_conflict(args.get("expected_hash"), before_content or "", "set_yaml_config")
        if conflict is not None:
            return conflict
        recovery_receipt: dict[str, str] = {}
        recovery_result = await _recoverable_file_write(
            path=path, rel=rel, before_exists=existed,
            before_content=before_content, content=content,
            token=token, data=data, operation="set_yaml_config",
            display_path=rel, hass=hass, snapshot_type="yaml_file",
            recovery_id_out=recovery_receipt,
        )
        if recovery_result is not None:
            return recovery_result
        # Inside the lock so the history cannot record two writes out of order.
        await _record_version(
            data, token, resource_type="yaml_config", resource_id=rel,
            action="edit" if existed else "create",
            before=_version_content_payload(before_content),
            after=_version_content_payload(content),
            alias=rel,
        )
    return (
        _tool_success(json.dumps({
            "path": rel,
            "bytes_written": len(content.encode("utf-8")),
            "recovery_id": recovery_receipt.get("recovery_id"),
            "note": "Run check_ha_config and restart Home Assistant to apply.",
        })),
        "allowed", "set_yaml_config",
    )


# ---------------------------------------------------------------------------
# Key-addressed configuration.yaml edit (cap_yaml_edit)
# ---------------------------------------------------------------------------


def _declared_entry_removals(args: dict) -> int:
    """The entry count this write declares it removes. Never raises.

    A wrong-shaped value reads as 0, i.e. it refuses rather than waves the
    write through, matching str_list_arg's degrade-to-absent on remove_keys.
    """
    declared = args.get("remove_entries")
    if isinstance(declared, bool) or not isinstance(declared, int) or declared < 0:
        return 0
    return declared


def _yaml_patch_op(args: dict) -> str:
    """The requested op, defaulting to set. Never raises on a wrong-shaped value."""
    op = str_arg(args.get("op")).strip().lower()
    return op if op in yaml_patch.PATCH_OPS else "set"


def _patch_declared_removals(args: dict, op: str) -> list[str]:
    """The top-level keys this patch declares it removes, for the removal guard.

    A `remove` of a top-level key IS its own declaration: the key argument names
    exactly what goes and lands in the approval diff, which is what remove_keys
    provides for a whole-file write. Nothing else a patch does drops a top-level
    key, so the guard still runs on every patch and any removal it reports is a
    splice bug.

    A LIST INDEX declares nothing and needs to: the guard covers top-level mapping
    keys, so removing a list entry never reaches it. What protects that case is
    yaml_patch._verify, which proves every entry the patch did not address
    survived the splice.
    """
    if op != "remove":
        return []
    try:
        path = yaml_patch.normalize_path(args.get("key"), args.get("path"))
    except yaml_patch.PatchError:
        return []
    if len(path) != 1 or not isinstance(path[0], str):
        return []
    return [path[0]]


def _prepare_yaml_patch(
    current: str | None, args: dict, tool_name: str
) -> yaml_patch.PatchResult | tuple[dict, str, str]:
    """Build the patched file text, or an error tuple refusing it.

    Validates pre-gate, after the capability-deny check, so an invalid request never
    becomes a pending approval. Runs again at apply time against what is on disk:
    the addressed key can appear, move or change shape while an approval waits,
    and a patch aimed at a line span is more sensitive to that than a whole-file
    replace.
    """
    op = _yaml_patch_op(args)
    try:
        value = yaml_patch.parse_value(args.get("content")) if op in {"set", "append"} else None
    except yaml_patch.PatchError as err:
        return _tool_error(str(err)), "invalid_request", tool_name
    # A redacted placeholder is never written back as configuration. A
    # configuration.yaml read is not itself lossy, so the sentinel can only arrive
    # in a value the caller carried across from a read that IS (a dashboard layout,
    # a service response).
    offender = redaction_sentinel_path(value)
    if offender is not None:
        where = ".".join(str(segment) for segment in offender) or "the top level"
        return (
            _tool_error(
                f"content contains the redaction placeholder {REDACTION_SENTINEL!r} at {where}. "
                "That placeholder is what a read substitutes for an entity this access key cannot "
                "resolve (out of scope, or one that no longer exists), so writing it back would "
                "replace real configuration with the placeholder. Send the intended value instead."
            ),
            "invalid_request", tool_name,
        )
    try:
        result = yaml_patch.apply_patch(
            current or "", args.get("key"), op, args.get("content"), value, args.get("path"))
    except yaml_patch.PatchError as err:
        return _tool_error(str(err)), "invalid_request", tool_name
    protected = _yaml_protected_check(current, result.text, tool_name)
    if protected is not None:
        return protected
    removal = _yaml_removal_check(
        current, result.text, {"remove_keys": _patch_declared_removals(args, op)}, tool_name,
    )
    if removal is not None:
        return removal
    return result


async def _tool_patch_yaml_config(
    args: dict, token: TokenRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: change ONE key inside a YAML configuration file (Confirm-gated).

    The counterpart to patch_dashboard. set_yaml_config replaces the whole file, so
    a small fix reaches an approver as a diff of every line; addressing a key makes
    the diff the key, and the untouched bytes (comments, ordering, spacing) are
    never resent, so they cannot be lost either.
    """
    tool = "patch_yaml_config"
    if effective_cap(token, "cap_yaml_edit") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    args, error = normalize_tool_args(tool, args)
    if error:
        return _tool_error(error), "invalid_request", tool
    target = _yaml_write_target(hass, args)
    if target is None:
        return _tool_error(_YAML_WRITE_REFUSED), "invalid_request", tool
    path, rel = target
    if rel != _CONFIG_YAML:
        mount = await hass.async_add_executor_job(
            _yaml_mount_refusal, hass.config.config_dir, path, rel, tool,
        )
        if mount is not None:
            return mount
    current = await _read_yaml_text(hass, path)
    prepared = await hass.async_add_executor_job(_prepare_yaml_patch, current, args, tool)
    if isinstance(prepared, tuple):
        return prepared
    conflict = await _text_file_cas_conflict(
        args.get("expected_hash"), path, hass, tool,
    )
    if conflict is not None:
        return conflict
    pre = await _file_recovery_precheck(hass, path, rel, prepared.text, tool, "yaml_file")
    if pre is not None:
        return pre
    blocked = await _gate(
        "cap_yaml_edit", token, hass, data,
        tool_name=tool, args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_patch_yaml_config(args, token, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_patch_yaml_config(args, token, hass, data)


async def _execute_patch_yaml_config(
    args: dict, token: TokenRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    tool = "patch_yaml_config"
    target = _yaml_write_target(hass, args)
    if target is None:
        return _tool_error(_YAML_WRITE_REFUSED), "invalid_request", tool
    path, rel = target
    op = _yaml_patch_op(args)
    try:
        addressed = yaml_patch.describe_path(
            yaml_patch.normalize_path(args.get("key"), args.get("path")))
    except yaml_patch.PatchError as err:
        return _tool_error(str(err)), "invalid_request", tool
    # Read, patch, CAS and write are one critical section, sharing the whole-file
    # writer's lock: the patch is computed from the text read just before, so an
    # interleaved second writer would both lose an edit and leave this splice
    # aimed at stale line numbers.
    async with _get_yaml_file_lock(hass, rel):
        # Same approval-window reasoning as set_yaml_config's re-check.
        if rel != _CONFIG_YAML:
            mount = await hass.async_add_executor_job(
                _yaml_mount_refusal, hass.config.config_dir, path, rel, tool,
            )
            if mount is not None:
                return mount
        existed = await hass.async_add_executor_job(os.path.isfile, path)
        before_content: str | None = None
        if existed:
            try:
                before_content = await hass.async_add_executor_job(_read_text_capped, path)
            except (OSError, ValueError):
                before_content = None  # unreadable/too large: capture as no prior content
        prepared = await hass.async_add_executor_job(_prepare_yaml_patch, before_content, args, tool)
        if isinstance(prepared, tuple):
            return prepared
        content = prepared.text
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            return _tool_error("The patched file would exceed the maximum file size."), "invalid_request", tool
        # Optimistic-concurrency guard at apply time: catches a change made during
        # the read/approve window (an absent/unreadable file hashes as "").
        conflict = _cas_conflict(args.get("expected_hash"), before_content or "", tool)
        if conflict is not None:
            return conflict
        recovery_receipt: dict[str, str] = {}
        recovery_result = await _recoverable_file_write(
            path=path, rel=rel, before_exists=existed,
            before_content=before_content, content=content,
            token=token, data=data, operation=tool,
            display_path=rel, hass=hass, snapshot_type="yaml_file",
            recovery_id_out=recovery_receipt,
        )
        if recovery_result is not None:
            return recovery_result
        # Inside the lock so the history cannot record two writes out of order.
        # The snapshot is the WHOLE file on both sides, so a restore reproduces
        # configuration.yaml exactly as any other yaml_config version does.
        await _record_version(
            data, token, resource_type="yaml_config", resource_id=rel,
            action="edit" if existed else "create",
            before=_version_content_payload(before_content),
            after=_version_content_payload(content),
            alias=rel,
            summary=_version_summary(f"patch.{op}", subject=addressed),
        )
    return (
        _tool_success(json.dumps({
            "path": rel,
            "key": addressed,
            "op": op,
            "bytes_written": len(content.encode("utf-8")),
            "recovery_id": recovery_receipt.get("recovery_id"),
            # Structural like patch_dashboard's card ops, not a blind replace, so
            # the hash is safe to hand back and patches can be chained without
            # another read.
            "content_hash": content_hash(content),
            "note": "Run check_ha_config and restart Home Assistant to apply.",
        })),
        "allowed", tool,
    )


def _read_capped_if_file(path: str) -> str | None:
    """isfile-guarded _read_text_capped for diff builders.

    Returns None when the file is missing, too large, or unreadable. Safe to run
    in an executor job.
    """
    try:
        if os.path.isfile(path):
            return _read_text_capped(path)
    except (OSError, ValueError):
        return None
    return None


async def _build_diff_write_file(args: dict, token: TokenRecord, hass: HomeAssistant) -> dict:
    path = args.get("path", "")
    content = args.get("content") if isinstance(args.get("content"), str) else ""
    target = _resolve_fs_path(hass, path)
    before = None
    if target is not None:
        raw = await hass.async_add_executor_job(_read_capped_if_file, target)
        before = _truncate(raw) if raw is not None else None
    return {
        "kind": "file_write",
        **_summary("write_file", path=path),
        "target": {"type": "file", "id": path, "label": path},
        "before": _redact_secrets_in_text(before),
        "after": _redact_secrets_in_text(_truncate(str(content))),
        "preview": {"path": path, "outside_allowed_dirs": target is None,
                    "bytes": len(str(content).encode("utf-8"))},
    }


async def _build_diff_set_yaml_config(args: dict, token: TokenRecord, hass: HomeAssistant) -> dict:
    raw_content = args.get("content")
    content: str = raw_content if isinstance(raw_content, str) else ""
    # Best-effort like every diff builder: an unresolvable path still produces a
    # readable diff, and the tool path has already refused it by the time an
    # approval could exist.
    target = _yaml_write_target(hass, args)
    path, rel = target if target is not None else (hass.config.path(_CONFIG_YAML), _CONFIG_YAML)
    raw = await hass.async_add_executor_job(_read_capped_if_file, path)
    before = _truncate(raw) if raw is not None else None
    # Computed from the content, never from the caller's own remove_keys: the
    # summary is the line an admin reads in History, so it must report what the
    # write actually drops rather than what it claims to drop.
    removing = await hass.async_add_executor_job(
        _removed_top_level_keys, raw, content, [],
    )
    dropped = await hass.async_add_executor_job(_removed_list_entries, raw, content)
    if removing:
        summary = _summary(
            "set_yaml_config.removing", file=rel, keys=", ".join(sorted(removing)))
    elif dropped == 1:
        summary = _summary("set_yaml_config.removing_entry", file=rel)
    elif dropped:
        summary = _summary("set_yaml_config.removing_entries", file=rel, count=str(dropped))
    else:
        summary = _summary("set_yaml_config", file=rel)
    return {
        "kind": "yaml_diff",
        **summary,
        "target": {"type": "file", "id": rel, "label": rel},
        "before": _redact_secrets_in_text(before),
        "after": _redact_secrets_in_text(_truncate(str(content))),
        "preview": {
            "path": rel,
            "warning": (
                f"Replaces the entire {_CONFIG_YAML}; a broken file blocks HA startup."
                if rel == _CONFIG_YAML
                else f"Replaces the entire {rel}, which {_CONFIG_YAML} loads."
            ),
        },
    }


async def _build_diff_patch_yaml_config(args: dict, token: TokenRecord, hass: HomeAssistant) -> dict:
    """The addressed key's before and after, which is the point of the tool.

    Both sides describe the same thing (the key's VALUE, not the pair), and the
    before side is the file's own source text rather than a re-dump, so the
    comments an operator wrote next to the setting are on the side they are
    reading when they decide.
    """
    op = _yaml_patch_op(args)
    try:
        key = yaml_patch.describe_path(
            yaml_patch.normalize_path(args.get("key"), args.get("path")))
    except yaml_patch.PatchError:
        key = ""
    target = _yaml_write_target(hass, args)
    path, rel = target if target is not None else (hass.config.path(_CONFIG_YAML), _CONFIG_YAML)
    current = await _read_yaml_text(hass, path)
    prepared = await hass.async_add_executor_job(
        _prepare_yaml_patch, current, args, "patch_yaml_config",
    )
    before = prepared.before if isinstance(prepared, yaml_patch.PatchResult) else None
    after = str_arg(args.get("content")) if op in {"set", "append"} else None
    return {
        "kind": "yaml_diff",
        # `path`, not `key`: diff_summary_fields takes the template key as its
        # own first parameter, so a template param of that name collides with it.
        **_summary(f"patch_yaml_config.{op}", path=key or "(no key)", file=rel),
        "target": {"type": "file", "id": rel, "label": rel},
        "before": _redact_secrets_in_text(_truncate(before) if before is not None else None),
        "after": _redact_secrets_in_text(_truncate(after) if after is not None else None),
        "preview": {
            "file": rel, "key": key, "op": op,
            "warning": f"Changes one key; the rest of {rel} is untouched.",
        },
    }
