import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.credits.pricing import quote_for
from framemeld.credits.service import hold
from framemeld.jobs.service import JobDispatch, ensure_job
from framemeld.melds.service import get_meld
from framemeld.models.cast import Character, CharacterImage, CharacterImageKind
from framemeld.models.job import Job, JobKind
from framemeld.models.script import ArtifactState, DialogueKind, Scene, Shot
from framemeld.models.shots import (
    AudioClip,
    AudioKind,
    AudioOwnerType,
    ShotCharacter,
    ShotClip,
    ShotImage,
)
from framemeld.moderation.service import Blocked, check_and_record
from framemeld.providers.speech.voices import DEFAULT_VOICE_ID
from framemeld.shots.generation import shots_in_film_order
from framemeld.shots.hashes import (
    CLIP_PARAMS,
    audio_clip_input_hash,
    cast_hash,
    casting_hash_for_character,
    casting_hash_for_shot,
    motion_hash_for_shot,
    shot_characters_input_hash,
    shot_clip_input_hash,
    shot_image_input_hash,
    speech_hash_for_shot,
    visual_hash_for_shot,
)


class NothingToCast(Exception):
    """Casting was asked for before there was a cast, or before there were
    shots. Refused in the API rather than in the job, so it costs a 422
    instead of a held credit and a provider call that can only fail."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MissingSheets(Exception):
    """A still was asked for while somebody in the shot has no sheet (M4-D6).

    Generating anyway would produce the drifting face spec section 11 lists
    as a high-severity risk, and would leave the arithmetic wrong for good:
    a still whose reference list is missing a character can never go stale
    when that character changes.
    """

    def __init__(self, names: list[str]) -> None:
        self.names = names
        joined = ", ".join(names)
        super().__init__(joined)
        self.reason = (
            f"Draw a character sheet for {joined} first — a still is drawn from the sheets, "
            "so a character without one would come out looking like somebody else."
        )


class NothingToAnimate(Exception):
    """A clip was asked for on a shot with no drawn still (M6-D2).

    Refused rather than generated: image-to-video is conditioned on a
    picture, so there is literally nothing to send. A *stale* still is
    allowed through -- spec section 11's mitigation for character drift is
    that stills are approved before any clip is made, and a user animating
    a still they can see on screen is that approval. Refusing on stale
    would also make a Meld unfinishable after any upstream edit until every
    still had been paid for again.
    """

    def __init__(self) -> None:
        super().__init__("no still")
        self.reason = (
            "Draw this shot's still first — a clip is animated from the picture, "
            "so there is nothing to animate yet."
        )


class NothingToSay(Exception):
    """Speech was asked for on a shot with no line. Same reasoning: a 422
    rather than a paid recording of silence."""


@dataclass(frozen=True)
class ShotMedia:
    """One shot with everything the Shots page draws for it."""

    shot: Shot
    characters: list[Character]
    still: ShotImage | None
    audio: AudioClip | None
    clip: ShotClip | None


async def _owned_shot(db: DbSession, meld_id: uuid.UUID, shot_id: uuid.UUID) -> Shot | None:
    """Joined to Scene so the Meld actually owns this shot. Selecting by
    shot id alone would let a caller spend credits against any shot in the
    system as long as they owned *some* Meld."""
    return (
        await db.execute(
            select(Shot)
            .join(Scene, Shot.scene_id == Scene.id)
            .where(Shot.id == shot_id, Scene.meld_id == meld_id)
        )
    ).scalar_one_or_none()


async def _characters_by_shot(
    db: DbSession, meld_id: uuid.UUID
) -> dict[uuid.UUID, list[Character]]:
    """The mapping, resolved to character rows and kept in film order.

    Ordered by the character's order_index rather than by the join row, so
    a shot's reference sheets are always attached in the same sequence --
    still_prompt names them in that order, and a sequence that shuffled
    between calls would relabel the references and change the picture
    without changing the hash.
    """
    rows = (
        await db.execute(
            select(ShotCharacter.shot_id, Character)
            .join(Character, ShotCharacter.character_id == Character.id)
            .where(Character.meld_id == meld_id)
            .order_by(Character.order_index)
        )
    ).all()
    grouped: dict[uuid.UUID, list[Character]] = {}
    for shot_id, character in rows:
        grouped.setdefault(shot_id, []).append(character)
    return grouped


async def _canonical_sheets(db: DbSession, meld_id: uuid.UUID) -> dict[uuid.UUID, CharacterImage]:
    """Each character's one sheet, keyed by character id.

    Only sheets that actually have bytes: a row in `generating` or `failed`
    has a content_hash of "" and referencing it would put an empty string
    into a still's input_hash, where it would collide with every other
    unfinished sheet.
    """
    rows = (
        await db.execute(
            select(CharacterImage)
            .join(Character, CharacterImage.character_id == Character.id)
            .where(
                Character.meld_id == meld_id,
                CharacterImage.kind == CharacterImageKind.SHEET,
                CharacterImage.asset_id.isnot(None),
            )
        )
    ).scalars()
    return {sheet.character_id: sheet for sheet in rows}


async def get_shots(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID
) -> list[ShotMedia] | None:
    """Every shot in one Meld with its cast, still and audio, or None if the
    Meld is not this user's.

    Goes through get_meld first: that is the only place a Meld is resolved
    by id, and it is what makes an unauthorised read a 404.
    """
    if await get_meld(db, user_id, meld_id) is None:
        return None

    shots = await shots_in_film_order(db, meld_id)
    shot_ids = [shot.id for shot in shots]
    by_shot = await _characters_by_shot(db, meld_id)

    stills = {
        still.shot_id: still
        for still in (
            await db.execute(select(ShotImage).where(ShotImage.shot_id.in_(shot_ids)))
        ).scalars()
    }
    clips = {
        clip.owner_id: clip
        for clip in (
            await db.execute(
                select(AudioClip).where(
                    AudioClip.owner_type == AudioOwnerType.SHOT,
                    AudioClip.owner_id.in_(shot_ids),
                )
            )
        ).scalars()
    }

    videos = {
        video.shot_id: video
        for video in (
            await db.execute(select(ShotClip).where(ShotClip.shot_id.in_(shot_ids)))
        ).scalars()
    }

    return [
        ShotMedia(
            shot=shot,
            characters=by_shot.get(shot.id, []),
            still=stills.get(shot.id),
            audio=clips.get(shot.id),
            clip=videos.get(shot.id),
        )
        for shot in shots
    ]


def count_stale(media: list[ShotMedia]) -> tuple[int, int]:
    stale_stills = sum(
        1 for item in media if item.still is not None and item.still.state is ArtifactState.STALE
    )
    stale_audio = sum(
        1 for item in media if item.audio is not None and item.audio.state is ArtifactState.STALE
    )
    return stale_stills, stale_audio


async def casting_input_hash(db: DbSession, meld_id: uuid.UUID) -> str | None:
    """What the shot/character mapping would be produced from right now.

    Returned by the service rather than recomputed in the worker, so the
    hash that priced the job and the hash the job records are the same
    function of the same rows. None when there is nothing to map.
    """
    characters = list(
        (
            await db.execute(
                select(Character)
                .where(Character.meld_id == meld_id)
                .order_by(Character.order_index)
            )
        ).scalars()
    )
    shots = await shots_in_film_order(db, meld_id)
    if not characters or not shots:
        return None
    return shot_characters_input_hash(
        [casting_hash_for_shot(shot) for shot in shots],
        cast_hash([casting_hash_for_character(character) for character in characters]),
    )


async def request_casting(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID
) -> JobDispatch | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None

    input_hash = await casting_input_hash(db, meld_id)
    if input_hash is None:
        raise NothingToCast(
            "Generate the script and the cast first — there is nobody to place in a shot yet."
        )

    dispatch = await ensure_job(
        db,
        meld_id=meld_id,
        kind=JobKind.MAP_SHOT_CHARACTERS,
        target_table="melds",
        target_id=meld_id,
        input_hash=input_hash,
    )

    if dispatch.enqueued:
        # Held only when work will actually happen. A cache hit -- this
        # exact cast was mapped onto these exact shots before -- is free,
        # per spec 4.1.
        credits = quote_for(JobKind.MAP_SHOT_CHARACTERS)
        await hold(
            db,
            user_id=user_id,
            job_id=dispatch.job.id,
            meld_id=meld_id,
            credits=credits,
            description="casting the shots",
        )
        dispatch.job.credits_held = credits
        await db.flush()

    return dispatch


async def request_still(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, shot_id: uuid.UUID
) -> JobDispatch | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    shot = await _owned_shot(db, meld_id, shot_id)
    if shot is None:
        return None
    if not shot.image_prompt.strip():
        raise NothingToCast("Describe what this shot looks like first.")

    characters = (await _characters_by_shot(db, meld_id)).get(shot.id, [])
    sheets = await _canonical_sheets(db, meld_id)
    missing = [c.name or "this character" for c in characters if c.id not in sheets]
    if missing:
        raise MissingSheets(missing)

    # Before ensure_job and before the hold (spec 4.5). This one gates the
    # expensive call: an image provider bills us for a refusal the same as
    # for a picture.
    verdict = await check_and_record(db, meld, shot.image_prompt)
    if not verdict.allowed:
        raise Blocked(verdict.reason or "This shot can't be used to generate an image.")

    refs = [sheets[character.id] for character in characters]
    input_hash = shot_image_input_hash(
        visual_hash_for_shot(shot), [sheet.content_hash for sheet in refs]
    )

    # One still row per shot, rewritten in place on regeneration. The row is
    # created before the job so target_id is stable across retries and the
    # idempotency key means what it says.
    still = (
        await db.execute(select(ShotImage).where(ShotImage.shot_id == shot.id))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if still is None:
        still = ShotImage(
            shot_id=shot.id,
            ref_character_image_ids=[sheet.id for sheet in refs],
            input_hash=input_hash,
            state=ArtifactState.GENERATING,
            created_at=now,
            updated_at=now,
        )
        db.add(still)
        await db.flush()

    dispatch = await ensure_job(
        db,
        meld_id=meld_id,
        kind=JobKind.GENERATE_SHOT_IMAGE,
        target_table="shot_images",
        target_id=still.id,
        input_hash=input_hash,
    )

    if dispatch.enqueued:
        credits = quote_for(JobKind.GENERATE_SHOT_IMAGE)
        await hold(
            db,
            user_id=user_id,
            job_id=dispatch.job.id,
            meld_id=meld_id,
            credits=credits,
            description="shot still",
        )
        dispatch.job.credits_held = credits
        still.state = ArtifactState.GENERATING
        still.updated_at = now
        await db.flush()

    return dispatch


async def voice_for_shot(db: DbSession, shot: Shot) -> str:
    """Whose voice reads this shot.

    The speaker's, when the casting pass found one; otherwise the default
    narrator voice. Falling back rather than refusing, because unattributed
    narration is the script generator's *preferred* output (spec section 8
    biases away from on-screen dialogue), not an error state.
    """
    if shot.speaker_character_id is None:
        return DEFAULT_VOICE_ID
    speaker = (
        await db.execute(select(Character).where(Character.id == shot.speaker_character_id))
    ).scalar_one_or_none()
    if speaker is None or not speaker.voice_id:
        return DEFAULT_VOICE_ID
    return speaker.voice_id


def audio_kind_for(shot: Shot) -> AudioKind:
    return (
        AudioKind.NARRATION if shot.dialogue_kind is DialogueKind.NARRATION else AudioKind.DIALOGUE
    )


async def request_speech(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, shot_id: uuid.UUID
) -> JobDispatch | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    shot = await _owned_shot(db, meld_id, shot_id)
    if shot is None:
        return None
    if shot.dialogue_kind is DialogueKind.NONE or not shot.speech_text.strip():
        raise NothingToSay

    verdict = await check_and_record(db, meld, shot.speech_text)
    if not verdict.allowed:
        raise Blocked(verdict.reason or "These words can't be read aloud.")

    voice_id = await voice_for_shot(db, shot)
    kind = audio_kind_for(shot)
    input_hash = audio_clip_input_hash(speech_hash_for_shot(shot, voice_id), kind)

    # One clip per shot, keyed on the owner alone rather than on the owner
    # and the kind: switching a line from narration to dialogue re-records
    # it, it does not leave two clips behind for one shot.
    clip = (
        await db.execute(
            select(AudioClip).where(
                AudioClip.owner_type == AudioOwnerType.SHOT, AudioClip.owner_id == shot.id
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if clip is None:
        clip = AudioClip(
            owner_type=AudioOwnerType.SHOT,
            owner_id=shot.id,
            kind=kind,
            text=shot.speech_text,
            voice_id=voice_id,
            input_hash=input_hash,
            state=ArtifactState.GENERATING,
            created_at=now,
            updated_at=now,
        )
        db.add(clip)
        await db.flush()

    dispatch = await ensure_job(
        db,
        meld_id=meld_id,
        kind=JobKind.GENERATE_SPEECH,
        target_table="audio_clips",
        target_id=clip.id,
        input_hash=input_hash,
    )

    if dispatch.enqueued:
        credits = quote_for(JobKind.GENERATE_SPEECH)
        await hold(
            db,
            user_id=user_id,
            job_id=dispatch.job.id,
            meld_id=meld_id,
            credits=credits,
            description="shot speech",
        )
        dispatch.job.credits_held = credits
        # Rewritten here rather than only in the worker: these are what the
        # job is about to be generated from, and a user looking at the row
        # while it runs should see the words they are paying to hear.
        clip.kind = kind
        clip.text = shot.speech_text
        clip.voice_id = voice_id
        clip.state = ArtifactState.GENERATING
        clip.updated_at = now
        await db.flush()

    return dispatch


async def request_clip(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, shot_id: uuid.UUID
) -> JobDispatch | None:
    """Queue the one expensive generation in this product (M6).

    No moderation call: the still this animates already passed the gate
    when it was drawn, and `motion_prompt` describes camera movement rather
    than content. A second classification of the same approved picture
    would be a second chance to refuse work the user has already paid to
    produce.
    """
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    shot = await _owned_shot(db, meld_id, shot_id)
    if shot is None:
        return None

    still = (
        await db.execute(
            select(ShotImage).where(ShotImage.shot_id == shot.id, ShotImage.asset_id.isnot(None))
        )
    ).scalar_one_or_none()
    if still is None:
        raise NothingToAnimate

    input_hash = shot_clip_input_hash(motion_hash_for_shot(shot), still.content_hash)

    # One clip row per shot, rewritten in place on regeneration -- the same
    # rule as one still per shot. Created before the job so target_id is
    # stable across retries and the idempotency key means what it says.
    clip = (
        await db.execute(select(ShotClip).where(ShotClip.shot_id == shot.id))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if clip is None:
        clip = ShotClip(
            shot_id=shot.id,
            source_image_id=still.id,
            duration_sec=shot.duration_sec,
            input_hash=input_hash,
            state=ArtifactState.GENERATING,
            created_at=now,
            updated_at=now,
        )
        db.add(clip)
        await db.flush()

    dispatch = await ensure_job(
        db,
        meld_id=meld_id,
        kind=JobKind.GENERATE_SHOT_CLIP,
        target_table="shot_clips",
        target_id=clip.id,
        input_hash=input_hash,
    )

    if dispatch.enqueued:
        credits = quote_for(JobKind.GENERATE_SHOT_CLIP)
        await hold(
            db,
            user_id=user_id,
            job_id=dispatch.job.id,
            meld_id=meld_id,
            credits=credits,
            description="shot clip",
        )
        dispatch.job.credits_held = credits
        # Rewritten here, not in the worker, for the reason M4's _load_refs
        # gives: these are what the hold was priced against, and the user
        # can edit the shot while the job sits in the queue.
        clip.source_image_id = still.id
        clip.duration_sec = shot.duration_sec
        clip.params = {**CLIP_PARAMS, "motion_prompt": shot.motion_prompt}
        clip.state = ArtifactState.GENERATING
        clip.updated_at = now
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
