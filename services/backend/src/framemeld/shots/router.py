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
from framemeld.script.schemas import ShotOut
from framemeld.shots import service
from framemeld.shots.schemas import (
    AudioClipOut,
    ShotClipOut,
    ShotImageOut,
    ShotMediaOut,
    ShotsOut,
)

router = APIRouter(prefix="/melds/{meld_id}", tags=["shots"])

Db = Annotated[DbSession, Depends(get_session)]
Jobs = Annotated[Queue, Depends(get_queue)]

MAP_SHOT_CHARACTERS_TASK = "map_shot_characters"
GENERATE_SHOT_IMAGE_TASK = "generate_shot_image"
GENERATE_SPEECH_TASK = "generate_speech"
GENERATE_SHOT_CLIP_TASK = "generate_shot_clip"


def _not_found() -> HTTPException:
    """A fresh instance per raise — see melds/router.py for why."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


async def _cannot_pay(db: DbSession, exc: InsufficientCredits) -> HTTPException:
    """402, not 403: the request is well-formed and authorised, it just
    cannot be paid for. Rolls back first, for the reason cast/router.py
    spells out — the hold runs after the job row exists, so a refused hold
    leaves an uncommitted job in the session, and "nothing was created" is
    a guarantee about a user's money that should not rest on nobody adding
    a commit later in the request.
    """
    await db.rollback()
    return HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail=f"This costs {exc.required} credits and you have {exc.available}.",
    )


async def _blocked(db: DbSession, exc: Blocked) -> HTTPException:
    """Committed, not rolled back. Spec 4.5 wants flagged content recorded
    so it can be reviewed, and the only thing pending on this path is the
    verdict itself — the refusal happens before any job row or hold exists.
    """
    await db.commit()
    return _unprocessable(exc.reason)


def _as_media(item: service.ShotMedia) -> ShotMediaOut:
    return ShotMediaOut(
        # The shot's own fields come through ShotOut so the two pages agree
        # on them by construction; only the generated halves are added here.
        **ShotOut.model_validate(item.shot).model_dump(),
        character_ids=[character.id for character in item.characters],
        character_names=[character.name for character in item.characters],
        still=ShotImageOut.model_validate(item.still) if item.still is not None else None,
        audio=AudioClipOut.model_validate(item.audio) if item.audio is not None else None,
        clip=ShotClipOut.model_validate(item.clip) if item.clip is not None else None,
    )


@router.get("/shots", response_model=ShotsOut)
async def get_shots(meld_id: uuid.UUID, user: CurrentUser, db: Db) -> ShotsOut:
    media = await service.get_shots(db, user.id, meld_id)
    if media is None:
        raise _not_found()

    stale_stills, stale_audio = service.count_stale(media)
    return ShotsOut(
        shots=[_as_media(item) for item in media],
        stale_stills=stale_stills,
        stale_audio=stale_audio,
        cast_into_shots=any(item.characters for item in media),
    )


@router.post("/shots/cast", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
async def cast_shots(meld_id: uuid.UUID, user: CurrentUser, db: Db, jobs: Jobs):
    try:
        dispatch = await service.request_casting(db, user.id, meld_id)
    except service.NothingToCast as exc:
        raise _unprocessable(exc.reason) from None
    except Blocked as exc:
        raise await _blocked(db, exc) from None
    except InsufficientCredits as exc:
        raise await _cannot_pay(db, exc) from None
    if dispatch is None:
        raise _not_found()

    # Committed *before* the message is pushed. The worker resolves the job
    # by id in its own session, so enqueueing first would race it to a row
    # that does not exist yet.
    await db.commit()
    if dispatch.enqueued:
        await jobs.enqueue(MAP_SHOT_CHARACTERS_TASK, dispatch.job.id)
    return dispatch.job


@router.post("/shots/{shot_id}/still", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
async def generate_still(
    meld_id: uuid.UUID, shot_id: uuid.UUID, user: CurrentUser, db: Db, jobs: Jobs
):
    try:
        dispatch = await service.request_still(db, user.id, meld_id, shot_id)
    except service.MissingSheets as exc:
        raise _unprocessable(exc.reason) from None
    except service.NothingToCast as exc:
        raise _unprocessable(exc.reason) from None
    except Blocked as exc:
        raise await _blocked(db, exc) from None
    except InsufficientCredits as exc:
        raise await _cannot_pay(db, exc) from None
    if dispatch is None:
        raise _not_found()

    await db.commit()
    if dispatch.enqueued:
        await jobs.enqueue(GENERATE_SHOT_IMAGE_TASK, dispatch.job.id)
    return dispatch.job


@router.post("/shots/{shot_id}/speech", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
async def generate_speech(
    meld_id: uuid.UUID, shot_id: uuid.UUID, user: CurrentUser, db: Db, jobs: Jobs
):
    try:
        dispatch = await service.request_speech(db, user.id, meld_id, shot_id)
    except service.NothingToSay:
        raise _unprocessable(
            "Write the line first, and set this shot to narration or dialogue."
        ) from None
    except Blocked as exc:
        raise await _blocked(db, exc) from None
    except InsufficientCredits as exc:
        raise await _cannot_pay(db, exc) from None
    if dispatch is None:
        raise _not_found()

    await db.commit()
    if dispatch.enqueued:
        await jobs.enqueue(GENERATE_SPEECH_TASK, dispatch.job.id)
    return dispatch.job


@router.post("/shots/{shot_id}/clip", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
async def generate_clip(
    meld_id: uuid.UUID, shot_id: uuid.UUID, user: CurrentUser, db: Db, jobs: Jobs
):
    try:
        dispatch = await service.request_clip(db, user.id, meld_id, shot_id)
    except Blocked as exc:
        raise await _blocked(db, exc) from None
    except InsufficientCredits as exc:
        raise await _cannot_pay(db, exc) from None
    if dispatch is None:
        raise _not_found()

    await db.commit()
    if dispatch.enqueued:
        await jobs.enqueue(GENERATE_SHOT_CLIP_TASK, dispatch.job.id)
    return dispatch.job
