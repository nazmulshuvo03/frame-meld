import io
import wave

import pytest

from framemeld.assets.service import wav_duration
from framemeld.providers.base import ProviderRefusedError
from framemeld.providers.speech.stub import StubSpeechProvider
from framemeld.providers.speech.voices import VOICE_IDS, voice_for_index
from framemeld.providers.speech.wav import SAMPLE_RATE, wrap_pcm16


async def test_the_stub_returns_a_real_wav():
    """M4-D8. Everything downstream treats this as audio -- the duration
    reader parses it, the sha256 becomes a content_hash, a browser plays
    it -- so a placeholder blob would let all three ship broken."""
    result = await StubSpeechProvider().speak(
        text="She had known for years.", voice_id="Kore", params={}
    )
    assert result.bytes_ is not None
    with wave.open(io.BytesIO(result.bytes_), "rb") as source:
        assert source.getframerate() == SAMPLE_RATE
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        assert source.getnframes() > 0


async def test_two_voices_produce_different_audio():
    """A stub that ignored voice_id would let a broken path -- one that
    never passes the voice to the provider -- pass every test here."""
    a = await StubSpeechProvider().speak(text="Same words.", voice_id="Kore", params={})
    b = await StubSpeechProvider().speak(text="Same words.", voice_id="Puck", params={})
    assert a.bytes_ != b.bytes_


async def test_two_lines_produce_different_audio():
    a = await StubSpeechProvider().speak(text="One line.", voice_id="Kore", params={})
    b = await StubSpeechProvider().speak(text="Another line.", voice_id="Kore", params={})
    assert a.bytes_ != b.bytes_


async def test_a_longer_line_is_a_longer_clip():
    short = await StubSpeechProvider().speak(text="Yes.", voice_id="Kore", params={})
    long = await StubSpeechProvider().speak(
        text="She had known for years, and had never once said so aloud.",
        voice_id="Kore",
        params={},
    )
    assert wav_duration(long.bytes_) > wav_duration(short.bytes_)


async def test_nothing_to_say_is_refused_terminally():
    with pytest.raises(ProviderRefusedError) as caught:
        await StubSpeechProvider().speak(text="   ", voice_id="Kore", params={})
    assert caught.value.retryable is False


async def test_the_stub_costs_nothing():
    result = await StubSpeechProvider().speak(text="A line.", voice_id="Kore", params={})
    assert result.cost_usd == 0


# --- the duration reader --------------------------------------------------


def test_wav_duration_reads_a_wav():
    one_second = wrap_pcm16(b"\x00\x00" * SAMPLE_RATE)
    assert wav_duration(one_second) == 1


def test_wav_duration_returns_none_for_anything_else():
    """Null columns rather than a failed job: duration is metadata M5 wants,
    not something a generation depends on."""
    assert wav_duration(b"not audio at all") is None
    assert wav_duration(b"") is None


def test_wav_duration_is_quantised_to_the_column_scale():
    """Numeric(8, 2). A value that changed when it was read back would make
    the row look edited -- the same trap _duration_at_column_scale exists
    for on shots."""
    odd = wrap_pcm16(b"\x00\x00" * (SAMPLE_RATE // 3))
    assert wav_duration(odd).as_tuple().exponent == -2


# --- voices ---------------------------------------------------------------


def test_voice_for_index_is_deterministic_and_wraps():
    """voice_id is inside content_hash_for_character, so a random seed would
    give the same story a different hash on every extraction and every clip
    would look stale for no reason."""
    assert voice_for_index(0) == voice_for_index(0)
    assert voice_for_index(0) != voice_for_index(1)
    assert voice_for_index(len(VOICE_IDS)) == voice_for_index(0)
