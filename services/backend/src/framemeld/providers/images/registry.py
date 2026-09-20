from framemeld.config import settings
from framemeld.providers.images.base import ImageProvider
from framemeld.providers.images.stub import StubImageProvider


def get_image_provider() -> ImageProvider:
    """Mirrors providers/registry.py, including the lazy import: a
    stub-configured install never pays for the real adapter's imports, and
    an unknown name raises rather than falling back to the stub, which
    would let a misconfigured production instance serve placeholder
    artwork to paying users."""
    if settings.image_provider == "stub":
        return StubImageProvider()
    if settings.image_provider == "gemini":
        from framemeld.providers.images.gemini import GeminiImageProvider

        return GeminiImageProvider()
    if settings.image_provider == "comfyui":
        from framemeld.providers.images.comfyui import ComfyUIImageProvider

        return ComfyUIImageProvider()
    raise ValueError(f"Unknown IMAGE_PROVIDER: {settings.image_provider!r}")
