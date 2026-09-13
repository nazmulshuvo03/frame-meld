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
from framemeld.jobs.service import mark_failed, mark_running, mark_succeeded
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
        await mark_failed(db, job, str(exc))
        logger.warning("generate_script failed for job %s: %s", job.id, exc)
        return JobOutcome(JobState.FAILED, retryable=exc.retryable)

    await apply_script(db, meld, draft, job.input_hash)

    # The user may have edited the story while this ran. The rows just
    # written carry the hash of the story this job was queued for, so if
    # that is no longer current they are genuinely out of date and must say
    # so rather than claim fresh.
    await recompute_staleness(db, job.meld_id)

    await mark_succeeded(db, job)
    return JobOutcome(JobState.SUCCEEDED, retryable=False)


async def generate_script(ctx: dict[str, Any], job_id: str) -> str:
    """The arq task. Owns the session and the retry decision; the work
    itself is in run_generate_script."""
    async with SessionLocal() as db:
        outcome = await run_generate_script(db, uuid.UUID(job_id))
        # Committed before any Retry is raised, so the recorded failure and
        # the incremented attempt count survive the re-queue. Raising first
        # would roll them back and the budget would never be spent.
        await db.commit()
        attempts = (
            await db.execute(select(Job.attempts).where(Job.id == uuid.UUID(job_id)))
        ).scalar_one_or_none() or MAX_ATTEMPTS

    if outcome.retryable and attempts < MAX_ATTEMPTS:
        # Exponential backoff: 2s, then 4s. A content refusal or a bad
        # response never reaches here — those are terminal by spec 5.4,
        # and retrying them would spend money to be refused again.
        raise Retry(defer=2**attempts)

    return outcome.state.value


class WorkerSettings:
    """arq's entry point: `arq framemeld.worker.WorkerSettings`."""

    functions: ClassVar[list[WorkerCoroutine]] = [generate_script]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    #: Retries are decided above, from the job row, so arq must not add its
    #: own on top — two independent budgets would multiply.
    max_tries = 1
    job_timeout = 300
