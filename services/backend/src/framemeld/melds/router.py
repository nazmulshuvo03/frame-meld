import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.auth.deps import CurrentUser
from framemeld.db import get_session
from framemeld.melds import service
from framemeld.melds.schemas import (
    MeldCreate,
    MeldDetail,
    MeldOut,
    MeldRename,
    MeldSummary,
    StoryOut,
    StorySave,
)

router = APIRouter(prefix="/melds", tags=["melds"])

Db = Annotated[DbSession, Depends(get_session)]


def _not_found() -> HTTPException:
    """A fresh instance per raise.

    A module-level HTTPException would accumulate tracebacks — raising an
    instance that already has a __traceback__ appends to the chain — and
    each retained frame holds that request's session and user. 404 is the
    most frequently hit path here, so that grows without bound.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meld not found")


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MeldOut)
async def create_meld(payload: MeldCreate, user: CurrentUser, db: Db):
    meld = await service.create_meld(db, user.id, payload.title)
    await db.commit()
    return meld


@router.get("", response_model=list[MeldSummary])
async def list_melds(user: CurrentUser, db: Db):
    return await service.list_melds(db, user.id)


@router.get("/{meld_id}", response_model=MeldDetail)
async def get_meld(meld_id: uuid.UUID, user: CurrentUser, db: Db):
    meld = await service.get_meld(db, user.id, meld_id)
    if meld is None:
        raise _not_found()
    return meld


@router.patch("/{meld_id}", response_model=MeldOut)
async def rename_meld(meld_id: uuid.UUID, payload: MeldRename, user: CurrentUser, db: Db):
    meld = await service.rename_meld(db, user.id, meld_id, payload.title)
    if meld is None:
        raise _not_found()
    await db.commit()
    return meld


@router.delete("/{meld_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meld(meld_id: uuid.UUID, user: CurrentUser, db: Db) -> None:
    if not await service.soft_delete_meld(db, user.id, meld_id):
        raise _not_found()
    await db.commit()


@router.put("/{meld_id}/story", response_model=StoryOut)
async def save_story(meld_id: uuid.UUID, payload: StorySave, user: CurrentUser, db: Db):
    story = await service.save_story(db, user.id, meld_id, payload.text)
    if story is None:
        raise _not_found()
    await db.commit()
    return story
