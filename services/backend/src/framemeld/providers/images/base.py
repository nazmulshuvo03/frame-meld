from typing import Any, Protocol

from framemeld.providers.base import ProviderResult


class ImageProvider(Protocol):
    name: str
    model: str

    async def generate(
        self, *, prompt: str, refs: list[bytes], params: dict[str, Any]
    ) -> ProviderResult: ...
