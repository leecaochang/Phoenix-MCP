"""Tool-level loading for providers whose tools-array cap the catalog exceeds.

Phoenix's own loops declare such a catalog in parts: a starting set, the client-local
load_tools function and the loaded deferred tools, with a directory in the system prompt.
Nothing is hidden or granted: every call still goes through the shared dispatcher. External
clients and uncapped providers keep the complete catalog.
"""

from __future__ import annotations

from collections import OrderedDict
import json
import re
from typing import Any

from .provider_constants import AGENT_CHAT_DEFERRED_TOOL_GROUPS, AGENT_CHAT_LOAD_TOOLS_NAME
from .tool_catalog import _TOOL_DEFS_BY_NAME, _tool_caps


def tool_group(name: str) -> str | None:
    """The deferred group a published tool belongs to, or None for the starting set."""

    definition = _TOOL_DEFS_BY_NAME.get(name)
    caps = _tool_caps(definition) if definition is not None else ()
    for group, (group_caps, fragments, _summary) in AGENT_CHAT_DEFERRED_TOOL_GROUPS.items():
        if (caps and caps[0] in group_caps) or any(fragment in name for fragment in fragments):
            return group
    return None


def _first_sentence(description: Any) -> str:
    """The first sentence of a tool description, which is what the directory shows."""
    text = str(description or "").strip()
    match = re.match(r"(.+?[.!?])(\s|$)", text, re.S)
    return (match.group(1) if match else text).replace("\n", " ")


class ToolLoader:
    """One turn's declared catalog under a provider's tools-array cap.

    Loaded tools are evicted least recently used first, but never in the round
    that loaded them: two loads in one round cannot push out each other's tools
    before the model has seen either, so the overflow is refused instead.
    """

    def __init__(self, catalog: list[dict], limit: int) -> None:
        self.limit = limit
        self._by_name = {tool["name"]: tool for tool in catalog}
        members: dict[str, list[str]] = {group: [] for group in AGENT_CHAT_DEFERRED_TOOL_GROUPS}
        self.starting: list[dict] = []
        for tool in catalog:
            group = tool_group(tool["name"])
            if group is None:
                self.starting.append(tool)
            else:
                members[group].append(tool["name"])
        self.groups = {group: names for group, names in members.items() if names}
        self.deferred = frozenset(name for names in self.groups.values() for name in names)
        self.capacity = limit - len(self.starting) - 1
        self._loaded: OrderedDict[str, None] = OrderedDict()
        self._pinned: set[str] = set()
        self._declared: frozenset[str] = frozenset()

    @classmethod
    def plan(cls, catalog: list[dict], limit: int | None) -> ToolLoader | None:
        """A loader when the catalog exceeds the limit and loading leaves room, else None."""
        if limit is None or len(catalog) <= limit:
            return None
        loader = cls(catalog, limit)
        return loader if loader.capacity >= 1 and loader.deferred else None

    def round_tools(self) -> list[dict]:
        """Start a model round and return the definitions it declares.
        A tool loaded earlier in the same response stays refused, as its call lacked the
        definition.
        """
        self._pinned.clear()
        tools = [self.loader_tool(), *self.starting, *(self._by_name[name] for name in self._loaded)]
        self._declared = frozenset(tool["name"] for tool in tools)
        return tools

    def loader_tool(self) -> dict:
        return {
            "name": AGENT_CHAT_LOAD_TOOLS_NAME,
            "description": ("Load deferred tools from the deferred tool directory so they can be called from the "
                            "next step. Give exact tool names, or one group to load all of its tools. Loading "
                            "grants no permission; every call is still checked."),
            "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
                "names": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "group": {"type": "string", "enum": list(self.groups)},
            }},
        }

    def directory(self) -> str:
        """The system-prompt section listing every deferred tool."""
        lines = [
            "\n\nDeferred tools: this provider accepts at most "
            f"{self.limit} tools in one request, so these tools of this access key are not loaded yet. Call "
            f"{AGENT_CHAT_LOAD_TOOLS_NAME} with exact names, or one group, before calling any of them. At most "
            f"{self.capacity} deferred tools stay loaded; loading more unloads the least recently used ones. "
            "The rule that a tool absent from the list is unavailable does not apply to the tools listed here.",
        ]
        for group, names in self.groups.items():
            lines.append(f"\n[{group}] {AGENT_CHAT_DEFERRED_TOOL_GROUPS[group][2]}")
            lines += [f"- {name}: {_first_sentence(self._by_name[name].get('description'))}" for name in names]
        return "\n".join(lines)

    def blocked(self, name: str) -> str | None:
        """The refusal for a call to a deferred tool this round did not declare, else None."""
        if name in self.deferred and name not in self._declared:
            return f"{name} is not loaded. Load it with {AGENT_CHAT_LOAD_TOOLS_NAME} first; nothing was run."
        return None

    def use(self, name: str) -> None:
        if name in self._loaded:
            self._loaded.move_to_end(name)

    def load(self, arguments: Any) -> tuple[str, bool]:
        """Load what fits of the requested tools; return the result text and whether it is an error."""
        names = arguments.get("names") if isinstance(arguments, dict) else None
        group = arguments.get("group") if isinstance(arguments, dict) else None
        if (not isinstance(arguments, dict) or set(arguments) - {"names", "group"} or (names is None and group is None)
                or (names is not None and (not isinstance(names, list) or not all(isinstance(n, str) for n in names)))
                or (group is not None and group not in self.groups)):
            return (f"Give names (a list of tool names from the deferred directory) and/or group (one of: "
                    f"{', '.join(self.groups)}). Nothing was loaded."), True
        body: dict[str, Any] = {}
        requested = list(dict.fromkeys(names or []))
        if group is not None:
            members = [name for name in self.groups[group] if name not in self._loaded]
            if len(members) > self._room():
                body["group_not_loaded"] = group
                body["note"] = (f"The {group} group has {len(members)} tools that are not loaded, more than fit now. "
                                "Load only the names you need.")
            else:
                requested += [name for name in members if name not in requested]
        body["already_available"] = [n for n in requested
                                     if (n in self._by_name and n not in self.deferred) or n in self._loaded]
        body["unknown"] = [n for n in requested if n not in self._by_name]
        loaded, evicted, refused = [], [], []
        for name in (n for n in requested if n in self.deferred and n not in self._loaded):
            if len(self._loaded) >= self.capacity:
                victim = next((n for n in self._loaded if n not in self._pinned), None)
                if victim is None:
                    refused.append(name)
                    continue
                del self._loaded[victim]
                evicted.append(victim)
            self._loaded[name] = None
            self._pinned.add(name)
            loaded.append(name)
        body["loaded"] = loaded
        body["required_arguments"] = {name: list((self._by_name[name].get("inputSchema") or {}).get("required") or [])
                                      for name in loaded}
        body["unloaded"] = evicted
        body["not_loaded"] = refused
        if evicted:
            body["unloaded_note"] = "Unloaded tools must be loaded again before use."
        if refused:
            body["not_loaded_note"] = (f"At most {self.capacity} deferred tools fit, and tools loaded in this step "
                                       "are kept. Use the loaded tools first or load fewer.")
        if body["unknown"]:
            body["unknown_note"] = "These names are not tools of this access key."
        if loaded:
            body["loaded_note"] = "Call the loaded tools from the next step, with the required arguments listed."
        result = {key: value for key, value in body.items() if value}
        return json.dumps(result, ensure_ascii=False), not loaded and not body["already_available"]

    def _room(self) -> int:
        """Slots a load in this round can still fill, counting evictable tools."""
        return self.capacity - len(self._loaded) + sum(1 for name in self._loaded if name not in self._pinned)
