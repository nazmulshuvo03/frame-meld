import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.hashing import canonical_hash
from framemeld.models.job import Job, JobKind, JobState
from framemeld.models.script import ArtifactState

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
    # Committed, not just flushed, and this is the difference between a
    # claim and a note-to-self.
    #
    # A worker job commits once, at the end, in its arq wrapper. Until then
    # a flush is visible only inside this transaction -- so for a job's
    # entire life the api, on its own connection, still reads `queued`. On
    # the jobs that take 50 ms nobody noticed. On a render that takes twenty
    # seconds (or ten minutes at thirty shots) the button says "Queued…"
    # from the first frame to the last, and `started_at` is null the whole
    # time.
    #
    # It also makes the double-delivery guard above mean what it says: two
    # workers handed the same message cannot both see `queued` and both
    # claim it, because the winner's claim is committed before either runs.
    #
    # Safe to commit here because this is the first write every run_* makes,
    # so the only thing pending is the claim itself. `expire_on_commit` is
    # False on both session factories, so `job` stays usable afterwards
    # rather than needing a refresh -- which on an AsyncSession would be the
    # MissingGreenlet m3-decisions.md records.
    await db.commit()
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


async def mark_for_retry(db: DbSession, job: Job, error: str) -> None:
    """Hand a job back to the queue after a failure worth repeating.

    Returns the row to `queued` so the redelivered message can claim it —
    `mark_running` only claims from `queued`, so a job left `failed` is a
    job the retry silently skips. `attempts` is deliberately kept: it is
    the row's history and the budget reads it.

    The error is recorded even though the job has not finally failed, so a
    user watching a job retry can see why it is retrying.
    """
    job.state = JobState.QUEUED
    job.error = error[:2000]
    job.started_at = None
    await db.flush()


class AlreadyRunning(Exception):
    """A job was asked to stop after a worker had already claimed it.

    Refused rather than force-marked cancelled. The worker is inside a
    provider call by then: the money is already committed, the response is
    still coming, and a row marked `cancelled` under it would be overwritten
    by `mark_succeeded` seconds later -- leaving a user who was told the job
    stopped with a charge and an artifact.

    Stopping a running generation properly means cancelling it at the
    provider, which none of the five adapters supports.
    """

    reason = "That one has already started. It will finish or fail on its own."


#: Where a job's target lives, by `target_table`. Spelled out rather than
#: resolved from the mapper registry: the set is fixed, and a lookup that
#: could reach any table is a lookup that could clear any row.
_TARGETS = {
    "shot_images": "framemeld.models.shots:ShotImage",
    "shot_clips": "framemeld.models.shots:ShotClip",
    "audio_clips": "framemeld.models.shots:AudioClip",
    "character_images": "framemeld.models.cast:CharacterImage",
    "renders": "framemeld.models.render:Render",
}


async def _release_target(db: DbSession, job: Job) -> None:
    """Take the job's artifact out of `generating`.

    A cancelled job leaves a row claiming a worker is about to write it,
    which the staleness engine deliberately will not touch (`generating` is
    owned by the worker) -- so without this the row is stuck out of the walk
    forever, invisible to both the user and the recompute.

    `empty` when there are no bytes, `fresh` when there are: a regeneration
    that was cancelled leaves the previous artwork exactly where it was, and
    recompute_staleness decides immediately afterwards whether that artwork
    is still current.
    """
    if job.target_id is None or job.target_table not in _TARGETS:
        return

    module_name, class_name = _TARGETS[job.target_table].split(":")
    model = getattr(import_module(module_name), class_name)

    row = (await db.execute(select(model).where(model.id == job.target_id))).scalar_one_or_none()
    if row is None or row.state is not ArtifactState.GENERATING:
        return
    row.state = ArtifactState.FRESH if row.asset_id is not None else ArtifactState.EMPTY
    row.updated_at = datetime.now(UTC)


async def cancel_job(db: DbSession, job: Job) -> Job:
    """Stop a queued job and give the money back.

    Only `queued`. `mark_running` claims from that state and no other, so a
    job left here is a job the worker will decline to run when the message
    is eventually delivered -- which is what makes this safe without
    reaching into arq: the queue may still hold a message, and it becomes a
    no-op rather than a race.
    """
    if job.state is JobState.RUNNING:
        raise AlreadyRunning
    if job.state is not JobState.QUEUED:
        # Already finished, failed or cancelled. Idempotent rather than an
        # error: two clicks on a button that was about to disappear anyway.
        return job

    job.state = JobState.CANCELLED
    job.finished_at = datetime.now(UTC)
    await _release_target(db, job)
    await db.flush()
    return job
