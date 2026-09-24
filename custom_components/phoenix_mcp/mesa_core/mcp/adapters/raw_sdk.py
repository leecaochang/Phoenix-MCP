"""Adapter for the raw MCP Python SDK low-level Server.

Installs a single ``list_tools``/``call_tool`` handler pair on the server and
dispatches by tool name; results are returned as JSON text content.
"""

from __future__ import annotations

import json
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

        tools = self._tools  # closures observe later registrations

        if callable(getattr(self.server, "add_request_handler", None)):
            # SDK v2 replaces decorators with method/params/context handlers.
            async def list_v2(context: Any, params: Any) -> Any:
                return types.ListToolsResult(
                    tools=[
                        types.Tool(name=name, description=description, input_schema=schema)
                        for name, (_, schema, description) in tools.items()
                    ]
                )

            async def call_v2(context: Any, params: Any) -> Any:
                result = await self.dispatch(params.name, params.arguments)
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=json.dumps(result))]
                )

            self.server.add_request_handler("tools/list", types.PaginatedRequestParams, list_v2)
            self.server.add_request_handler("tools/call", types.CallToolRequestParams, call_v2)
            self._installed = True
            return

        @self.server.list_tools()  # type: ignore[untyped-decorator]
        async def _list_tools() -> list[Any]:
            return [
                types.Tool(name=name, description=description, inputSchema=schema)
                for name, (_, schema, description) in tools.items()
            ]

        @self.server.call_tool()  # type: ignore[untyped-decorator]
        async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
            entry = tools.get(name)
            if entry is None:
                result = self._unknown_tool(name)
            else:
                handler, _, _ = entry
                result = await handler(arguments or {})
            return [types.TextContent(type="text", text=json.dumps(result))]

        self._installed = True
