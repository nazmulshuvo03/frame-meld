import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.auth.deps import CurrentUser
from framemeld.auth.passwords import hash_password, verify_password
from framemeld.auth.sessions import issue_session, revoke_session
from framemeld.config import settings
from framemeld.db import get_session
from framemeld.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])

Db = Annotated[DbSession, Depends(get_session)]


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


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def signup(credentials: Credentials, db: Db) -> User:
    existing = await db.execute(select(User).where(User.email == credentials.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=credentials.email, password_hash=hash_password(credentials.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=UserOut)
async def login(credentials: Credentials, response: Response, db: Db) -> User:
    result = await db.execute(
        select(User).where(User.email == credentials.email, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    # One error for both "no such user" and "wrong password", so the
    # response cannot be used to enumerate registered emails.
    if user is None or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

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
