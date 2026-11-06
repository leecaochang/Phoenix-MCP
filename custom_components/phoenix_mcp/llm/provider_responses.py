"""The OpenAI Responses provider client."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator

from aiohttp import ClientError, ClientSession, SocketTimeoutError

from ..conversation_log import record_rejected_tool
from .base import (
    _CHAT_TIMEOUT, _error_body, _http_failure, _http_failure_code, _norm, _parse_provider_error,
    _provider_object, _ProviderResponseError, _rate_limit_headers, _refused_option,
    _RETRY_AFTER_HEADER_FIELDS, _RETRYABLE_STATUSES, _silent_stream_error, _sse_lines,
    _transient_stream_error, EV_DONE, EV_ERROR, EV_INVALID_TOOLS,
)
from .provider_openai import OpenAICompatProvider


# OpenAI model families that reason by default and refuse a sampling temperature
# unless their effort is "none".
_OPENAI_REASONING_MODEL_RE = re.compile(r"^(?:o\d|gpt-5|gpt-6)", re.I)


class _TranslatedStream:
    """A response-like object whose body is a sequence of pre-built SSE frames."""

    def __init__(self, frames: AsyncIterator[dict]) -> None:
        self.content = self
        self._frames = frames

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        async for frame in self._frames:
            yield f"data: {json.dumps(frame)}\n\n".encode()


def _responses_tools(tools: list[dict]) -> list[dict]:
    """Chat Completions function tools in the Responses API's flat shape.

    `strict` is sent as false: Responses otherwise tries to make every schema
    strict, which Phoenix's published schemas are not written for.
    """
    out = []
    for tool in tools:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(fn, dict):
            out.append({"type": "function", "name": fn.get("name"), "description": fn.get("description", ""),
                        "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                        "strict": False})
    return out


def _responses_content(content: Any) -> Any:
    """A user message's Chat Completions content as Responses input parts."""
    if not isinstance(content, list):
        return content if isinstance(content, str) else ""
    parts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            parts.append({"type": "input_text", "text": str(part.get("text") or "")})
        elif part.get("type") == "image_url":
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if isinstance(url, str):
                parts.append({"type": "input_image", "image_url": url, "detail": "auto"})
    return parts


def _responses_input(messages: list[dict]) -> list[dict]:
    """Chat Completions history as Responses input items.

    An assistant message carrying its response's output items replays them unchanged and in order,
    as reasoning requires; only call arguments Phoenix replaced come from the message.
    """
    items: list[dict] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "user":
            items.append({"role": "user", "content": _responses_content(message.get("content"))})
        elif role in ("system", "developer"):
            items.append({"role": role, "content": _responses_content(message.get("content"))})
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": message.get("tool_call_id"),
                          "output": str(message.get("content") or "")})
        elif role == "assistant":
            calls = [call for call in message.get("tool_calls") or [] if isinstance(call, dict)]
            arguments = {call.get("id"): (call.get("function") or {}).get("arguments") for call in calls}
            replay = message.get("response_items")
            if isinstance(replay, list) and replay:
                for item in replay:
                    if isinstance(item, dict) and item.get("type") == "function_call" and item.get("call_id") in arguments:
                        item = {**item, "arguments": arguments[item["call_id"]]}
                    if isinstance(item, dict):
                        items.append(item)
                continue
            text = message.get("content")
            if isinstance(text, str) and text:
                items.append({"role": "assistant", "content": text})
            for call in calls:
                fn = call.get("function") or {}
                items.append({"type": "function_call", "call_id": call.get("id"), "name": fn.get("name"),
                              "arguments": fn.get("arguments") or "{}"})
    return items


class OpenAIResponsesProvider(OpenAICompatProvider):
    """OpenAI through the Responses API, which current models need for agent turns.

    From GPT-5.4, Chat Completions combines tools only with reasoning effort "none", and some models
    take tools only here. This adapter converts Chat Completions history per request and translates
    events back for the shared parser. Requests are stateless (`store: false`), so a tool loop's
    reasoning returns as encrypted `response_items` on the assistant message, which
    decision_history.without_thinking removes after the turn. Probes, validation and the model list
    keep the inherited calls.
    """

    async def stream_turn(
        self, session: ClientSession, *, system_prompt: str, messages: list[dict],
        tools: list[dict], options: dict,
    ) -> AsyncIterator[dict]:
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "instructions": system_prompt,
            "input": _responses_input(messages),
            "tools": _responses_tools(tools),
            "stream": True,
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "stream_options": {"include_obfuscation": False},
        }
        effort = options.get("effort")
        show_thinking = bool(options.get("show_thinking"))
        if effort:
            body["reasoning"] = {"effort": effort, **({"summary": "auto"} if show_thinking else {})}
        temp = options.get("temperature")
        reasoning = effort not in (None, "none") or (effort is None and _OPENAI_REASONING_MODEL_RE.match(self.cfg.model))
        if temp is not None and not reasoning:
            try:
                body["temperature"] = float(temp)
            except (TypeError, ValueError):
                pass
        # The fields a refusal can name, under the keys refusal learning knows.
        sent = {**body, **({"reasoning_effort": effort} if effort else {})}
        try:
            async with session.post(f"{self.cfg.base_url}/responses", headers=self._headers(), json=body,
                                    timeout=_CHAT_TIMEOUT, allow_redirects=False) as resp:
                if resp.status != 200:
                    text = await _error_body(resp)
                    msg = _parse_provider_error(text)[0]
                    rate_limit = (_rate_limit_headers(resp.headers, _RETRY_AFTER_HEADER_FIELDS)
                                  if resp.status in (429, 503, 529) else {})
                    yield _http_failure(self.kind, resp.status, text, code=_http_failure_code(resp.status),
                                        retryable=resp.status in _RETRYABLE_STATUSES, api_key=self.cfg.api_key,
                                        refused=_refused_option(resp.status, msg.replace("reasoning.effort",
                                                                                         "reasoning_effort"), sent),
                                        **({"rate_limit": rate_limit} if rate_limit else {}))
                    return
                items: dict[int, dict] = {}
                async for ev in self._parse(_TranslatedStream(self._chunks(resp, items)), show_thinking):
                    if ev.get("type") in (EV_DONE, EV_INVALID_TOOLS) and isinstance(ev.get("assistant_msg"), dict):
                        replay = [items[index] for index in sorted(items)]
                        if ev["assistant_msg"].get("tool_calls") and replay:
                            ev = {**ev, "assistant_msg": {**ev["assistant_msg"], "response_items": replay}}
                    yield ev
        except SocketTimeoutError:
            yield _silent_stream_error()
        except (ClientError, asyncio.TimeoutError):
            yield _norm(EV_ERROR, status=0, code="network", message="Provider connection failed.", retryable=True)

    async def _chunks(self, resp: Any, items: dict[int, dict]) -> AsyncIterator[dict]:
        """Responses stream events as the Chat Completions chunks _parse reads."""
        calls = 0
        async for line in _sse_lines(resp):
            if not line.startswith("data:"):
                if line.lstrip().startswith(("{", "[")):
                    _provider_object(line)
                    raise _ProviderResponseError()
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            if payload == "[DONE]":
                return
            event = _provider_object(payload)
            etype = event.get("type")
            if etype == "response.output_text.delta":
                if not isinstance(event.get("delta"), str):
                    raise _ProviderResponseError()
                yield {"choices": [{"delta": {"content": event["delta"]}}]}
            elif etype == "response.reasoning_summary_text.delta":
                if isinstance(event.get("delta"), str):
                    yield {"choices": [{"delta": {"reasoning": event["delta"]}}]}
            elif etype == "response.refusal.delta":
                raise _ProviderResponseError("provider_blocked", retryable=False)
            elif etype == "response.output_item.done":
                item, index = event.get("item"), event.get("output_index")
                if not isinstance(item, dict) or type(index) is not int or index < 0 or index in items:
                    raise _ProviderResponseError()
                items[index] = item
                if item.get("type") == "function_call":
                    if not isinstance(item.get("arguments"), str):
                        record_rejected_tool(item.get("call_id"), item.get("name"), item.get("arguments"),
                                             reason="The provider must send tool arguments as JSON text.")
                        raise _ProviderResponseError()
                    yield {"choices": [{"delta": {"tool_calls": [{
                        "index": calls, "id": item.get("call_id"), "type": "function",
                        "function": {"name": item.get("name"), "arguments": item["arguments"]},
                    }]}}]}
                    calls += 1
            elif etype in ("response.completed", "response.incomplete"):
                given = event.get("response")
                response: dict = given if isinstance(given, dict) else {}
                usage = response.get("usage")
                if isinstance(usage, dict):
                    details = usage.get("input_tokens_details")
                    yield {"choices": [], "usage": {
                        "prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens"),
                        "prompt_tokens_details": {"cached_tokens": details.get("cached_tokens")
                                                  if isinstance(details, dict) else 0},
                    }}
                if etype == "response.completed":
                    finish = "tool_calls" if calls else "stop"
                else:
                    details = response.get("incomplete_details")
                    reason = details.get("reason") if isinstance(details, dict) else None
                    if reason == "content_filter":
                        raise _ProviderResponseError("provider_blocked", retryable=False)
                    if reason != "max_output_tokens":
                        raise _ProviderResponseError("provider_incomplete", retryable=False)
                    finish = "length"
                yield {"choices": [{"delta": {}, "finish_reason": finish}]}
                return
            elif etype in ("response.failed", "error"):
                given = event.get("response")
                failed: dict = given if isinstance(given, dict) else {}
                error = failed.get("error") if etype == "response.failed" else event
                if isinstance(error, dict) and _transient_stream_error(error):
                    raise _ProviderResponseError("provider_transient")
                raise _ProviderResponseError("provider_blocked", retryable=False)
