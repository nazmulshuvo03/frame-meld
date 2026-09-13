import hashlib
import uuid

from sqlalchemy import select

from framemeld.assets.service import get_asset, png_dimensions, store_asset
from framemeld.models.asset import Asset
from framemeld.models.user import User
from framemeld.providers.images.stub import solid_png


async def _user(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    return user


def test_png_dimensions_reads_the_ihdr():
    assert png_dimensions(solid_png(64, 32, (1, 2, 3))) == (64, 32)


def test_png_dimensions_returns_none_for_anything_else():
    """Width and height are metadata for a future UI, not something a
    generation depends on, so an unreadable format leaves them null rather
    than failing the job that produced the bytes."""
    assert png_dimensions(b"GIF89a-not-really") is None
    assert png_dimensions(b"") is None


async def test_storing_an_asset_writes_the_file_and_the_row(db):
    user = await _user(db)
    data = solid_png(8, 8, (200, 100, 50))

    asset = await store_asset(db, user_id=user.id, data=data, mime="image/png", ext="png")

    assert asset.sha256 == hashlib.sha256(data).hexdigest()
    assert asset.bytes == len(data)
    assert asset.width == 8 and asset.height == 8
    assert asset.storage_key.startswith(f"{user.id}/")

    from framemeld.storage.registry import get_storage

    assert await get_storage().get(key=asset.storage_key) == data


async def test_an_asset_is_only_readable_by_its_owner(db):
    """Assets are authorised on their own user_id, not through a Meld: an
    asset outlives the generation that made it, and the serving route has
    no Meld in hand."""
    owner = await _user(db)
    stranger = await _user(db)
    asset = await store_asset(
        db, user_id=owner.id, data=solid_png(4, 4, (0, 0, 0)), mime="image/png", ext="png"
    )
    # Read the ids before expiring: a refresh on an expired attribute would
    # be sync IO inside an async session.
    owner_id, stranger_id, asset_id = owner.id, stranger.id, asset.id
    db.expire_all()

    assert await get_asset(db, owner_id, asset_id) is not None
    assert await get_asset(db, stranger_id, asset_id) is None


async def test_two_assets_never_share_a_key(db):
    user = await _user(db)
    first = await store_asset(
        db, user_id=user.id, data=solid_png(4, 4, (1, 1, 1)), mime="image/png", ext="png"
    )
    second = await store_asset(
        db, user_id=user.id, data=solid_png(4, 4, (1, 1, 1)), mime="image/png", ext="png"
    )

    assert first.storage_key != second.storage_key
    # Identical bytes, two rows: deduplication by sha256 is a later
    # optimisation (spec section 5.3), and doing it now would mean deleting
    # one Meld's image could blank another's.
    assert first.sha256 == second.sha256
    assert len((await db.execute(select(Asset))).scalars().all()) == 2
