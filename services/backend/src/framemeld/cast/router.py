import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.auth.deps import CurrentUser
from framemeld.cast import service
from framemeld.cast.schemas import CastOut, CharacterImageOut, CharacterOut, CharacterPatch
from framemeld.credits.service import InsufficientCredits
from framemeld.db import get_session
from framemeld.jobs.queue import Queue, get_queue
from framemeld.jobs.schemas import JobOut
from framemeld.moderation.service import Blocked
from framemeld.providers.speech.voices import VOICE_IDS

router = APIRouter(prefix="/melds/{meld_id}", tags=["cast"])

Db = Annotated[DbSession, Depends(get_session)]
Jobs = Annotated[Queue, Depends(get_queue)]

GENERATE_CHARACTERS_TASK = "generate_characters"
GENERATE_CHARACTER_IMAGE_TASK = "generate_character_image"


def _not_found() -> HTTPException:
    """A fresh instance per raise — see melds/router.py for why."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def _cannot_pay(db: DbSession, exc: InsufficientCredits) -> HTTPException:
    """402, not 403: the request is well-formed and authorised, it just
    cannot be paid for. The numbers are in the body so the UI can say how
    short the user is rather than only that they are.

    Rolls back first. The hold is taken after the job row exists (it needs
    the job id), so a refused hold leaves an uncommitted job in the
    session. Today that row would vanish anyway when get_session closes
    without committing -- but "nothing was created" is the guarantee this
    endpoint makes about a user's money, and it should not rest on nobody
    ever adding a commit somewhere later in the request.
    """
    await db.rollback()
    return HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail=f"This costs {exc.required} credits and you have {exc.available}.",
    )


def _as_character(character) -> CharacterOut:
    sheet = service.sheet_of(character)
    return CharacterOut(
        id=character.id,
        order_index=character.order_index,
        name=character.name,
        role=character.role,
        description=character.description,
        appearance=character.appearance,
        voice_id=character.voice_id,
        state=character.state,
        content_hash=character.content_hash,
        updated_at=character.updated_at,
        sheet=CharacterImageOut.model_validate(sheet) if sheet is not None else None,
    )


@router.get("/voices", response_model=list[str])
async def get_voices(meld_id: uuid.UUID, user: CurrentUser, db: Db) -> list[str]:
    """The voices a character may be given.

    Served rather than hard-coded in the frontend so the list cannot drift
    from the one the speech adapter will actually accept -- a voice the
    provider rejects is a failed job and a refunded credit, for a value the
    UI offered.

    Authorised through the Meld like every other route under this prefix,
    even though the list is the same for everybody: an anonymous endpoint
    is a thing to think about, and nothing needs one.
    """
    if await service.get_cast(db, user.id, meld_id) is None:
        raise _not_found()
    return list(VOICE_IDS)


@router.get("/cast", response_model=CastOut)
async def get_cast(meld_id: uuid.UUID, user: CurrentUser, db: Db) -> CastOut:
    characters = await service.get_cast(db, user.id, meld_id)
    if characters is None:
        raise _not_found()

    stale_characters, stale_sheets = service.count_stale(characters)
    return CastOut(
        characters=[_as_character(character) for character in characters],
        stale_characters=stale_characters,
        stale_sheets=stale_sheets,
    )


@router.post("/cast/generate", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
async def generate_cast(meld_id: uuid.UUID, user: CurrentUser, db: Db, jobs: Jobs):
    try:
        dispatch = await service.request_extraction(db, user.id, meld_id)
    except service.EmptyStory:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Write some story first — there is nobody to cast yet.",
        ) from None
    except Blocked as exc:
        # Committed, not rolled back. Spec 4.5 wants flagged content
        # recorded so it can be reviewed, and the only thing pending on
        # this path is the verdict itself -- the refusal happens before any
        # job row or hold exists. Without this the FLAGGED state dies with
        # the request and every retry re-classifies from scratch.
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.reason
        ) from None
    except InsufficientCredits as exc:
        raise await _cannot_pay(db, exc) from None
    if dispatch is None:
        raise _not_found()

    # Committed *before* the message is pushed. The worker resolves the job
    # by id in its own session, so enqueueing first would race it to a row
    # that does not exist yet.
    await db.commit()
    if dispatch.enqueued:
        await jobs.enqueue(GENERATE_CHARACTERS_TASK, dispatch.job.id)
    return dispatch.job


@router.post(
    "/characters/{character_id}/sheet",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobOut,
)
async def generate_sheet(
    meld_id: uuid.UUID, character_id: uuid.UUID, user: CurrentUser, db: Db, jobs: Jobs
):
    try:
        dispatch = await service.request_sheet(db, user.id, meld_id, character_id)
    except service.EmptyAppearance:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Describe what this character looks like first.",
        ) from None
    except Blocked as exc:
        # Committed, not rolled back. Spec 4.5 wants flagged content
        # recorded so it can be reviewed, and the only thing pending on
        # this path is the verdict itself -- the refusal happens before any
        # job row or hold exists. Without this the FLAGGED state dies with
        # the request and every retry re-classifies from scratch.
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.reason
        ) from None
    except InsufficientCredits as exc:
        raise await _cannot_pay(db, exc) from None
    if dispatch is None:
        raise _not_found()

    await db.commit()
    if dispatch.enqueued:
        await jobs.enqueue(GENERATE_CHARACTER_IMAGE_TASK, dispatch.job.id)
    return dispatch.job


@router.patch("/characters/{character_id}", response_model=CharacterOut)
async def patch_character(
    meld_id: uuid.UUID,
    character_id: uuid.UUID,
    payload: CharacterPatch,
    user: CurrentUser,
    db: Db,
):
    character = await service.update_character(
        db, user.id, meld_id, character_id, payload.model_dump(exclude_unset=True)
    )
    if character is None:
        raise _not_found()
    await db.commit()
    return _as_character(character)


@router.post("/characters", status_code=status.HTTP_201_CREATED, response_model=CharacterOut)
async def add_character(meld_id: uuid.UUID, user: CurrentUser, db: Db) -> CharacterOut:
    """Add somebody the extraction missed.

    Spec section 2's "the user is in control at every stage": until now the
    only way to add a character was to regenerate the cast, which replaces
    rather than merges and takes every sheet already paid for with it.
    """
    character = await service.add_character(db, user.id, meld_id)
    if character is None:
        raise _not_found()
    await db.commit()
    # Through _as_character like every other route here: CharacterOut
    # carries the canonical sheet, which is derived from the character's
    # images rather than read off a column.
    return _as_character(character)


@router.delete("/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(
    meld_id: uuid.UUID, character_id: uuid.UUID, user: CurrentUser, db: Db
) -> None:
    if await service.delete_character(db, user.id, meld_id, character_id) is None:
        raise _not_found()
    await db.commit()
