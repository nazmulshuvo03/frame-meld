from framemeld.config import settings
from framemeld.providers.video.base import VideoProvider
from framemeld.providers.video.gemini import VeoVideoProvider
from framemeld.providers.video.stub import StubVideoProvider


def get_video_provider() -> VideoProvider:
    """Mirrors the other four registries, including the refusal to fall
    back. That matters most here: this is the only adapter whose calls cost
    dollars rather than cents, and a fallback would answer a $24 render
    with a colour field.

    M6-D9 said adding Veo would be a two-line change in the one place a
    provider name becomes an implementation. It was.
    """
    if settings.video_provider == "stub":
        return StubVideoProvider()
    if settings.video_provider == "veo":
        return VeoVideoProvider()
    raise ValueError(f"Unknown VIDEO_PROVIDER: {settings.video_provider!r}")
