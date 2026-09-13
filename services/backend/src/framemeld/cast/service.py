import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.orm import selectinload

from framemeld.cast.hashes import (
    character_image_input_hash,
    characters_input_hash,
    content_hash_for_character,
)
from framemeld.credits.pricing import quote_for
from framemeld.credits.service import hold
from framemeld.jobs.service import JobDispatch, ensure_job
from framemeld.melds.service import get_meld
from framemeld.models.cast import Character, CharacterImage, CharacterImageKind
from framemeld.models.job import Job, JobKind
from framemeld.models.script import ArtifactState


class EmptyStory(Exception):
    """Extraction was asked for before there was anything to extract from.

    Refused in the API rather than in the job, so it costs a 422 instead of
    a held credit and a provider call that can only fail.
    """


class EmptyAppearance(Exception):
    """A sheet was asked for with nothing to draw.

    Same reasoning as EmptyStory, and it matters more here: an image call
    is the expensive one, and "draw nothing" is a prompt a real model
    answers with something arbitrary rather than an error.
    """


async def get_cast(db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID) -> list[Character] | None:
    """Every character in one Meld, with images eager-loaded, or None if the
    Meld is not this user's.

    Goes through get_meld first: that is the only place a Meld is resolved
    by id, and it is what makes an unauthorised read a 404.
    """
    if await get_meld(db, user_id, meld_id) is None:
        return None

    result = await db.execute(
        select(Character)
        .options(selectinload(Character.images))
        .where(Character.meld_id == meld_id)
        .order_by(Character.order_index)
    )
    return list(result.scalars().all())


def sheet_of(character: Character) -> CharacterImage | None:
    """The one sheet, or None. M3 produces exactly one image per character
    (M3-D8), so this is a lookup rather than a filter over kinds."""
    return next(
        (image for image in character.images if image.kind is CharacterImageKind.SHEET), None
    )


def count_stale(characters: list[Character]) -> tuple[int, int]:
    stale_characters = sum(1 for character in characters if character.state is ArtifactState.STALE)
    stale_sheets = sum(
        1
        for character in characters
        for image in character.images
        if image.state is ArtifactState.STALE
    )
    return stale_characters, stale_sheets


async def request_extraction(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID
) -> JobDispatch | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None or meld.story is None:
        return None
    if not meld.story.text.strip():
        raise EmptyStory

    dispatch = await ensure_job(
        db,
        meld_id=meld_id,
        kind=JobKind.GENERATE_CHARACTERS,
        target_table="melds",
        target_id=meld_id,
        input_hash=characters_input_hash(meld.story.content_hash),
    )

    if dispatch.enqueued:
        # Held only when work will actually happen. A cache hit -- these
        # exact inputs were extracted before -- is free, per spec 4.1.
        credits = quote_for(JobKind.GENERATE_CHARACTERS)
        await hold(
            db,
            user_id=user_id,
            job_id=dispatch.job.id,
            meld_id=meld_id,
            credits=credits,
            description="cast extraction",
        )
        dispatch.job.credits_held = credits
        await db.flush()

    return dispatch


async def request_sheet(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, character_id: uuid.UUID
) -> JobDispatch | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    character = (
        await db.execute(
            select(Character).where(Character.id == character_id, Character.meld_id == meld_id)
        )
    ).scalar_one_or_none()
    if character is None:
        return None
    if not character.appearance.strip():
        # Refused here rather than in the job, so it costs a 422 instead of
        # a paid image of nothing.
        raise EmptyAppearance

    input_hash = character_image_input_hash(character.content_hash, CharacterImageKind.SHEET)

    # One sheet row per character, rewritten in place on regeneration. The
    # row is created before the job so target_id is stable across retries
    # and the idempotency key means what it says.
    image = (
        await db.execute(
            select(CharacterImage).where(
                CharacterImage.character_id == character.id,
                CharacterImage.kind == CharacterImageKind.SHEET,
            )
        )
    ).scalar_one_or_none()
    if image is None:
        image = CharacterImage(
            character_id=character.id,
            kind=CharacterImageKind.SHEET,
            is_canonical=True,
            input_hash=input_hash,
            state=ArtifactState.GENERATING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(image)
        await db.flush()

    dispatch = await ensure_job(
        db,
        meld_id=meld_id,
        kind=JobKind.GENERATE_CHARACTER_IMAGE,
        target_table="character_images",
        target_id=image.id,
        input_hash=input_hash,
    )

    if dispatch.enqueued:
        # Held only when work will actually happen. A cache hit -- these
        # exact inputs were generated before -- is free, per spec 4.1.
        credits = quote_for(JobKind.GENERATE_CHARACTER_IMAGE)
        await hold(
            db,
            user_id=user_id,
            job_id=dispatch.job.id,
            meld_id=meld_id,
            credits=credits,
            description="character sheet",
        )
        dispatch.job.credits_held = credits
        image.state = ArtifactState.GENERATING
        image.updated_at = datetime.now(UTC)
        await db.flush()

    return dispatch


async def get_job(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, job_id: uuid.UUID
) -> Job | None:
    if await get_meld(db, user_id, meld_id) is None:
        return None
    # Filtered on meld_id as well as id: a job id alone would let any
    # logged-in user poll any other user's job by guessing one.
    return (
        await db.execute(select(Job).where(Job.id == job_id, Job.meld_id == meld_id))
    ).scalar_one_or_none()


async def update_character(
    db: DbSession,
    user_id: uuid.UUID,
    meld_id: uuid.UUID,
    character_id: uuid.UUID,
    fields: dict[str, object],
) -> Character | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    character = (
        await db.execute(
            select(Character)
            .options(selectinload(Character.images))
            .where(Character.id == character_id, Character.meld_id == meld_id)
        )
    ).scalar_one_or_none()
    if character is None:
        return None

    now = datetime.now(UTC)
    for name, value in fields.items():
        setattr(character, name, value)
    character.content_hash = content_hash_for_character(character)
    # D4: `state` is deliberately untouched. A row the user just edited
    # holds what the user wants, so it is not out of date. What moves is
    # content_hash, which is what downstream artifacts read -- and here
    # that means the sheet drawn from this appearance.
    character.updated_at = now
    meld.updated_at = now
    await db.flush()
    return character
