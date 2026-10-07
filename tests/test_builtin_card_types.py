"""The built-in card type list must cover the installed HA frontend's card picker.

list_dashboard_cards answers a bare built-in type as always available without a
harvest, so a type missing here reads as an unseen custom card instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custom_components.phoenix_mcp.const import BUILTIN_CARD_TYPES


def _installed_picker_types() -> set[str]:
    try:
        import hass_frontend
    except ImportError:
        pytest.skip("HA frontend package not installed")
    chunks = Path(hass_frontend.__file__).parent / "frontend_latest"
    for chunk in chunks.glob("*.js"):
        text = chunk.read_text(errors="ignore")
        start = text.find('type:"alarm-panel",showElement')
        if start == -1:
            continue
        # The picker's core card array: every {type:"..."} entry around the anchor.
        window = text[max(0, start - 400):start + 4000]
        return set(re.findall(r'type:"([a-z0-9-]+)"', window))
    pytest.skip("card picker list not found in the installed HA frontend")


def test_every_card_the_installed_picker_offers_is_known() -> None:
    picker = _installed_picker_types()

    assert len(picker) > 30
    assert picker - BUILTIN_CARD_TYPES == set()
