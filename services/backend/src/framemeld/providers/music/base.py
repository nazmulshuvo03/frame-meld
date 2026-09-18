from typing import Any, Protocol

from framemeld.providers.base import ProviderResult


class MusicProvider(Protocol):
    """Spec section 3.3's fifth and last adapter.

    `compose` takes a duration as well as a prompt, which is what separates
    it from every other generator here: a score has to fit a film that
    already exists, so length is an input rather than whatever the model
    felt like producing.
    """

    name: str
    model: str

    async def compose(
        self, *, prompt: str, duration_sec: float, params: dict[str, Any]
    ) -> ProviderResult: ...
