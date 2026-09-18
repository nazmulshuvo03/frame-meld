import uuid

import pytest
from sqlalchemy import select

from framemeld.credits.pricing import QUOTES, quote_for
from framemeld.credits.service import (
    InsufficientCredits,
    balance,
    grant,
    hold,
    refund,
    settle,
)
from framemeld.models.credits import CreditKind, CreditLedger
from framemeld.models.job import Job, JobKind, JobState
from framemeld.models.meld import Meld
from framemeld.models.user import User


async def _user(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    return user


async def _job(db, meld_id) -> Job:
    job = Job(
        meld_id=meld_id,
        kind=JobKind.GENERATE_CHARACTER_IMAGE,
        target_table="character_images",
        target_id=None,
        input_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        idempotency_key=uuid.uuid4().hex + uuid.uuid4().hex,
        state=JobState.QUEUED,
    )
    db.add(job)
    await db.flush()
    return job


async def _meld(db, user) -> Meld:
    meld = Meld(user_id=user.id, title="T")
    db.add(meld)
    await db.flush()
    return meld


async def test_a_new_user_has_no_credits(db):
    user = await _user(db)
    assert await balance(db, user.id) == 0


async def test_a_grant_moves_the_balance(db):
    user = await _user(db)
    await grant(db, user.id, 500, "welcome")
    assert await balance(db, user.id) == 500


async def test_a_hold_subtracts_immediately(db):
    """Not when the job finishes -- the point of a hold is that the credits
    are gone the moment the work is promised, so a second click cannot
    spend them again."""
    user = await _user(db)
    meld = await _meld(db, user)
    job = await _job(db, meld.id)
    await grant(db, user.id, 100, "welcome")

    await hold(db, user_id=user.id, job_id=job.id, meld_id=meld.id, credits=12, description="sheet")

    assert await balance(db, user.id) == 88


async def test_a_hold_beyond_the_balance_is_refused(db):
    user = await _user(db)
    meld = await _meld(db, user)
    job = await _job(db, meld.id)
    await grant(db, user.id, 10, "welcome")

    with pytest.raises(InsufficientCredits) as caught:
        await hold(
            db, user_id=user.id, job_id=job.id, meld_id=meld.id, credits=12, description="sheet"
        )

    assert caught.value.required == 12
    assert caught.value.available == 10
    # And nothing was written: a refused hold must not leave a partial row.
    assert await balance(db, user.id) == 10


async def test_a_hold_of_exactly_the_balance_is_allowed(db):
    user = await _user(db)
    meld = await _meld(db, user)
    job = await _job(db, meld.id)
    await grant(db, user.id, 12, "welcome")

    await hold(db, user_id=user.id, job_id=job.id, meld_id=meld.id, credits=12, description="sheet")
    assert await balance(db, user.id) == 0


async def test_settling_for_less_than_quoted_refunds_the_difference(db):
    user = await _user(db)
    meld = await _meld(db, user)
    job = await _job(db, meld.id)
    await grant(db, user.id, 100, "welcome")
    await hold(db, user_id=user.id, job_id=job.id, meld_id=meld.id, credits=12, description="sheet")

    await settle(db, job_id=job.id, user_id=user.id, held=12, used=5, description="sheet")

    # 100 granted, 12 held, 7 of it given back: the user paid for the 5 they used.
    assert await balance(db, user.id) == 95


async def test_settling_never_charges_more_than_quoted(db):
    """M3-D3: the quote is a ceiling. A provider that came in over budget is
    our problem, not a surprise on the user's balance."""
    user = await _user(db)
    meld = await _meld(db, user)
    job = await _job(db, meld.id)
    await grant(db, user.id, 100, "welcome")
    await hold(db, user_id=user.id, job_id=job.id, meld_id=meld.id, credits=12, description="sheet")

    await settle(db, job_id=job.id, user_id=user.id, held=12, used=40, description="sheet")

    assert await balance(db, user.id) == 88


async def test_a_failed_job_refunds_in_full(db):
    user = await _user(db)
    meld = await _meld(db, user)
    job = await _job(db, meld.id)
    await grant(db, user.id, 100, "welcome")
    await hold(db, user_id=user.id, job_id=job.id, meld_id=meld.id, credits=12, description="sheet")

    await refund(db, job_id=job.id, user_id=user.id, held=12, description="failed")

    assert await balance(db, user.id) == 100


async def test_a_job_is_only_closed_once(db):
    """A retried-then-failed job, or a redelivered arq message, must not
    refund twice -- that is free money, and the ledger is append-only so
    there is no undo."""
    user = await _user(db)
    meld = await _meld(db, user)
    job = await _job(db, meld.id)
    await grant(db, user.id, 100, "welcome")
    await hold(db, user_id=user.id, job_id=job.id, meld_id=meld.id, credits=12, description="sheet")

    assert (
        await refund(db, job_id=job.id, user_id=user.id, held=12, description="failed") is not None
    )
    assert await refund(db, job_id=job.id, user_id=user.id, held=12, description="failed") is None
    assert (
        await settle(db, job_id=job.id, user_id=user.id, held=12, used=1, description="x") is None
    )

    assert await balance(db, user.id) == 100


async def test_a_settle_that_changes_nothing_writes_no_row(db):
    """The common case: the work cost exactly what was quoted. A zero-delta
    row is noise in an audit trail that a human has to read."""
    user = await _user(db)
    meld = await _meld(db, user)
    job = await _job(db, meld.id)
    await grant(db, user.id, 100, "welcome")
    await hold(db, user_id=user.id, job_id=job.id, meld_id=meld.id, credits=12, description="sheet")

    assert (
        await settle(db, job_id=job.id, user_id=user.id, held=12, used=12, description="x") is None
    )

    rows = (await db.execute(select(CreditLedger).where(CreditLedger.job_id == job.id))).scalars()
    assert [row.kind for row in rows] == [CreditKind.HOLD]


async def test_balances_are_per_user(db):
    first = await _user(db)
    second = await _user(db)
    await grant(db, first.id, 500, "welcome")

    assert await balance(db, first.id) == 500
    assert await balance(db, second.id) == 0


def test_every_billable_job_kind_has_a_price():
    """A kind reaching the quote path with no entry would either crash at
    request time or, worse, be quoted as free."""
    for kind in (
        JobKind.GENERATE_CHARACTERS,
        JobKind.GENERATE_CHARACTER_IMAGE,
        JobKind.MAP_SHOT_CHARACTERS,
        JobKind.GENERATE_SHOT_IMAGE,
        JobKind.GENERATE_SPEECH,
        JobKind.GENERATE_MUSIC,
        JobKind.GENERATE_SHOT_CLIP,
        JobKind.RENDER_FINAL,
    ):
        assert quote_for(kind) > 0
        assert kind in QUOTES


def test_an_unpriced_kind_raises_rather_than_defaulting_to_free():
    """RENDER_PREVIEW rather than an arbitrary kind: it is the one action
    that is deliberately and permanently unpriced (M5-D7), so this asserts
    the behaviour *and* the decision. It used to name RENDER_FINAL, which
    M6 priced -- a free Final render would have been a revenue-report
    discovery rather than a test failure, which is what this test is for.
    """
    with pytest.raises(KeyError):
        quote_for(JobKind.RENDER_PREVIEW)
