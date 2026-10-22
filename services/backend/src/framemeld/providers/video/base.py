from typing import Any, Protocol

from framemeld.providers.base import ProviderResult


class VideoProvider(Protocol):
    """Spec section 3.3's third adapter, and the expensive one.

    `animate` takes an image and a description of how it should move --
    never a description of what to draw. `image=None` is the exception: a
    clip made from the script alone, where `motion_prompt` carries the whole
    description of the frame as well as its movement. Image-to-video is the whole design
    (spec section 4.2): a clip is conditioned on a still the user has
    already approved, which is what bounds character drift to within a
    single five-second shot rather than across a film.

    The image arrives as *bytes* rather than a URL. Every other adapter here
    takes what it needs directly, the worker already holds the bytes to hash
    them, and a URL would mean a provider's fetcher had to be able to reach
    this deployment -- which is a network topology requirement hidden inside
    a type signature. A hosted adapter that wants a URL can upload and get
    one; that is its business, not the protocol's.
    """

    name: str
    model: str

    async def animate(
        self,
        *,
        image: bytes | None,
        motion_prompt: str,
        duration_sec: float,
        params: dict[str, Any],
    ) -> ProviderResult: ...
