"""Every translated Guide page must match the English it was translated from.

The build holds a translation to English's sections and place in the contents,
but a page translated before its English was rewritten would keep describing the
old behaviour. Source stamps (written by scripts/guide_i18n_stamp.py) catch that.
The synthetic cases keep the audit honest while no translated Guide exists yet: a
check that classified nothing would pass for every future translation.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _stamper():
    """Load the script by path; scripts/ is deliberately not a package."""
    spec = importlib.util.spec_from_file_location("guide_i18n_stamp", REPO / "scripts" / "guide_i18n_stamp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("language", _stamper().locales())
def test_translated_pages_match_the_english_they_came_from(language: str, capsys) -> None:
    assert _stamper().check(language) == 0, capsys.readouterr().out


def test_readable_text_is_the_title_and_body_only() -> None:
    stamp = _stamper()
    page = '---\n{ "id": "window.a", "title": "Access Keys", "keywords": ["x"] }\n---\nBody.\n'
    rekeyed = '---\n{ "id": "window.a", "title": "Access Keys", "keywords": ["y"], "covers": ["z"] }\n---\nBody.\n'
    assert stamp.readable(page) == "Access Keys\nBody."
    assert stamp.readable(page) == stamp.readable(rekeyed)
    assert stamp.readable("no header") is None


def test_audit_reports_stale_unstamped_and_orphaned_pages() -> None:
    stamp = _stamper()
    english = {"window.a": "A new", "window.b": "B"}
    locale = {"window.a": "甲", "window.b": "乙"}
    stamped = {"window.a": f"{stamp.digest('A old')}/{stamp.digest('甲')}", "window.gone": "x/y"}
    assert stamp.audit(english, locale, stamped) == {"stale": ["window.a"], "unstamped": ["window.b"], "orphaned": ["window.gone"]}


def test_audit_is_quiet_when_every_page_is_current() -> None:
    stamp = _stamper()
    english, locale = {"window.a": "A"}, {"window.a": "甲"}
    stamped = {"window.a": f"{stamp.digest('A')}/{stamp.digest('甲')}"}
    assert stamp.audit(english, locale, stamped) == {"stale": [], "unstamped": [], "orphaned": []}


def _fixture(monkeypatch, tmp_path, english: str, translated: str, stamped: dict[str, str]):
    """The stamper over a topics folder and baseline in tmp_path."""
    stamp = _stamper()
    for language, body in (("en", english), ("zh-Hans", translated)):
        (tmp_path / language).mkdir()
        (tmp_path / language / "window.a.md").write_text(f'---\n{{ "id": "window.a", "title": "T" }}\n---\n{body}\n', encoding="utf-8")
    monkeypatch.setattr(stamp, "TOPICS", tmp_path)
    monkeypatch.setattr(stamp, "BASELINE", tmp_path / "hashes.json")
    (tmp_path / "hashes.json").write_text(json.dumps({"locales": {"zh-Hans": stamped}}), encoding="utf-8")
    return stamp


def test_stamping_refuses_a_page_left_unchanged_after_the_english_moved(monkeypatch, tmp_path) -> None:
    stamp = _stamper()
    old = {"window.a": f"{stamp.digest('T' + chr(10) + 'Old')}/{stamp.digest('T' + chr(10) + '旧')}"}
    stamp = _fixture(monkeypatch, tmp_path, "New", "旧", old)
    assert stamp.locales() == ["zh-Hans"]
    assert stamp.stamp("zh-Hans") == 1
    assert stamp.load_baseline()["zh-Hans"] == old
    assert stamp.check("zh-Hans") == 1
    assert stamp.stamp("zh-Hans", allow_unchanged=True) == 0
    assert stamp.check("zh-Hans") == 0


def test_stamping_accepts_a_retranslated_page(monkeypatch, tmp_path) -> None:
    stamp = _stamper()
    old = {"window.a": f"{stamp.digest('T' + chr(10) + 'Old')}/{stamp.digest('T' + chr(10) + '旧')}"}
    stamp = _fixture(monkeypatch, tmp_path, "New", "新", old)
    assert stamp.stamp("zh-Hans") == 0
    assert stamp.check("zh-Hans") == 0
