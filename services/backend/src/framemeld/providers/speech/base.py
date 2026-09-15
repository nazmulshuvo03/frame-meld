from typing import Any, Protocol

from framemeld.providers.base import ProviderResult


class SpeechProvider(Protocol):
    """Spec section 3.3's fourth adapter.

    `speak` rather than `generate` because the two are not interchangeable:
    an image is produced from a prompt, speech is produced from words that
    are *already written*. Keeping the verbs distinct means a caller can
    never pass a shot's image prompt where its dialogue belonged and get
    plausible-sounding nonsense back.
    """

    name: str
    model: str

    async def speak(
        self, *, text: str, voice_id: str, params: dict[str, Any]
    ) -> ProviderResult: ...
