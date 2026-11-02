"""Every locale must match the English it was translated from.

The completeness, placeholder and identical-to-English checks judge a locale
against the current en.json, so a translation of a reworded English string passes
all of them while stating the old behaviour. Source stamps (a baseline written by
scripts/i18n_source_stamp.py) catch that drift. The synthetic cases keep the
audit honest: one that classified nothing would leave the real check green.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _stamper():
    """Load the script by path; scripts/ is deliberately not a package."""
    spec = importlib.util.spec_from_file_location(
        "i18n_source_stamp", REPO / "scripts" / "i18n_source_stamp.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _locales() -> list[str]:
    return _stamper().locales()


@pytest.mark.parametrize("language", _locales())
def test_locale_matches_the_english_it_was_translated_from(language: str, capsys) -> None:
    """Any drift fails with the script's own report, naming the keys."""
    exit_code = _stamper().check(language)
    if exit_code != 0:
        pytest.fail(f"{language}.json:\n{capsys.readouterr().out}")


def test_every_shipped_locale_is_stamped() -> None:
    """Every shipped locale has stamps, so a baseline missing a whole locale fails."""
    baseline = _stamper().load_baseline()
    assert _locales(), "no locale files found; did the translations directory move?"
    for language in _locales():
        assert baseline.get(language), f"{language} has no stamps at all"


def test_audit_reports_a_reworded_english_string() -> None:
    stamp = _stamper()
    problems = stamp.audit(
        english={"a": "Revoking an access key also clears the Assist binding."},
        locale={"a": "撤销令牌还会清除 Assist 绑定。"},
        stamped={"a": stamp.digest("Revoking an access key cancels its pending approvals.")},
    )
    assert problems["stale"] == ["a"]


def test_audit_is_quiet_when_the_english_is_unchanged() -> None:
    stamp = _stamper()
    problems = stamp.audit(
        english={"a": "Pending"},
        locale={"a": "待处理"},
        stamped={"a": stamp.digest("Pending")},
    )
    assert problems == {"stale": [], "unstamped": [], "orphaned": []}


def test_audit_reports_a_translation_with_no_stamp() -> None:
    stamp = _stamper()
    problems = stamp.audit(english={"a": "Pending"}, locale={"a": "待处理"}, stamped={})
    assert problems["unstamped"] == ["a"]


def test_audit_reports_a_stamp_whose_key_is_gone() -> None:
    stamp = _stamper()
    problems = stamp.audit(english={}, locale={}, stamped={"a": stamp.digest("Pending")})
    assert problems["orphaned"] == ["a"]


def test_keys_a_locale_deliberately_leaves_english_need_no_stamp() -> None:
    """The keep-English keys are absent from the locale, so they are not drift."""
    stamp = _stamper()
    problems = stamp.audit(english={"entity.sensor.status.name": "Status"}, locale={}, stamped={})
    assert problems == {"stale": [], "unstamped": [], "orphaned": []}


def test_audit_reads_the_english_half_of_a_two_part_stamp() -> None:
    stamp = _stamper()
    current = f"{stamp.digest('Pending')}/{stamp.digest('待处理')}"
    assert stamp.audit(english={"a": "Pending"}, locale={"a": "待处理"}, stamped={"a": current})["stale"] == []
    moved = f"{stamp.digest('Waiting')}/{stamp.digest('待处理')}"
    assert stamp.audit(english={"a": "Pending"}, locale={"a": "待处理"}, stamped={"a": moved})["stale"] == ["a"]


def _stamp_fixture(monkeypatch, tmp_path, english, locale, stamped):
    """The stamper over in-memory catalogs and a baseline file in tmp_path."""
    import json

    stamp = _stamper()
    monkeypatch.setattr(stamp, "BASELINE", tmp_path / "hashes.json")
    stamp.BASELINE.write_text(json.dumps({"locales": {"xx": stamped}}), encoding="utf-8")
    monkeypatch.setattr(stamp, "catalog", lambda name: english if name == "en" else locale)
    return stamp


def test_stamping_refuses_a_translation_left_unchanged_after_the_english_moved(monkeypatch, tmp_path) -> None:
    stamp = _stamper()
    old = {"a": f"{stamp.digest('Waiting')}/{stamp.digest('待处理')}"}
    stamp = _stamp_fixture(monkeypatch, tmp_path, {"a": "Pending"}, {"a": "待处理"}, old)
    assert stamp.stamp("xx") == 1
    assert stamp.load_baseline()["xx"] == old
    assert stamp.stamp("xx", allow_unchanged=True) == 0
    assert stamp.load_baseline()["xx"]["a"] == f"{stamp.digest('Pending')}/{stamp.digest('待处理')}"


def test_stamping_accepts_a_retranslated_key(monkeypatch, tmp_path) -> None:
    stamp = _stamper()
    old = {"a": f"{stamp.digest('Waiting')}/{stamp.digest('等待中')}"}
    stamp = _stamp_fixture(monkeypatch, tmp_path, {"a": "Pending"}, {"a": "待处理"}, old)
    assert stamp.stamp("xx") == 0


def test_stamping_chosen_keys_leaves_other_stale_keys_stale(monkeypatch, tmp_path) -> None:
    """A merged batch must not mark the locale's other stale keys as reviewed."""
    stamp = _stamper()
    old = {k: f"{stamp.digest('Old')}/{stamp.digest('旧')}" for k in ("a", "b")}
    stamp = _stamp_fixture(monkeypatch, tmp_path, {"a": "New", "b": "New"}, {"a": "新", "b": "旧"}, old)
    assert stamp.stamp("xx", keys={"a"}) == 0
    assert stamp.load_baseline()["xx"]["b"] == old["b"]
    assert stamp.audit({"a": "New", "b": "New"}, {"a": "新", "b": "旧"}, stamp.load_baseline()["xx"])["stale"] == ["b"]


def test_a_stamp_without_a_translation_half_never_refuses(monkeypatch, tmp_path) -> None:
    stamp = _stamper()
    stamp = _stamp_fixture(monkeypatch, tmp_path, {"a": "Pending"}, {"a": "待处理"}, {"a": stamp.digest("Waiting")})
    assert stamp.stamp("xx") == 0
