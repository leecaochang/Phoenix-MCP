"""Tests for the image check that Check options runs on models of unknown vision.

Covers how a reply is scored, what each adapter sends and reads back, and when
the probe endpoint runs the check and records its answer.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import custom_components.phoenix_mcp.agentcli as ac
from custom_components.phoenix_mcp.agentcli import ClaudeProvider, OpenAICompatProvider, ProviderConfig
from custom_components.phoenix_mcp.const import AGENTCLI_PROBE_IMAGE_MAX_TOKENS

STRIPES = ["blue", "white", "red"]


class _Resp:
    def __init__(self, status: int, payload: object = None, text: str = "") -> None:
        self.status = status
        self._payload = payload
        self._text = text

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _Session:
    def __init__(self, resp: _Resp) -> None:
        self.resp = resp
        self.sent: list[tuple[str, dict]] = []

    def post(self, url, headers=None, json=None, timeout=None, allow_redirects=None):  # noqa: A002
        self.sent.append((url, json))
        return self.resp


class _Provider:
    def __init__(self, status, text: str = "") -> None:
        self.answer = (status, text)
        self.asked: list[tuple[str, str]] = []

    async def probe_image(self, session, image, question):
        self.asked.append((image, question))
        return self.answer


async def _vision(status, text: str = ""):
    provider = _Provider(status, text)
    cfg = ProviderConfig(kind="deepseek", model="m", base_url="https://x", api_key="k")
    with patch.object(ac, "build_provider", return_value=provider), \
         patch.object(ac, "_probe_stripes", return_value=list(STRIPES)):
        return await ac.async_probe_vision(MagicMock(), cfg), provider


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "blue, white, red",
    "Blue, White, Red.",
    "<think>Not green. Left looks blue, then white, then red.</think>blue, white, red",
    "The stripes are blue, white and red.",
])
async def test_naming_the_stripes_in_order_establishes_vision(text):
    vision, provider = await _vision(200, text)
    assert vision is True
    image, question = provider.asked[0]
    assert ac._image_kind_and_size(base64.b64decode(image)) == ("image/png", 240, 80)
    assert "three vertical stripes" in question


@pytest.mark.asyncio
@pytest.mark.parametrize("status, text", [
    (200, "I can't view the image; it came through as unsupported."),
    (200, "red, white, blue"),
    (200, "blue, white"),
    (400, "Failed to deserialize the JSON body: messages[0]: unknown variant `image_url`, expected `text`"),
    (400, "Image input is not supported for this model."),
])
async def test_a_wrong_or_refused_answer_marks_the_model_text_only(status, text):
    assert (await _vision(status, text))[0] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status, text", [
    (200, ""),
    (200, "<think>The left stripe is blue, then white, then</think>"),
    (400, "max_tokens is too large for this model"),
    (429, "slow down"),
    (503, "overloaded"),
    (None, ""),
])
async def test_no_usable_answer_establishes_nothing(status, text):
    assert (await _vision(status, text))[0] is None


def test_the_stripes_are_three_different_listed_colours():
    for _ in range(20):
        stripes = ac._probe_stripes()
        assert len(set(stripes)) == 3 and set(stripes) <= set(ac._PROBE_COLOURS)


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_claude_sends_the_image_and_reads_the_text_blocks():
    session = _Session(_Resp(200, {"content": [
        {"type": "thinking", "thinking": "red?"}, {"type": "text", "text": "blue, white, red"}]}))
    provider = ClaudeProvider(ProviderConfig(kind="claude", model="m", base_url="https://x", api_key="k"))

    status, text = await provider.probe_image(session, "QUJD", "question?")

    assert (status, text) == (200, "blue, white, red")
    url, body = session.sent[0]
    assert url == "https://x/v1/messages"
    assert body["max_tokens"] == AGENTCLI_PROBE_IMAGE_MAX_TOKENS
    assert body["messages"] == [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
        {"type": "text", "text": "question?"}]}]


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["blue, white, red", [{"type": "text", "text": "blue, white, red"}]])
async def test_openai_compatible_reads_string_or_part_content(content):
    session = _Session(_Resp(200, {"choices": [{"message": {"role": "assistant", "content": content}}]}))
    provider = OpenAICompatProvider(ProviderConfig(kind="chatgpt", model="m", base_url="https://x", api_key="k"))

    assert await provider.probe_image(session, "QUJD", "question?") == (200, "blue, white, red")
    _url, body = session.sent[0]
    assert body["stream"] is False
    assert body["max_completion_tokens"] == AGENTCLI_PROBE_IMAGE_MAX_TOKENS
    assert body["messages"][0]["content"][0] == {
        "type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind, thinking", [("deepseek", {"type": "disabled"}), ("chatgpt", None)])
async def test_deepseek_answers_without_thinking(kind, thinking):
    session = _Session(_Resp(200, {"choices": [{"message": {"content": "blue, white, red"}}]}))
    provider = OpenAICompatProvider(ProviderConfig(kind=kind, model="m", base_url="https://x", api_key="k"))

    await provider.probe_image(session, "QUJD", "q")

    assert session.sent[0][1].get("thinking") == thinking


@pytest.mark.asyncio
async def test_a_refused_image_reports_the_providers_message():
    error = json.dumps({"error": {"message": "unknown variant `image_url`, expected `text`"}})
    session = _Session(_Resp(400, text=error))
    provider = OpenAICompatProvider(ProviderConfig(kind="deepseek", model="m", base_url="https://x", api_key="k"))

    assert await provider.probe_image(session, "QUJD", "q") == (400, "unknown variant `image_url`, expected `text`")


# --------------------------------------------------------------------------- #
# The probe endpoint
# --------------------------------------------------------------------------- #

def _request():
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER
    user = MagicMock(is_admin=True, id="admin")
    request = MagicMock()
    request.__getitem__ = MagicMock(side_effect=lambda k: user if k == KEY_HASS_USER else None)
    request.get = MagicMock(side_effect=lambda k, default=None: (
        user if k == KEY_HASS_USER else True if k == KEY_AUTHENTICATED else default))
    return request


async def _probe_endpoint(hass, *, vision, answered=True, found=None):
    store = MagicMock()
    store.resolve.return_value = ProviderConfig(kind="deepseek", model="m", base_url="https://x", api_key="k",
                                                vision=vision)
    store.get.return_value = {"capabilities": {"m": {"tools": True}}}
    store.set_capabilities = AsyncMock(return_value=True)
    view = ac.PhoenixAgentCliProbeCapsView()
    view.hass = hass
    with patch.object(ac, "_get_secret_store", AsyncMock(return_value=store)), \
         patch.object(ac, "async_get_clientsession", return_value=MagicMock()), \
         patch.object(ac, "async_probe_capabilities", AsyncMock(return_value=({}, 3, answered))), \
         patch.object(ac, "async_probe_vision", AsyncMock(return_value=found)) as check:
        response = await view.post(_request(), instance_id="i1")
    return json.loads(response.text), store, check


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [True, False])
async def test_an_unknown_model_is_checked_and_the_answer_stored(hass, found):
    body, store, check = await _probe_endpoint(hass, vision=None, found=found)

    check.assert_awaited_once()
    assert body["vision_checked"] is True and body["probed"]["vision"] is found and body["calls"] == 4
    stored = store.set_capabilities.call_args.args[1]
    assert stored["m"] == {"tools": True, "vision": found}


@pytest.mark.asyncio
async def test_an_inconclusive_check_stores_nothing_about_vision(hass):
    body, store, _check = await _probe_endpoint(hass, vision=None, found=None)

    assert body["vision_checked"] is True and "vision" not in body["probed"]
    assert "vision" not in store.set_capabilities.call_args.args[1]["m"]


@pytest.mark.asyncio
@pytest.mark.parametrize("vision, answered", [(True, True), (False, True), (None, False)])
async def test_known_support_or_an_unanswering_provider_is_not_checked(hass, vision, answered):
    body, _store, check = await _probe_endpoint(hass, vision=vision, answered=answered)

    check.assert_not_awaited()
    assert body["vision_checked"] is False and body["calls"] == 3
