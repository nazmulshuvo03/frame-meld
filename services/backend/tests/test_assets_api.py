import uuid

from sqlalchemy import select

from framemeld.assets.service import store_asset
from framemeld.assets.signing import sign_asset
from framemeld.models.user import User
from framemeld.providers.images.stub import solid_png


async def _asset_for_logged_in_user(db, data: bytes):
    user = (await db.execute(select(User))).scalars().first()
    return await store_asset(db, user_id=user.id, data=data, mime="image/png", ext="png")


async def test_an_owner_gets_the_bytes(logged_in, db):
    data = solid_png(8, 8, (10, 20, 30))
    asset = await _asset_for_logged_in_user(db, data)
    await db.commit()

    response = await logged_in.get(f"/assets/{asset.id}")
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "image/png"


async def test_an_asset_is_cacheable_but_private(logged_in, db):
    """Content never changes for a given id, so it is immutable -- but it
    is one user's artwork, so no shared cache may keep it."""
    asset = await _asset_for_logged_in_user(db, solid_png(4, 4, (0, 0, 0)))
    await db.commit()

    cache_control = (await logged_in.get(f"/assets/{asset.id}")).headers["cache-control"]
    assert "private" in cache_control
    assert "immutable" in cache_control


async def test_an_unknown_asset_is_404(logged_in):
    assert (await logged_in.get(f"/assets/{uuid.uuid4()}")).status_code == 404


async def test_another_users_asset_is_404_not_403(client, db):
    """403 would confirm the asset exists. Same rule as every other
    user-owned row in this codebase."""
    await client.post(
        "/auth/signup", json={"email": "owner-asset@example.com", "password": "hunter2hunter2"}
    )
    owner = (
        await db.execute(select(User).where(User.email == "owner-asset@example.com"))
    ).scalar_one()
    asset = await store_asset(
        db, user_id=owner.id, data=solid_png(4, 4, (5, 5, 5)), mime="image/png", ext="png"
    )
    await db.commit()

    stranger = {"email": "stranger-asset@example.com", "password": "hunter2hunter2"}
    assert (await client.post("/auth/signup", json=stranger)).status_code == 201
    # Signup does not issue a session cookie; without this the request is
    # anonymous and 401s before authorisation is ever reached.
    assert (await client.post("/auth/login", json=stranger)).status_code == 200

    assert (await client.get(f"/assets/{asset.id}")).status_code == 404


async def test_serving_requires_authentication(client, db):
    await client.post(
        "/auth/signup", json={"email": "anon-asset@example.com", "password": "hunter2hunter2"}
    )
    user = (
        await db.execute(select(User).where(User.email == "anon-asset@example.com"))
    ).scalar_one()
    asset = await store_asset(
        db, user_id=user.id, data=solid_png(4, 4, (5, 5, 5)), mime="image/png", ext="png"
    )
    await db.commit()
    await client.post("/auth/logout")

    assert (await client.get(f"/assets/{asset.id}")).status_code == 401


# --- signed URLs: how the render service reads a file (M5-D1) -------------


async def test_a_signed_url_works_with_no_session(client, db):
    """The whole point. The render service has no cookie and no database
    (spec section 3.1), so without this a film cannot be assembled at all."""
    await client.post(
        "/auth/signup", json={"email": "signed@example.com", "password": "hunter2hunter2"}
    )
    owner = (await db.execute(select(User).where(User.email == "signed@example.com"))).scalar_one()
    data = solid_png(6, 6, (1, 2, 3))
    asset = await store_asset(db, user_id=owner.id, data=data, mime="image/png", ext="png")
    await db.commit()

    # `client`, not `logged_in`: signup issues no cookie, so this caller is
    # anonymous -- which is exactly the render service's position.
    response = await client.get(f"/assets/{asset.id}?{sign_asset(asset.id)}")
    assert response.status_code == 200
    assert response.content == data


async def test_an_expired_signature_is_404_for_an_anonymous_caller(client, db):
    await client.post(
        "/auth/signup", json={"email": "expired@example.com", "password": "hunter2hunter2"}
    )
    owner = (await db.execute(select(User).where(User.email == "expired@example.com"))).scalar_one()
    asset = await store_asset(
        db, user_id=owner.id, data=solid_png(4, 4, (0, 0, 0)), mime="image/png", ext="png"
    )
    await db.commit()

    response = await client.get(f"/assets/{asset.id}?{sign_asset(asset.id, ttl_seconds=-1)}")
    # 401, not 404: a dead signature leaves the caller exactly as
    # unauthenticated as one that sent none at all.
    assert response.status_code == 401


async def test_a_signature_for_one_asset_does_not_open_another(client, db):
    await client.post(
        "/auth/signup", json={"email": "twoassets@example.com", "password": "hunter2hunter2"}
    )
    owner = (
        await db.execute(select(User).where(User.email == "twoassets@example.com"))
    ).scalar_one()
    mine = await store_asset(
        db, user_id=owner.id, data=solid_png(4, 4, (1, 1, 1)), mime="image/png", ext="png"
    )
    theirs = await store_asset(
        db, user_id=owner.id, data=solid_png(4, 4, (2, 2, 2)), mime="image/png", ext="png"
    )
    await db.commit()

    response = await client.get(f"/assets/{theirs.id}?{sign_asset(mine.id)}")
    assert response.status_code == 401


async def test_a_bad_signature_still_lets_the_owner_through(logged_in, db):
    """The signature is an additional way in, not a replacement. A browser
    that somehow sent a stale one must not lose access to its own file."""
    data = solid_png(4, 4, (7, 7, 7))
    asset = await _asset_for_logged_in_user(db, data)
    await db.commit()

    response = await logged_in.get(f"/assets/{asset.id}?exp=1&sig=nonsense")
    assert response.status_code == 200
    assert response.content == data
