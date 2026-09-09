from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.auth.sessions import resolve_session
from framemeld.config import settings
from framemeld.db import get_session
from framemeld.models.user import User


async def current_user(
    request: Request,
    db: Annotated[DbSession, Depends(get_session)],
) -> User:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = await resolve_session(db, raw_token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    return user


CurrentUser = Annotated[User, Depends(current_user)]
