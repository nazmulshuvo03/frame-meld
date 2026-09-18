from framemeld.config import settings
from framemeld.providers.music.base import MusicProvider
from framemeld.providers.music.gemini import LyriaMusicProvider
from framemeld.providers.music.stub import StubMusicProvider


def get_music_provider() -> MusicProvider:
    """Mirrors the other three registries, including the refusal to fall
    back: an unknown name raises rather than quietly scoring a paying
    user's film with a sine chord.

    M5-D2 said adding Lyria would be a two-line change in the one place a
    provider name becomes an implementation. It was.
    """
    if settings.music_provider == "stub":
        return StubMusicProvider()
    if settings.music_provider == "lyria":
        return LyriaMusicProvider()
    raise ValueError(f"Unknown MUSIC_PROVIDER: {settings.music_provider!r}")
