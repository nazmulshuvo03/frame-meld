import hashlib
import struct
import zlib
from decimal import Decimal
from typing import Any

from framemeld.config import settings
from framemeld.providers.base import ProviderRefusedError, ProviderResult

#: Small enough to keep the suite fast, large enough that a human looking
#: at one in a browser can tell it is a placeholder and not a failure.
STUB_SIZE = 64


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A real, valid PNG of one solid colour, from zlib and struct.

    Real bytes rather than a fixture blob because everything downstream
    treats these as an image: the sha256 becomes a content_hash, the asset
    service reads dimensions out of the IHDR, and a browser renders the
    result. A stub returning b"not-an-image" would let all three ship
    broken and only fail against a live provider.

    Colour type 2 (truecolour), bit depth 8, and every scanline prefixed
    with filter byte 0 -- the simplest encoding the format allows.
    """
    scanline = b"\x00" + bytes(rgb) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(scanline * height))
        + _chunk(b"IEND", b"")
    )


class StubImageProvider:
    """A local image model that costs nothing and never leaves the process.

    The default provider (M3-D10), so the app and the whole suite run with
    no API key and no network. Its output is *derived from the prompt*
    rather than fixed: two characters must not produce byte-identical
    sheets, or a test asserting that each character got its own image would
    pass against a broken generation path.
    """

    name = "stub"

    def __init__(self) -> None:
        self.model = settings.image_model

    async def generate(
        self, *, prompt: str, refs: list[bytes], params: dict[str, Any]
    ) -> ProviderResult:
        if not prompt.strip():
            # A real model asked to draw nothing would refuse or
            # hallucinate. Refusing keeps the failure honest and gives the
            # refund path something to exercise.
            raise ProviderRefusedError("There is nothing to draw yet.")

        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        return ProviderResult(
            text=None,
            bytes_=solid_png(STUB_SIZE, STUB_SIZE, (digest[0], digest[1], digest[2])),
            cost_usd=Decimal("0"),
            provider=self.name,
            model=self.model,
            raw={"stub": True},
        )
