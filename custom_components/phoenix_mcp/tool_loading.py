"""Tool-level loading: Phoenix's own loops declare a catalog in parts.

A turn declares a small core, the client-local load_tools function and the deferred tools
loaded so far, with a directory of the rest in the system prompt. It applies when the
provider account asks for it (automatic above a catalog size) and always past a provider's
tools-array cap. Nothing is hidden or granted: the directory lists only the access key's own
tools, and every call still goes through the shared dispatcher. External clients keep the
complete catalog.
"""

from __future__ import annotations

from collections import OrderedDict
import json
import re
from typing import Any

from .provider_constants import (
    AGENT_CHAT_CORE_TOOLS, AGENT_CHAT_LOAD_TOOLS_NAME, AGENT_CHAT_TOOL_GROUPS, AGENT_CHAT_TOOL_LOADING_MIN_BYTES,
    AGENT_CHAT_TOOL_SEARCH_BLOCKS, AGENT_CHAT_TOOL_SEARCH_MODELS, AGENT_CHAT_TOOL_SEARCH_TOOL,
    AGENT_CHAT_VOICE_PRELOADED_GROUPS,
)


def tool_group(name: str) -> str | None:
    """The deferred group a tool belongs to, or None for the starting set.

    Exact names win over fragments; core tools and tools matching nothing are never deferred.
    """
    if name in AGENT_CHAT_CORE_TOOLS:
        return None
    for group, (members, _summary) in AGENT_CHAT_TOOL_GROUPS.items():
        if name in members:
            return group
    for group, (members, _summary) in AGENT_CHAT_TOOL_GROUPS.items():
        if any(fragment in name for fragment in members):
            return group
    return None


def catalog_bytes(catalog: list[dict]) -> int:
    """The catalog's compact JSON size, the measure the automatic threshold uses."""
    return len(json.dumps(catalog, separators=(",", ":"), ensure_ascii=False).encode())


def history_tool_calls(messages: list[dict]) -> list[tuple[str, Any]]:
    """Every tool call in a provider history as (name, arguments), in order.

    Reads Anthropic tool_use blocks and OpenAI-shaped tool_calls; anything else is skipped.
    """
    calls: list[tuple[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, list):
            calls += [(str(block.get("name") or ""), block.get("input")) for block in content
                      if isinstance(block, dict) and block.get("type") == "tool_use"]
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = None
            calls.append((str(function.get("name") or ""), arguments))
    return calls


def _first_sentence(description: Any) -> str:
    """The first sentence of a tool description, which is what the directory shows."""
    text = str(description or "").strip()
    match = re.match(r"(.+?[.!?])(\s|$)", text, re.S)
    return (match.group(1) if match else text).replace("\n", " ")


class ToolLoader:
    """One turn's declared catalog: a starting set, load_tools and the loaded deferred tools.

    Under a provider's tools-array cap, loaded tools are evicted least recently used first, but
    never in the round that loaded them: two loads in one round cannot push out each other's tools
    before the model has seen either, so the overflow is refused instead. Without a cap nothing is
    evicted.
    """

    def __init__(self, catalog: list[dict], limit: int | None, preload: tuple[str, ...] = ()) -> None:
        self.limit = limit
        self._by_name = {tool["name"]: tool for tool in catalog}
        members: dict[str, list[str]] = {group: [] for group in AGENT_CHAT_TOOL_GROUPS}
        self.starting: list[dict] = []
        for tool in catalog:
            group = tool_group(tool["name"])
            if group is None or group in preload:
                self.starting.append(tool)
            else:
                members[group].append(tool["name"])
        self.groups = {group: names for group, names in members.items() if names}
        self.deferred = frozenset(name for names in self.groups.values() for name in names)
        self.capacity: int | None = None if limit is None else limit - len(self.starting) - 1
        self._loaded: OrderedDict[str, None] = OrderedDict()
        self._pinned: set[str] = set()
        self._declared: frozenset[str] = frozenset()

    @classmethod
    def plan(cls, catalog: list[dict], limit: int | None, *, mode: str = "auto", surface: str = "chat",
             exempt: bool = False) -> ToolLoader | None:
        """A loader for this turn, or None to declare the complete catalog.

        Past the provider's cap it always loads, since the complete catalog would be refused. Otherwise
        "never" and exempt turns never load, "always" loads, and "auto" loads from the size threshold.
        """
        over_cap = limit is not None and len(catalog) > limit
        if not over_cap and (exempt or mode == "never"
                             or (mode != "always" and catalog_bytes(catalog) < AGENT_CHAT_TOOL_LOADING_MIN_BYTES)):
            return None
        preload = AGENT_CHAT_VOICE_PRELOADED_GROUPS if surface == "voice" and not over_cap else ()
        loader = cls(catalog, limit, preload)
        return loader if loader.deferred and (loader.capacity is None or loader.capacity >= 1) else None

    def restore(self, messages: list[dict]) -> None:
        """Load again what earlier turns of this conversation loaded or called, oldest first.

        A loaded tool stays available for the conversation, so a follow-up needs no second load.
        """
        names: list[str] = []
        for name, arguments in history_tool_calls(messages):
            if name == AGENT_CHAT_LOAD_TOOLS_NAME and isinstance(arguments, dict):
                requested = arguments.get("names")
                names += [n for n in requested if isinstance(n, str)] if isinstance(requested, list) else []
                for group in self._requested_groups(arguments) or ():
                    names += self.groups.get(group, [])
            elif name in self.deferred:
                names.append(name)
        for name in dict.fromkeys(n for n in names if n in self.deferred):
            if self.capacity is not None and len(self._loaded) >= self.capacity:
                self._loaded.popitem(last=False)
            self._loaded[name] = None

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
            "description": ("Load tools from the list of tools not loaded yet so they can be called from the "
                            "next step. Give exact tool names, or groups to load all of their tools. Loading "
                            "grants no permission; every call is still checked."),
            "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
                "names": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "groups": {"type": "array", "items": {"type": "string", "enum": list(self.groups)}, "minItems": 1},
            }},
        }

    def directory(self) -> str:
        """The system-prompt section listing every deferred tool by group."""
        capped = ""
        if self.capacity is not None and self.limit is not None:
            capped = (f" This provider accepts at most {self.limit} tools in one request, so at most "
                      f"{self.capacity} of these stay loaded; loading more unloads the least recently used ones.")
        lines = [
            "\n\nTools not loaded yet: to keep each request small, these tools of this access key are listed "
            f"here but not loaded. Call {AGENT_CHAT_LOAD_TOOLS_NAME} with exact names, or groups, before calling "
            "any of them, and load what a request needs before saying it cannot be done. Loaded tools stay "
            f"available for the rest of the conversation.{capped} The rule that a tool absent from the list "
            "is unavailable does not apply to the tools listed here.",
        ]
        for group, names in self.groups.items():
            lines.append(f"\n[{group}] {AGENT_CHAT_TOOL_GROUPS[group][1]}")
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

    def _requested_groups(self, arguments: dict) -> list[str] | None:
        """The groups a load names, from groups (a list) or the older group (one name); None if malformed."""
        groups = arguments.get("groups")
        single = arguments.get("group")
        if groups is None and single is None:
            return []
        if groups is not None and (not isinstance(groups, list) or not all(isinstance(g, str) for g in groups)):
            return None
        if single is not None and not isinstance(single, str):
            return None
        return list(dict.fromkeys([*(groups or []), *([single] if single is not None else [])]))

    def load(self, arguments: Any) -> tuple[str, bool]:
        """Load what fits of the requested tools; return the result text and whether it is an error."""
        names = arguments.get("names") if isinstance(arguments, dict) else None
        groups = self._requested_groups(arguments) if isinstance(arguments, dict) else None
        if (not isinstance(arguments, dict) or set(arguments) - {"names", "groups", "group"} or groups is None
                or (names is None and not groups)
                or (names is not None and (not isinstance(names, list) or not all(isinstance(n, str) for n in names)))
                or any(group not in self.groups for group in groups)):
            return (f"Give names (a list of tool names from the list of tools not loaded yet) and/or groups (from: "
                    f"{', '.join(self.groups)}). Nothing was loaded."), True
        body: dict[str, Any] = {}
        requested = list(dict.fromkeys(names or []))
        refused_groups = []
        for group in groups:
            members = [name for name in self.groups[group] if name not in self._loaded and name not in requested]
            if self.capacity is not None and len(members) > self._room():
                refused_groups.append(group)
            else:
                requested += members
        if refused_groups:
            body["groups_not_loaded"] = refused_groups
            body["note"] = ("These groups have more tools than fit now. Load only the names you need.")
        body["already_available"] = [n for n in requested
                                     if (n in self._by_name and n not in self.deferred) or n in self._loaded]
        body["unknown"] = [n for n in requested if n not in self._by_name]
        loaded, evicted, refused = [], [], []
        for name in (n for n in requested if n in self.deferred and n not in self._loaded):
            if self.capacity is not None and len(self._loaded) >= self.capacity:
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
        """Slots a load in this round can still fill, counting evictable tools; unbounded without a cap."""
        if self.capacity is None:
            return len(self.deferred)
        return self.capacity - len(self._loaded) + sum(1 for name in self._loaded if name not in self._pinned)


def without_tool_search(messages: list[dict]) -> list[dict]:
    """A copy without Anthropic tool search blocks, for a turn that does not use tool search.

    Any other provider, and Claude without the search tool declared, would refuse them.
    """
    out = []
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(
                isinstance(block, dict) and block.get("type") in AGENT_CHAT_TOOL_SEARCH_BLOCKS for block in content):
            message = {**message, "content": [block for block in content if not (
                isinstance(block, dict) and block.get("type") in AGENT_CHAT_TOOL_SEARCH_BLOCKS)]}
        out.append(message)
    return out


class ToolSearch:
    """One turn's catalog for Anthropic's server-side tool search.

    The starting set is declared as usual and every deferred tool with defer_loading, beside the
    search tool, so the tools array is the same on every request of a conversation. A short fixed
    note names what the deferred tools cover, since a model that does not know they exist does not
    search for them.
    """

    def __init__(self, catalog: list[dict], preload: tuple[str, ...] = ()) -> None:
        groups: dict[str, list[str]] = {}
        for tool in catalog:
            group = tool_group(tool["name"])
            if group is not None and group not in preload:
                groups.setdefault(group, []).append(tool["name"])
        self.groups = {group: groups[group] for group in AGENT_CHAT_TOOL_GROUPS if group in groups}
        self.deferred = frozenset(name for names in self.groups.values() for name in names)

    @classmethod
    def plan(cls, catalog: list[dict], kind: str, model: str, *, mode: str = "auto", surface: str = "chat",
             exempt: bool = False) -> ToolSearch | None:
        """Tool search for a Claude model that supports it, unless the account says never or the turn is exempt."""
        if kind != "claude" or mode == "never" or exempt or not re.match(AGENT_CHAT_TOOL_SEARCH_MODELS, model or ""):
            return None
        search = cls(catalog, AGENT_CHAT_VOICE_PRELOADED_GROUPS if surface == "voice" else ())
        return search if search.deferred else None

    def request_tools(self, formatted: list[dict]) -> list[dict]:
        """The provider-formatted catalog with the search tool first and deferred tools marked."""
        return [dict(AGENT_CHAT_TOOL_SEARCH_TOOL), *(
            {**tool, "defer_loading": True} if tool.get("name") in self.deferred else tool for tool in formatted)]

    def note(self) -> str:
        """The system-prompt note naming what the deferred tools cover."""
        covered = "; ".join(f"{group.replace('_', ' ')} ({AGENT_CHAT_TOOL_GROUPS[group][1].rstrip('.')})"
                            for group in self.groups)
        return ("\n\nMore tools of this access key are available through tool search: search for them before "
                f"saying something cannot be done. They cover: {covered}.")
