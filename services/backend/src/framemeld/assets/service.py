import contextlib
import hashlib
import io
import struct
import uuid
import wave
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.models.asset import Asset
from framemeld.storage.registry import get_storage

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    """Width and height from a PNG's IHDR chunk, or None.

    Eight lines of struct instead of a Pillow dependency: the only thing
    this codebase needs from an image decoder is two integers from a header
    that is always in the same place. Anything that is not a PNG returns
    None and the columns stay null -- they are metadata for a future UI,
    not something a generation depends on, so an unreadable format must not
    fail the job that produced the bytes.
    """
    if len(data) < 24 or not data.startswith(_PNG_MAGIC) or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def wav_duration(data: bytes) -> Decimal | None:
    """How long a WAV runs, in seconds, or None.

    The audio sibling of png_dimensions above, and the same trade: stdlib
    `wave` rather than a media library, because the only thing this
    codebase needs from a decoder is frames divided by frame rate. Anything
    that is not a readable WAV returns None and the column stays null --
    M5's scene spec wants this number, but a generation must not fail
    because a provider sent a container we cannot measure.

    Quantised to the column's scale (Numeric(8, 2)) with Postgres's
    rounding mode, for the same reason script/hashes.py quantises a shot's
    duration: a value that changes when it is read back would make the row
    look edited.
    """
    with contextlib.suppress(wave.Error, EOFError, ValueError):
        with wave.open(io.BytesIO(data), "rb") as source:
            rate = source.getframerate()
            if rate:
                seconds = Decimal(source.getnframes()) / Decimal(rate)
                return seconds.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return None


async def store_asset(
    db: DbSession, *, user_id: uuid.UUID, data: bytes, mime: str, ext: str
) -> Asset:
    """Write the bytes, then record them.

    That order, deliberately: if the row's transaction rolls back we leak a
    file nobody references, which is garbage to collect later. The other
    order leaks a row pointing at nothing, which is a 404 in a user's
    gallery and a much worse thing to debug.
    """
    asset_id = uuid.uuid4()
    key = f"{user_id}/{asset_id}.{ext}"
    await get_storage().put(key=key, data=data, mime=mime)

    dimensions = png_dimensions(data)
    asset = Asset(
        id=asset_id,
        user_id=user_id,
        storage_key=key,
        mime=mime,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        width=dimensions[0] if dimensions else None,
        height=dimensions[1] if dimensions else None,
        duration_sec=wav_duration(data),
        created_at=datetime.now(UTC),
    )
    db.add(asset)
    await db.flush()
    return asset


async def get_asset(db: DbSession, user_id: uuid.UUID, asset_id: uuid.UUID) -> Asset | None:
    """Authorised on assets.user_id directly rather than through get_meld:
    an asset outlives the generation that produced it, and the serving
    route has no Meld in hand."""
    return (
        await db.execute(select(Asset).where(Asset.id == asset_id, Asset.user_id == user_id))
    ).scalar_one_or_none()


async def get_asset_unscoped(db: DbSession, asset_id: uuid.UUID) -> Asset | None:
    """An asset by id alone, with no ownership check.

    The one caller is the signed-URL path in the router, where the
    signature has already proved the bearer was given this exact asset id
    by this system (M5-D1) -- there is no user in hand to scope by. Kept
    separate from get_asset above, and named so that reaching for it by
    accident reads as the mistake it would be.
    """
    return (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
