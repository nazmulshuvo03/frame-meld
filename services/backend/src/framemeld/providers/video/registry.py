from framemeld.config import settings
from framemeld.providers.video.base import VideoProvider
from framemeld.providers.video.stub import StubVideoProvider


def get_video_provider() -> VideoProvider:
    """Mirrors the other four registries, including the refusal to fall
    back. That matters most here: this is the only adapter whose calls cost
    dollars rather than cents, and a fallback would answer a $24 render
    with a colour field.

    Only one name resolves today (M6-D9). The branch shape is kept so that
    adding Wan 2.2 or Veo is a two-line change in the one place a provider
    name becomes an implementation.
    """
    if settings.video_provider == "stub":
        return StubVideoProvider()
    raise ValueError(f"Unknown VIDEO_PROVIDER: {settings.video_provider!r}")
