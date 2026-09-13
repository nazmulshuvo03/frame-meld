import logging
import uuid
from dataclasses import dataclass
from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.typing import WorkerCoroutine
from arq.worker import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.config import settings
from framemeld.db import SessionLocal
from framemeld.jobs.service import mark_failed, mark_for_retry, mark_running, mark_succeeded
from framemeld.models.job import Job, JobState
from framemeld.models.meld import Meld, Story
from framemeld.providers.base import ProviderError
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


@dataclass(frozen=True)
class JobOutcome:
    state: JobState
    #: Whether asking for this job again could plausibly succeed. Decided by
    #: the adapter (see ProviderError), never inferred from the state — a
    #: FAILED job may be a timeout worth retrying or a refusal that never
    #: will be, and the two look identical from outside.
    retryable: bool


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
        return JobOutcome(JobState.FAILED, retryable=False)
    except Exception:
        # Anything unforeseen still has to leave the row in a state a user
        # can act on. An exception escaping here rolls back mark_running,
        # stranding the job in `queued` with no worker behind it and no way
        # to re-enqueue it, because ensure_job treats `queued` as live.
        logger.exception("generate_script crashed for job %s", job.id)
        await mark_failed(db, job, "Generating the script failed unexpectedly. Try again.")
        return JobOutcome(JobState.FAILED, retryable=False)

    await apply_script(db, meld, draft, job.input_hash)

    # The user may have edited the story while this ran. The rows just
    # written carry the hash of the story this job was queued for, so if
    # that is no longer current they are genuinely out of date and must say
    # so rather than claim fresh.
    await recompute_staleness(db, job.meld_id)

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


class WorkerSettings:
    """arq's entry point: `arq framemeld.worker.WorkerSettings`."""

    functions: ClassVar[list[WorkerCoroutine]] = [generate_script]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    #: arq checks `job_try > max_tries` before executing, so this gates the
    #: explicit Retry above, not just arq's own automatic retries. Set below
    #: MAX_ATTEMPTS and a retryable failure is dropped rather than retried.
    max_tries = MAX_ATTEMPTS
    job_timeout = 300
