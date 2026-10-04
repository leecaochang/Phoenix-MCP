"""Archetype-based adversarial sweep over the whole tool surface.

Per-tool suites assert what ONE tool does with sensible input; this asserts what
EVERY tool does with hostile input. Tools and attacks are derived from the
published inputSchema, so a new tool is covered without writing a case. Attacks
are shapes real models emit: a list where a string was declared (HA's matcher
raises AttributeError on it), a JSON-encoded string where an object was
declared, and stray extra arguments.

_call_tool is called DIRECTLY, not through the dispatcher, on purpose: the
tools/call dispatcher's catch-all turns any exception into a clean "Internal
error", so a tool crashing on every input would still pass.

Invariants checked on every (tool, attack) pair:
1. It returns, rather than raising.
2. It returns the MCP content contract with a recognised audit outcome.
3. It never leaks a traceback or interpreter path into the response.
4. A FULLY DENIED access key learns nothing about its payload: each attack string
   carries a marker, and its return would make the error text a validator.
"""
from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import mcp_view, mesa_tools
from custom_components.phoenix_mcp.const import CAPABILITY_NAMES
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.access_key_store import GlobalSettings, PermissionTree, AccessKeyRecord
from custom_components.phoenix_mcp.tool_contracts import normalize_tool_args
from custom_components.phoenix_mcp.tools import config_files, discovery
from custom_components.phoenix_mcp.version_store import VersionStore

# Threaded through every attack string. Its absence from a denied response is
# invariant 4; making it distinctive keeps that check from matching prose.
MARKER = "PHXTORTURE"

VALID_OUTCOMES = frozenset({
    "allowed", "denied", "not_found", "rate_limited",
    "not_implemented", "invalid_request", "pending_approval",
})

# Unambiguous evidence that an internal failure reached the caller. Deliberately
# narrow: legitimate responses do carry config-relative paths and validation
# messages, and a broad "no path anywhere" rule would fire on those.
LEAK_MARKERS = ("Traceback (most recent call last)", "site-packages/homeassistant")


# ---------------------------------------------------------------- the surface

def _all_defs() -> list[dict]:
    return [
        *mcp_view._ENTITY_TOOL_DEFS,
        *mcp_view._NATIVE_TOOL_DEFS,
        *mcp_view._SYSTEM_TOOL_DEFS,
        *mesa_tools.mesa_tool_defs(),
    ]


def _archetype(tool_def: dict) -> str:
    """Classify a tool by the SHAPE of its declared input, derived from the schema."""
    props: dict = tool_def.get("inputSchema", {}).get("properties", {}) or {}
    names = set(props)
    if not names:
        return "parameterless"
    if names & {"area", "area_id", "device_id", "floor"}:
        return "bulk_target"
    if "entity_id" in names:
        return "entity_target"
    if {"config", "content", "card"} & names:
        return "config_blob_write"
    if any(n.endswith("_id") for n in names):
        return "optional_id"
    return "scalar_args"


# ------------------------------------------------------------------ the attacks

def _placeholder(name: str, schema: dict):
    """Build a recursively valid seed so one hostile field reaches its own boundary."""
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for choice_key in ("oneOf", "anyOf"):
        choices = schema.get(choice_key)
        if choices and not schema.get("type") and not schema.get("properties"):
            return _placeholder(name, choices[0])
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), kind[0])
    if kind == "object":
        properties = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])
        if schema.get("anyOf"):
            required.update(schema["anyOf"][0].get("required", []) or [])
        for clause in schema.get("allOf", []) or []:
            options = clause.get("anyOf", []) or []
            if options:
                required.update(options[0].get("required", []) or [])
            required.update(clause.get("required", []) or [])
        minimum = int(schema.get("minProperties", 0) or 0)
        if len(required) < minimum:
            required.update(list(properties)[:minimum])
        return {
            field: _placeholder(field, properties.get(field, {}))
            for field in properties
            if field in required
        }
    if kind == "array":
        count = max(0, int(schema.get("minItems", 0) or 0))
        return [_placeholder(name, schema.get("items", {})) for _ in range(count)]
    if kind == "boolean":
        return False
    if kind in ("integer", "number"):
        value = schema.get("minimum", 1)
        exclusive = schema.get("exclusiveMinimum")
        if isinstance(exclusive, (int, float)) and not isinstance(exclusive, bool):
            value = max(value, exclusive + 1)
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)):
            value = min(value, maximum)
        return int(value) if kind == "integer" else value
    if name == "entity_id":
        return "light.phx_ghost"
    if name.endswith("_id"):
        return "phx_ghost_id"
    value = "light.phx_ghost" if name == "entity_id" else f"{MARKER}_value"
    minimum = int(schema.get("minLength", 0) or 0)
    if len(value) < minimum:
        value += "x" * (minimum - len(value))
    maximum = schema.get("maxLength")
    return value[:maximum] if isinstance(maximum, int) else value


def _baseline(tool_def: dict) -> dict:
    """Use a semantic fixture when a schema omits a cross-field requirement."""
    if tool_def["name"] == "edit_energy_config":
        return {
            "operation": "remove_device",
            "target": {"kind": "statistic", "id": "sensor.phx_ghost"},
            "changes": {},
        }
    return _placeholder("arguments", tool_def.get("inputSchema", {}) or {})


def _hostile_values(kind: str | None) -> list:
    """Wrong-shaped values for a declared type, in the flavours real clients emit."""
    common = [None, 0, True, [], {}]
    if kind == "string":
        # A list where a string was declared crashes HA's name matcher (a model
        # targeting two entities by name sends one); the long string is the
        # unbounded-field case; the rest are traversal and template probes that
        # must be refused rather than interpreted.
        return common + [
            [f"{MARKER}_a", f"{MARKER}_b"],
            {"nested": MARKER},
            "",
            f"../../../etc/{MARKER}",
            "{{ states('person.someone') }}" + MARKER,
            f"{MARKER}\x00truncated",
            MARKER + "x" * 20000,
        ]
    if kind == "object":
        # A model that JSON-encodes an object it was told to send as an object.
        return common + [f'{{"json_as_string": "{MARKER}"}}', [{"k": MARKER}], f"{MARKER}"]
    if kind == "array":
        return common + [f"{MARKER}_not_a_list", {"0": MARKER}]
    if kind == "boolean":
        return [None, f"{MARKER}", 2, [], {}]
    if kind in ("integer", "number"):
        return [None, f"{MARKER}", [], {}, True, -1, 10**18]
    return common + [f"{MARKER}"]


def _attacks(tool_def: dict) -> list[tuple[str, dict]]:
    """Every (label, arguments) pair to throw at one tool."""
    schema = tool_def.get("inputSchema", {}) or {}
    props: dict = schema.get("properties", {}) or {}
    required: list[str] = schema.get("required", []) or []
    base = _baseline(tool_def)

    cases: list[tuple[str, dict]] = [("empty", {})]
    # A stray argument must not hard-fail: unknown keys are tolerated by design,
    # because weaker models emit stray fields and refusing them hurts task
    # completion without protecting anything.
    cases.append(("unknown_key", {**base, f"{MARKER}_unknown": MARKER}))
    if required:
        # Every required field present but every one of them nonsense.
        cases.append(("all_required_wrong_type", {
            name: _hostile_values(props.get(name, {}).get("type"))[0] for name in required
        }))
    for name, spec in props.items():
        for value in _hostile_values(spec.get("type")):
            cases.append((f"{name}={type(value).__name__}", {**base, name: value}))
        for label, value in _nested_attacks(spec):
            cases.append((f"{name}.{label}", {**deepcopy(base), name: value}))
    return cases


def _nested_attacks(schema: dict) -> list[tuple[str, object]]:
    """Exercise each declared nested field, including a malformed later row."""
    attacks: list[tuple[str, object]] = []
    if schema.get("type") == "object" and schema.get("properties"):
        seed = _placeholder("nested", schema)
        attacks.append(("unknown", {**seed, f"{MARKER}_unknown": MARKER}))
        for name, spec in schema["properties"].items():
            if name in schema.get("required", []):
                attacks.append((f"missing_{name}", {key: value for key, value in seed.items() if key != name}))
            for value in _hostile_values(spec.get("type")):
                attacks.append((f"{name}={type(value).__name__}", {**seed, name: value}))
            for label, value in _nested_attacks(spec):
                attacks.append((f"{name}.{label}", {**seed, name: value}))
    elif schema.get("type") == "array":
        spec = schema.get("items", {})
        seed_item = _placeholder("nested", spec)
        for value in _hostile_values(spec.get("type")):
            attacks.append((f"later_row={type(value).__name__}", [deepcopy(seed_item), value]))
        for label, value in _nested_attacks(spec):
            attacks.append((f"later_row.{label}", [deepcopy(seed_item), value]))
    return attacks


# ------------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def isolated_config_dir(hass: HomeAssistant, tmp_path):
    """Anything that reaches a file write lands in a throwaway dir, not the shared config."""
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text(
        "automation: !include automations.yaml\nscript: !include scripts.yaml\n",
        encoding="utf-8",
    )
    return tmp_path


def _data() -> PhoenixData:
    store = MagicMock()
    store.get_settings.return_value = GlobalSettings()
    return PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())


def _access_key(mode: str) -> AccessKeyRecord:
    """An access key whose every capability is `mode`, with an EMPTY permission tree.

    No entity is ever in scope, so nothing real is actuated.
    """
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="torture", access_key_hash="x", created_at=utcnow(),
        created_by="admin", permissions=PermissionTree(domains={}),
        **{cap: mode for cap in CAPABILITY_NAMES},
    )


def _assert_contract(result, outcome, resource, label: str) -> str:
    """Invariants 2 and 3. Returns the response text for further assertions."""
    assert isinstance(result, dict), f"{label}: returned {type(result).__name__}, not a dict"
    content = result.get("content")
    assert isinstance(content, list) and content, f"{label}: no content list"
    texts = []
    for item in content:
        assert item.get("type") == "text", f"{label}: content item is not text"
        assert isinstance(item.get("text"), str), f"{label}: content text is not a string"
        texts.append(item["text"])
    assert outcome in VALID_OUTCOMES, f"{label}: unrecognised outcome {outcome!r}"
    assert isinstance(resource, str), f"{label}: resource is not a string"
    joined = "\n".join(texts)
    for leak in LEAK_MARKERS:
        assert leak not in joined, f"{label}: leaked internals into the response ({leak})"
    return joined


# ---------------------------------------------------------------------- tests

ALL_DEFS = _all_defs()
BY_ARCHETYPE: dict[str, list[dict]] = {}
for _d in ALL_DEFS:
    BY_ARCHETYPE.setdefault(_archetype(_d), []).append(_d)

# Tools whose EXECUTION is not something a test run should trigger. Taken from
# the tool's own destructiveHint rather than a hand-kept list, so a new
# destructive tool excludes itself. They still get the denied-access-key pass below,
# which is where the uniform-refusal invariant lives anyway.
EXECUTABLE_DEFS = [d for d in ALL_DEFS if not d.get("annotations", {}).get("destructiveHint")]


@pytest.mark.parametrize("tool_def", ALL_DEFS, ids=lambda item: item["name"])
def test_every_adversarial_baseline_normalizes(tool_def):
    _, error = normalize_tool_args(tool_def["name"], _baseline(tool_def))
    assert error is None


@pytest.mark.parametrize(
    "tool_name",
    [
        "get_state",
        "get_states",
        "call_service",
        "dry_run_service",
        "get_relationships",
        "patch_yaml_config",
    ],
)
def test_structured_adversarial_baseline_passes_normalization(tool_name: str):
    """Positive control: each structured family's seed passes normalization, so its sweep bites."""
    tool_def = next(item for item in ALL_DEFS if item["name"] == tool_name)
    schema = tool_def["inputSchema"]
    seed = _placeholder("arguments", schema)

    _, error = normalize_tool_args(tool_name, seed)

    assert error is None


async def test_structured_get_state_seed_reaches_permission_resolution(
    hass: HomeAssistant,
):
    """Positive control: the seed reaches permission resolution, not an early validation failure."""
    tool_def = next(item for item in ALL_DEFS if item["name"] == "get_state")
    properties = tool_def["inputSchema"]["properties"]
    seed = {name: _placeholder(name, spec) for name, spec in properties.items()}
    resolver = MagicMock(return_value=mcp_view.Permission.NOT_FOUND)

    with patch.object(mcp_view, "resolve", resolver):
        result, outcome, resource = await _call_tool(
            "get_state", seed, _access_key("allow"), hass, _data(), "req", "1.2.3.4"
        )

    resolver.assert_called_once_with(seed["entity_id"], ANY, hass)
    _assert_contract(result, outcome, resource, "get_state/positive-control")


async def test_structured_get_states_seed_reaches_scope_filter(
    hass: HomeAssistant,
):
    tool_def = next(item for item in ALL_DEFS if item["name"] == "get_states")
    seed = _placeholder("arguments", tool_def["inputSchema"])
    scope_filter = MagicMock(return_value=[])

    with patch.object(mcp_view, "filter_entities_for_access_key", scope_filter):
        await _call_tool("get_states", seed, _access_key("allow"), hass, _data())

    scope_filter.assert_called_once()


async def test_structured_call_service_seed_reaches_executor(hass: HomeAssistant):
    tool_def = next(item for item in ALL_DEFS if item["name"] == "call_service")
    seed = _placeholder("arguments", tool_def["inputSchema"])
    seed["targets"] = [{"kind": "entity", "ids": ["light.test"]}]
    expected = ({"content": [{"type": "text", "text": "reached"}]}, "allowed", "service")
    executor = AsyncMock(return_value=expected)

    with patch.object(mcp_view, "_execute_call_service", executor):
        result = await _call_tool("call_service", seed, _access_key("allow"), hass, _data())

    assert result == expected
    executor.assert_awaited_once()


async def test_structured_dry_run_seed_reaches_target_resolution(
    hass: HomeAssistant,
):
    tool_def = next(item for item in ALL_DEFS if item["name"] == "dry_run_service")
    seed = _placeholder("arguments", tool_def["inputSchema"])
    seed["targets"] = [{"kind": "entity", "ids": ["light.test"]}]
    resolver = MagicMock(return_value=([], 0))

    with patch.object(discovery, "resolve_service_targets", resolver):
        await _call_tool("dry_run_service", seed, _access_key("allow"), hass, _data())

    resolver.assert_called_once()


async def test_structured_relationship_seed_reaches_scope_resolution(
    hass: HomeAssistant,
):
    tool_def = next(item for item in ALL_DEFS if item["name"] == "get_relationships")
    seed = _placeholder("arguments", tool_def["inputSchema"])
    expected = ({"content": [{"type": "text", "text": "reached"}]}, "not_found", "scope")
    resolver = MagicMock(return_value=expected)

    with patch.object(discovery, "_relationship_scope", resolver):
        result = await _call_tool("get_relationships", seed, _access_key("allow"), hass, _data())

    assert result == expected
    resolver.assert_called_once()


async def test_structured_yaml_patch_seed_reaches_target_resolution(
    hass: HomeAssistant,
):
    tool_def = next(item for item in ALL_DEFS if item["name"] == "patch_yaml_config")
    seed = _placeholder("arguments", tool_def["inputSchema"])

    with patch.object(
        config_files, "_yaml_write_target", side_effect=RuntimeError("target reached")
    ):
        with pytest.raises(RuntimeError, match="target reached"):
            await _call_tool(
                "patch_yaml_config", seed, _access_key("allow"), hass, _data()
            )


async def test_get_state_attack_reaches_deliberately_broken_resolver(
    hass: HomeAssistant,
):
    """Mutation control: unrelated seed fields cannot mask the resolver boundary."""
    tool_def = next(item for item in ALL_DEFS if item["name"] == "get_state")
    attack = next(
        args
        for _, args in _attacks(tool_def)
        if str(args.get("entity_id", "")).startswith("../../../etc/")
    )

    with patch.object(mcp_view, "resolve", side_effect=RuntimeError("mutation reached")):
        with pytest.raises(RuntimeError, match="mutation reached"):
            await _call_tool(
                "get_state", attack, _access_key("allow"), hass, _data(), "req", "1.2.3.4"
            )


def test_every_tool_falls_into_a_known_archetype():
    # A tool whose shape matches nothing would silently get no torture at all.
    assert set(BY_ARCHETYPE) <= {
        "parameterless", "entity_target", "bulk_target",
        "config_blob_write", "optional_id", "scalar_args",
    }
    assert sum(len(v) for v in BY_ARCHETYPE.values()) == len(ALL_DEFS)
    # Each archetype must actually have members, or the suite is quietly testing
    # fewer shapes than it claims to.
    for name in ("parameterless", "entity_target", "config_blob_write"):
        assert BY_ARCHETYPE.get(name), f"no tools classified as {name}"

@pytest.mark.parametrize("tool_def", ALL_DEFS, ids=lambda d: d["name"])
async def test_denied_access_key_survives_hostile_input(tool_def: dict, hass: HomeAssistant):
    """Invariants 1-3 against an access key holding nothing."""
    access_key, data, name = _access_key("deny"), _data(), tool_def["name"]
    for label, args in _attacks(tool_def):
        result, outcome, resource = await _call_tool(name, args, access_key, hass, data, "req", "1.2.3.4")
        _assert_contract(result, outcome, resource, f"{name}/{label}")


# Only a CAP-gated tool owes the uniform refusal. A capless tool (the native
# Hass* family) is scoped by entity and validates its arguments first, and its
# "'x' is not of type 'integer'" is a statement about the caller's own input,
# not about the instance; the same reasoning lets ServiceValidationError text
# through so an agent can self-correct.
CAP_GATED_DEFS = [d for d in ALL_DEFS if mcp_view._tool_caps(d)]


@pytest.mark.parametrize("tool_def", CAP_GATED_DEFS, ids=lambda d: d["name"])
async def test_cap_denied_tool_never_echoes_the_payload(tool_def: dict, hass: HomeAssistant):
    """Invariant 4, the security one.

    An access key whose capability is denied must not be able to use the tool's error
    messages as a validator for its own input, so the cap-deny check runs BEFORE
    any precheck; otherwise it could ask "would this have worked?" and read the
    answer off the error text.
    """
    access_key, data, name = _access_key("deny"), _data(), tool_def["name"]
    for label, args in _attacks(tool_def):
        result, _outcome, _resource = await _call_tool(name, args, access_key, hass, data, "req", "1.2.3.4")
        text = "\n".join(i["text"] for i in result["content"])
        assert MARKER not in text, (
            f"{name}/{label}: an access key denied {mcp_view._tool_caps(tool_def)} got its own payload "
            f"echoed back, so the error text is a validity oracle: {text[:300]}"
        )


@pytest.mark.parametrize("tool_def", EXECUTABLE_DEFS, ids=lambda d: d["name"])
async def test_permitted_access_key_survives_hostile_input(tool_def: dict, hass: HomeAssistant):
    """Invariants 1-3 with every capability granted.

    The attack reaches the tool's own argument handling instead of stopping at the
    cap gate.
    """
    access_key, data, name = _access_key("allow"), _data(), tool_def["name"]
    for label, args in _attacks(tool_def):
        result, outcome, resource = await _call_tool(name, args, access_key, hass, data, "req", "1.2.3.4")
        _assert_contract(result, outcome, resource, f"{name}/{label}")


@pytest.mark.parametrize("tool_def", EXECUTABLE_DEFS, ids=lambda d: d["name"])
async def test_concurrent_identical_calls_do_not_break_each_other(
    tool_def: dict, hass: HomeAssistant,
):
    """Two identical calls in flight at once (the double-delete or racing-writer shape).

    An agent retrying a slow tool produces this. Neither may raise; gather is given
    no return_exceptions so a raise fails the test rather than hiding in a result
    list.
    """
    access_key, data, name = _access_key("allow"), _data(), tool_def["name"]
    args = _baseline(tool_def)
    outcomes = await asyncio.gather(*(
        _call_tool(name, dict(args), access_key, hass, data, f"req{i}", "1.2.3.4") for i in range(2)
    ))
    for i, (result, outcome, resource) in enumerate(outcomes):
        _assert_contract(result, outcome, resource, f"{name}/concurrent-{i}")


async def test_unknown_arguments_are_tolerated_not_rejected(hass: HomeAssistant):
    """A stray argument must not turn a valid call into an error.

    Pinned separately from the sweep because tolerating unknown arguments is a
    DELIBERATE decision (no per-tool additionalProperties: false) that reads like
    an oversight, and the sweep only asserts the call survives, not that it
    succeeds.
    """
    access_key, data = _access_key("allow"), _data()
    hass.states.async_set("light.kitchen", "on")
    clean, _, _ = await _call_tool(
        "get_states", {"projection": {"kind": "compact"}}, access_key, hass, data
    )
    dirty, outcome, _ = await _call_tool(
        "get_states",
        {
            "projection": {"kind": "compact"},
            f"{MARKER}_unknown": MARKER,
            "nonsense": [1, 2],
        },
        access_key,
        hass,
        data,
    )
    assert outcome == "allowed"
    assert dirty["content"][0]["text"] == clean["content"][0]["text"]
