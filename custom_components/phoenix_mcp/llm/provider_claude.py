"""The Anthropic Messages provider client."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from aiohttp import ClientError, ClientSession, SocketTimeoutError

from ..conversation_log import record_rejected_tool
from ..catalog_text import LocalizedReason
from .accounts import ProviderConfig
from .base import (
    _apply_claude_options, _bounded_catalog, _cache_marked, _CHAT_TIMEOUT, _CLAUDE_NO_ADAPTIVE,
    _CLAUDE_TRANSIENT_ERRORS, _created_map, _error_body, _http_failure, _http_failure_code,
    _IMAGE_PROBE_TIMEOUT, _invalid_tool_calls, _is_expired, _json_error_hint,
    _LEGACY_MAX_TOKENS, _MODEL_LIST_MAX_PAGES, _model_result_text, _norm, _norm_stop,
    _parse_provider_error, _PROBE_TIMEOUT, _provider_object, _provider_tool,
    _ProviderResponseError, _rate_limit_headers, _read_json, _refused_option,
    _RETRY_AFTER_HEADER_FIELDS, _retry_transient, _RETRYABLE_STATUSES, _silent_stream_error,
    _sse_lines, _thinking_type, _tool_call_problem, _total_provider_response, _usage_int,
    _use_enabled_thinking, _use_smaller_output, _valid_tool_call, EV_DONE, EV_ERROR, EV_TEXT,
    EV_THINKING, EV_TOOL, EV_USAGE,
)
from ..provider_constants import (
    AGENT_CHAT_ANTHROPIC_VERSION, AGENT_CHAT_EFFORT_LEVEL_ORDER, AGENT_CHAT_MINIMAX_MODELS,
    AGENT_CHAT_PROBE_IMAGE_MAX_TOKENS,
)


class ClaudeProvider:
    """Anthropic Messages API, streaming, over raw HTTP.

    Also backs MiniMax, whose API is Anthropic-compatible; kind-specific bits
    (auth header, thinking options, model listing) branch on self.kind.
    """

    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg
        self.kind = cfg.kind
        # Filled by list_models: creation time per listed id, for model selection.
        self.model_created: dict[str, float] = {}
        self._model_rows: dict[str, dict] = {}

    def format_tools(self, mcp_tools: list[dict]) -> list[dict]:
        out = []
        for t in mcp_tools:
            name, description, schema = _provider_tool(t)
            out.append({"name": name, "description": description, "input_schema": schema})
        return out

    def user_message(self, text: str, images: list[dict[str, str]]) -> dict:
        """The administrator's message with attached images first, as Anthropic recommends."""
        content: list[dict] = [{
            "type": "image",
            "source": {"type": "base64", "media_type": image["mime_type"], "data": image["data"]},
        } for image in images]
        if text:
            content.append({"type": "text", "text": text})
        return {"role": "user", "content": content}

    def append_assistant(self, messages: list[dict], assistant_msg: dict) -> None:
        messages.append(assistant_msg)

    def append_tool_results(self, messages: list[dict], results: list[dict]) -> None:
        # All tool_result blocks for a turn go in ONE user message.
        content = []
        for r in results:
            result_text = _model_result_text(r, self.cfg.vision is True)
            result_content: list[dict] = [{"type": "text", "text": result_text}]
            if self.cfg.vision is True:
                for image in r.get("images", []):
                    result_content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image["mime_type"],
                            "data": image["data"],
                        },
                    })
            block: dict = {
                "type": "tool_result",
                "tool_use_id": r["tool_use_id"],
                "content": result_content,
            }
            if r.get("is_error"):
                block["is_error"] = True
            content.append(block)
        messages.append({"role": "user", "content": content})

    async def validate(self, session: ClientSession) -> tuple[bool, str]:
        async def _probe() -> tuple[bool, str]:
            async with session.get(
                f"{self.cfg.base_url}/v1/models",
                headers=self._headers(), timeout=_PROBE_TIMEOUT, allow_redirects=False,
            ) as resp:
                if resp.status == 200:
                    return True, ""
                if resp.status in (401, 403):
                    return False, LocalizedReason("API key rejected.", "providerKeyRejected")
                if self.kind == "minimax":
                    # MiniMax's Anthropic-compatible base may not expose /v1/models;
                    # a non-auth error there does not mean the key is bad. Soft-accept
                    # (a real problem surfaces on the first message with a clean error).
                    return True, ""
                return False, LocalizedReason(f"Provider returned HTTP {resp.status}.", "providerHttpStatus", status=resp.status)
        try:
            return await _retry_transient(_probe)
        except ValueError:
            return False, LocalizedReason("Invalid provider connection settings.", "providerSettingsInvalid")
        except (ClientError, asyncio.TimeoutError) as exc:
            return False, LocalizedReason(f"Could not reach the provider: {exc}", "providerUnreachable", detail=str(exc))

    def _model_fallback(self) -> list[str]:
        return (list(AGENT_CHAT_MINIMAX_MODELS) if self.kind == "minimax"
                else [self.cfg.model] if self.cfg.model else [])

    @_bounded_catalog
    async def list_models(self, session: ClientSession) -> list[str]:
        rows: list[dict] = []
        after_id = ""
        try:
            # Anthropic pages /v1/models (default 20 per page), so follow
            # `has_more` or an account with many models silently loses the rest.
            for _ in range(_MODEL_LIST_MAX_PAGES):
                params = {"limit": "1000", **({"after_id": after_id} if after_id else {})}
                async with session.get(
                    f"{self.cfg.base_url}/v1/models", headers=self._headers(),
                    params=params, timeout=_PROBE_TIMEOUT, allow_redirects=False,
                ) as resp:
                    if resp.status != 200:
                        break
                    body = await _read_json(resp)
                if not isinstance(body, dict):
                    break
                rows.extend(r for r in body.get("data", []) if isinstance(r, dict))
                after_id = str(body.get("last_id") or "")
                if not body.get("has_more") or not after_id:
                    break
        except (ClientError, asyncio.TimeoutError, ValueError):
            pass
        rows = [row for row in rows if not _is_expired(row)]
        self.model_created = _created_map(rows)
        self._model_rows = {m["id"]: m for m in rows if m.get("id")}
        return [m["id"] for m in rows if m.get("id")] or self._model_fallback()

    def _headers(self) -> dict:
        # MiniMax's Anthropic-compatible API authenticates with a Bearer token
        # rather than Anthropic's x-api-key; both send the anthropic-version.
        if self.kind == "minimax":
            return {
                "Authorization": f"Bearer {self.cfg.api_key or ''}",
                "anthropic-version": AGENT_CHAT_ANTHROPIC_VERSION,
                "content-type": "application/json",
            }
        return {
            "x-api-key": self.cfg.api_key or "",
            "anthropic-version": AGENT_CHAT_ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    async def probe_image(self, session: ClientSession, image: str, question: str) -> tuple[int | None, str]:
        """Status and reply text for one question about an attached PNG (see async_probe_vision)."""
        body = {"model": self.cfg.model, "max_tokens": AGENT_CHAT_PROBE_IMAGE_MAX_TOKENS,
                "messages": [self.user_message(question, [{"mime_type": "image/png", "data": image}])]}
        try:
            async with session.post(f"{self.cfg.base_url}/v1/messages", headers=self._headers(), json=body,
                                    timeout=_IMAGE_PROBE_TIMEOUT, allow_redirects=False) as resp:
                if resp.status != 200:
                    return resp.status, _parse_provider_error(await _error_body(resp))[0]
                data = await _read_json(resp)
        except (ClientError, asyncio.TimeoutError, ValueError):
            return None, ""
        blocks = data.get("content") if isinstance(data, dict) else None
        if not isinstance(blocks, list):
            return 200, ""
        return 200, "".join(block["text"] for block in blocks
                            if isinstance(block, dict) and block.get("type") == "text"
                            and isinstance(block.get("text"), str))

    async def probe_option(self, session: ClientSession, extra: dict) -> int | None:
        """HTTP status for the smallest possible completion carrying `extra`.

        None when the provider could not be reached at all, which is not an
        answer about the option and must not be recorded as one.
        """
        body = {"model": self.cfg.model, "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}], **extra}
        try:
            async with session.post(f"{self.cfg.base_url}/v1/messages", headers=self._headers(),
                                    json=body, timeout=_PROBE_TIMEOUT, allow_redirects=False) as resp:
                return resp.status
        except (ClientError, asyncio.TimeoutError):
            return None

    async def list_model_capabilities(
        self, session: ClientSession, models: list[str],
    ) -> dict[str, dict]:
        """Per-model capabilities from the `capabilities` object on each model row.

        Anthropic declares image input, thinking and effort levels, so they are not probed. MiniMax
        rows carry none and yield nothing.
        """
        out: dict[str, dict] = {}
        for model in models:
            declared = (self._model_rows.get(model) or {}).get("capabilities")
            if not isinstance(declared, dict):
                continue
            caps: dict[str, Any] = {}
            vision = (declared.get("image_input") or {}).get("supported")
            if isinstance(vision, bool):
                caps["vision"] = vision
            thinking = (declared.get("thinking") or {}).get("supported")
            if isinstance(thinking, bool):
                caps["thinking"] = thinking
            effort = declared.get("effort") or {}
            if isinstance(effort, dict) and effort.get("supported") is True:
                caps["effort_levels"] = [
                    level for level in AGENT_CHAT_EFFORT_LEVEL_ORDER
                    if (effort.get(level) or {}).get("supported") is True
                ]
            if caps:
                out[model] = caps
        return out

    async def stream_turn(
        self, session: ClientSession, *, system_prompt: str, messages: list[dict],
        tools: list[dict], options: dict,
    ) -> AsyncIterator[dict]:
        # Prompt caching is Anthropic-specific; MiniMax's compatible API is not
        # verified to accept cache_control, so it gets the plain uncached body.
        system, messages = (
            _cache_marked(system_prompt, messages) if self.kind == "claude"
            else (system_prompt, messages)
        )
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "system": system,
            "messages": messages,
            "tools": tools,
            "stream": True,
        }
        _apply_claude_options(body, options, self.kind)
        if (self.cfg.base_url, self.cfg.model) in _CLAUDE_NO_ADAPTIVE:
            _use_enabled_thinking(body)
        try:
            for attempt in range(3):
                async with session.post(
                    f"{self.cfg.base_url}/v1/messages", headers=self._headers(),
                    json=body, timeout=_CHAT_TIMEOUT, allow_redirects=False,
                ) as resp:
                    if resp.status != 200:
                        text = await _error_body(resp)
                        msg = _parse_provider_error(text)[0]
                        if (attempt == 0 and resp.status == 400
                                and _thinking_type(body) == "adaptive"
                                and "adaptive" in msg.lower()):
                            # An older model (e.g. Haiku 4.5) takes an enabled
                            # thinking budget, not adaptive thinking.
                            _CLAUDE_NO_ADAPTIVE.add((self.cfg.base_url, self.cfg.model))
                            _use_enabled_thinking(body)
                            continue
                        if (attempt < 2 and resp.status == 400 and "max_tokens" in msg.lower()
                                and body["max_tokens"] > _LEGACY_MAX_TOKENS):
                            # An older model with a smaller output range.
                            _use_smaller_output(body)
                            continue
                        rate_limit = (_rate_limit_headers(resp.headers, _RETRY_AFTER_HEADER_FIELDS)
                                      if resp.status in (429, 503, 529) else {})
                        yield _http_failure(self.kind, resp.status, text, code=_http_failure_code(resp.status),
                                            retryable=resp.status in _RETRYABLE_STATUSES,
                                            api_key=self.cfg.api_key,
                                            refused=_refused_option(resp.status, msg, body),
                                            **({"rate_limit": rate_limit} if rate_limit else {}))
                        return
                    async for ev in self._parse(resp):
                        yield ev
                    return
        except SocketTimeoutError:
            yield _silent_stream_error()
        except (ClientError, asyncio.TimeoutError):
            yield _norm(EV_ERROR, status=0, code="network", message="Provider connection failed.", retryable=True)

    @_total_provider_response
    async def _parse(self, resp: Any) -> AsyncIterator[dict]:
        # Assemble content blocks by index into an assistant message.
        blocks: dict[int, dict] = {}
        tool_json: dict[int, str] = {}
        order: list[int] = []
        stop_reason = "end_turn"
        saw_stop = False
        saw_reason = False
        open_blocks: set[int] = set()
        parsed_tools: list[dict] = []
        # message_start reports the call's input tokens; the closing
        # message_delta reports the final cumulative output tokens.
        input_tokens = cache_read = cache_write = 0
        async for line in _sse_lines(resp):
            if not line.startswith("data:"):
                if line.lstrip().startswith(("{", "[")):
                    _provider_object(line)
                    raise _ProviderResponseError()
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            ev = _provider_object(payload)
            etype = ev.get("type")
            if etype == "content_block_start":
                idx = ev.get("index", 0)
                cb = ev.get("content_block", {})
                if type(idx) is not int or idx < 0 or idx in blocks or not isinstance(cb, dict):
                    raise _ProviderResponseError()
                open_blocks.add(idx)
                order.append(idx)
                if cb.get("type") == "tool_use":
                    initial_input = cb.get("input", {})
                    if not isinstance(initial_input, dict):
                        record_rejected_tool(cb.get("id"), cb.get("name"), initial_input,
                                             reason="Tool arguments must be a JSON object.")
                        raise _ProviderResponseError()
                    blocks[idx] = {"type": "tool_use", "id": cb.get("id"), "name": cb.get("name"), "input": {}}
                    tool_json[idx] = json.dumps(initial_input) if initial_input else ""
                elif cb.get("type") == "thinking":
                    blocks[idx] = {"type": "thinking", "thinking": "", "signature": ""}
                elif cb.get("type") == "redacted_thinking":
                    if not isinstance(cb.get("data"), str):
                        raise _ProviderResponseError()
                    blocks[idx] = {"type": "redacted_thinking", "data": cb["data"]}
                else:
                    blocks[idx] = {"type": "text", "text": ""}
            elif etype == "content_block_delta":
                idx = ev.get("index", 0)
                delta = ev.get("delta", {})
                if idx not in open_blocks or not isinstance(delta, dict):
                    raise _ProviderResponseError()
                dtype = delta.get("type")
                if dtype == "text_delta":
                    blocks.setdefault(idx, {"type": "text", "text": ""})
                    blocks[idx]["text"] = blocks[idx].get("text", "") + delta.get("text", "")
                    yield _norm(EV_TEXT, text=delta.get("text", ""))
                elif dtype == "thinking_delta":
                    if not isinstance(delta.get("thinking", ""), str) or blocks[idx].get("type") != "thinking":
                        raise _ProviderResponseError()
                    blocks[idx]["thinking"] += delta.get("thinking", "")
                    yield _norm(EV_THINKING, text=delta.get("thinking", ""))
                elif dtype == "signature_delta":
                    if not isinstance(delta.get("signature"), str) or blocks[idx].get("type") != "thinking":
                        raise _ProviderResponseError()
                    blocks[idx]["signature"] += delta["signature"]
                elif dtype == "input_json_delta":
                    tool_json[idx] = tool_json.get(idx, "") + delta.get("partial_json", "")
            elif etype == "content_block_stop":
                idx = ev.get("index", 0)
                if idx not in open_blocks:
                    raise _ProviderResponseError()
                open_blocks.remove(idx)
                blk = blocks.get(idx)
                if blk and blk.get("type") == "tool_use":
                    parsed_tools.append(_norm(EV_TOOL, id=blk["id"], name=blk["name"],
                                              input=tool_json.get(idx) or "{}", _block_index=idx))
            elif etype == "message_start":
                usage = (ev.get("message") or {}).get("usage") or {}
                if usage:
                    # Context actually sent = fresh input + cache writes + cache
                    # reads (all three are prompt tokens on the wire).
                    cache_read = _usage_int(usage.get("cache_read_input_tokens"))
                    cache_write = _usage_int(usage.get("cache_creation_input_tokens"))
                    input_tokens = _usage_int(usage.get("input_tokens")) + cache_write + cache_read
                    yield _norm(EV_USAGE, input_tokens=input_tokens,
                                output_tokens=_usage_int(usage.get("output_tokens")),
                                cache_read_tokens=cache_read, cache_write_tokens=cache_write)
            elif etype == "message_delta":
                sr = ev.get("delta", {}).get("stop_reason")
                if sr:
                    stop_reason = sr
                    saw_reason = True
                usage = ev.get("usage") or {}
                if "output_tokens" in usage:
                    yield _norm(EV_USAGE, input_tokens=input_tokens,
                                output_tokens=_usage_int(usage.get("output_tokens")),
                                cache_read_tokens=cache_read, cache_write_tokens=cache_write)
            elif etype == "message_stop":
                saw_stop = True
                break
            elif etype == "error":
                # Anthropic can fail AFTER answering 200: an overloaded or
                # internal error is transient and worth a retry, anything else
                # is a refusal of this request.
                err = ev.get("error")
                if isinstance(err, dict) and err.get("type") in _CLAUDE_TRANSIENT_ERRORS:
                    raise _ProviderResponseError("provider_incomplete", retryable=True)
                raise _ProviderResponseError("provider_blocked", retryable=False)
        if not saw_stop or not saw_reason or open_blocks:
            for idx, block in blocks.items():
                if block.get("type") == "tool_use":
                    record_rejected_tool(block.get("id"), block.get("name"), tool_json.get(idx, ""),
                                         reason="The provider response ended before the tool call was complete.",
                                         finish_reason=stop_reason if saw_reason else None)
            raise _ProviderResponseError("provider_incomplete", retryable=stop_reason != "max_tokens")
        if stop_reason in ("refusal", "content_filter"):
            raise _ProviderResponseError("provider_blocked", retryable=False)
        if stop_reason == "model_context_window_exceeded":
            raise _ProviderResponseError("provider_context_limit", retryable=False)
        if stop_reason not in ("end_turn", "tool_use", "max_tokens", "stop_sequence"):
            raise _ProviderResponseError()
        if stop_reason == "tool_use" and not parsed_tools:
            raise _ProviderResponseError()
        if parsed_tools and stop_reason != "tool_use":
            raise _ProviderResponseError("provider_incomplete", retryable=stop_reason != "max_tokens")
        # Model-facing error per block index whose arguments did not parse.
        invalid: dict[int, str] = {}
        for tool in parsed_tools:
            raw_arguments = tool["input"]
            try:
                tool["input"] = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                record_rejected_tool(tool["id"], tool["name"], raw_arguments,
                                     reason="Tool arguments are not valid JSON.", finish_reason=stop_reason, error=exc)
                invalid[tool["_block_index"]] = _json_error_hint(raw_arguments, exc)
                # History placeholder only: EV_INVALID_TOOLS never dispatches.
                tool["input"] = {}
                continue
            if not _valid_tool_call(tool["id"], tool["name"], tool["input"]):
                record_rejected_tool(tool["id"], tool["name"], raw_arguments,
                                     reason=_tool_call_problem(tool["id"], tool["name"]),
                                     finish_reason=stop_reason)
                raise _ProviderResponseError()
        if len({tool["id"] for tool in parsed_tools}) != len(parsed_tools):
            for tool in parsed_tools:
                record_rejected_tool(tool["id"], tool["name"], tool["input"],
                                     reason="The response contains duplicate tool call IDs.", finish_reason=stop_reason)
            raise _ProviderResponseError()
        for tool in parsed_tools:
            blocks[tool["_block_index"]]["input"] = tool["input"]
        # Assemble the assistant message in block order. A tool round keeps its
        # thinking blocks, signature included and unmodified: the API requires
        # them back with the tool results, and on models using a fixed thinking
        # budget it refuses the next request without them. A final answer drops
        # them, and the chat view strips them once the next user message
        # arrives (decision_history.without_thinking), so finished turns stay
        # plain history.
        content = []
        for idx in order:
            blk = blocks.get(idx)
            if not blk:
                continue
            if blk["type"] in ("thinking", "redacted_thinking"):
                if parsed_tools:
                    content.append(dict(blk))
            elif blk["type"] == "text" and blk.get("text"):
                content.append({"type": "text", "text": blk["text"]})
            elif blk["type"] == "tool_use":
                content.append({"type": "tool_use", "id": blk["id"], "name": blk["name"], "input": blk["input"]})
        assistant_msg = {"role": "assistant", "content": content}
        if invalid:
            # The response finished cleanly but a call's arguments do not parse:
            # withhold every call and let the loop hand the error back.
            event = _invalid_tool_calls(
                [(tool["id"], tool["name"], tool["input"], invalid.get(tool["_block_index"])) for tool in parsed_tools],
                assistant_msg,
            )
            if event is None:
                raise _ProviderResponseError()
            yield event
            return
        for tool in parsed_tools:
            tool.pop("_block_index")
            yield tool
        yield _norm(EV_DONE, stop_reason=_norm_stop(stop_reason), assistant_msg=assistant_msg)
