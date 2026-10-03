"""JSON budget boundaries for compact diagnostic text."""

import json

import pytest

from custom_components.phoenix_mcp.system_log_payload import compact_log_text, log_result_size


@pytest.mark.parametrize("text", ["plain", "", "\u4e2d\u6587", '"\\\n', chr(0x10000), "\ud800"])
def test_exact_string_boundary_preserves_the_complete_value(text):
    budget = len(json.dumps(text).encode("utf-8"))
    preview, metadata = compact_log_text(text, budget)
    assert preview == text
    assert metadata == {"original_chars": len(text), "omitted_chars": 0}


@pytest.mark.parametrize("unit", ["x", "\u4e2d", chr(0x10000), '"\\\n', "\ud800"])
@pytest.mark.parametrize("traceback", [False, True])
def test_escaped_unicode_and_control_text_stays_inside_budget(unit, traceback):
    text = "BEGIN " + unit * 10000 + " INNERMOST END"
    preview, metadata = compact_log_text(text, 2048, traceback=traceback)
    assert len(json.dumps(preview).encode("utf-8")) <= 2048
    assert preview.startswith("BEGIN ") and preview.endswith("INNERMOST END")
    assert metadata["omitted_chars"] + metadata["head_chars"] + metadata["tail_chars"] == len(text)
    assert preview[:metadata["head_chars"]] == text[:metadata["head_chars"]]
    assert preview[-metadata["tail_chars"]:] == text[-metadata["tail_chars"]:]
    if traceback:
        assert metadata["tail_chars"] >= 3 * metadata["head_chars"]


def test_result_measurement_includes_nested_json_escaping():
    body = {"text": '"\\\n' * 100 + "\u4e2d" * 100}
    expected = {"content": [{"type": "text", "text": json.dumps(body)}]}
    assert log_result_size(body) == len(json.dumps(expected).encode("utf-8"))
    assert log_result_size(body) > len(json.dumps(body).encode("utf-8"))
