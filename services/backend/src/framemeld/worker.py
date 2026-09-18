import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.typing import WorkerCoroutine
from arq.worker import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.assets.service import store_asset
from framemeld.cast.generation import (
    CHARACTERS_JSON_SCHEMA,
    apply_characters,
    build_character_messages,
    parse_characters,
    sheet_prompt,
)
from framemeld.cast.hashes import SHEET_PARAMS, content_hash_for_character_image
from framemeld.config import settings
from framemeld.credits.service import refund, settle
from framemeld.db import SessionLocal
from framemeld.jobs.service import mark_failed, mark_for_retry, mark_running, mark_succeeded
from framemeld.models.asset import Asset
from framemeld.models.cast import Character, CharacterImage
from framemeld.models.job import Job, JobState
from framemeld.models.meld import Meld, Story
from framemeld.models.render import Render, RenderKind
from framemeld.models.script import ArtifactState, Shot
from framemeld.models.shots import AudioClip, ShotClip, ShotImage
from framemeld.providers.base import ProviderError, ProviderResponseError
from framemeld.providers.images.registry import get_image_provider
from framemeld.providers.music.registry import get_music_provider
from framemeld.providers.registry import get_llm_provider
from framemeld.providers.speech.registry import get_speech_provider
from framemeld.providers.speech.wav import WAV_MIME
from framemeld.providers.video.registry import get_video_provider
from framemeld.render.client import get_render_client
from framemeld.render.hashes import (
    content_hash_for_render,
    render_input_hash,
)
from framemeld.render.service import (
    MissingClips,
    MissingStills,
    NothingToRender,
    build_scene_spec,
)
from framemeld.script.generation import (
    SCRIPT_JSON_SCHEMA,
    apply_script,
    build_messages,
    parse_script,
)
from framemeld.shots.generation import (
    SHOT_CHARACTERS_JSON_SCHEMA,
    apply_shot_characters,
    build_casting_messages,
    parse_shot_characters,
    shots_in_film_order,
    still_prompt,
)
from framemeld.shots.hashes import (
    CLIP_PARAMS,
    SPEECH_PARAMS,
    STILL_PARAMS,
    content_hash_for_audio_clip,
    content_hash_for_shot_clip,
    content_hash_for_shot_image,
)
from framemeld.staleness import recompute_staleness
from framemeld.storage.registry import get_storage

logger = logging.getLogger(__name__)

#: Spec section 5.4: three attempts. Counted on the job row rather than in
#: arq, so a redelivery after a worker crash cannot restart the budget.
MAX_ATTEMPTS = 3

#: The formats an image provider may hand back. Anything else is a
#: response this codebase cannot store under a name a browser will
#: recognise, so it is a terminal response error rather than a file called
#: ".bin" that renders as a broken image forever.
_IMAGE_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


@dataclass(frozen=True)
class JobOutcome:
    state: JobState
    #: Whether asking for this job again could plausibly succeed. Decided by
    #: the adapter (see ProviderError), never inferred from the state — a
    #: FAILED job may be a timeout worth retrying or a refusal that never
    #: will be, and the two look identical from outside.
    retryable: bool


async def _close_hold(db: DbSession, job: Job, *, succeeded: bool) -> None:
    """Every terminal job closes its hold exactly once.

    On success this calls settle with used == held, which writes no row
    today: both M3 actions are a single unit of work, so nothing is ever
    returned. It is still called, rather than skipped, so the invariant is
    one greppable line -- and so the first action with variable units (M4's
    per-shot images) has one place to report what it actually did.

    On failure the whole hold comes back (spec section 4.3), including for
    a provider content refusal, which is terminal rather than retried.
    """
    if not job.credits_held:
        return

    meld = (await db.execute(select(Meld).where(Meld.id == job.meld_id))).scalar_one_or_none()
    if meld is None:
        # Nothing to refund to. A soft-deleted Meld still returns a row, so
        # reaching this means a hard delete raced the worker.
        logger.error("job %s has no meld to settle against", job.id)
        return

    if succeeded:
        await settle(
            db,
            job_id=job.id,
            user_id=meld.user_id,
            held=job.credits_held,
            used=job.credits_held,
            description=job.kind.value,
        )
    else:
        await refund(
            db,
            job_id=job.id,
            user_id=meld.user_id,
            held=job.credits_held,
            description=f"{job.kind.value} failed",
        )


async def run_generate_script(db: DbSession, job_id: uuid.UUID) -> JobOutcome:
    """Generate one Meld's script.

    Everything the arq task does except talk to arq, so it can be driven by
    an ordinary session with no Redis in the test suite.
    """
    job = await mark_running(db, job_id)
    if job is None:
        # Already running, already finished, or unknown. A redelivered
        # message must not generate the same script a second time.
        existing = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        state = JobState.CANCELLED if existing is None else existing.state
        return JobOutcome(state, retryable=False)

    meld = (await db.execute(select(Meld).where(Meld.id == job.meld_id))).scalar_one_or_none()
    story = (
        await db.execute(select(Story).where(Story.meld_id == job.meld_id))
    ).scalar_one_or_none()
    if meld is None or story is None:
        await mark_failed(db, job, "This Meld no longer exists.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    provider = get_llm_provider()
    system, user = build_messages(story.text)

    try:
        result = await provider.complete(system=system, user=user, json_schema=SCRIPT_JSON_SCHEMA)
        draft = parse_script(result.text or "")
    except ProviderError as exc:
        # The message reaches the user through jobs.error, so adapters and
        # the parser both phrase their errors for a reader, not a log.
        logger.warning("generate_script failed for job %s: %s", job.id, exc)
        if exc.retryable and job.attempts < MAX_ATTEMPTS:
            await mark_for_retry(db, job, str(exc))
            return JobOutcome(JobState.QUEUED, retryable=True)
        await mark_failed(db, job, str(exc))
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)
    except Exception:
        # Anything unforeseen still has to leave the row in a state a user
        # can act on. An exception escaping here rolls back mark_running,
        # stranding the job in `queued` with no worker behind it and no way
        # to re-enqueue it, because ensure_job treats `queued` as live.
        logger.exception("generate_script crashed for job %s", job.id)
        await mark_failed(db, job, "Generating the script failed unexpectedly. Try again.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    await apply_script(db, meld, draft, job.input_hash)

    # The user may have edited the story while this ran. The rows just
    # written carry the hash of the story this job was queued for, so if
    # that is no longer current they are genuinely out of date and must say
    # so rather than claim fresh.
    await recompute_staleness(db, job.meld_id)

    await _close_hold(db, job, succeeded=True)
    await mark_succeeded(db, job)
    return JobOutcome(JobState.SUCCEEDED, retryable=False)


async def generate_script(ctx: dict[str, Any], job_id: str) -> str:
    """The arq task. Owns the session and the redelivery; the work itself,
    including the retry decision, is in run_generate_script."""
    async with SessionLocal() as db:
        outcome = await run_generate_script(db, uuid.UUID(job_id))
        attempts = (
            await db.execute(select(Job.attempts).where(Job.id == uuid.UUID(job_id)))
        ).scalar_one_or_none()
        # Committed before any Retry is raised, so the recorded failure and
        # the incremented attempt count survive the re-queue. Raising first
        # would roll them back and the budget would never be spent.
        await db.commit()

    if outcome.retryable:
        # Exponential backoff: 2s, then 4s. The budget itself was already
        # decided in run_generate_script; a True here only ever means
        # "retry", never "retry forever" — a content refusal or a bad
        # response is terminal by spec 5.4 and never sets retryable=True.
        raise Retry(defer=2 ** (attempts or 1))

    return outcome.state.value


async def run_generate_characters(db: DbSession, job_id: uuid.UUID) -> JobOutcome:
    """Extract one Meld's cast from its story.

    Everything the arq task does except talk to arq, so it can be driven by
    an ordinary session with no Redis in the test suite.
    """
    job = await mark_running(db, job_id)
    if job is None:
        existing = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        state = JobState.CANCELLED if existing is None else existing.state
        return JobOutcome(state, retryable=False)

    meld = (await db.execute(select(Meld).where(Meld.id == job.meld_id))).scalar_one_or_none()
    story = (
        await db.execute(select(Story).where(Story.meld_id == job.meld_id))
    ).scalar_one_or_none()
    if meld is None or story is None:
        await mark_failed(db, job, "This Meld no longer exists.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    provider = get_llm_provider()
    system, user = build_character_messages(story.text)

    try:
        result = await provider.complete(
            system=system, user=user, json_schema=CHARACTERS_JSON_SCHEMA
        )
        draft = parse_characters(result.text or "")
    except ProviderError as exc:
        logger.warning("generate_characters failed for job %s: %s", job.id, exc)
        if exc.retryable and job.attempts < MAX_ATTEMPTS:
            # No refund: the hold stays until the job is terminal, or a
            # retry would have to take it again and could fail on a balance
            # the user has since spent elsewhere.
            await mark_for_retry(db, job, str(exc))
            return JobOutcome(JobState.QUEUED, retryable=True)
        await mark_failed(db, job, str(exc))
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)
    except Exception:
        logger.exception("generate_characters crashed for job %s", job.id)
        await mark_failed(db, job, "Extracting the cast failed unexpectedly. Try again.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    await apply_characters(db, meld, draft, job.input_hash)

    # The user may have edited the story while this ran. The rows just
    # written carry the hash of the story this job was queued for, so if
    # that is no longer current they are genuinely out of date and must say
    # so rather than claim fresh.
    await recompute_staleness(db, job.meld_id)

    job.cost_usd = result.cost_usd
    await _close_hold(db, job, succeeded=True)
    await mark_succeeded(db, job)
    return JobOutcome(JobState.SUCCEEDED, retryable=False)


async def generate_characters(ctx: dict[str, Any], job_id: str) -> str:
    """The arq task. Identical in shape to generate_script -- owns the
    session and the redelivery; the work and the retry decision live in
    run_generate_characters."""
    async with SessionLocal() as db:
        outcome = await run_generate_characters(db, uuid.UUID(job_id))
        attempts = (
            await db.execute(select(Job.attempts).where(Job.id == uuid.UUID(job_id)))
        ).scalar_one_or_none()
        await db.commit()

    if outcome.retryable:
        raise Retry(defer=2 ** (attempts or 1))
    return outcome.state.value


async def run_generate_character_image(db: DbSession, job_id: uuid.UUID) -> JobOutcome:
    job = await mark_running(db, job_id)
    if job is None:
        existing = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        state = JobState.CANCELLED if existing is None else existing.state
        return JobOutcome(state, retryable=False)

    image = (
        await db.execute(select(CharacterImage).where(CharacterImage.id == job.target_id))
    ).scalar_one_or_none()
    character = (
        None
        if image is None
        else (
            await db.execute(select(Character).where(Character.id == image.character_id))
        ).scalar_one_or_none()
    )
    meld = (await db.execute(select(Meld).where(Meld.id == job.meld_id))).scalar_one_or_none()
    if image is None or character is None or meld is None:
        await mark_failed(db, job, "This character no longer exists.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    provider = get_image_provider()
    try:
        result = await provider.generate(
            prompt=sheet_prompt(character), refs=[], params=SHEET_PARAMS
        )
        if not result.bytes_:
            raise ProviderResponseError("The image model returned no image.")
        extension = _IMAGE_EXTENSIONS.get(str(result.raw.get("mimeType", "image/png")))
        if extension is None:
            raise ProviderResponseError("The image model returned a format we cannot store.")
    except ProviderError as exc:
        logger.warning("generate_character_image failed for job %s: %s", job.id, exc)
        if exc.retryable and job.attempts < MAX_ATTEMPTS:
            await mark_for_retry(db, job, str(exc))
            return JobOutcome(JobState.QUEUED, retryable=True)
        image.state = ArtifactState.FAILED
        image.updated_at = datetime.now(UTC)
        await mark_failed(db, job, str(exc))
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)
    except Exception:
        logger.exception("generate_character_image crashed for job %s", job.id)
        image.state = ArtifactState.FAILED
        image.updated_at = datetime.now(UTC)
        await mark_failed(db, job, "Generating the sheet failed unexpectedly. Try again.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    mime = str(result.raw.get("mimeType", "image/png"))
    asset = await store_asset(
        db, user_id=meld.user_id, data=result.bytes_, mime=mime, ext=extension
    )

    now = datetime.now(UTC)
    image.asset_id = asset.id
    image.content_hash = content_hash_for_character_image(asset.sha256)
    image.input_hash = job.input_hash
    image.state = ArtifactState.FRESH
    image.provider = result.provider
    image.model = result.model
    image.params = dict(SHEET_PARAMS)
    image.credits_spent = job.credits_held
    image.updated_at = now

    job.cost_usd = result.cost_usd
    # The user may have edited the character's appearance while this ran.
    # Same rule as the script stage: rows carrying a hash that is no longer
    # current must say stale rather than claim fresh.
    await recompute_staleness(db, job.meld_id)
    await _close_hold(db, job, succeeded=True)
    await mark_succeeded(db, job)
    return JobOutcome(JobState.SUCCEEDED, retryable=False)


async def generate_character_image(ctx: dict[str, Any], job_id: str) -> str:
    """The arq task. Same shape as generate_characters."""
    async with SessionLocal() as db:
        outcome = await run_generate_character_image(db, uuid.UUID(job_id))
        attempts = (
            await db.execute(select(Job.attempts).where(Job.id == uuid.UUID(job_id)))
        ).scalar_one_or_none()
        await db.commit()

    if outcome.retryable:
        raise Retry(defer=2 ** (attempts or 1))
    return outcome.state.value


async def run_map_shot_characters(db: DbSession, job_id: uuid.UUID) -> JobOutcome:
    """Work out which characters are in which shot (M4-D1).

    The pass M3-D2 deferred. Same shape as the other two LLM jobs; the only
    difference is that it reads two artifacts rather than one, which is why
    its input_hash has two parents.
    """
    job = await mark_running(db, job_id)
    if job is None:
        existing = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        state = JobState.CANCELLED if existing is None else existing.state
        return JobOutcome(state, retryable=False)

    meld = (await db.execute(select(Meld).where(Meld.id == job.meld_id))).scalar_one_or_none()
    characters = (
        []
        if meld is None
        else list(
            (
                await db.execute(
                    select(Character)
                    .where(Character.meld_id == job.meld_id)
                    .order_by(Character.order_index)
                )
            ).scalars()
        )
    )
    shots = [] if meld is None else await shots_in_film_order(db, job.meld_id)
    if meld is None or not characters or not shots:
        # The cast or the script was regenerated out from under this job.
        # Terminal rather than retried: re-running would map a cast that is
        # no longer there.
        await mark_failed(db, job, "The cast or the script changed. Try again.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    provider = get_llm_provider()
    system, user = build_casting_messages(characters, shots)

    try:
        result = await provider.complete(
            system=system, user=user, json_schema=SHOT_CHARACTERS_JSON_SCHEMA
        )
        draft = parse_shot_characters(result.text or "")
    except ProviderError as exc:
        logger.warning("map_shot_characters failed for job %s: %s", job.id, exc)
        if exc.retryable and job.attempts < MAX_ATTEMPTS:
            await mark_for_retry(db, job, str(exc))
            return JobOutcome(JobState.QUEUED, retryable=True)
        await mark_failed(db, job, str(exc))
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)
    except Exception:
        logger.exception("map_shot_characters crashed for job %s", job.id)
        await mark_failed(db, job, "Working out who is in each shot failed. Try again.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    await apply_shot_characters(db, meld, characters, shots, draft)

    # The mapping changes which sheets a still references, so a still
    # generated before this pass is keyed on the wrong parents and must say
    # stale rather than claim fresh.
    await recompute_staleness(db, job.meld_id)

    job.cost_usd = result.cost_usd
    await _close_hold(db, job, succeeded=True)
    await mark_succeeded(db, job)
    return JobOutcome(JobState.SUCCEEDED, retryable=False)


async def map_shot_characters(ctx: dict[str, Any], job_id: str) -> str:
    """The arq task. Same shape as generate_characters."""
    async with SessionLocal() as db:
        outcome = await run_map_shot_characters(db, uuid.UUID(job_id))
        attempts = (
            await db.execute(select(Job.attempts).where(Job.id == uuid.UUID(job_id)))
        ).scalar_one_or_none()
        await db.commit()

    if outcome.retryable:
        raise Retry(defer=2 ** (attempts or 1))
    return outcome.state.value


async def _load_refs(db: DbSession, still: ShotImage) -> tuple[list[bytes], list[str]]:
    """The reference sheets this still was queued to use, as bytes, plus who
    each one is.

    Read from storage on every call, with no cache (M4-D10). A shot with
    four characters is four reads; M3's roadmap flagged a cache here as
    worth measuring before building, and it has not been measured.

    Order is `ref_character_image_ids`, exactly as the service recorded it,
    because still_prompt names the references in the order they are
    attached. Sorting or regrouping them here would silently relabel every
    face.
    """
    if not still.ref_character_image_ids:
        return [], []

    rows = (
        await db.execute(
            select(CharacterImage, Character)
            .join(Character, CharacterImage.character_id == Character.id)
            .where(CharacterImage.id.in_(still.ref_character_image_ids))
        )
    ).all()
    by_id = {image.id: (image, character) for image, character in rows}

    storage = get_storage()
    data: list[bytes] = []
    names: list[str] = []
    for ref_id in still.ref_character_image_ids:
        found = by_id.get(ref_id)
        if found is None:
            # A sheet that existed when the job was queued and does not now.
            # Terminal: the hash this job carries describes a reference set
            # that can no longer be assembled.
            raise ProviderResponseError("A character sheet this shot needs has gone. Try again.")
        image, character = found
        asset = (
            await db.execute(select(Asset).where(Asset.id == image.asset_id))
        ).scalar_one_or_none()
        if asset is None:
            raise ProviderResponseError("A character sheet this shot needs has gone. Try again.")
        try:
            data.append(await storage.get(key=asset.storage_key))
        except FileNotFoundError as exc:
            raise ProviderResponseError(
                "A character sheet this shot needs could not be read. Try again."
            ) from exc
        names.append(character.name or "an unnamed character")
    return data, names


async def run_generate_shot_image(db: DbSession, job_id: uuid.UUID) -> JobOutcome:
    job = await mark_running(db, job_id)
    if job is None:
        existing = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        state = JobState.CANCELLED if existing is None else existing.state
        return JobOutcome(state, retryable=False)

    still = (
        await db.execute(select(ShotImage).where(ShotImage.id == job.target_id))
    ).scalar_one_or_none()
    shot = (
        None
        if still is None
        else (await db.execute(select(Shot).where(Shot.id == still.shot_id))).scalar_one_or_none()
    )
    meld = (await db.execute(select(Meld).where(Meld.id == job.meld_id))).scalar_one_or_none()
    if still is None or shot is None or meld is None:
        await mark_failed(db, job, "This shot no longer exists.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    provider = get_image_provider()
    try:
        refs, names = await _load_refs(db, still)
        result = await provider.generate(
            prompt=still_prompt(shot, names), refs=refs, params=STILL_PARAMS
        )
        if not result.bytes_:
            raise ProviderResponseError("The image model returned no image.")
        extension = _IMAGE_EXTENSIONS.get(str(result.raw.get("mimeType", "image/png")))
        if extension is None:
            raise ProviderResponseError("The image model returned a format we cannot store.")
    except ProviderError as exc:
        logger.warning("generate_shot_image failed for job %s: %s", job.id, exc)
        if exc.retryable and job.attempts < MAX_ATTEMPTS:
            await mark_for_retry(db, job, str(exc))
            return JobOutcome(JobState.QUEUED, retryable=True)
        still.state = ArtifactState.FAILED
        still.updated_at = datetime.now(UTC)
        await mark_failed(db, job, str(exc))
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)
    except Exception:
        logger.exception("generate_shot_image crashed for job %s", job.id)
        still.state = ArtifactState.FAILED
        still.updated_at = datetime.now(UTC)
        await mark_failed(db, job, "Generating the still failed unexpectedly. Try again.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    mime = str(result.raw.get("mimeType", "image/png"))
    asset = await store_asset(
        db, user_id=meld.user_id, data=result.bytes_, mime=mime, ext=extension
    )

    now = datetime.now(UTC)
    still.asset_id = asset.id
    still.content_hash = content_hash_for_shot_image(asset.sha256)
    still.input_hash = job.input_hash
    still.state = ArtifactState.FRESH
    still.provider = result.provider
    still.model = result.model
    still.params = dict(STILL_PARAMS)
    still.credits_spent = job.credits_held
    still.updated_at = now

    job.cost_usd = result.cost_usd
    # The user may have edited the shot, or a character, while this ran.
    # Rows carrying a hash that is no longer current must say stale rather
    # than claim fresh.
    await recompute_staleness(db, job.meld_id)
    await _close_hold(db, job, succeeded=True)
    await mark_succeeded(db, job)
    return JobOutcome(JobState.SUCCEEDED, retryable=False)


async def generate_shot_image(ctx: dict[str, Any], job_id: str) -> str:
    """The arq task. Same shape as generate_character_image."""
    async with SessionLocal() as db:
        outcome = await run_generate_shot_image(db, uuid.UUID(job_id))
        attempts = (
            await db.execute(select(Job.attempts).where(Job.id == uuid.UUID(job_id)))
        ).scalar_one_or_none()
        await db.commit()

    if outcome.retryable:
        raise Retry(defer=2 ** (attempts or 1))
    return outcome.state.value


async def run_generate_speech(db: DbSession, job_id: uuid.UUID) -> JobOutcome:
    job = await mark_running(db, job_id)
    if job is None:
        existing = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        state = JobState.CANCELLED if existing is None else existing.state
        return JobOutcome(state, retryable=False)

    clip = (
        await db.execute(select(AudioClip).where(AudioClip.id == job.target_id))
    ).scalar_one_or_none()
    meld = (await db.execute(select(Meld).where(Meld.id == job.meld_id))).scalar_one_or_none()
    if clip is None or meld is None:
        await mark_failed(db, job, "This shot no longer exists.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    provider = get_speech_provider()
    try:
        # The words and the voice are read from the clip row, not from the
        # shot: the row is what the hold was priced against, and the user
        # may have edited the shot since. Reading the shot here would let a
        # job quietly record something the user never approved.
        result = await provider.speak(text=clip.text, voice_id=clip.voice_id, params=SPEECH_PARAMS)
        if not result.bytes_:
            raise ProviderResponseError("The speech model returned no audio.")
    except ProviderError as exc:
        logger.warning("generate_speech failed for job %s: %s", job.id, exc)
        if exc.retryable and job.attempts < MAX_ATTEMPTS:
            await mark_for_retry(db, job, str(exc))
            return JobOutcome(JobState.QUEUED, retryable=True)
        clip.state = ArtifactState.FAILED
        clip.updated_at = datetime.now(UTC)
        await mark_failed(db, job, str(exc))
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)
    except Exception:
        logger.exception("generate_speech crashed for job %s", job.id)
        clip.state = ArtifactState.FAILED
        clip.updated_at = datetime.now(UTC)
        await mark_failed(db, job, "Generating the audio failed unexpectedly. Try again.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    asset = await store_asset(
        db, user_id=meld.user_id, data=result.bytes_, mime=WAV_MIME, ext="wav"
    )

    now = datetime.now(UTC)
    clip.asset_id = asset.id
    clip.content_hash = content_hash_for_audio_clip(asset.sha256)
    clip.input_hash = job.input_hash
    clip.state = ArtifactState.FRESH
    clip.provider = result.provider
    clip.model = result.model
    clip.params = dict(SPEECH_PARAMS)
    clip.credits_spent = job.credits_held
    # Copied from the asset rather than recomputed: M5's scene spec lays
    # out a timeline from this column and must not have to join to `assets`
    # for a number that can be null on either side.
    clip.duration_sec = asset.duration_sec
    clip.updated_at = now

    job.cost_usd = result.cost_usd
    await recompute_staleness(db, job.meld_id)
    await _close_hold(db, job, succeeded=True)
    await mark_succeeded(db, job)
    return JobOutcome(JobState.SUCCEEDED, retryable=False)


async def generate_speech(ctx: dict[str, Any], job_id: str) -> str:
    """The arq task. Same shape as generate_shot_image."""
    async with SessionLocal() as db:
        outcome = await run_generate_speech(db, uuid.UUID(job_id))
        attempts = (
            await db.execute(select(Job.attempts).where(Job.id == uuid.UUID(job_id)))
        ).scalar_one_or_none()
        await db.commit()

    if outcome.retryable:
        raise Retry(defer=2 ** (attempts or 1))
    return outcome.state.value


#: The video container the render service returns. Fixed rather than read
#: off the response, because the composition writes exactly one format and
#: a surprise here is a file a browser will not play.
_VIDEO_MIME = "video/mp4"


async def run_generate_music(db: DbSession, job_id: uuid.UUID) -> JobOutcome:
    """Score one Meld (M5-D2).

    Same shape as run_generate_speech, and reads its inputs from the clip
    row for the same reason: the prompt and the requested length are what
    the job was keyed on, and the user can edit a shot's duration while
    this sits in the queue.
    """
    job = await mark_running(db, job_id)
    if job is None:
        existing = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        state = JobState.CANCELLED if existing is None else existing.state
        return JobOutcome(state, retryable=False)

    clip = (
        await db.execute(select(AudioClip).where(AudioClip.id == job.target_id))
    ).scalar_one_or_none()
    meld = (await db.execute(select(Meld).where(Meld.id == job.meld_id))).scalar_one_or_none()
    if clip is None or meld is None:
        await mark_failed(db, job, "This film no longer exists.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    provider = get_music_provider()
    try:
        result = await provider.compose(
            prompt=clip.text,
            duration_sec=float(clip.params.get("duration_sec") or 0),
            params=clip.params,
        )
        if not result.bytes_:
            raise ProviderResponseError("The music model returned no audio.")
    except ProviderError as exc:
        logger.warning("generate_music failed for job %s: %s", job.id, exc)
        if exc.retryable and job.attempts < MAX_ATTEMPTS:
            await mark_for_retry(db, job, str(exc))
            return JobOutcome(JobState.QUEUED, retryable=True)
        clip.state = ArtifactState.FAILED
        clip.updated_at = datetime.now(UTC)
        await mark_failed(db, job, str(exc))
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)
    except Exception:
        logger.exception("generate_music crashed for job %s", job.id)
        clip.state = ArtifactState.FAILED
        clip.updated_at = datetime.now(UTC)
        await mark_failed(db, job, "Scoring the film failed unexpectedly. Try again.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    asset = await store_asset(
        db, user_id=meld.user_id, data=result.bytes_, mime=WAV_MIME, ext="wav"
    )

    clip.asset_id = asset.id
    clip.content_hash = content_hash_for_audio_clip(asset.sha256)
    clip.input_hash = job.input_hash
    clip.state = ArtifactState.FRESH
    clip.provider = result.provider
    clip.model = result.model
    clip.credits_spent = job.credits_held
    clip.duration_sec = asset.duration_sec
    clip.updated_at = datetime.now(UTC)

    job.cost_usd = result.cost_usd
    await recompute_staleness(db, job.meld_id)
    await _close_hold(db, job, succeeded=True)
    await mark_succeeded(db, job)
    return JobOutcome(JobState.SUCCEEDED, retryable=False)


async def generate_music(ctx: dict[str, Any], job_id: str) -> str:
    """The arq task. Same shape as generate_speech."""
    async with SessionLocal() as db:
        outcome = await run_generate_music(db, uuid.UUID(job_id))
        attempts = (
            await db.execute(select(Job.attempts).where(Job.id == uuid.UUID(job_id)))
        ).scalar_one_or_none()
        await db.commit()

    if outcome.retryable:
        raise Retry(defer=2 ** (attempts or 1))
    return outcome.state.value


async def _run_render(db: DbSession, job_id: uuid.UUID, kind: RenderKind) -> JobOutcome:
    """Assemble one Meld into a film (spec section 4.2).

    The scene spec is rebuilt here rather than read back off the render
    row, because the row stores the hash payload -- content hashes, no
    asset ids -- and a signed URL cannot be recovered from that. The
    rebuild is then checked against the key the job was queued under: a
    film the user has edited since is a *different* film, and rendering it
    under the old key would leave a render claiming to be something it is
    not. A Preview involves no money (M5-D7) and a Final refunds in full,
    so the honest answer is to say so and let them press the button again.
    """
    job = await mark_running(db, job_id)
    if job is None:
        existing = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        state = JobState.CANCELLED if existing is None else existing.state
        return JobOutcome(state, retryable=False)

    render = (
        await db.execute(select(Render).where(Render.id == job.target_id))
    ).scalar_one_or_none()
    meld = (await db.execute(select(Meld).where(Meld.id == job.meld_id))).scalar_one_or_none()
    if render is None or meld is None:
        await mark_failed(db, job, "This film no longer exists.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    try:
        spec = await build_scene_spec(db, job.meld_id, kind)
    except (MissingClips, MissingStills, NothingToRender) as exc:
        render.state = ArtifactState.FAILED
        render.updated_at = datetime.now(UTC)
        await mark_failed(db, job, exc.reason)
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    if render_input_hash(spec.to_hash_payload(), kind) != job.input_hash:
        render.state = ArtifactState.STALE
        render.updated_at = datetime.now(UTC)
        await mark_failed(db, job, "The film changed while it was rendering. Render it again.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    client = get_render_client()
    try:
        video = await client.render(spec.to_request())
    except ProviderError as exc:
        logger.warning("%s failed for job %s: %s", job.kind.value, job.id, exc)
        if exc.retryable and job.attempts < MAX_ATTEMPTS:
            await mark_for_retry(db, job, str(exc))
            return JobOutcome(JobState.QUEUED, retryable=True)
        render.state = ArtifactState.FAILED
        render.updated_at = datetime.now(UTC)
        await mark_failed(db, job, str(exc))
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)
    except Exception:
        logger.exception("%s crashed for job %s", job.kind.value, job.id)
        render.state = ArtifactState.FAILED
        render.updated_at = datetime.now(UTC)
        await mark_failed(db, job, "Assembling the film failed unexpectedly. Try again.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    asset = await store_asset(db, user_id=meld.user_id, data=video, mime=_VIDEO_MIME, ext="mp4")

    render.asset_id = asset.id
    render.content_hash = content_hash_for_render(asset.sha256)
    render.input_hash = job.input_hash
    render.scene_spec = spec.to_hash_payload()
    render.duration_sec = spec.duration_sec
    render.resolution = spec.resolution
    render.state = ArtifactState.FRESH
    render.provider = "remotion"
    render.model = kind.value
    render.credits_spent = job.credits_held
    render.updated_at = datetime.now(UTC)

    # A Preview holds nothing (M5-D7), so this is a no-op for it -- called
    # anyway rather than branched around, so "every terminal job closes its
    # hold" stays one greppable line.
    await _close_hold(db, job, succeeded=True)
    await mark_succeeded(db, job)
    return JobOutcome(JobState.SUCCEEDED, retryable=False)


async def run_render_preview(db: DbSession, job_id: uuid.UUID) -> JobOutcome:
    return await _run_render(db, job_id, RenderKind.PREVIEW)


async def run_render_final(db: DbSession, job_id: uuid.UUID) -> JobOutcome:
    return await _run_render(db, job_id, RenderKind.FINAL)


async def render_preview(ctx: dict[str, Any], job_id: str) -> str:
    """The arq task. Same shape as the rest."""
    async with SessionLocal() as db:
        outcome = await run_render_preview(db, uuid.UUID(job_id))
        attempts = (
            await db.execute(select(Job.attempts).where(Job.id == uuid.UUID(job_id)))
        ).scalar_one_or_none()
        await db.commit()

    if outcome.retryable:
        raise Retry(defer=2 ** (attempts or 1))
    return outcome.state.value


async def run_generate_shot_clip(db: DbSession, job_id: uuid.UUID) -> JobOutcome:
    """Animate one approved still into a clip (M6).

    The expensive one: at spec section 7's prices this single call is more
    than everything else in a film put together, which is why the still it
    reads comes off the *clip row* and not off the shot. The user can
    redraw a still while this sits in the queue, and a job that quietly
    animated a different picture would charge 80 credits for something
    nobody approved (M4's rule, at twenty times the stake).
    """
    job = await mark_running(db, job_id)
    if job is None:
        existing = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        state = JobState.CANCELLED if existing is None else existing.state
        return JobOutcome(state, retryable=False)

    clip = (
        await db.execute(select(ShotClip).where(ShotClip.id == job.target_id))
    ).scalar_one_or_none()
    still = (
        None
        if clip is None or clip.source_image_id is None
        else (
            await db.execute(select(ShotImage).where(ShotImage.id == clip.source_image_id))
        ).scalar_one_or_none()
    )
    meld = (await db.execute(select(Meld).where(Meld.id == job.meld_id))).scalar_one_or_none()
    if clip is None or still is None or meld is None:
        await mark_failed(db, job, "This shot no longer exists.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    provider = get_video_provider()
    try:
        asset = (
            None
            if still.asset_id is None
            else (
                await db.execute(select(Asset).where(Asset.id == still.asset_id))
            ).scalar_one_or_none()
        )
        if asset is None:
            raise ProviderResponseError("The still this clip is made from has gone. Try again.")
        try:
            image = await get_storage().get(key=asset.storage_key)
        except FileNotFoundError as exc:
            raise ProviderResponseError(
                "The still this clip is made from could not be read. Try again."
            ) from exc

        result = await provider.animate(
            image=image,
            # From the row, like the image: what the hold was priced against.
            motion_prompt=str(clip.params.get("motion_prompt", "")),
            duration_sec=float(clip.duration_sec or 0),
            params=clip.params,
        )
        if not result.bytes_:
            raise ProviderResponseError("The video model returned no clip.")
    except ProviderError as exc:
        logger.warning("generate_shot_clip failed for job %s: %s", job.id, exc)
        if exc.retryable and job.attempts < MAX_ATTEMPTS:
            await mark_for_retry(db, job, str(exc))
            return JobOutcome(JobState.QUEUED, retryable=True)
        clip.state = ArtifactState.FAILED
        clip.updated_at = datetime.now(UTC)
        await mark_failed(db, job, str(exc))
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)
    except Exception:
        logger.exception("generate_shot_clip crashed for job %s", job.id)
        clip.state = ArtifactState.FAILED
        clip.updated_at = datetime.now(UTC)
        await mark_failed(db, job, "Animating the shot failed unexpectedly. Try again.")
        await _close_hold(db, job, succeeded=False)
        return JobOutcome(JobState.FAILED, retryable=False)

    stored = await store_asset(
        db, user_id=meld.user_id, data=result.bytes_, mime=_VIDEO_MIME, ext="mp4"
    )

    clip.asset_id = stored.id
    clip.content_hash = content_hash_for_shot_clip(stored.sha256)
    clip.input_hash = job.input_hash
    clip.state = ArtifactState.FRESH
    clip.provider = result.provider
    clip.model = result.model
    clip.params = {**clip.params, **CLIP_PARAMS}
    clip.credits_spent = job.credits_held
    clip.updated_at = datetime.now(UTC)

    job.cost_usd = result.cost_usd
    await recompute_staleness(db, job.meld_id)
    await _close_hold(db, job, succeeded=True)
    await mark_succeeded(db, job)
    return JobOutcome(JobState.SUCCEEDED, retryable=False)


async def generate_shot_clip(ctx: dict[str, Any], job_id: str) -> str:
    """The arq task. Same shape as generate_shot_image."""
    async with SessionLocal() as db:
        outcome = await run_generate_shot_clip(db, uuid.UUID(job_id))
        attempts = (
            await db.execute(select(Job.attempts).where(Job.id == uuid.UUID(job_id)))
        ).scalar_one_or_none()
        await db.commit()

    if outcome.retryable:
        raise Retry(defer=2 ** (attempts or 1))
    return outcome.state.value


async def render_final(ctx: dict[str, Any], job_id: str) -> str:
    """The arq task. Same shape as render_preview."""
    async with SessionLocal() as db:
        outcome = await run_render_final(db, uuid.UUID(job_id))
        attempts = (
            await db.execute(select(Job.attempts).where(Job.id == uuid.UUID(job_id)))
        ).scalar_one_or_none()
        await db.commit()

    if outcome.retryable:
        raise Retry(defer=2 ** (attempts or 1))
    return outcome.state.value


class WorkerSettings:
    """arq's entry point: `arq framemeld.worker.WorkerSettings`."""

    functions: ClassVar[list[WorkerCoroutine]] = [
        generate_script,
        generate_characters,
        generate_character_image,
        map_shot_characters,
        generate_shot_image,
        generate_speech,
        generate_music,
        render_preview,
        generate_shot_clip,
        render_final,
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    #: arq checks `job_try > max_tries` before executing, so this gates the
    #: explicit Retry above, not just arq's own automatic retries. Set below
    #: MAX_ATTEMPTS and a retryable failure is dropped rather than retried.
    max_tries = MAX_ATTEMPTS
    #: Long enough for a preview render, which the worker waits on in
    #: line (M5-D10). settings.render_timeout_seconds bounds the render
    #: itself; this has to be comfortably above it, or arq kills the job
    #: while the render service is still working and nothing records why.
    job_timeout = settings.render_timeout_seconds + 120
