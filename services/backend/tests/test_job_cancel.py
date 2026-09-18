"""Cancelling a queued generation.

The complaint: stopping the worker left a button on "Queued…" indefinitely,
with the user's credits still held against a job nothing would ever run.
"""

import uuid

from sqlalchemy import select
from test_render_api import _user_id, drawn, scripted

from framemeld.credits.service import balance
from framemeld.models.credits import CreditKind, CreditLedger
from framemeld.models.job import Job, JobState
from framemeld.models.script import ArtifactState
from framemeld.models.shots import ShotImage
from framemeld.worker import run_generate_shot_image


async def test_a_queued_job_is_cancelled_and_refunded(logged_in, db):
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]

    # Force a regeneration so there is a fresh queued job to cancel.
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "a different room"}
    )
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
    await db.commit()
    before = await balance(db, await _user_id(db))

    response = await logged_in.post(f"/melds/{meld_id}/jobs/{started.json()['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    # Every credit held for it is back; the exact figure is pinned by the
    # next test, which reads it off the job row rather than assuming a price.
    assert await balance(db, await _user_id(db)) > before


async def test_the_hold_comes_back_in_full(logged_in, db):
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "another room"}
    )
    await db.commit()

    before = await balance(db, await _user_id(db))
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
    held = (
        await db.execute(select(Job).where(Job.id == uuid.UUID(started.json()["id"])))
    ).scalar_one()
    assert held.credits_held > 0
    assert await balance(db, await _user_id(db)) == before - held.credits_held

    await logged_in.post(f"/melds/{meld_id}/jobs/{started.json()['id']}/cancel")
    assert await balance(db, await _user_id(db)) == before

    kinds = [
        row.kind
        for row in (
            await db.execute(select(CreditLedger).where(CreditLedger.job_id == held.id))
        ).scalars()
    ]
    assert CreditKind.REFUND in kinds


async def test_the_artifact_comes_out_of_generating(logged_in, db):
    """A cancelled job otherwise leaves a row claiming a worker is about to
    write it — and `generating` is deliberately outside the staleness walk,
    so it would be stuck there forever."""
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "somewhere else"}
    )
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
    await db.commit()

    still = (
        await db.execute(select(ShotImage).where(ShotImage.shot_id == uuid.UUID(shot["id"])))
    ).scalar_one()
    assert still.state is ArtifactState.GENERATING

    await logged_in.post(f"/melds/{meld_id}/jobs/{started.json()['id']}/cancel")
    await db.refresh(still)
    # It already had bytes from the first draw, so the previous picture is
    # still there — and it is stale, because the prompt moved.
    assert still.state is ArtifactState.STALE
    assert still.asset_id is not None


async def test_a_cancelled_job_never_runs(logged_in, db):
    """`mark_running` claims from `queued` and no other state, which is what
    makes cancelling safe without reaching into arq: the queue may still
    hold the message, and delivering it becomes a no-op."""
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "nowhere at all"}
    )
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
    await db.commit()
    job_id = uuid.UUID(started.json()["id"])

    await logged_in.post(f"/melds/{meld_id}/jobs/{job_id}/cancel")
    await db.commit()

    outcome = await run_generate_shot_image(db, job_id)
    assert outcome.state is JobState.CANCELLED
    assert outcome.retryable is False


async def test_a_running_job_is_refused_rather_than_half_stopped(logged_in, db):
    """The worker is inside a provider call by then: the money is committed,
    and a row marked cancelled under it would be overwritten seconds later."""
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "mid flight"}
    )
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
    await db.commit()

    job = (
        await db.execute(select(Job).where(Job.id == uuid.UUID(started.json()["id"])))
    ).scalar_one()
    job.state = JobState.RUNNING
    await db.commit()

    response = await logged_in.post(f"/melds/{meld_id}/jobs/{job.id}/cancel")
    assert response.status_code == 409
    assert "already started" in response.json()["detail"]


async def test_cancelling_twice_is_not_an_error(logged_in, db):
    """Two clicks on a button that was about to disappear anyway."""
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "twice over"}
    )
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
    await db.commit()
    before = await balance(db, await _user_id(db))

    first = await logged_in.post(f"/melds/{meld_id}/jobs/{started.json()['id']}/cancel")
    after_one = await balance(db, await _user_id(db))
    second = await logged_in.post(f"/melds/{meld_id}/jobs/{started.json()['id']}/cancel")

    assert first.status_code == 200 and second.status_code == 200
    # And the second one refunds nothing: `_already_closed` in the ledger.
    assert await balance(db, await _user_id(db)) == after_one
    assert after_one > before


async def test_another_users_job_cannot_be_cancelled(client, db):
    await client.post(
        "/auth/signup", json={"email": "owner@example.com", "password": "hunter2hunter2"}
    )
    await client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "hunter2hunter2"}
    )
    meld_id = await scripted(client, db)
    job = (await db.execute(select(Job).where(Job.meld_id == uuid.UUID(meld_id)))).scalars().first()
    await client.post("/auth/logout")

    stranger = {"email": "stranger@example.com", "password": "hunter2hunter2"}
    await client.post("/auth/signup", json=stranger)
    await client.post("/auth/login", json=stranger)

    assert (await client.post(f"/melds/{meld_id}/jobs/{job.id}/cancel")).status_code == 404
