"""Pin the enumerable parts of the docs site against the code.

Documentation drift is mostly enumerable facts (tool parameters, provider
kinds, error codes, SSE frame names, routes, capability lists) restated by hand
and left behind by code changes. Each expectation is derived from the code side,
so a new tool, provider, code, frame, route or capability is covered without a
new test. Extraction-based checks assert a floor on what they extracted, so a
broken extraction fails loudly instead of passing vacuously.
"""

from __future__ import annotations

import ast
import pathlib
import html as html_parser
import json
import re

import pytest

from custom_components.phoenix_mcp.const import (
    AGENTCLI_PROVIDERS,
    CAPABILITY_NAMES,
)
from custom_components.phoenix_mcp.mcp_view import (
    _ENTITY_TOOL_DEFS,
    _NATIVE_TOOL_DEFS,
    _SYSTEM_TOOL_DEFS,
    tool_catalog_counts,
)
from custom_components.phoenix_mcp.mesa_tools import mesa_tool_defs
from custom_components.phoenix_mcp.tool_defs import _NATIVE_TOOL_NAMES

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
PKG = ROOT / "custom_components" / "phoenix_mcp"


def _all_defs() -> list[dict]:
    return list(_ENTITY_TOOL_DEFS) + list(_NATIVE_TOOL_DEFS) + list(_SYSTEM_TOOL_DEFS) + list(mesa_tool_defs())


def _tools_articles() -> dict[str, str]:
    """Map each tool name to the <article> block documenting it.

    An article may document several tools (the ESPHome firmware family shares
    one), so multiple names can map to the same block.
    """
    html = (DOCS / "tools.html").read_text(encoding="utf-8")
    articles: dict[str, str] = {}
    for block in re.findall(r"<article\b.*?</article>", html, re.DOTALL):
        for name in re.findall(r'<span class="tool-name">(\w+)</span>', block):
            articles[name] = block
    return articles


def test_every_tool_has_an_article() -> None:
    articles = _tools_articles()
    missing = sorted({d["name"] for d in _all_defs()} - set(articles))
    assert missing == [], f"tools.html has no article for: {missing}"
    assert len(articles) >= tool_catalog_counts()["total"]


def test_every_required_param_is_named_in_its_article() -> None:
    """A parameter the schema REQUIRES must appear in the tool's article.

    Otherwise a reader of the docs could not produce a valid call.
    """
    articles = _tools_articles()
    gaps: list[str] = []
    for d in _all_defs():
        required = (d.get("inputSchema") or {}).get("required") or []
        block = articles.get(d["name"], "")
        for param in required:
            if not re.search(rf"\b{re.escape(param)}\b", block):
                gaps.append(f"{d['name']}: required param '{param}' not in its article")
    assert gaps == [], "\n".join(gaps)


def test_required_params_are_marked_required_in_single_tool_articles() -> None:
    """Naming a required parameter is not enough; the article must SAY it is required.

    Scoped to articles documenting exactly ONE tool: a shared article covers
    several tools whose required sets differ, so "required" cannot be attributed
    mechanically there and the prose has to carry it (a manual docs
    verification pass covers those).
    """
    articles = _tools_articles()
    shared = {block for name, block in articles.items()
              if sum(1 for n in articles if articles[n] is block) > 1}
    gaps: list[str] = []
    for d in _all_defs():
        required = (d.get("inputSchema") or {}).get("required") or []
        block = articles.get(d["name"], "")
        if not required or block in shared:
            continue
        # Tags become SPACES, not nothing: "<dt>params</dt><dd><code>entity_id"
        # would otherwise collapse to "paramsentity_id" and lose the word
        # boundary. A tag-aware window is wrong too, since "<code>domain</code>
        # and <code>service</code> (both required)" is a correct marking whose
        # markup would read as distance.
        text = re.sub(r"<[^>]+>", " ", block)
        for param in required:
            # The marker must sit in the same sentence, within a short window
            # after the parameter, so an unrelated "required" elsewhere in the
            # article cannot satisfy it.
            if not re.search(rf"\b{re.escape(param)}\b[^.]{{0,60}}required", text):
                gaps.append(
                    f"{d['name']}: '{param}' is required by the schema but its article"
                    " never marks it required"
                )
    assert gaps == [], "\n".join(gaps)


def test_native_target_any_of_is_described_as_required() -> None:
    """Detailed native articles must not call schema-required targets optional."""
    target_fields = {"name", "area", "floor", "domain", "device_class"}
    articles = _tools_articles()
    gaps: list[str] = []
    for tool in _NATIVE_TOOL_DEFS:
        schema = tool.get("inputSchema") or {}
        clauses = [schema, *schema.get("allOf", [])]
        has_target_requirement = any(
            {
                name
                for option in clause.get("anyOf", [])
                for name in option.get("required", [])
            }
            and {
                name
                for option in clause.get("anyOf", [])
                for name in option.get("required", [])
            }.issubset(target_fields)
            for clause in clauses
        )
        if has_target_requirement and "at least one" not in articles[tool["name"]].lower():
            gaps.append(f"{tool['name']}: target anyOf is not described as required")
    assert gaps == [], "\n".join(gaps)


def test_shared_volume_article_attributes_each_target_contract() -> None:
    """The two volume tools share prose but accept different target selectors."""
    articles = _tools_articles()
    by_name = {tool["name"]: tool for tool in _NATIVE_TOOL_DEFS}
    for tool_name in (
        _NATIVE_TOOL_NAMES["HassSetVolume"],
        _NATIVE_TOOL_NAMES["HassSetVolumeRelative"],
    ):
        schema = by_name[tool_name]["inputSchema"]
        expected = {
            name
            for option in schema["anyOf"]
            for name in option.get("required", [])
        }
        match = re.search(
            rf"<code>{re.escape(tool_name)}</code> requires at least one of (.*?)\.",
            articles[tool_name],
            re.DOTALL,
        )
        assert match is not None, f"{tool_name}: target requirement is not attributed"
        documented = set(re.findall(r"<code>(\w+)</code>", match.group(1)))
        assert documented == expected


def test_service_target_expansion_modes_are_explicit() -> None:
    """Refused omissions and explicit domain-wide selection stay distinct."""
    article = _tools_articles()["call_service"]
    plain = " ".join(re.sub(r"<[^>]+>", " ", article).lower().split())

    assert "omitting targets" in plain
    assert "omitting targets is refused" in plain
    assert "targetless services are not supported" in plain
    assert "phoenix refuses primary target keys in service.data" in plain
    assert 'kind: all' in plain
    assert "every entity in the service domain" in plain
    assert all(word in plain for word in ("configuration", "diagnostic", "hidden"))
    assert "permission filtering still applies in every mode" in plain
    assert "repeated groups of the same kind are merged" in plain
    assert "all cannot be combined with another target kind" in plain


# (tool, parameter) pairs whose enum values are deliberately not spelled out in
# the docs, each with the reason. An exemption claims that listing the values
# would make the page WORSE, a higher bar than "it would be tedious".
_ENUM_DOC_EXEMPT: dict[tuple[str, str], str] = {
    (_NATIVE_TOOL_NAMES["HassMediaSearchAndPlay"], "media_class"): (
        "Home Assistant's own 20-value media taxonomy (album/artist/episode/genre/"
        "podcast/season/track/tv_show/...). It is a value domain, not a set of "
        "behaviours: no reader is choosing between them from the docs, and listing "
        "twenty nouns would bury the one sentence that says what the tool does."
    ),
}


def test_every_enum_value_is_named_in_its_article() -> None:
    """An enum value a parameter accepts must appear in the tool's article.

    The required-parameter checks above do not notice a new operation added to an
    existing tool's `op` enum, yet an enum value names a distinct behaviour the
    tool DOES. Optional parameters are deliberately excluded (many narrow
    refinements that belong in the schema, not the prose). Matching is
    case-insensitive and only for string-only enums. Add an `_ENUM_DOC_EXEMPT`
    entry only with a written reason that listing the values would make the page
    worse.
    """
    articles = _tools_articles()
    gaps: list[str] = []
    checked = 0
    for d in _all_defs():
        props = ((d.get("inputSchema") or {}).get("properties") or {})
        block = articles.get(d["name"], "")
        for param, spec in props.items():
            if (d["name"], param) in _ENUM_DOC_EXEMPT:
                continue
            values = spec.get("enum") if isinstance(spec, dict) else None
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                continue
            for value in values:
                checked += 1
                if not re.search(rf"\b{re.escape(value)}\b", block, re.IGNORECASE):
                    gaps.append(
                        f"{d['name']}: '{param}' accepts '{value}' but its article never names it"
                    )
    assert gaps == [], "\n".join(gaps)
    # Floor, per the module docstring: a refactor that stops finding enums must
    # fail loudly rather than pass by checking nothing. Set below the real count
    # with room for a tool to be retired, but far above what a broken extraction
    # returns.
    assert checked >= 40, f"only {checked} enum values found; the extraction is probably broken"


def test_admin_api_documents_every_capability() -> None:
    html = (DOCS / "admin-api.html").read_text(encoding="utf-8")
    missing = sorted(c for c in CAPABILITY_NAMES if c not in html)
    assert missing == [], f"admin-api.html PATCH schema missing caps: {missing}"


def test_capabilities_page_documents_every_capability() -> None:
    html = (DOCS / "capabilities.html").read_text(encoding="utf-8")
    missing = sorted(c for c in CAPABILITY_NAMES if c not in html)
    assert missing == [], f"capabilities.html missing caps: {missing}"


def test_admin_api_documents_every_provider_kind() -> None:
    # <code>-wrapped, not bare substring: "meta" would otherwise be satisfied
    # by the word "metadata" anywhere on the page.
    html = (DOCS / "admin-api.html").read_text(encoding="utf-8")
    missing = sorted(k for k in AGENTCLI_PROVIDERS if f"<code>{k}</code>" not in html)
    assert missing == [], f"admin-api.html provider kind enum missing: {missing}"


def test_admin_api_documents_every_emitted_error_code() -> None:
    src = "\n".join((PKG / name).read_text(encoding="utf-8") for name in ("admin_view.py", "agentcli.py"))
    codes = set(re.findall(r'_err\(\s*"([a-z_]+)"', src))
    assert len(codes) >= 6, f"error-code extraction broke: {sorted(codes)}"
    html = (DOCS / "admin-api.html").read_text(encoding="utf-8")
    missing = sorted(c for c in codes if f"<code>{c}</code>" not in html)
    assert missing == [], f"admin-api.html error-code table missing: {missing}"


def test_admin_api_documents_every_sse_frame() -> None:
    src = (PKG / "agentcli.py").read_text(encoding="utf-8")
    frames = set(re.findall(r'emit\(\s*"([a-z_]+)"', src))
    assert len(frames) >= 10, f"SSE frame extraction broke: {sorted(frames)}"
    html = (DOCS / "admin-api.html").read_text(encoding="utf-8")
    missing = sorted(f for f in frames if f"<code>{f}</code>" not in html)
    assert missing == [], f"admin-api.html SSE frame list missing: {missing}"


@pytest.mark.parametrize("page,module_names", [
    ("operations.html", ("mcp_view", "skill_view")),
    ("admin-api.html", ("admin_view", "agentcli")),
])
def test_route_reference_matches_methods_and_paths(page, module_names) -> None:
    """A documented URL alone cannot pin an omitted DELETE or wrong verb."""
    import importlib
    from homeassistant.components.http import HomeAssistantView

    expected = set()
    for name in module_names:
        module = importlib.import_module(f"custom_components.phoenix_mcp.{name}")
        for cls in vars(module).values():
            if not isinstance(cls, type) or not issubclass(cls, HomeAssistantView) or cls.__module__ != module.__name__:
                continue
            url = cls.__dict__.get("url")
            if not isinstance(url, str):
                continue
            for method in ("get", "post", "put", "patch", "delete"):
                if method in cls.__dict__:
                    expected.add((method.upper(), re.sub(r"\{[^}]+\}", "{}", url)))
    assert len(expected) >= (4 if page == "operations.html" else 60)
    source = html_parser.unescape((DOCS / page).read_text())
    documented = {
        (method, re.sub(r"\{[^}]+\}", "{}", path))
        for methods, path in re.findall(r"\b((?:GET|POST|PUT|PATCH|DELETE)(?:/(?:GET|POST|PUT|PATCH|DELETE))*)\s+(/api/[\w/{}.-]+)", source)
        for method in methods.split("/")
    }
    assert expected <= documented, sorted(expected - documented)
    assert documented <= expected, sorted(documented - expected)


def test_capability_labels_and_persona_defaults_match_shipped_catalog() -> None:
    from custom_components.phoenix_mcp.personas import PERSONA_DEFINITIONS

    catalog = json.loads((PKG / "catalogs/en.json").read_text())["panel"]
    source = (DOCS / "capabilities.html").read_text()
    labels = dict((slug, html_parser.unescape(label)) for label, slug in re.findall(
        r'<span class="badge badge-cap">([^<]+)</span><br /><code class="cap-slug">(cap_\w+)</code>', source))
    assert labels == {name: catalog["caps"][name]["label"] for name in CAPABILITY_NAMES}
    defaults = {key: [html_parser.unescape(value) for value in (label, allow, confirm)]
                for key, label, allow, confirm in re.findall(
                    r'<tr data-persona-defaults="([^"]+)"><td>([^<]+)</td><td>([^<]+)</td><td>([^<]+)</td></tr>', source)}
    assert set(defaults) == set(PERSONA_DEFINITIONS)
    for key, caps in PERSONA_DEFINITIONS.items():
        assert defaults[key][0] == catalog["personas"][key]["label"]
        for index, mode in enumerate(("allow", "confirm"), 1):
            expected = sorted((catalog["caps"][cap]["label"] for cap, value in caps.items() if value == mode), key=str.casefold)
            assert defaults[key][index] == (", ".join(expected) or "None"), (key, mode)


def test_install_requirements_match_manifest_constraints() -> None:
    manifest = json.loads((PKG / "manifest.json").read_text())
    source = html_parser.unescape((DOCS / "install.html").read_text())
    for requirement in manifest["requirements"]:
        assert f"<code>{requirement}</code>" in source
        for readme in ROOT.glob("README*.md"):
            assert f"`{requirement}`" in readme.read_text(), readme.name


def test_connect_page_states_the_ha_version_floor() -> None:
    """Assist and AI Task are available at the supported base release."""
    from custom_components.phoenix_mcp.const import MIN_HA_VERSION

    major_minor = ".".join(MIN_HA_VERSION.split(".")[:2])
    html = (DOCS / "connect.html").read_text(encoding="utf-8")
    assert major_minor in html, (
        f"connect.html never mentions the supported HA {major_minor}+ base"
    )
    assert "Available on every supported Home Assistant version" in html


def _plain_page(name: str) -> str:
    return " ".join(html_parser.unescape(re.sub(r"<[^>]+>", " ", (DOCS / name).read_text())).split())


def test_tool_badges_use_current_capability_labels() -> None:
    catalog = json.loads((PKG / "catalogs/en.json").read_text())["panel"]["caps"]
    labels = {catalog[name]["label"] for name in CAPABILITY_NAMES}
    # Shared articles describe combined gates or the selected authoring family.
    labels.update({"Diagnostics + Radio management", "Diagnostics + System log read",
                   "System log read + Integration log levels", "Search & discovery or Configuration read",
                   "Family write", "Home Companion only"})
    badges = {html_parser.unescape(value) for value in re.findall(
        r'class="badge badge-cap">([^<]+)</span>', (DOCS / "tools.html").read_text())}
    assert len(badges) >= 30
    assert badges <= labels, badges - labels


def test_documented_mesa_baseline_and_default_match_runtime() -> None:
    from custom_components.phoenix_mcp.mesa_core import DOMAIN_SAFETY_BASELINE
    from custom_components.phoenix_mcp.token_store import GlobalSettings

    assert GlobalSettings().mesa_mode == "advisory"
    assert DOMAIN_SAFETY_BASELINE["light"] == "autonomous"
    assert DOMAIN_SAFETY_BASELINE["lock"] == DOMAIN_SAFETY_BASELINE["alarm_control_panel"] == "prohibited"
    assert all(mode == "confirm" for domain, mode in DOMAIN_SAFETY_BASELINE.items()
               if domain not in ("light", "lock", "alarm_control_panel"))
    mesa = _plain_page("mesa.html")
    assert "lights inherit an autonomous baseline, locks and alarm panels inherit prohibited baselines, and every other domain inherits a confirm baseline" in mesa
    assert "The default is advisory" in mesa
    assert "Advisory is the default" in _plain_page("index.html")
    assert "every other domain requires confirmation under enforced mode" in _plain_page("capabilities.html")


def test_audit_prose_keeps_master_off_and_administrator_provenance() -> None:
    operations = _plain_page("operations.html")
    assert "unless Disable all logging is enabled" in operations
    assert "pending approval records bypass the per-outcome switches but are suppressed by Disable all logging" in operations
    assert '"Never" disables the periodic flush; stop, reload and unload still attempt a final save' in operations
    for name in ("security.html", "admin-api.html"):
        page = _plain_page(name)
        assert "originating_token_id" in page
        assert "administrator as actor" in page
        assert "not the Phoenix audit store" not in page


def test_event_reference_covers_runtime_events_and_rate_limit_coalescing() -> None:
    source = (DOCS / "operations.html").read_text()
    events = set()
    for path in PKG.rglob("*.py"):
        if "mesa_core" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or not node.args:
                continue
            if node.func.attr == "async_fire":
                argument = node.args[0]
            elif (node.func.attr == "add_job" and len(node.args) >= 2
                  and isinstance(node.args[0], ast.Attribute) and node.args[0].attr == "async_fire"):
                argument = node.args[1]
            else:
                continue
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                event = argument.value
            elif (isinstance(argument, ast.JoinedStr) and len(argument.values) == 2
                  and isinstance(argument.values[0], ast.FormattedValue)
                  and isinstance(argument.values[0].value, ast.Name) and argument.values[0].value.id == "DOMAIN"
                  and isinstance(argument.values[1], ast.Constant)):
                event = "phoenix_mcp" + argument.values[1].value
            else:
                continue
            if event.startswith("phoenix_mcp_"):
                events.add(event)
    assert len(events) >= 8
    documented = set(re.findall(r'<td><code>(phoenix_mcp_\w+)</code></td>', source))
    assert events <= documented, events - documented
    row = re.search(r'<tr><td><code>phoenix_mcp_rate_limited</code>.*?</tr>', source).group(0)
    assert "at most once per token per minute" in row
    assert "every rejected request" not in row


def test_recovery_and_household_limitations_are_explicit() -> None:
    operations = _plain_page("operations.html")
    assert "no aggregate byte budget" in operations
    assert "without rewriting unchanged snapshots" in operations
    assert "Keep the index and snapshot files together in backups" in operations
    assert "next successful protected write" in operations
    assert "Active and unresolved records are never evicted" in operations
    security = _plain_page("security.html")
    assert "household-only authority, even when an administrator starts the task" in security
    assert "shared" in security and "per-call" in security
    assert "removal is not a full data wipe" in security
    panel = _plain_page("panel.html")
    assert "500" in panel and "seven days" in panel and "one hour" in panel
    assert "Reject with error message" not in panel
