from framemeld.config import settings
from framemeld.providers.speech.base import SpeechProvider
from framemeld.providers.speech.stub import StubSpeechProvider


def get_speech_provider() -> SpeechProvider:
    """Mirrors providers/registry.py and providers/images/registry.py,
    including the lazy import and the refusal to fall back: an unknown name
    raises rather than quietly serving stub audio to a paying user."""
    if settings.speech_provider == "stub":
        return StubSpeechProvider()
    if settings.speech_provider == "gemini":
        from framemeld.providers.speech.gemini import GeminiSpeechProvider

        return GeminiSpeechProvider()
    raise ValueError(f"Unknown SPEECH_PROVIDER: {settings.speech_provider!r}")
