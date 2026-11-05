"""Contracts that keep Phoenix-authored operator text behind localization keys."""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
AGENT_FILES = (
    ROOT / "custom_components/phoenix_mcp/agent_chat.py",
    ROOT / "custom_components/phoenix_mcp/agent_turn.py",
    ROOT / "custom_components/phoenix_mcp/agentcli_views.py",
    *sorted((ROOT / "custom_components/phoenix_mcp/llm").glob("*.py")),
)
BACKEND_FILES = (ROOT / "custom_components/phoenix_mcp/admin_view.py", *AGENT_FILES)
CATALOG_FILES = tuple(sorted((ROOT / "custom_components/phoenix_mcp/catalogs").glob("*.json")))


def _panel_catalog(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["panel"]


def _agent_module() -> ast.Module:
    """Every Agent Chat, provider and account module as one tree."""
    return ast.Module([node for path in AGENT_FILES for node in ast.parse(path.read_text(encoding="utf-8")).body], [])


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _literal_string_choices(node: ast.AST | None) -> list[str]:
    literal = _literal_string(node)
    if literal is not None:
        return [literal]
    if isinstance(node, ast.IfExp):
        choices = [*_literal_string_choices(node.body), *_literal_string_choices(node.orelse)]
        return choices if len(choices) == 2 else []
    return []


def _dict_items(node: ast.Dict) -> dict[str, ast.AST]:
    return {
        key.value: value
        for key, value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def test_every_admin_error_is_keyed_or_explicit_upstream_passthrough() -> None:
    catalogs = [_panel_catalog(path)["adminError"] for path in CATALOG_FILES]
    findings: list[str] = []

    for path in BACKEND_FILES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "_err"
            ):
                continue
            kwargs = {kw.arg: kw.value for kw in call.keywords}
            has_key = "key" in kwargs
            has_passthrough = "passthrough" in kwargs
            if has_key == has_passthrough:
                findings.append(
                    f"{path.name}:{call.lineno} must declare exactly one of key or passthrough"
                )
                continue
            if has_passthrough:
                value = kwargs["passthrough"]
                if not (isinstance(value, ast.Constant) and value.value is True):
                    findings.append(f"{path.name}:{call.lineno} passthrough must be literal True")
                message = call.args[1] if len(call.args) > 1 else kwargs.get("message")
                if isinstance(message, (ast.Constant, ast.JoinedStr)):
                    findings.append(
                        f"{path.name}:{call.lineno} Phoenix-authored literals cannot be passthrough"
                    )
                continue

            key = _literal_string(kwargs["key"])
            # The only dynamic keys are a ProbeConfigError or LocalizedReason
            # instance's, or recovery_error_key's. Their constructors, and that
            # function's table, are checked separately below.
            if key is None:
                if not _is_checked_dynamic_key(kwargs["key"]):
                    findings.append(f"{path.name}:{call.lineno} has an uncheckable dynamic key")
                continue
            for catalog_path, catalog in zip(CATALOG_FILES, catalogs):
                if key not in catalog:
                    findings.append(f"{path.name}:{call.lineno} adminError.{key} missing from {catalog_path.name}")

    assert findings == []


def _is_checked_dynamic_key(node: ast.AST | None) -> bool:
    """`probe.key`, `reason.key` or `recovery_error_key(...)`: keys whose sources this file checks."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id == "recovery_error_key"
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"probe", "reason"}
        and node.attr == "key"
    )


def test_recovery_error_keys_have_catalog_keys() -> None:
    """Every key recovery_error_key can return, its fallback included, is in every catalog."""
    from custom_components.phoenix_mcp.config_recovery import _OPERATOR_KEYS, recovery_error_key

    catalogs = [_panel_catalog(path)["adminError"] for path in CATALOG_FILES]
    keys = {*_OPERATOR_KEYS.values(), recovery_error_key("")}
    for key in keys:
        for catalog_path, catalog in zip(CATALOG_FILES, catalogs):
            assert key in catalog, f"recovery adminError.{key} missing from {catalog_path.name}"


def test_localized_reasons_have_catalog_keys() -> None:
    """Every LocalizedReason names an adminError key present in every catalog."""
    catalogs = [_panel_catalog(path)["adminError"] for path in CATALOG_FILES]
    found = 0
    package = ROOT / "custom_components/phoenix_mcp"
    for path in sorted([*package.glob("*.py"), *package.glob("routing/*.py")]):
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "LocalizedReason"):
                continue
            key_node = call.args[1] if len(call.args) > 1 else None
            if path.name == "decision_provider.py" and isinstance(key_node, ast.Name):
                continue  # decision_error_reason's table, checked in full below
            key = _literal_string(key_node)
            assert key is not None, f"{path.name}:{call.lineno} LocalizedReason key must be literal"
            found += 1
            for catalog_path, catalog in zip(CATALOG_FILES, catalogs):
                assert key in catalog, f"{path.name}:{call.lineno} adminError.{key} missing from {catalog_path.name}"
    # Not vacuous: provider checks and decision provider reasons use it.
    assert found >= 10
    from custom_components.phoenix_mcp.routing.decision_provider import _DECISION_ERROR_REASONS
    for _text, key in _DECISION_ERROR_REASONS.values():
        for catalog_path, catalog in zip(CATALOG_FILES, catalogs):
            assert key in catalog, f"decision reason adminError.{key} missing from {catalog_path.name}"


def test_probe_config_errors_have_catalog_keys() -> None:
    tree = _agent_module()
    catalogs = [_panel_catalog(catalog_path)["adminError"] for catalog_path in CATALOG_FILES]
    keys: list[tuple[int, str]] = []
    for call in ast.walk(tree):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "ProbeConfigError"
        ):
            continue
        key_node = call.args[1] if len(call.args) > 1 else None
        if _is_checked_dynamic_key(key_node):
            continue
        key = _literal_string(key_node)
        assert key is not None, f"line {call.lineno}: ProbeConfigError key must be literal"
        keys.append((call.lineno, key))

    assert keys, "ProbeConfigError constructors were not found"
    for line, key in keys:
        for catalog_path, catalog in zip(CATALOG_FILES, catalogs):
            assert key in catalog, f"line {line}: adminError.{key} missing from {catalog_path.name}"


def test_successful_error_payloads_declare_key_or_passthrough() -> None:
    """HTTP 200 probe failures bypass _err, so guard their payloads too."""
    tree = _agent_module()
    findings: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        items = _dict_items(node)
        ok = items.get("ok")
        if not (isinstance(ok, ast.Constant) and ok.value is False):
            continue
        if not ({"message_key", "message_passthrough"} & items.keys()):
            findings.append(node.lineno)
    assert findings == []


def test_agent_chat_authored_sse_codes_and_progress_have_catalog_keys() -> None:
    catalogs = [_panel_catalog(path) for path in CATALOG_FILES]
    findings: list[str] = []

    agent_tree = _agent_module()
    for call in ast.walk(agent_tree):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "emit"
            and len(call.args) >= 2
            and _literal_string(call.args[0]) in {"notice", "error"}
            and isinstance(call.args[1], ast.Dict)
        ):
            continue
        code = _literal_string(_dict_items(call.args[1]).get("code"))
        if code is None:
            continue
        for catalog_path, catalog in zip(CATALOG_FILES, catalogs):
            if code not in catalog["agentchat"]["notice"]:
                findings.append(f"line {call.lineno}: agentchat.notice.{code} missing from {catalog_path.name}")

    for relative in (
        "custom_components/phoenix_mcp/tool_common.py",
        "custom_components/phoenix_mcp/mcp_transport.py",
        "custom_components/phoenix_mcp/tool_dispatch.py",
        "custom_components/phoenix_mcp/tools/approvals.py",
        "custom_components/phoenix_mcp/tools/backup.py",
        "custom_components/phoenix_mcp/tools/history.py",
        "custom_components/phoenix_mcp/tools/integrations.py",
        "custom_components/phoenix_mcp/tools/logs.py",
        "custom_components/phoenix_mcp/tools/registry.py",
        "custom_components/phoenix_mcp/tools/restore.py",
        "custom_components/phoenix_mcp/tools/service.py",
        "custom_components/phoenix_mcp/tools/system.py",
        "custom_components/phoenix_mcp/tools/esphome.py",
    ):
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "_set_progress_status"
                and call.args
                and not (isinstance(call.args[0], ast.Constant) and call.args[0].value is None)
            ):
                continue
            kwargs = {kw.arg: kw.value for kw in call.keywords}
            keys = _literal_string_choices(kwargs.get("key"))
            if not keys:
                findings.append(f"{relative}:{call.lineno} progress text has no literal key")
                continue
            for key in keys:
                parts = key.split(".")
                for catalog_path, catalog in zip(CATALOG_FILES, catalogs):
                    value: object = catalog
                    for part in parts:
                        value = value.get(part) if isinstance(value, dict) else None
                    if not isinstance(value, str):
                        findings.append(f"{relative}:{call.lineno} {key} missing from {catalog_path.name}")

    assert findings == []


def test_tool_image_sse_contains_data_not_backend_authored_alt_text() -> None:
    tree = _agent_module()
    payloads: list[ast.Dict] = []
    for call in ast.walk(tree):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "emit"
            and len(call.args) >= 2
            and _literal_string(call.args[0]) == "tool_image"
            and isinstance(call.args[1], ast.Dict)
        ):
            payloads.append(call.args[1])
    assert payloads
    for payload in payloads:
        assert "alt" not in _dict_items(payload)


def test_every_recorded_chat_event_code_has_a_notice_entry():
    """A code without an entry shows its stored English in every language."""
    import json
    import re
    from pathlib import Path

    package = Path(__file__).parent.parent / "custom_components" / "phoenix_mcp"
    english = json.loads((package / "catalogs" / "en.json").read_text(encoding="utf-8"))
    notices = english["panel"]["agentchat"]["notice"]
    for code in ("result_withheld", "provider_slow"):
        assert code in notices
    source = "".join((package / name).read_text(encoding="utf-8") for name in (
        "home_companion.py", "home_companion_turn.py", "agent_turn.py", "agent_chat.py"))
    # Each code's params are recorded with it, or the entry shows raw placeholders.
    for code, placeholder in (("result_withheld", "length"), ("provider_slow", "seconds")):
        assert "{" + placeholder + "}" in notices[code]
        block = source[source.index(f'"{code}"'):][:400]
        assert re.search(rf'"message_params": \{{"{placeholder}"', block), code
