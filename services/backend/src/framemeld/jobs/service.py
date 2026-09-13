import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.hashing import canonical_hash
from framemeld.models.job import Job, JobKind, JobState

#: States in which an existing job is *the* answer: it is already going to
#: run, is running, or has already produced what was asked for.
_LIVE = (JobState.QUEUED, JobState.RUNNING, JobState.SUCCEEDED)


def idempotency_key(kind: JobKind, target_id: uuid.UUID | None, input_hash: str) -> str:
    """Spec section 5.4. Unique in the database, which is what makes a
    second Generate click free rather than expensive."""
    return canonical_hash(
        {
            "kind": kind.value,
            "target_id": None if target_id is None else str(target_id),
            "input_hash": input_hash,
        }
    )


@dataclass(frozen=True)
class JobDispatch:
    job: Job
    #: False means the caller must NOT push a message: the job is already
    #: queued, already running, or already finished.
    enqueued: bool


async def _by_key(db: DbSession, key: str) -> Job | None:
    return (await db.execute(select(Job).where(Job.idempotency_key == key))).scalar_one_or_none()


async def ensure_job(
    db: DbSession,
    *,
    meld_id: uuid.UUID,
    kind: JobKind,
    target_table: str,
    target_id: uuid.UUID | None,
    input_hash: str,
) -> JobDispatch:
    """Find or create the one job for these inputs.

    Never creates a second row for the same (kind, target, input_hash): the
    unique index forbids it, and the point of the index is that the caller
    does not have to coordinate. A previously failed job is reset to
    `queued` in place rather than duplicated, because the unique key would
    otherwise make every failure permanent.
    """
    key = idempotency_key(kind, target_id, input_hash)

    existing = await _by_key(db, key)
    if existing is not None:
        if existing.state in _LIVE:
            return JobDispatch(existing, enqueued=False)
        # failed or cancelled: hand the same row back to the queue.
        # `attempts` is deliberately not reset — it is the row's history,
        # and the retry budget depends on it.
        existing.state = JobState.QUEUED
        existing.error = None
        existing.started_at = None
        existing.finished_at = None
        await db.flush()
        return JobDispatch(existing, enqueued=True)

    job = Job(
        meld_id=meld_id,
        kind=kind,
        target_table=target_table,
        target_id=target_id,
        input_hash=input_hash,
        idempotency_key=key,
        state=JobState.QUEUED,
        created_at=datetime.now(UTC),
    )
    try:
        # A savepoint, so losing the insert race does not poison the
        # caller's transaction. Two clicks 5 ms apart both see no existing
        # row; the unique index is what actually decides, and the loser
        # simply reads what the winner wrote.
        async with db.begin_nested():
            db.add(job)
            await db.flush()
    except IntegrityError:
        winner = await _by_key(db, key)
        if winner is None:
            # Not the unique index, then — a foreign key or a check
            # constraint. Nothing here knows how to recover from those, and
            # swallowing one would turn a caller's bad meld_id into a
            # mysteriously missing job.
            raise
        return JobDispatch(winner, enqueued=False)

    return JobDispatch(job, enqueued=True)


async def mark_running(db: DbSession, job_id: uuid.UUID) -> Job | None:
    """Claim a queued job, or return None if there is nothing to claim.

    arq can deliver the same message twice, and a retried job is re-queued
    under the same id. Returning None rather than running anyway is what
    stops one story being generated into two scripts.
    """
    job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None or job.state is not JobState.QUEUED:
        return None
    job.state = JobState.RUNNING
    job.attempts += 1
    job.started_at = datetime.now(UTC)
    await db.flush()
    return job


async def mark_succeeded(db: DbSession, job: Job) -> None:
    job.state = JobState.SUCCEEDED
    job.error = None
    job.finished_at = datetime.now(UTC)
    await db.flush()


async def mark_failed(db: DbSession, job: Job, error: str) -> None:
    """Record a failure. The message is shown to the user, so it must say
    what went wrong without leaking a stack trace or an API key."""
    job.state = JobState.FAILED
    job.error = error[:2000]
    job.finished_at = datetime.now(UTC)
    await db.flush()
