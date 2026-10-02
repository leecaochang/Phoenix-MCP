"""Adapter for the raw MCP Python SDK low-level Server.

Installs a single ``list_tools``/``call_tool`` handler pair on the server and
dispatches by tool name; results are returned as JSON text content.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from custom_components.phoenix_mcp.mesa_core.exceptions import MesaError
from custom_components.phoenix_mcp.mesa_core.mcp.adapters import ToolHandler


class RawSDKRegistry:
    def __init__(self, server: Any) -> None:
        if server is None:
            raise MesaError("the raw_sdk adapter requires server=<mcp.server.Server instance>")
        self.server = server
        self._tools: dict[str, tuple[ToolHandler, dict[str, Any], str]] = {}
        self._installed = False

    @property
    def registered(self) -> list[str]:
        return list(self._tools)

    def register_tool(
        self, name: str, handler: ToolHandler, schema: dict[str, Any], description: str
    ) -> None:
        self._tools[name] = (handler, schema, description)
        self._install_once()

    @staticmethod
    def _unknown_tool(name: str) -> dict[str, Any]:
        # Spec 9.6 envelope, not a raised KeyError: every other failure path
        # already answers with an error object.
        return {
            "error": "unknown_tool",
            "message": f"tool {name!r} is not registered",
            "details": {"tool": name},
        }

    async def dispatch(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        entry = self._tools.get(name)
        if entry is None:
            return self._unknown_tool(name)
        handler, _, _ = entry
        return await handler(arguments or {})

    def _install_once(self) -> None:
        if self._installed:
            return
        try:
            from mcp import types as sdk_types

            types: Any = sdk_types
        except ImportError as err:
            raise MesaError(
                "the raw_sdk adapter requires the 'mcp' package (pip install mesa-core[mcp])"
            ) from err

        tools = self._tools
        is_v2 = callable(getattr(self.server, "add_request_handler", None))
        list_key = "tools/list" if is_v2 else types.ListToolsRequest
        call_key = "tools/call" if is_v2 else types.CallToolRequest
        attribute = "_request_handlers" if is_v2 else "request_handlers"
        original = getattr(self.server, attribute)

        def wrap(key: Any, entry: Any) -> Any:
            if key not in (list_key, call_key):
                return entry
            delegate = entry.handler if is_v2 and entry is not None else entry

            async def route(*args: Any) -> Any:
                request = args[-1]
                if key == call_key:
                    params = request if is_v2 else request.params
                    if params.name not in tools and delegate is not None:
                        return await delegate(*args)
                    result = await self.dispatch(params.name, params.arguments)
                    payload = types.CallToolResult(
                        content=[types.TextContent(type="text", text=json.dumps(result))]
                    )
                else:
                    prior = await delegate(*args) if delegate is not None else None
                    prior = prior if is_v2 or prior is None else prior.root
                    existing = list(prior.tools) if prior is not None else []
                    if any(tool.name in tools for tool in existing):
                        raise MesaError("host tool name collides with a MESA tool")
                    schema_key = "input_schema" if is_v2 else "inputSchema"
                    published = [
                        types.Tool(name=name, description=description, **{schema_key: schema})
                        for name, (_, schema, description) in tools.items()
                    ]
                    payload = (
                        prior.model_copy(update={"tools": [*existing, *published]})
                        if prior is not None
                        else types.ListToolsResult(tools=published)
                    )
                return payload if is_v2 else types.ServerResult(payload)

            return replace(entry, handler=route) if is_v2 else route

        # Both SDKs replace one handler per method. Compose registrations at
        # that shared boundary, including host decorators installed later.
        # Non-tool handlers keep their SDK behavior unchanged.
        if is_v2:
            for key, params in (
                (list_key, types.PaginatedRequestParams),
                (call_key, types.CallToolRequestParams),
            ):
                if key not in original:

                    async def empty(context: Any, params: Any) -> Any:
                        if hasattr(params, "name"):
                            return types.CallToolResult(
                                content=[
                                    types.TextContent(
                                        type="text",
                                        text=json.dumps(self._unknown_tool(params.name)),
                                    )
                                ]
                            )
                        return types.ListToolsResult(tools=[])

                    self.server.add_request_handler(key, params, empty)
        composed = _ComposedHandlers(original, wrap)
        if not is_v2:
            for key in (list_key, call_key):
                if key not in composed:
                    composed[key] = None
        setattr(self.server, attribute, composed)
        self._installed = True


class _ComposedHandlers(dict[Any, Any]):
    """Retain MESA dispatch when a host replaces its SDK tool handlers."""

    def __init__(self, existing: dict[Any, Any], wrap: Callable[[Any, Any], Any]) -> None:
        super().__init__()
        self._wrap = wrap
        for key, entry in existing.items():
            self[key] = entry

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, self._wrap(key, value))
