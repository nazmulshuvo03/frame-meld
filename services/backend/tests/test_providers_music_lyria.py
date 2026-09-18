import base64
import json
import os
from decimal import Decimal

import httpx
import pytest

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderTransportError,
)
from framemeld.providers.music.gemini import MUSIC_MIME, LyriaMusicProvider

_KEY = "test-music-key"

#: Not a real MP3, and it does not need to be: every test here is about the
#: request/response contract, and the one test that cares whether the bytes
#: are really audio is the live one at the bottom.
_TRACK = b"ID3\x03\x00\x00\x00fake-mp3-payload"


def _provider(handler, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", _KEY)
    monkeypatch.setattr(settings, "music_model", "lyria-3.5")
    return LyriaMusicProvider(transport=httpx.MockTransport(handler))


def _ok(data: bytes = _TRACK, mime: str = MUSIC_MIME, finish_reason: str = "STOP", **extra):
    """The shape a live call actually returns: a text part carrying section
    markers, then the audio part. The text part is first on purpose -- it is
    what a naive `parts[0]` would pick up."""
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "[[A0]]\n[[B1]]\n[[C2]]"},
                        {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}},
                    ]
                },
                "finishReason": finish_reason,
            }
        ],
        "modelVersion": "lyria-3.5",
    } | extra
    return httpx.Response(200, json=body)


async def test_a_successful_call_returns_decoded_bytes(monkeypatch):
    provider = _provider(lambda request: _ok(), monkeypatch)

    result = await provider.compose(prompt="slow strings, dusk", duration_sec=21.5, params={})

    assert result.bytes_ == _TRACK
    assert result.text is None
    assert result.provider == "lyria"
    assert result.raw == {"mimeType": MUSIC_MIME}


async def test_the_audio_part_is_found_past_the_text_part(monkeypatch):
    """Lyria answers with the section markers first and the audio second.
    Indexing parts[0] would return the marker text as if it were a track."""
    provider = _provider(lambda request: _ok(), monkeypatch)
    result = await provider.compose(prompt="x", duration_sec=10, params={})
    assert result.bytes_ == _TRACK


async def test_the_cost_is_flat_per_request(monkeypatch):
    """Lyria bills per song, not per second: a 10-second film and a
    3-minute one cost the same, which is why `compose` does not try to ask
    for a shorter piece to save money."""
    provider = _provider(lambda request: _ok(), monkeypatch)
    short = await provider.compose(prompt="x", duration_sec=10, params={})
    long = await provider.compose(prompt="x", duration_sec=175, params={})
    assert short.cost_usd == long.cost_usd == Decimal("0.08")


async def test_the_request_carries_the_key_in_a_header_and_not_the_url(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _ok()

    await _provider(handler, monkeypatch).compose(prompt="a waltz", duration_sec=30, params={})

    assert seen["key"] == _KEY
    assert _KEY not in seen["url"]
    assert seen["url"].endswith("/models/lyria-3.5:generateContent")
    assert seen["body"]["contents"][0]["parts"][0]["text"] == "a waltz"


async def test_zero_duration_is_refused_before_any_call(monkeypatch):
    """Same refusal as the stub's, and for the same reason: a caller asking
    to score a film of no length has a bug, and refusing exercises the
    refund path rather than billing for it."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _ok()

    provider = _provider(handler, monkeypatch)
    with pytest.raises(ProviderRefusedError):
        await provider.compose(prompt="x", duration_sec=0, params={})
    assert called is False


async def test_a_blocked_prompt_is_a_refusal_not_a_failure(monkeypatch):
    provider = _provider(
        lambda request: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
        monkeypatch,
    )
    with pytest.raises(ProviderRefusedError):
        await provider.compose(prompt="x", duration_sec=10, params={})


async def test_a_safety_finish_reason_is_a_refusal(monkeypatch):
    provider = _provider(lambda request: _ok(finish_reason="SAFETY"), monkeypatch)
    with pytest.raises(ProviderRefusedError):
        await provider.compose(prompt="x", duration_sec=10, params={})


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_retryable_statuses_are_transport_errors(monkeypatch, status):
    provider = _provider(lambda request: httpx.Response(status, json={}), monkeypatch)
    with pytest.raises(ProviderTransportError):
        await provider.compose(prompt="x", duration_sec=10, params={})


async def test_a_400_is_terminal(monkeypatch):
    provider = _provider(lambda request: httpx.Response(400, json={}), monkeypatch)
    with pytest.raises(ProviderResponseError):
        await provider.compose(prompt="x", duration_sec=10, params={})


async def test_a_response_with_no_audio_is_an_error(monkeypatch):
    body = {"candidates": [{"content": {"parts": [{"text": "[[A0]]"}]}, "finishReason": "STOP"}]}
    provider = _provider(lambda request: httpx.Response(200, json=body), monkeypatch)
    with pytest.raises(ProviderResponseError):
        await provider.compose(prompt="x", duration_sec=10, params={})


async def test_no_error_path_quotes_the_api_key(monkeypatch):
    provider = _provider(lambda request: httpx.Response(400, json={"error": _KEY}), monkeypatch)
    with pytest.raises(ProviderResponseError) as caught:
        await provider.compose(prompt="x", duration_sec=10, params={})
    assert _KEY not in str(caught.value)


async def test_a_missing_key_refuses_to_construct(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    with pytest.raises(ValueError):
        LyriaMusicProvider()


@pytest.mark.skipif(
    not os.getenv("FRAMEMELD_LIVE_MUSIC"),
    reason="opt-in: calls the real Lyria endpoint (this spends money)",
)
async def test_live_music(monkeypatch):
    """The probe that M5-D2 could not run, because the key had no billing.

    Asserts the two facts the adapter is built on rather than just that
    something came back: the payload is an MP3 (every other audio path here
    is a WAV), and it is long enough to score a film -- a live call for a
    21-second film returned 174 seconds, which is why `duration_sec` is not
    sent and the composition trims instead.
    """
    monkeypatch.setattr(settings, "music_provider", "lyria")
    monkeypatch.setattr(settings, "music_model", "lyria-3.5")
    result = await LyriaMusicProvider().compose(
        prompt="sparse piano, melancholy, slow", duration_sec=20.0, params={}
    )
    assert result.bytes_
    assert result.bytes_[:3] == b"ID3" or result.bytes_[:2] == b"\xff\xfb"
    assert len(result.bytes_) > 100_000
    assert result.cost_usd == Decimal("0.08")
