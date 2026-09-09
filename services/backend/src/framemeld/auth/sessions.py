import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.config import settings
from framemeld.models.user import Session, User

TOKEN_BYTES = 32


def _hash_token(raw_token: str) -> str:
    """Session tokens are high-entropy random values, so a plain SHA-256 is
    the right tool here — unlike passwords, there is nothing to brute-force,
    and we need lookups to be fast and deterministic."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def issue_session(db: DbSession, user_id: uuid.UUID) -> str:
    raw_token = secrets.token_urlsafe(TOKEN_BYTES)
    db.add(
        Session(
            user_id=user_id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(days=settings.session_ttl_days),
        )
    )
    await db.flush()
    return raw_token


async def resolve_session(db: DbSession, raw_token: str) -> User | None:
    result = await db.execute(
        select(User)
        .join(Session, Session.user_id == User.id)
        .where(
            Session.token_hash == _hash_token(raw_token),
            Session.revoked_at.is_(None),
            Session.expires_at > datetime.now(UTC),
            User.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def revoke_session(db: DbSession, raw_token: str) -> None:
    result = await db.execute(select(Session).where(Session.token_hash == _hash_token(raw_token)))
    session = result.scalar_one_or_none()
    if session is not None:
        session.revoked_at = datetime.now(UTC)
        await db.flush()
