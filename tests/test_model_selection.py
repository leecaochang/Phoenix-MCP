"""Model selection: automatic mode, successor mapping and the retired-versus-custom rule."""

from custom_components.phoenix_mcp.model_selection import (
    best_model, effective_model, family_key, rank_models, successor,
)


def test_family_key_strips_versions_and_dates_but_keeps_sizes():
    assert family_key("claude-opus-4-8") == family_key("claude-opus-5-5-20260101") == "claude opus"
    assert family_key("gemini-2.5-flash") == family_key("gemini-3.8-flash") == "gemini flash"
    assert family_key("deepseek-v4-flash") == family_key("deepseek-flash") == "deepseek flash"
    assert family_key("llama-3.3-70b-instruct") != family_key("llama-3.3-8b-instruct")


def test_claude_prefers_opus_then_newest():
    models = ["claude-haiku-4-5", "claude-sonnet-5-5", "claude-opus-4-8", "claude-opus-5-5"]
    assert best_model("claude", models) == "claude-opus-5-5"
    created = {"claude-opus-4-8": 200.0, "claude-opus-5-5": 100.0}
    # A provider timestamp outranks the number parsed from the id.
    assert best_model("claude", models, created) == "claude-opus-4-8"


def test_stable_beats_preview_and_plain_beats_speed_variant():
    assert best_model("x", ["m-3-preview", "m-2"]) == "m-2"
    assert best_model("minimax", ["MiniMax-M2.7-highspeed", "MiniMax-M2.7"]) == "MiniMax-M2.7"
    assert best_model("mistral", ["mistral-small-2503", "mistral-small-latest"]) == "mistral-small-latest"


def test_gemini_prefers_flash_over_flash_lite_and_newest():
    models = ["gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.8-pro"]
    assert best_model("gemini", models) == "gemini-3.8-flash"


def test_empty_and_duplicate_catalogues():
    assert best_model("claude", []) == ""
    assert rank_models("x", ["a-1", "a-1", "", "a-2"]) == ["a-2", "a-1"]


def test_successor_stays_in_family_then_falls_back():
    models = ["deepseek-flash", "deepseek-v4-pro"]
    assert successor("deepseek", "deepseek-v4-flash", models) == "deepseek-flash"
    assert successor("deepseek", "something-unrelated", models) == "deepseek-flash"
    assert successor("deepseek", "anything", []) == ""


def test_automatic_mode_follows_the_catalogue():
    assert effective_model("claude", "", ["claude-opus-4-8"], []) == ("claude-opus-4-8", "")
    assert effective_model("claude", "", ["claude-opus-4-8", "claude-opus-5-5"], []) == ("claude-opus-5-5", "")
    assert effective_model("claude", "", [], []) == ("", "")


def test_retired_model_is_replaced_but_custom_model_is_not():
    catalogue = ["deepseek-flash", "deepseek-v4-pro"]
    # Listed before, gone now: retired, so replaced by its family's successor.
    assert effective_model("deepseek", "deepseek-v4-flash", catalogue, ["deepseek-v4-flash"]) \
        == ("deepseek-flash", "deepseek-v4-flash")
    # Never listed: a custom deployment, left exactly as the operator wrote it.
    assert effective_model("deepseek", "my-finetune", catalogue, []) == ("my-finetune", "")
    # Still listed: an explicit choice is never overridden by a newer model.
    assert effective_model("deepseek", "deepseek-v4-pro", catalogue, ["deepseek-v4-pro"]) \
        == ("deepseek-v4-pro", "")
    # Provider unreachable (empty catalogue): nothing changes.
    assert effective_model("deepseek", "deepseek-v4-flash", [], ["deepseek-v4-flash"]) \
        == ("deepseek-v4-flash", "")


def test_eligible_pool_narrows_automatic_choice_only():
    catalogue = ["a-3", "a-2"]
    assert effective_model("x", "", catalogue, [], None, eligible=["a-2"]) == ("a-2", "")
    # An explicit id that is still listed stays even when not eligible.
    assert effective_model("x", "a-3", catalogue, ["a-3"], None, eligible=["a-2"]) == ("a-3", "")
