import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.auth.deps import CurrentUser
from framemeld.credits.service import InsufficientCredits
from framemeld.db import get_session
from framemeld.jobs.queue import Queue, get_queue
from framemeld.jobs.schemas import JobOut
from framemeld.moderation.service import Blocked
from framemeld.render import service
from framemeld.render.schemas import FilmOut, MusicOut, RenderOut

router = APIRouter(prefix="/melds/{meld_id}", tags=["film"])

Db = Annotated[DbSession, Depends(get_session)]
Jobs = Annotated[Queue, Depends(get_queue)]

GENERATE_MUSIC_TASK = "generate_music"
RENDER_PREVIEW_TASK = "render_preview"
RENDER_FINAL_TASK = "render_final"


def _not_found() -> HTTPException:
    """A fresh instance per raise — see melds/router.py for why."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


async def _cannot_pay(db: DbSession, exc: InsufficientCredits) -> HTTPException:
    """402, and rolled back first — see shots/router.py for why both."""
    await db.rollback()
    return HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail=f"This costs {exc.required} credits and you have {exc.available}.",
    )


async def _blocked(db: DbSession, exc: Blocked) -> HTTPException:
    """Committed, not rolled back: spec 4.5 wants the verdict recorded."""
    await db.commit()
    return _unprocessable(exc.reason)


@router.get("/film", response_model=FilmOut)
async def get_film(meld_id: uuid.UUID, user: CurrentUser, db: Db) -> FilmOut:
    view = await service.get_film(db, user.id, meld_id)
    if view is None:
        raise _not_found()

    return FilmOut(
        shot_count=view.shot_count,
        stills_ready=view.stills_ready,
        clips_ready=view.clips_ready,
        clips_stale=view.clips_stale,
        duration_sec=view.duration_sec,
        music=MusicOut.model_validate(view.music) if view.music is not None else None,
        preview=RenderOut.model_validate(view.render) if view.render is not None else None,
        final=RenderOut.model_validate(view.final) if view.final is not None else None,
    )


@router.post("/film/music", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
async def generate_music(meld_id: uuid.UUID, user: CurrentUser, db: Db, jobs: Jobs):
    try:
        dispatch = await service.request_music(db, user.id, meld_id)
    except service.NothingToRender as exc:
        raise _unprocessable(exc.reason) from None
    except Blocked as exc:
        raise await _blocked(db, exc) from None
    except InsufficientCredits as exc:
        raise await _cannot_pay(db, exc) from None
    if dispatch is None:
        raise _not_found()

    # Committed before the message is pushed — the worker resolves the job
    # in its own session and would otherwise race a row that does not exist.
    await db.commit()
    if dispatch.enqueued:
        await jobs.enqueue(GENERATE_MUSIC_TASK, dispatch.job.id)
    return dispatch.job


@router.post("/film/preview", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
async def render_preview(meld_id: uuid.UUID, user: CurrentUser, db: Db, jobs: Jobs):
    try:
        dispatch = await service.request_preview(db, user.id, meld_id)
    except service.MissingStills as exc:
        raise _unprocessable(exc.reason) from None
    except service.NothingToRender as exc:
        raise _unprocessable(exc.reason) from None
    if dispatch is None:
        raise _not_found()

    await db.commit()
    if dispatch.enqueued:
        await jobs.enqueue(RENDER_PREVIEW_TASK, dispatch.job.id)
    return dispatch.job


@router.post("/film/final", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
async def render_final(meld_id: uuid.UUID, user: CurrentUser, db: Db, jobs: Jobs):
    try:
        dispatch = await service.request_final(db, user.id, meld_id)
    except service.MissingClips as exc:
        raise _unprocessable(exc.reason) from None
    except service.MissingStills as exc:
        raise _unprocessable(exc.reason) from None
    except service.NothingToRender as exc:
        raise _unprocessable(exc.reason) from None
    except InsufficientCredits as exc:
        raise await _cannot_pay(db, exc) from None
    if dispatch is None:
        raise _not_found()

    await db.commit()
    if dispatch.enqueued:
        await jobs.enqueue(RENDER_FINAL_TASK, dispatch.job.id)
    return dispatch.job
