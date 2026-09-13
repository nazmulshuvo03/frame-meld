import uuid

from sqlalchemy import func, select

from framemeld.jobs.queue import RecordingQueue
from framemeld.jobs.service import ensure_job, mark_failed, mark_running, mark_succeeded
from framemeld.melds import service as melds_service
from framemeld.models.job import Job, JobKind, JobState
from framemeld.models.user import User


async def _meld(db):
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    return await melds_service.create_meld(db, user.id, "T")


async def _dispatch(db, meld, input_hash="a" * 64):
    return await ensure_job(
        db,
        meld_id=meld.id,
        kind=JobKind.GENERATE_SCRIPT,
        target_table="melds",
        target_id=meld.id,
        input_hash=input_hash,
    )


async def test_a_first_request_creates_and_enqueues(db):
    meld = await _meld(db)
    dispatch = await _dispatch(db, meld)
    assert dispatch.enqueued is True
    assert dispatch.job.state is JobState.QUEUED


async def test_a_second_request_with_the_same_inputs_is_a_cache_hit(db):
    """Spec section 5.4: double-clicking Generate must be free, not
    expensive."""
    meld = await _meld(db)
    first = await _dispatch(db, meld)
    second = await _dispatch(db, meld)

    assert second.enqueued is False
    assert second.job.id == first.job.id
    assert (await db.execute(select(func.count()).select_from(Job))).scalar_one() == 1


async def test_different_inputs_create_different_jobs(db):
    meld = await _meld(db)
    first = await _dispatch(db, meld, input_hash="a" * 64)
    second = await _dispatch(db, meld, input_hash="b" * 64)
    assert first.job.id != second.job.id


async def test_a_succeeded_job_is_not_run_again(db):
    meld = await _meld(db)
    first = await _dispatch(db, meld)
    await mark_running(db, first.job.id)
    await mark_succeeded(db, first.job)

    again = await _dispatch(db, meld)
    assert again.enqueued is False
    assert again.job.state is JobState.SUCCEEDED


async def test_a_failed_job_can_be_retried_in_place(db):
    """The unique key would otherwise block every retry forever, so a
    failed job is reset rather than duplicated."""
    meld = await _meld(db)
    first = await _dispatch(db, meld)
    await mark_running(db, first.job.id)
    await mark_failed(db, first.job, "provider exploded")

    retry = await _dispatch(db, meld)
    assert retry.enqueued is True
    assert retry.job.id == first.job.id
    assert retry.job.state is JobState.QUEUED
    assert retry.job.error is None
    assert retry.job.finished_at is None
    assert retry.job.attempts == 1  # the failed attempt is still counted


async def test_mark_running_refuses_a_job_that_is_not_queued(db):
    """Two workers can be handed the same message. The second must find
    nothing to do rather than generate the script twice."""
    meld = await _meld(db)
    dispatch = await _dispatch(db, meld)

    assert await mark_running(db, dispatch.job.id) is not None
    assert await mark_running(db, dispatch.job.id) is None


async def test_mark_running_counts_the_attempt_and_stamps_the_start(db):
    meld = await _meld(db)
    dispatch = await _dispatch(db, meld)
    job = await mark_running(db, dispatch.job.id)
    assert job.attempts == 1
    assert job.started_at is not None


async def test_mark_running_on_an_unknown_id_returns_none(db):
    assert await mark_running(db, uuid.uuid4()) is None


async def test_the_recording_queue_records(db):
    queue = RecordingQueue()
    await queue.enqueue("generate_script", uuid.UUID(int=1))
    assert queue.enqueued == [("generate_script", uuid.UUID(int=1))]
