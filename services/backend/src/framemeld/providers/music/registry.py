from framemeld.config import settings
from framemeld.providers.music.base import MusicProvider
from framemeld.providers.music.stub import StubMusicProvider


def get_music_provider() -> MusicProvider:
    """Mirrors the other three registries, including the refusal to fall
    back: an unknown name raises rather than quietly scoring a paying
    user's film with a sine chord.

    Only one name resolves today (M5-D2). The branch shape is kept anyway
    so adding ACE-Step or Lyria is a two-line change in the one place a
    provider name becomes an implementation.
    """
    if settings.music_provider == "stub":
        return StubMusicProvider()
    raise ValueError(f"Unknown MUSIC_PROVIDER: {settings.music_provider!r}")
