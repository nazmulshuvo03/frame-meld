import base64
import io
import json
import os
import struct
import wave
from decimal import Decimal

import httpx
import pytest

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderTransportError,
)
from framemeld.providers.speech.gemini import GeminiSpeechProvider
from framemeld.providers.speech.wav import wrap_pcm16

_KEY = "test-speech-key"

#: A tenth of a second of silence at 24 kHz, as Gemini documents it:
#: headerless little-endian 16-bit PCM.
_PCM = struct.pack("<2400h", *([0] * 2400))


def _provider(handler, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", _KEY)
    monkeypatch.setattr(settings, "speech_model", "gemini-2.5-flash-preview-tts")
    return GeminiSpeechProvider(transport=httpx.MockTransport(handler))


def _ok(data: bytes, mime="audio/L16;codec=pcm;rate=24000", finish_reason="STOP", **extra):
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}
                    ]
                },
                "finishReason": finish_reason,
            }
        ]
    } | extra
    return httpx.Response(200, json=body)


async def test_headerless_pcm_comes_back_as_a_playable_wav(monkeypatch):
    """The single most load-bearing behaviour in this adapter. Gemini
    answers with raw samples, which no browser and no media library will
    play, so the header has to be written here or the whole audio path
    ships silently broken."""
    provider = _provider(lambda request: _ok(_PCM), monkeypatch)
    result = await provider.speak(text="A line.", voice_id="Kore", params={})

    with wave.open(io.BytesIO(result.bytes_), "rb") as source:
        assert source.getframerate() == 24000
        assert source.getnframes() == 2400


async def test_the_rate_is_read_from_the_mime_type(monkeypatch):
    """A WAV header written at the wrong rate plays at the wrong pitch
    rather than failing, which is the sort of bug that reaches a user."""
    provider = _provider(
        lambda request: _ok(_PCM, mime="audio/L16;codec=pcm;rate=16000"), monkeypatch
    )
    result = await provider.speak(text="A line.", voice_id="Kore", params={})
    with wave.open(io.BytesIO(result.bytes_), "rb") as source:
        assert source.getframerate() == 16000


async def test_audio_that_is_already_a_wav_is_not_wrapped_twice(monkeypatch):
    already = wrap_pcm16(_PCM)
    provider = _provider(lambda request: _ok(already, mime="audio/wav"), monkeypatch)
    result = await provider.speak(text="A line.", voice_id="Kore", params={})
    assert result.bytes_ == already


async def test_the_voice_travels_in_the_request(monkeypatch):
    seen: dict = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return _ok(_PCM)

    await _provider(handler, monkeypatch).speak(text="A line.", voice_id="Puck", params={})
    voice = seen["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]
    assert voice["voiceName"] == "Puck"
    assert seen["generationConfig"]["responseModalities"] == ["AUDIO"]


async def test_the_key_travels_in_a_header_and_never_in_the_url(monkeypatch):
    seen: dict = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["header"] = request.headers.get("x-goog-api-key")
        return _ok(_PCM)

    await _provider(handler, monkeypatch).speak(text="A line.", voice_id="Kore", params={})
    assert seen["header"] == _KEY
    assert _KEY not in seen["url"]


async def test_the_cost_is_priced_per_input_character(monkeypatch):
    provider = _provider(lambda request: _ok(_PCM), monkeypatch)
    result = await provider.speak(text="x" * 1000, voice_id="Kore", params={})
    assert result.cost_usd == Decimal("10.00") * 1000 / Decimal("1000000")


@pytest.mark.parametrize("status", [408, 429, 500, 503])
async def test_transient_statuses_are_retryable(status, monkeypatch):
    provider = _provider(lambda request: httpx.Response(status, json={}), monkeypatch)
    with pytest.raises(ProviderTransportError) as caught:
        await provider.speak(text="A line.", voice_id="Kore", params={})
    assert caught.value.retryable is True


async def test_a_400_is_terminal(monkeypatch):
    provider = _provider(lambda request: httpx.Response(400, json={}), monkeypatch)
    with pytest.raises(ProviderResponseError) as caught:
        await provider.speak(text="A line.", voice_id="Kore", params={})
    assert caught.value.retryable is False


async def test_a_blocked_prompt_is_a_refusal(monkeypatch):
    provider = _provider(
        lambda request: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
        monkeypatch,
    )
    with pytest.raises(ProviderRefusedError) as caught:
        await provider.speak(text="A line.", voice_id="Kore", params={})
    assert caught.value.retryable is False


async def test_a_safety_finish_reason_is_a_refusal(monkeypatch):
    provider = _provider(lambda request: _ok(_PCM, finish_reason="SAFETY"), monkeypatch)
    with pytest.raises(ProviderRefusedError):
        await provider.speak(text="A line.", voice_id="Kore", params={})


async def test_a_response_with_no_audio_is_terminal(monkeypatch):
    provider = _provider(
        lambda request: httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "sorry"}]}}]}
        ),
        monkeypatch,
    )
    with pytest.raises(ProviderResponseError):
        await provider.speak(text="A line.", voice_id="Kore", params={})


async def test_a_transport_failure_never_quotes_the_key(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(ProviderTransportError) as caught:
        await _provider(handler, monkeypatch).speak(text="A line.", voice_id="Kore", params={})
    assert _KEY not in str(caught.value)


async def test_a_missing_key_refuses_to_construct(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    with pytest.raises(ValueError):
        GeminiSpeechProvider()


@pytest.mark.skipif(
    not os.getenv("FRAMEMELD_LIVE_SPEECH"),
    reason="opt-in: calls the real Gemini TTS endpoint",
)
async def test_live_speech(monkeypatch):
    """The probe M4 has not run. TTS has a free tier, unlike image
    generation, so this is expected to work once a key is configured -- but
    "expected to" is exactly the assumption M2's responseSchema incident and
    M3's Imagen-4 probe both punished. Run this and correct
    `_extract_audio` against whatever actually comes back before trusting
    the adapter with a user's credits.
    """
    monkeypatch.setattr(settings, "speech_provider", "gemini")
    result = await GeminiSpeechProvider().speak(
        text="She had known for years.", voice_id="Kore", params={}
    )
    assert result.bytes_
    with wave.open(io.BytesIO(result.bytes_), "rb") as source:
        assert source.getnframes() > 0
