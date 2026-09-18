import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.orm import selectinload

from framemeld.cast.hashes import (
    character_image_input_hash,
    characters_input_hash,
    content_hash_for_character,
    visual_hash_for_character,
)
from framemeld.credits.pricing import quote_for
from framemeld.credits.service import hold
from framemeld.jobs.service import JobDispatch, ensure_job
from framemeld.melds.service import get_meld
from framemeld.models.cast import Character, CharacterImage, CharacterImageKind, CharacterRole
from framemeld.models.job import Job, JobKind
from framemeld.models.script import ArtifactState, Shot
from framemeld.moderation.service import Blocked, check_and_record
from framemeld.providers.speech.voices import voice_for_index
from framemeld.script.hashes import content_hash_for_shot
from framemeld.staleness import recompute_staleness


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

    # Before ensure_job and before the hold (spec 4.5). A blocked story that
    # reached the queue would mean a held credit and a paid provider call
    # for something we were never going to keep.
    verdict = await check_and_record(db, meld, meld.story.text)
    if not verdict.allowed:
        raise Blocked(verdict.reason or "This story can't be used to generate images.")

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

    # Again before the hold. This one gates the expensive call: an image
    # provider bills us for a refusal the same as for a picture.
    verdict = await check_and_record(db, meld, character.appearance)
    if not verdict.allowed:
        raise Blocked(verdict.reason or "This description can't be used to generate an image.")

    input_hash = character_image_input_hash(
        visual_hash_for_character(character), CharacterImageKind.SHEET
    )

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

    # An edit here is an upstream change for this character's artwork, the
    # same way a story edit is for the cast. Without this the sheet drawn
    # from an old `appearance` would keep claiming to be fresh -- which is
    # the milestone's headline promise, silently not happening.
    await recompute_staleness(db, meld_id)
    return character


async def add_character(db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID) -> Character | None:
    """Append an empty character for the user to describe.

    Its `input_hash` is the cast's current expectation rather than "" -- the
    character was not extracted, but they belong to this cast, so
    regenerating from the story marks them stale along with everyone else
    instead of leaving them permanently and misleadingly out of date.

    A voice is picked deterministically from the provider's list, the same
    way extraction picks one: a character with no voice reads in the default
    narrator's, which silently makes two people sound identical.
    """
    meld = await get_meld(db, user_id, meld_id)
    if meld is None or meld.story is None:
        return None

    now = datetime.now(UTC)
    existing = list(
        (
            await db.execute(
                select(Character)
                .where(Character.meld_id == meld_id)
                .order_by(Character.order_index)
            )
        ).scalars()
    )
    character = Character(
        meld_id=meld_id,
        order_index=len(existing),
        name="New character",
        role=CharacterRole.SUPPORTING,
        description="",
        appearance="",
        voice_id=voice_for_index(len(existing)),
        input_hash=characters_input_hash(meld.story.content_hash),
        state=ArtifactState.FRESH,
        # Populated rather than left unloaded: the response serialiser
        # reads `images` to find the canonical sheet, and a lazy load
        # there happens outside the async context and raises
        # MissingGreenlet. A new character genuinely has none.
        images=[],
        created_at=now,
        updated_at=now,
    )
    character.content_hash = content_hash_for_character(character)
    db.add(character)
    meld.updated_at = now
    await db.flush()
    await recompute_staleness(db, meld_id)
    return character


async def delete_character(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, character_id: uuid.UUID
) -> bool | None:
    """Remove a character, their sheet, and their place in every shot.

    The `shot_characters` join rows cascade, which is exactly why spec 5.2
    made it a real table rather than a uuid[]: a deleted character leaves no
    dangling id inside a shot. The stills that referenced their sheet keep
    it in `ref_character_image_ids` -- that column is a historical record of
    what was actually drawn, not a live relation, and it has to survive to
    explain a picture that exists.

    Those stills then go stale on the next recompute, because the shot no
    longer references a sheet they were drawn from. That is correct and it
    is the point: the picture still contains a face the film no longer has.
    """
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

    # Shots this character was the speaker of would otherwise hold a
    # dangling id: `speaker_character_id` is a real FK with no ON DELETE, so
    # this is a constraint violation rather than a silent one -- but the
    # right answer is still to clear it, because the line survives the
    # person who said it and can be re-attributed.
    for shot in (
        await db.execute(select(Shot).where(Shot.speaker_character_id == character_id))
    ).scalars():
        shot.speaker_character_id = None
        shot.content_hash = content_hash_for_shot(shot)

    await db.delete(character)
    await db.flush()

    now = datetime.now(UTC)
    for index, row in enumerate(
        (
            await db.execute(
                select(Character)
                .where(Character.meld_id == meld_id)
                .order_by(Character.order_index)
            )
        ).scalars()
    ):
        if row.order_index != index:
            row.order_index = index
            row.updated_at = now

    meld.updated_at = now
    await db.flush()
    await recompute_staleness(db, meld_id)
    return True
