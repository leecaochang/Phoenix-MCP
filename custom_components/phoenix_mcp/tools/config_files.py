"""Config-file read tools: the scoped filesystem jail and YAML configuration reads.

`cap_filesystem` covers only www/, themes/ and custom_templates/; `_resolve_fs_path`
realpaths and requires containment (const.FILESYSTEM_ALLOWED_DIRS), so symlinks and
traversal cannot escape and configuration.yaml is unreachable. `cap_yaml_edit` reads
configuration.yaml and the YAML files inside the configuration directory, never
secrets.yaml, hidden directories or ESPHome device files. Nothing here writes: YAML
configuration belongs to the operator, and Phoenix changes configuration only
through objects Home Assistant manages itself.
"""

from __future__ import annotations

from typing import Any
import json
import logging
import os

from homeassistant.core import HomeAssistant

from ..const import CAP_DENY, FILESYSTEM_ALLOWED_DIRS
from ..tool_contracts import unknown_argument_error
from ..helpers import content_hash, effective_cap, str_arg
from ..tool_common import _read_text_capped, _tool_error, _tool_success, _usable_path_arg
from ..access_key_store import AccessKeyRecord
from .. import yaml_includes

_LOGGER = logging.getLogger(__name__)


_CONFIG_YAML = "configuration.yaml"


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
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list files in an allowed config directory."""
    if effective_cap(access_key, "cap_filesystem") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "list_files"
    error = unknown_argument_error("list_files", args)
    if error:
        return _tool_error(error), "invalid_request", "list_files"
    path = str_arg(args.get("path"))
    if not path:
        return _tool_success(json.dumps({"directories": list(FILESYSTEM_ALLOWED_DIRS)})), "allowed", "list_files"
    target = await hass.async_add_executor_job(_resolve_fs_path, hass, path)
    if target is None or not await hass.async_add_executor_job(os.path.isdir, target):
        return _tool_error("Directory not found."), "not_found", path
    entries = await hass.async_add_executor_job(_listdir, target)
    return _tool_success(json.dumps({"path": path, "entries": entries}, default=str)), "allowed", path


async def _tool_read_file(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read a UTF-8 text file from an allowed config directory."""
    if effective_cap(access_key, "cap_filesystem") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "read_file"
    error = unknown_argument_error("read_file", args)
    if error:
        return _tool_error(error), "invalid_request", "read_file"
    path = str_arg(args.get("path"))
    target = await hass.async_add_executor_job(_resolve_fs_path, hass, path)
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


# ---------------------------------------------------------------------------
# YAML configuration reads (cap_yaml_edit)
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
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read a YAML config file, or one key inside it."""
    if effective_cap(access_key, "cap_yaml_edit") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_yaml_config"
    error = unknown_argument_error("get_yaml_config", args)
    if error:
        return _tool_error(error), "invalid_request", "get_yaml_config"
    resolved = await hass.async_add_executor_job(_resolve_yaml_read_path, hass, args.get("file"))
    if resolved is None:
        return _tool_error(_YAML_READ_REFUSED), "invalid_request", "get_yaml_config"
    path, rel = resolved
    key = args.get("key")
    if key is not None and (not isinstance(key, str) or not key.strip()):
        return _tool_error("key must be a non-empty string."), "invalid_request", "get_yaml_config"
    if not await hass.async_add_executor_job(os.path.isfile, path):
        # An absent file reads as empty content rather than an error, with the
        # same shape as the present-file case.
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
    # content_hash is over the WHOLE file even when a fragment is returned, so it
    # tells a caller whether the file changed between two reads.
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
