from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from framemeld.auth.sessions import issue_session, resolve_session, revoke_session
from framemeld.models.user import Session, User


async def _make_user(db, email="u@example.com") -> User:
    user = User(email=email, password_hash="x")
    db.add(user)
    await db.flush()
    return user


async def test_issue_returns_a_long_opaque_token(db):
    user = await _make_user(db)
    token = await issue_session(db, user.id)
    assert isinstance(token, str)
    assert len(token) >= 32


async def test_raw_token_is_not_stored_in_the_database(db):
    user = await _make_user(db)
    token = await issue_session(db, user.id)

    stored = (await db.execute(select(Session))).scalars().all()
    assert len(stored) == 1
    assert stored[0].token_hash != token


async def test_resolve_returns_the_user(db):
    user = await _make_user(db)
    token = await issue_session(db, user.id)

    resolved = await resolve_session(db, token)
    assert resolved is not None
    assert resolved.id == user.id


async def test_resolve_returns_none_for_an_unknown_token(db):
    assert await resolve_session(db, "nonsense") is None


async def test_resolve_returns_none_for_an_expired_session(db):
    user = await _make_user(db)
    token = await issue_session(db, user.id)

    session = (await db.execute(select(Session))).scalar_one()
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.flush()

    assert await resolve_session(db, token) is None


async def test_resolve_returns_none_after_revoke(db):
    user = await _make_user(db)
    token = await issue_session(db, user.id)

    await revoke_session(db, token)

    assert await resolve_session(db, token) is None


async def test_resolve_returns_none_for_a_soft_deleted_user(db):
    """A deleted account's live session must stop working immediately."""
    user = await _make_user(db)
    token = await issue_session(db, user.id)

    user.deleted_at = datetime.now(UTC)
    await db.flush()

    assert await resolve_session(db, token) is None


async def test_two_sessions_for_one_user_are_independent(db):
    user = await _make_user(db)
    first = await issue_session(db, user.id)
    second = await issue_session(db, user.id)

    await revoke_session(db, first)

    assert await resolve_session(db, first) is None
    assert await resolve_session(db, second) is not None
