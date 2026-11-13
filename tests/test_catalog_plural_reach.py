"""A plural sentence whose ``one`` form other counts reach states the count.

In some languages CLDR's ``one`` category also covers 21, 31, 101 and so on, so a
``one`` form written for a single item ("A card reported an error") is shown for 21
cards. Such a form must carry ``{count}``.
"""

from __future__ import annotations

import json
from pathlib import Path

from babel import Locale
import pytest

CATALOGS = Path(__file__).resolve().parents[1] / "custom_components" / "phoenix_mcp" / "catalogs"


def _plural_ones(node: object, path: str = ""):
    if isinstance(node, dict):
        if isinstance(node.get("one"), str) and "other" in node:
            yield path, node["one"]
        for key, value in node.items():
            yield from _plural_ones(value, f"{path}.{key}" if path else key)


@pytest.mark.parametrize("catalog", sorted(CATALOGS.glob("*.json")), ids=lambda path: path.stem)
def test_a_one_form_that_covers_more_than_one_names_the_count(catalog: Path) -> None:
    locale = Locale.parse(catalog.stem.replace("-", "_"))
    if not any(locale.plural_form(n) == "one" for n in range(2, 1001)):
        return
    countless = [path for path, text in _plural_ones(json.loads(catalog.read_text(encoding="utf-8")))
                 if "{count}" not in text]
    assert countless == [], f"{catalog.name}: put {{count}} in these one forms: {countless}"


def test_russian_one_reaches_twenty_one() -> None:
    assert Locale.parse("ru").plural_form(21) == "one"
