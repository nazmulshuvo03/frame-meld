import io
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
from framemeld.providers.speech.kokoro import KokoroSpeechProvider

#: A tenth of a second at 24 kHz, as Kokoro answers `response_format=pcm`:
#: headerless little-endian 16-bit samples.
_PCM = struct.pack("<2400h", *([0] * 2400))

#: What Kokoro answers for `response_format=wav`, header-wise: a streaming
#: RIFF whose declared sizes are 0xFFFFFFFF because the header is written
#: before the audio exists. Only used by the test that pins why this
#: adapter does not ask for it.
_STREAMING_WAV_HEADER = (
    b"RIFF" + b"\xff\xff\xff\xff" + b"WAVE"
    b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16) + b"data" + b"\xff\xff\xff\xff"
)


def _provider(handler, monkeypatch):
    monkeypatch.setattr(settings, "kokoro_base_url", "http://kokoro.test/v1")
    monkeypatch.setattr(settings, "speech_model", "kokoro")
    return KokoroSpeechProvider(transport=httpx.MockTransport(handler))


async def test_headerless_pcm_comes_back_as_a_measurable_wav(monkeypatch):
    """The load-bearing behaviour. assets/service.py measures a clip with
    `wave.getnframes()`, so a header that lies about its length is worse
    than no audio at all -- it fails nothing and records nonsense."""
    provider = _provider(lambda request: httpx.Response(200, content=_PCM), monkeypatch)
    result = await provider.speak(text="A line.", voice_id="Kore", params={})

    with wave.open(io.BytesIO(result.bytes_), "rb") as source:
        assert source.getframerate() == 24000
        assert source.getnchannels() == 1
        assert source.getnframes() == 2400
        assert source.getnframes() / source.getframerate() == pytest.approx(0.1)


async def test_it_asks_for_pcm_and_never_for_wav(monkeypatch):
    """Pins the decision, not the implementation. Kokoro's WAV declares
    0xFFFFFFFF for both its RIFF and data sizes, which `wave` reads as
    89478.49 seconds for a 1.5-second line. Asking for `pcm` is the only
    reason this adapter records a real duration, so a future edit that
    switches the format back has to delete this test to do it."""
    seen: dict[str, object] = {}

    def handler(request):
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, content=_PCM)

    provider = _provider(handler, monkeypatch)
    await provider.speak(text="A line.", voice_id="Kore", params={})

    assert seen["response_format"] == "pcm"

    # And this is what asking for "wav" would have cost, measured against
    # the same reader the pipeline uses.
    with wave.open(io.BytesIO(_STREAMING_WAV_HEADER + _PCM), "rb") as broken:
        assert broken.getnframes() / broken.getframerate() > 89000


async def test_a_gemini_voice_id_maps_to_a_kokoro_voice(monkeypatch):
    """`characters.voice_id` holds Gemini's names whichever provider is
    configured, because a voice id is inside the character's content_hash
    and rewriting one would invalidate every audio clip in the system."""
    seen: dict[str, object] = {}

    def handler(request):
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, content=_PCM)

    provider = _provider(handler, monkeypatch)
    await provider.speak(text="A line.", voice_id="Fenrir", params={})

    assert seen["voice"] == "am_fenrir"


async def test_an_unknown_voice_reads_rather_than_failing(monkeypatch):
    """A voice id that is not in the table is a bug elsewhere. Failing the
    generation over it would turn a cosmetic mismatch into a dead job."""
    seen: dict[str, object] = {}

    def handler(request):
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, content=_PCM)

    provider = _provider(handler, monkeypatch)
    result = await provider.speak(text="A line.", voice_id="NotAVoice", params={})

    assert seen["voice"] == "af_heart"
    assert result.bytes_


async def test_empty_text_is_refused_without_a_request(monkeypatch):
    """Same refusal as the stub and the Gemini adapter. A model asked to
    read nothing bills for silence."""

    def handler(request):  # pragma: no cover - the point is that it is not called
        raise AssertionError("no request should have been made")

    provider = _provider(handler, monkeypatch)
    with pytest.raises(ProviderRefusedError):
        await provider.speak(text="   ", voice_id="Kore", params={})


async def test_local_inference_costs_zero(monkeypatch):
    """Not a placeholder: settlement subtracts this number, and a
    development run must not charge a user."""
    provider = _provider(lambda request: httpx.Response(200, content=_PCM), monkeypatch)
    result = await provider.speak(text="A line.", voice_id="Kore", params={})

    assert result.cost_usd == Decimal("0")
    assert result.provider == "kokoro"


async def test_a_server_error_is_transport_not_response(monkeypatch):
    """The distinction the worker retries on."""
    provider = _provider(lambda request: httpx.Response(503), monkeypatch)
    with pytest.raises(ProviderTransportError):
        await provider.speak(text="A line.", voice_id="Kore", params={})


async def test_a_rejected_request_is_terminal(monkeypatch):
    provider = _provider(lambda request: httpx.Response(422, text="bad voice"), monkeypatch)
    with pytest.raises(ProviderResponseError):
        await provider.speak(text="A line.", voice_id="Kore", params={})


async def test_an_empty_body_is_not_silently_stored(monkeypatch):
    """A zero-byte reply would otherwise become a valid, empty WAV and a
    shot with no narration that nobody notices until the render."""
    provider = _provider(lambda request: httpx.Response(200, content=b""), monkeypatch)
    with pytest.raises(ProviderResponseError):
        await provider.speak(text="A line.", voice_id="Kore", params={})


async def test_an_unreachable_container_says_so(monkeypatch):
    """The most likely failure in development, and the message names the
    actual remedy rather than blaming the network."""

    def handler(request):
        raise httpx.ConnectError("connection refused")

    provider = _provider(handler, monkeypatch)
    with pytest.raises(ProviderTransportError, match="Kokoro container"):
        await provider.speak(text="A line.", voice_id="Kore", params={})
