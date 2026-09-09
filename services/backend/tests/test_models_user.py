from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from framemeld.models.user import Session, User


async def test_user_persists_with_generated_id_and_timestamp(db):
    user = User(email="a@example.com", password_hash="x")
    db.add(user)
    await db.flush()

    assert user.id is not None
    assert user.created_at is not None
    assert user.deleted_at is None


async def test_email_is_unique(db):
    db.add(User(email="dup@example.com", password_hash="x"))
    await db.flush()

    db.add(User(email="dup@example.com", password_hash="y"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_session_links_to_user(db):
    user = User(email="s@example.com", password_hash="x")
    db.add(user)
    await db.flush()

    session = Session(
        user_id=user.id,
        token_hash="deadbeef",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(session)
    await db.flush()

    found = (await db.execute(select(Session).where(Session.token_hash == "deadbeef"))).scalar_one()
    assert found.user_id == user.id
    assert found.revoked_at is None
