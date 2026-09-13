import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.assets import service
from framemeld.auth.deps import CurrentUser
from framemeld.db import get_session
from framemeld.storage.registry import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assets", tags=["assets"])

Db = Annotated[DbSession, Depends(get_session)]

#: A given asset id always names the same bytes -- ids are never reused and
#: content is never rewritten -- so it can be cached forever. `private`
#: because it is one user's artwork and no shared proxy may keep a copy.
_CACHE_CONTROL = "private, max-age=31536000, immutable"


def _not_found() -> HTTPException:
    """A fresh instance per raise -- see melds/router.py for why."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.get("/{asset_id}")
async def get_asset(asset_id: uuid.UUID, user: CurrentUser, db: Db) -> Response:
    asset = await service.get_asset(db, user.id, asset_id)
    if asset is None:
        raise _not_found()

    try:
        data = await get_storage().get(key=asset.storage_key)
    except FileNotFoundError:
        # A row pointing at bytes that are gone. The user gets the same 404
        # either way, but this is an inconsistency somebody has to fix, so
        # it is logged at ERROR rather than swallowed into the ordinary
        # not-found path.
        logger.error("asset %s has no file at %s", asset.id, asset.storage_key)
        raise _not_found() from None

    return Response(content=data, media_type=asset.mime, headers={"Cache-Control": _CACHE_CONTROL})
