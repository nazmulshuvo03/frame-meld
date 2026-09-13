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
from framemeld.models.cast import Character, CharacterImage
from framemeld.models.job import Job, JobState
from framemeld.models.meld import Meld, Story
from framemeld.models.script import ArtifactState
from framemeld.providers.base import ProviderError, ProviderResponseError
from framemeld.providers.images.registry import get_image_provider
from framemeld.providers.registry import get_llm_provider
from framemeld.script.generation import (
    SCRIPT_JSON_SCHEMA,
    apply_script,
    build_messages,
    parse_script,
)
from framemeld.staleness import recompute_staleness

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


class WorkerSettings:
    """arq's entry point: `arq framemeld.worker.WorkerSettings`."""

    functions: ClassVar[list[WorkerCoroutine]] = [
        generate_script,
        generate_characters,
        generate_character_image,
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    #: arq checks `job_try > max_tries` before executing, so this gates the
    #: explicit Retry above, not just arq's own automatic retries. Set below
    #: MAX_ATTEMPTS and a retryable failure is dropped rather than retried.
    max_tries = MAX_ATTEMPTS
    job_timeout = 300
