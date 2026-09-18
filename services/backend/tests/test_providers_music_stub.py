import pytest

from framemeld.assets.service import wav_duration
from framemeld.providers.base import ProviderRefusedError
from framemeld.providers.music.registry import get_music_provider
from framemeld.providers.music.stub import StubMusicProvider


async def test_the_stub_returns_a_real_wav():
    """M4-D8's rule, one adapter later: the duration reader parses this, its
    sha256 becomes a content_hash, Remotion mixes it and a browser plays it.
    A placeholder blob would let all four ship broken."""
    result = await StubMusicProvider().compose(prompt="a quiet score", duration_sec=3, params={})
    assert result.bytes_ is not None
    assert result.bytes_.startswith(b"RIFF")
    assert result.bytes_[8:12] == b"WAVE"


async def test_the_track_is_as_long_as_it_was_asked_to_be():
    """The one thing that separates a music provider from every other
    adapter here: a score has to fit a film that already exists."""
    result = await StubMusicProvider().compose(prompt="x", duration_sec=4, params={})
    assert wav_duration(result.bytes_ or b"") == pytest.approx(4.0, abs=0.05)


async def test_two_prompts_do_not_produce_identical_audio():
    """Otherwise a broken per-Meld path would look like a working one."""
    first = await StubMusicProvider().compose(prompt="a storm", duration_sec=2, params={})
    second = await StubMusicProvider().compose(prompt="a wedding", duration_sec=2, params={})
    assert first.bytes_ != second.bytes_


async def test_scoring_nothing_is_refused_rather_than_billed():
    with pytest.raises(ProviderRefusedError):
        await StubMusicProvider().compose(prompt="x", duration_sec=0, params={})


async def test_a_very_long_film_is_capped_rather_than_synthesised():
    """A caller asking for ten minutes is a bug; hiding it behind a slow
    test is worse than capping it."""
    result = await StubMusicProvider().compose(prompt="x", duration_sec=100_000, params={})
    duration = wav_duration(result.bytes_ or b"")
    assert duration is not None and duration <= 200


def test_an_unknown_provider_name_raises_rather_than_falling_back(monkeypatch):
    """Falling back would score a paying user's film with a sine chord.

    This test named `lyria` until 2026-09-19, when a Lyria adapter landed
    and the name started resolving. Renamed rather than deleted, to
    `ace-step` -- spec section 6's commercially-safe self-hosted candidate,
    which has no adapter. The assertion is about the *absence of a
    fallback*, not about any particular name, so it needs a name that is
    genuinely unimplemented to keep asserting anything.
    """
    from framemeld.config import settings

    monkeypatch.setattr(settings, "music_provider", "ace-step")
    with pytest.raises(ValueError, match="Unknown MUSIC_PROVIDER"):
        get_music_provider()


def test_the_lyria_name_resolves_to_the_lyria_adapter(monkeypatch):
    """The other half of the registry's contract, and the half that would
    otherwise only be exercised by a live call: a configured name has to
    reach its implementation, not just fail to raise."""
    from framemeld.config import settings
    from framemeld.providers.music.gemini import LyriaMusicProvider

    monkeypatch.setattr(settings, "music_provider", "lyria")
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    assert isinstance(get_music_provider(), LyriaMusicProvider)
