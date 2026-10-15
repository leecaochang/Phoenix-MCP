"""Assist bridge: expose Phoenix MCP's scoped tools to HA's native Assist/voice pipeline.

Registers a Home Assistant ``llm.API`` whose tools are the Phoenix MCP tool surface of a
single admin-chosen (bound) access key. Every tool call flows through the same
``_dispatch_mcp`` path an external MCP client uses, so per-access-key scoping, MESA, the
approval gate, and the audit log apply identically. A voice turn cannot show inline
Approve/Reject buttons, so confirm-gated actions degrade to "queued, approve in the
Phoenix MCP panel" (the per-call access key copy forces ``confirm_inline_wait_seconds`` to 0
so the turn never blocks). The bound access key stays a normal, live-editable access key; it is
re-resolved on every instance build and tool call, so cap/scope edits take effect on
the next call with no re-registration.

HA-COUPLING POINT (re-verify on HA upgrades, same posture as ws_dispatch):
``homeassistant.helpers.llm`` (async_register_api / API / Tool / APIInstance) is under
active refactoring. Probatio-based HA uses ``probatio.from_openapi``; the original
Voluptuous engine uses a JSON Schema validator wrapped as a native HA schema so bounds
and composition stay intact. Both paths keep the original JSON Schema through HA's
custom serializer hook. The validator is resolved in the executor before registration,
and schemas that cannot be supported are omitted individually.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from copy import deepcopy
from functools import cache
from typing import Any, cast

from homeassistant.core import HomeAssistant

from .assist_gemini import gemini_schema, unwrap_json_fields
from .const import ASSIST_CLIENT_IP, DOMAIN
from .household_authority import household_scope
from .request_authority import AuthorityChangedError
from .schema_compat import schema_engine

_LOGGER = logging.getLogger(__name__)

try:
    from homeassistant.helpers import llm as _llm

    _ASSIST_API_SUPPORTED = True
except ImportError:  # pragma: no cover - depends on HA version
    _llm = None  # type: ignore[assignment]
    _ASSIST_API_SUPPORTED = False


@cache
def _schema_converter() -> Callable[[Any], Any]:
    """Select a schema validator compatible with Home Assistant's engine."""
    engine = schema_engine()
    if engine.__name__ == "probatio":
        return cast(Callable[[Any], Any], engine.from_openapi)
    from .assist_schema import legacy_schema

    return legacy_schema



def _convert_to_voluptuous(schema: Any) -> Any:
    """Build HA parameters without weakening the advertised JSON Schema."""
    return _schema_converter()(schema)


def _gemini_schema_compatible(schema: Any) -> bool:
    """Refuse shapes HA's Gemini formatter cannot represent without loss."""
    if not isinstance(schema, dict):
        return False
    if isinstance(schema.get("type"), list) or any(
        key in schema for key in ("anyOf", "oneOf", "allOf")
    ):
        return False
    children = list(schema.get("properties", {}).values())
    if "items" in schema:
        children.append(schema["items"])
    return all(_gemini_schema_compatible(child) for child in children)


async def async_probe_assist_api(hass: HomeAssistant) -> bool:
    """Resolve the schema converter off the event loop and report support."""
    global _ASSIST_API_SUPPORTED
    if _llm is None:
        return False
    try:
        await hass.async_add_executor_job(_schema_converter)
    except ImportError as err:
        _LOGGER.warning(
            "Phoenix MCP Assist bridge is unavailable because its Home Assistant "
            "schema converter could not be imported: %s",
            err,
        )
        _ASSIST_API_SUPPORTED = False
        return False
    return True


def assist_api_supported() -> bool:
    """True when the running HA exposes the llm.API + schema-conversion seam."""
    return _ASSIST_API_SUPPORTED


_UNBOUND_PROMPT = (
    "No Phoenix MCP access key is currently bound to Phoenix MCP. An administrator must bind "
    "an access key in the Phoenix MCP panel (Access keys, then Tool Announcement & Assist) before this "
    "assistant can control anything. Until then you have no tools available."
)

_UNAVAILABLE_PROMPT = (
    "Phoenix MCP is currently unavailable or disabled. No Phoenix MCP tools are available. "
    "An administrator must restore availability before this assistant can use them."
)
_USER_UNAVAILABLE_PROMPT = (
    "The requesting Home Assistant user is no longer available. No Phoenix MCP tools are available. "
    "Ask an administrator to check the user's account before continuing."
)

_ASSIST_ADDENDUM = (
    "You are talking to a user through Home Assistant's voice/chat assistant. Keep "
    "answers brief and spoken-friendly. When a tool reports status "
    "\"queued_for_approval\", the action has NOT happened yet: it is waiting for an "
    "administrator to approve it in the Phoenix MCP panel. Tell the user it is queued for "
    "approval; do not retry it and do not claim it is done."
)


def _base_url(hass: HomeAssistant) -> str:
    """Best-effort external URL for the skill link in the primer; "" if none."""
    try:
        from homeassistant.helpers.network import get_url  # noqa: PLC0415

        return get_url(hass)
    except Exception:  # noqa: BLE001 - NoURLAvailableError and anything else
        return ""


_MCP_HINTS = (
    ("read_only", "readOnlyHint"),
    ("destructive", "destructiveHint"),
    ("idempotent", "idempotentHint"),
    ("open_world", "openWorldHint"),
)


def _tool_annotations(hints: Any) -> Any:
    """Map a tool's MCP hints onto HA's tool annotations, or None before HA has them.

    HA 2026.10 forwards these through its own MCP server. Phoenix states every hint;
    one that is missing or malformed keeps HA's least-safe default, as HA's own MCP
    client does.
    """
    annotations_type = getattr(_llm, "ToolAnnotations", None)
    if annotations_type is None:
        return None
    hints = hints if isinstance(hints, dict) else {}
    return annotations_type(**{
        field: hints[hint] for field, hint in _MCP_HINTS if isinstance(hints.get(hint), bool)
    })


def _tool_result(data: dict, *, error: bool) -> Any:
    """Return HA's ToolResult where it exists (HA 2026.10), the plain object before."""
    result_type = getattr(_llm, "ToolResult", None)
    return data if result_type is None else result_type(data=data, error=error)


def _unwrap_result(result: dict) -> dict:
    """Turn an MCP CallToolResult into the JSON object HA's Tool contract expects.

    Phoenix MCP tool results are ``{"content": [{"type": "text", "text": ...}], "isError"?}``.
    Most tools put a JSON object in that text; errors put a plain sentence. A dict
    passes through verbatim; anything else is wrapped so the return is always a dict.
    """
    from .agentcli import _flatten_text  # noqa: PLC0415

    text = _flatten_text(result.get("content", []))
    try:
        parsed: Any = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if result.get("isError"):
        return parsed if isinstance(parsed, dict) else {"error": text}
    if isinstance(parsed, dict):
        return parsed
    if parsed is not None:
        return {"result": parsed}
    return {"result": text}


if _ASSIST_API_SUPPORTED:

    class PhoenixAssistTool(_llm.Tool):
        """One announced Phoenix MCP tool, dispatched through the bound access key's scope."""

        integration = DOMAIN

        def __init__(self, name: str, description: str | None, parameters: Any, access_key_id: str,
                     json_paths: tuple[tuple[str, ...], ...] = (), annotations: Any = None) -> None:
            self.name = name
            self.description = description
            self.parameters = parameters
            if (mapped := _tool_annotations(annotations)) is not None:
                self.annotations = mapped
            self._access_key_id = access_key_id
            # Open objects Gemini was shown as JSON text, decoded before dispatch.
            self._json_paths = json_paths

        async def async_call(self, hass, tool_input, llm_context) -> Any:
            """Run one tool call end-to-end through Phoenix MCP's scoped dispatch path."""
            from .agentcli import _current_dispatch_access_key, _parse_pending, dispatch_tool_result  # noqa: PLC0415
            from .audit import generate_request_id  # noqa: PLC0415
            from .mcp_view import _dispatch_mcp  # noqa: PLC0415

            async with household_scope(hass, getattr(llm_context, "context", None)):
                data = hass.data[DOMAIN]
                # Re-resolve the bound access key per call: it may have been revoked,
                # expired, narrowed, or the kill switch flipped since the conversation
                # started. _current_dispatch_access_key also zeroes confirm_inline_wait so a
                # voice turn never blocks on an approval; the gate returns pending and
                # the result is reported as queued below.
                access_key = _current_dispatch_access_key(data, self._access_key_id)
                if access_key is None:
                    return _tool_result(
                        {"error": "This Phoenix MCP access key is no longer available."}, error=True
                    )
                # A tool built before a rebind or unbind still carries the old key;
                # the binding, not the key's own validity, is what grants Assist it.
                if data.store.get_settings().assist_bound_access_key_id != self._access_key_id:
                    return _tool_result(
                        {"error": "The Phoenix MCP Assist binding changed. Start a new conversation."},
                        error=True,
                    )

                request_id = generate_request_id()
                resp_msg, _m, _r, outcome = await _dispatch_mcp(
                    "tools/call",
                    tool_input.id,
                    {"name": self.name, "arguments": unwrap_json_fields(tool_input.tool_args or {}, self._json_paths)},
                    access_key,
                    hass,
                    data,
                    ASSIST_CLIENT_IP,
                    _base_url(hass),
                    request_id=request_id,
                )
                result = dispatch_tool_result(resp_msg)
                pending = _parse_pending(
                    result,
                    outcome=outcome,
                    data=data,
                    access_key_id=self._access_key_id,
                    tool_name=self.name,
                    request_id=request_id,
                )
                if pending:
                    return _tool_result({
                        "status": "queued_for_approval",
                        "approval_id": pending["approval_id"],
                        "message": (
                            "This action is queued for approval. An administrator must "
                            "approve it in the Phoenix MCP panel before it takes effect."
                        ),
                    }, error=False)
                return _tool_result(_unwrap_result(result), error=bool(result.get("isError")))

    class PhoenixAssistAPI(_llm.API):
        """An llm.API whose tools are the bound Phoenix MCP access key's scoped tool surface."""

        def __init__(self, hass: HomeAssistant, data: Any) -> None:
            super().__init__(hass=hass, id=DOMAIN, name="Phoenix MCP (scoped)")
            self._data = data

        async def async_get_api_instance(
            self, llm_context: "_llm.LLMContext"
        ) -> "_llm.APIInstance":
            """Build the per-turn tool set + primer for the currently bound access key."""
            data = self._data
            settings = data.store.get_settings()
            access_key = None
            # Resolve the bound access key FRESH every turn (never cache access key state).
            # Unbound, missing, invalid, or Phoenix MCP disabled yields zero tools and
            # never raises (raising would fail the whole conversation turn).
            if data.ready and not data.shutting_down and not settings.kill_switch:
                access_key_id = settings.assist_bound_access_key_id
                if access_key_id:
                    candidate = data.store.get_access_key_by_id(access_key_id)
                    if candidate is not None and candidate.is_valid():
                        access_key = candidate

            if access_key is None:
                return _llm.APIInstance(
                    api=self,
                    api_prompt=_UNAVAILABLE_PROMPT if settings.kill_switch or not data.ready or data.shutting_down else _UNBOUND_PROMPT,
                    llm_context=llm_context,
                    tools=[],
                )

            async with household_scope(self.hass, llm_context.context) as authority:
                try:
                    authority.check()
                except AuthorityChangedError:
                    return _llm.APIInstance(api=self, api_prompt=_USER_UNAVAILABLE_PROMPT,
                                            llm_context=llm_context, tools=[])
                from .agentcli import build_mcp_tool_list  # noqa: PLC0415
                from .mcp_view import _build_instructions  # noqa: PLC0415

                tools: list[Any] = []
                public_schemas: dict[int, tuple[Any, dict[str, Any]]] = {}
                for tool_def in build_mcp_tool_list(access_key, data):
                    name = tool_def.get("name")
                    try:
                        schema = deepcopy(tool_def.get("inputSchema") or {})
                        advertised = schema
                        json_paths: tuple[tuple[str, ...], ...] = ()
                        if llm_context.platform == "google_generative_ai_conversation":
                            # Validation keeps the full schema; Gemini is shown a
                            # representable copy (see assist_gemini).
                            advertised, json_paths = gemini_schema(schema)
                            if not _gemini_schema_compatible(advertised):
                                raise ValueError("Home Assistant's Gemini formatter cannot represent this schema")
                        parameters = _convert_to_voluptuous(deepcopy(schema))
                        # HA serializers unwrap Schema before invoking the hook.
                        node = parameters
                        engine = schema_engine(parameters)
                        while isinstance(node, engine.Schema):
                            node = node.schema
                        public_schemas[id(node)] = (node, advertised)
                    except Exception as err:  # noqa: BLE001 - one bad schema must not
                        # take down the whole voice assistant; skip just that tool.
                        _LOGGER.warning(
                            "Assist bridge: skipping tool %s (schema conversion failed: %s)",
                            name,
                            err,
                        )
                        continue
                    # name and description come from a tool def dict (Any to the type
                    # checker); the tool list always carries both, and a missing name
                    # would fail far earlier at tools/list.
                    tools.append(
                        PhoenixAssistTool(
                            str(name), tool_def.get("description"), parameters, access_key.id,
                            json_paths=json_paths, annotations=tool_def.get("annotations"),
                        )
                    )

                def serialize_schema(node: Any) -> Any:
                    """Advertise the original schema, scoped to this API instance."""
                    if (entry := public_schemas.get(id(node))) is not None and entry[0] is node:
                        return deepcopy(entry[1])
                    return _llm.selector_serializer(node)

                api_prompt = _build_instructions(access_key, data, _base_url(self.hass), self.hass) + "\n\n" + _ASSIST_ADDENDUM
                return _llm.APIInstance(
                    api=self,
                    api_prompt=api_prompt,
                    llm_context=llm_context,
                    tools=tools,
                    custom_serializer=serialize_schema,
                )

else:  # pragma: no cover - exercised only on HA versions lacking the seam
    PhoenixAssistTool = None  # type: ignore[assignment,misc]
    PhoenixAssistAPI = None  # type: ignore[assignment,misc]


def async_register_assist_api(hass: HomeAssistant, data: Any) -> None:
    """Register the single stable-id Phoenix MCP Assist API, if supported and not already up.

    Idempotent: a second call while registered is a no-op. Never raises; a failed
    registration is logged and the bridge stays absent.
    """
    if not _ASSIST_API_SUPPORTED or data.assist_api_unregister is not None:
        return
    try:
        data.assist_api_unregister = _llm.async_register_api(hass, PhoenixAssistAPI(hass, data))
    except Exception:  # noqa: BLE001 - HomeAssistantError on dup id, or anything
        _LOGGER.warning("Could not register the Phoenix MCP Assist API", exc_info=True)


def async_unregister_assist_api(data: Any) -> None:
    """Unregister the Assist API if registered. Idempotent; never raises."""
    unregister = data.assist_api_unregister
    if unregister is None:
        return
    data.assist_api_unregister = None
    try:
        unregister()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Assist API unregister failed", exc_info=True)
