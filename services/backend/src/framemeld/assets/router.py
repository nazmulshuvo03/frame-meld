import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.assets import service
from framemeld.assets.signing import verify_asset_signature
from framemeld.auth.sessions import resolve_session
from framemeld.config import settings
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
async def get_asset(
    asset_id: uuid.UUID,
    request: Request,
    db: Db,
    exp: Annotated[str | None, Query()] = None,
    sig: Annotated[str | None, Query()] = None,
) -> Response:
    """One asset's bytes, to a signed-in owner or to a valid signature.

    Two ways in rather than one, because the render service has neither a
    session nor a database (spec section 3.1) and still has to fetch every
    still and clip in a film. CurrentUser is deliberately *not* a
    dependency here: it raises 401 before a handler runs, which would make
    the signed path unreachable.

    A valid signature is checked first and is sufficient on its own. It is
    scoped to this one asset id and expires (M5-D1), so it grants exactly
    what the render service needs and nothing wider. A signature that is
    absent, expired or wrong falls through to the session, so a browser
    request is unaffected.
    """
    signed = verify_asset_signature(asset_id, exp, sig)

    asset = await service.get_asset_unscoped(db, asset_id) if signed else None
    if asset is None:
        raw_token = request.cookies.get(settings.session_cookie_name)
        user = await resolve_session(db, raw_token) if raw_token else None
        if user is None:
            # 401, exactly as before signatures existed. It leaks nothing --
            # this answer is identical whether or not the asset is real --
            # and it is the answer the rest of the API gives an
            # unauthenticated caller.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )
        asset = await service.get_asset(db, user.id, asset_id)

    # 404 rather than 403 when the caller is signed in but does not own
    # this asset: 403 would confirm it exists.
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
