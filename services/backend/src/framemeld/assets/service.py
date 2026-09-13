import hashlib
import struct
import uuid
from datetime import UTC, datetime

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
