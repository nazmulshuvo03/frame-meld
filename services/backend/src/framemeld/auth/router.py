import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.auth.deps import CurrentUser
from framemeld.auth.passwords import hash_password, verify_password
from framemeld.auth.sessions import issue_session, revoke_session
from framemeld.config import settings
from framemeld.db import get_session
from framemeld.models.user import User
from framemeld.ratelimit import clear, record_failure, retry_after
from framemeld.redis_client import get_redis

router = APIRouter(prefix="/auth", tags=["auth"])

Db = Annotated[DbSession, Depends(get_session)]
Cache = Annotated[Redis, Depends(get_redis)]


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        samesite="lax",
        secure=False,  # M1 runs on http://localhost; set True behind TLS
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        path="/",
    )


def _login_keys(request: Request, email: str) -> tuple[str, str]:
    """Two budgets, because they stop different attacks: the per-IP one
    bounds spraying many accounts from one place, the per-email one bounds
    hammering one account from many places.

    request.client is None when no peer address is available (an ASGI
    transport in tests, some proxies). Bucketing those together under one
    key is the safe direction — it over-limits rather than under-limits.

    Note this trusts the socket address, not X-Forwarded-For. Behind a
    proxy every request would share one key, so whoever adds the proxy
    must also add a trusted-host/proxy-headers middleware.
    """
    ip = request.client.host if request.client else "unknown"
    return f"rl:login:ip:{ip}", f"rl:login:email:{email.lower()}"


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def signup(credentials: Credentials, db: Db) -> User:
    existing = await db.execute(select(User).where(User.email == credentials.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=credentials.email, password_hash=hash_password(credentials.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Two concurrent signups for the same address both passed the check
        # above. The unique index is the real guard; convert the resulting
        # error into the same 409 the sequential path returns.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from None
    await db.refresh(user)
    return user


@router.post("/login", response_model=UserOut)
async def login(
    credentials: Credentials, request: Request, response: Response, db: Db, cache: Cache
) -> User:
    ip_key, email_key = _login_keys(request, credentials.email)
    budgets = (
        (ip_key, settings.login_max_attempts_per_ip),
        (email_key, settings.login_max_attempts_per_email),
    )
    for key, limit in budgets:
        wait = await retry_after(cache, key, limit)
        if wait is not None:
            # Checked before the password is verified, so a limited caller
            # cannot use the status code to tell a right password from a
            # wrong one.
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Try again later.",
                headers={"Retry-After": str(wait)},
            )

    result = await db.execute(
        select(User).where(User.email == credentials.email, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    # One error for both "no such user" and "wrong password", so the
    # response body cannot be used to enumerate registered emails.
    # Note the response *time* still can: argon2 only runs when a user
    # matched, so a wrong-password login is measurably slower than an
    # unknown-email one. Signup's 409 already reveals the same fact, so
    # this is accepted for now — but if signup ever stops enumerating,
    # this needs a dummy-hash verify on the user-is-None path.
    if user is None or not verify_password(credentials.password, user.password_hash):
        for key, _ in budgets:
            await record_failure(cache, key, settings.login_window_seconds)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    # Only the email budget. See _login_keys and the config comment.
    await clear(cache, email_key)

    raw_token = await issue_session(db, user.id)
    await db.commit()
    _set_session_cookie(response, raw_token)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: Db) -> None:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        await revoke_session(db, raw_token)
        await db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
