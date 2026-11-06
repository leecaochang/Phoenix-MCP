"""Timestamps from text Phoenix does not own, and the crash in the parser underneath them."""

from __future__ import annotations

import ast
import asyncio
import datetime as dt
from pathlib import Path
import subprocess
import sys
import textwrap
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.phoenix_mcp import agent_turn, time_text
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.rate_limiter import RateLimiter

PACKAGE = Path(__file__).parents[1] / "custom_components" / "phoenix_mcp"
# Runs in a disposable worker over scrubbed snapshots, where a crash ends only the worker.
TEMPLATE_WORKER = {"template_sandbox.py"}
UNSAFE = {"parse_datetime", "parse_date"}


def _survives(body: str) -> subprocess.CompletedProcess:
    """Run code in its own interpreter: the failure under test ends the process in C."""
    script = "from pathlib import Path\nimport sys\nsys.path.insert(0, str(Path.cwd()))\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120,
                          cwd=Path(__file__).parents[1], check=False)


def test_the_parser_itself_crashes_on_a_lone_surrogate():
    """The hazard the helper exists for; a parser that stops crashing retires it."""
    result = _survives("""
        from homeassistant.util.dt import parse_datetime
        parse_datetime("\\ud800")
    """)
    assert result.returncode < 0


@pytest.mark.parametrize("body", [
    pytest.param("""
        from custom_components.phoenix_mcp.time_text import parse_date, parse_datetime
        assert parse_datetime("\\ud800") is None
        assert parse_datetime("2026-01-01T00:00:00\\ud800") is None
        assert parse_date("\\ud800") is None
    """, id="helper"),
    pytest.param("""
        from custom_components.phoenix_mcp.llm.base import _created_map, _epoch, _is_expired
        assert _epoch("\\ud800") is None
        assert _is_expired({"shutdown_date": "2020-01-01T\\ud800"}) is False
        assert _is_expired({"deprecation": "\\ud800"}) is False
        assert _created_map([{"id": "m", "created_at": "\\ud800"}]) == {}
    """, id="provider-catalogue"),
    pytest.param("""
        from custom_components.phoenix_mcp.agent_turn import _agentcli_time_arg_error
        from homeassistant.util import dt as dt_util
        assert _agentcli_time_arg_error("get_history", {"start_time": "\\ud800"}, dt_util.UTC) is None
    """, id="agent-chat-argument"),
    pytest.param("""
        from custom_components.phoenix_mcp.admin.common import _request_time
        assert _request_time("\\ud800") is None
    """, id="admin-expiry"),
    pytest.param("""
        from custom_components.phoenix_mcp.helpers import parse_time_param
        try:
            parse_time_param("\\ud800")
        except ValueError:
            pass
        else:
            raise SystemExit("accepted")
    """, id="time-parameter"),
    pytest.param("""
        from custom_components.phoenix_mcp.tools.calendar import _calendar_event_start, _calendar_time
        try:
            _calendar_time("2026-01-01T\\ud800")
        except ValueError:
            pass
        else:
            raise SystemExit("accepted")
        _calendar_event_start({"start": "\\ud800"})
    """, id="calendar-event"),
    pytest.param("""
        import asyncio
        from custom_components.phoenix_mcp.llm.accounts import ProviderConfig
        from custom_components.phoenix_mcp.llm.provider_openai import OpenAICompatProvider

        class Content:
            async def iter_chunked(self, _size):
                yield b'{"models": [{"name": "m1", "modified_at": "\\\\ud800"}]}'

        class Reply:
            status, content = 200, Content()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_args):
                return False

        class Session:
            def get(self, *_args, **_kwargs):
                return Reply()

        provider = OpenAICompatProvider(ProviderConfig(kind="ollama", model="", base_url="http://h:11434", api_key=None))
        assert asyncio.run(provider.list_models(Session())) == ["m1"]
        assert provider.model_created == {}
    """, id="provider-reply"),
    pytest.param("""
        from custom_components.phoenix_mcp.approvals import PendingApproval
        record = PendingApproval.from_dict({"id": "a", "access_key_id": "k", "tool_name": "t", "args": {},
                                            "created_at": "\\ud800", "expires_at": "\\ud800"})
        assert record.created_at is not None
    """, id="saved-approval"),
])
def test_a_lone_surrogate_is_refused_without_reaching_the_parser(body):
    result = _survives(body)
    assert result.returncode == 0, result.stderr[-600:]


async def test_agent_chat_reads_only_arguments_the_dispatcher_would_admit(hass):
    store = AccessKeyStore(hass, AsyncMock())
    access_key, _raw = await store.async_create_access_key("chat", "admin", rate_limit_requests=0)
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    with patch.object(agent_turn, "_agentcli_time_arg_error", side_effect=AssertionError("read")) as read:
        batch = await agent_turn._run_tool_batch(
            [{"id": "call", "name": "get_history", "input": {"start_time": "\ud800"}}], hass=hass, data=data,
            access_key_id=access_key.id, client_ip="agentcli", base_url="", emit=AsyncMock(), cancel=asyncio.Event())
    read.assert_not_called()
    assert "valid Unicode" in batch.results[0]["result_text"]
    assert batch.results[0]["is_error"] is True


def test_a_text_that_encodes_parses_as_home_assistant_parses_it():
    assert time_text.parse_datetime("2026-01-02T03:04:05+00:00") == dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)
    assert time_text.parse_date("2026-01-02") == dt.date(2026, 1, 2)
    assert time_text.parse_datetime("not a time") is None
    assert time_text.parse_date("not a date") is None


def test_an_impossible_date_and_a_wrong_type_raise_what_callers_already_handle():
    with pytest.raises(ValueError):
        time_text.parse_datetime("2026-13-45T00:00:00")
    with pytest.raises(TypeError):
        time_text.parse_datetime(None)  # type: ignore[arg-type]


def _direct_parser_use(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = {alias.name for alias in node.names}
            if node.module == "ciso8601" or (node.module == "homeassistant.util.dt" and names & UNSAFE):
                found.append(f"{path.name}:{node.lineno}")
        elif isinstance(node, ast.Import) and any(alias.name == "ciso8601" for alias in node.names):
            found.append(f"{path.name}:{node.lineno}")
        elif (isinstance(node, ast.Attribute) and node.attr in UNSAFE
              and isinstance(node.value, ast.Name) and node.value.id in {"dt_util", "dt"}):
            found.append(f"{path.name}:{node.lineno}")
    return found


def test_the_package_parses_time_only_through_the_helper():
    checked = 0
    for path in PACKAGE.rglob("*.py"):
        if "mesa_core" in path.parts or path.name in TEMPLATE_WORKER | {"time_text.py"}:
            continue
        checked += 1
        assert not _direct_parser_use(path)
    assert checked > 50


def test_the_guard_sees_each_form_of_direct_use(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("import ciso8601\nfrom homeassistant.util.dt import parse_datetime\n"
                      "from homeassistant.util import dt as dt_util\ndt_util.parse_date('x')\n", encoding="utf-8")
    assert len(_direct_parser_use(source)) == 3
