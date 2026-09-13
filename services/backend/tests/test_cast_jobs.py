import uuid
from decimal import Decimal

from sqlalchemy import select

import framemeld.worker as worker_module
from framemeld.cast.hashes import characters_input_hash
from framemeld.credits.service import balance, grant, hold
from framemeld.jobs.service import ensure_job
from framemeld.melds import service as melds_service
from framemeld.models.cast import Character
from framemeld.models.job import Job, JobKind, JobState
from framemeld.models.script import ArtifactState
from framemeld.models.user import User
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResult,
    ProviderTransportError,
)
from framemeld.script.hashes import content_hash_for_story
from framemeld.worker import run_generate_characters

STORY = (
    "Elena found the letter beneath the floorboard. She read it twice. "
    "Her hands would not stop shaking.\n\n"
    "Outside, the rain began. Marcus watched her from the doorway and said nothing."
)

CAST_JSON = (
    '{"characters":['
    '{"name":"Elena","role":"lead","description":"Finds the letter.",'
    '"appearance":"A woman in her sixties, silver hair pinned back."},'
    '{"name":"Marcus","role":"supporting","description":"Watches.",'
    '"appearance":"A thin man in a wet raincoat."}'
    "]}"
)

QUOTE = 5


class _FakeProvider:
    """Returns whatever it was built with, or raises it."""

    name = "fake"
    model = "fake-model"

    def __init__(self, text=CAST_JSON, error=None, cost=Decimal("0.000123")):
        self._text = text
        self._error = error
        self._cost = cost
        self.calls = 0

    async def complete(self, *, system, user, json_schema):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return ProviderResult(
            text=self._text,
            bytes_=None,
            cost_usd=self._cost,
            provider=self.name,
            model=self.model,
            raw={},
        )


async def _arrange(db, monkeypatch, provider=None, *, credits=500, held=QUOTE):
    """A Meld with a story, a funded user, and a queued job with a hold
    already taken -- the state Task 12's API leaves behind."""
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    meld = await melds_service.create_meld(db, user.id, "T")
    await melds_service.save_story(db, user.id, meld.id, STORY)
    await grant(db, user.id, credits, "welcome")

    dispatch = await ensure_job(
        db,
        meld_id=meld.id,
        kind=JobKind.GENERATE_CHARACTERS,
        target_table="melds",
        target_id=meld.id,
        input_hash=characters_input_hash(content_hash_for_story(STORY)),
    )
    job = dispatch.job
    if held:
        await hold(
            db,
            user_id=user.id,
            job_id=job.id,
            meld_id=meld.id,
            credits=held,
            description="cast",
        )
        job.credits_held = held
        await db.flush()

    provider = provider or _FakeProvider()
    monkeypatch.setattr(worker_module, "get_llm_provider", lambda: provider)
    return user, meld, job, provider


async def test_a_successful_run_persists_the_cast_and_records_the_cost(db, monkeypatch):
    _user, _meld, job, _ = await _arrange(db, monkeypatch)

    outcome = await run_generate_characters(db, job.id)

    assert outcome.state is JobState.SUCCEEDED
    assert outcome.retryable is False

    stored = (await db.execute(select(Character).order_by(Character.order_index))).scalars().all()
    assert [c.name for c in stored] == ["Elena", "Marcus"]
    assert all(c.state is ArtifactState.FRESH for c in stored)

    refreshed = (await db.execute(select(Job).where(Job.id == job.id))).scalar_one()
    assert refreshed.state is JobState.SUCCEEDED
    assert refreshed.cost_usd == Decimal("0.000123")


async def test_a_successful_run_leaves_the_balance_down_by_exactly_the_hold(db, monkeypatch):
    """No double charge and no accidental refund: both M3 actions are one
    unit of work, so settle returns nothing and the hold stands."""
    user, _meld, job, _ = await _arrange(db, monkeypatch)

    await run_generate_characters(db, job.id)

    assert await balance(db, user.id) == 500 - QUOTE


async def test_a_refusal_refunds_every_credit(db, monkeypatch):
    """Spec 4.3: a failed generation refunds in full, and a content refusal
    is terminal rather than retried -- so this is the path a user actually
    hits when they write something the model will not touch."""
    user, _meld, job, _ = await _arrange(
        db, monkeypatch, provider=_FakeProvider(error=ProviderRefusedError("no"))
    )

    outcome = await run_generate_characters(db, job.id)

    assert outcome.state is JobState.FAILED
    assert outcome.retryable is False
    assert await balance(db, user.id) == 500


async def test_a_retry_does_not_refund(db, monkeypatch):
    """The hold survives until the job is terminal. Refunding on every
    retry would mean re-taking it on the next attempt, which can fail
    against a balance the user has since spent somewhere else."""
    user, _meld, job, _ = await _arrange(
        db, monkeypatch, provider=_FakeProvider(error=ProviderTransportError("timeout"))
    )

    outcome = await run_generate_characters(db, job.id)

    assert outcome.state is JobState.QUEUED
    assert outcome.retryable is True
    assert await balance(db, user.id) == 500 - QUOTE


async def test_an_exhausted_retry_budget_refunds_exactly_once(db, monkeypatch):
    user, _meld, job, _ = await _arrange(
        db, monkeypatch, provider=_FakeProvider(error=ProviderTransportError("timeout"))
    )
    job.attempts = worker_module.MAX_ATTEMPTS
    await db.flush()

    outcome = await run_generate_characters(db, job.id)

    assert outcome.state is JobState.FAILED
    assert outcome.retryable is False
    assert await balance(db, user.id) == 500


async def test_an_unforeseen_crash_still_refunds_and_leaves_the_job_actionable(db, monkeypatch):
    """An exception escaping the task would roll back mark_running and
    strand the job in `queued` with no worker behind it."""
    user, _meld, job, _ = await _arrange(
        db, monkeypatch, provider=_FakeProvider(error=RuntimeError("boom"))
    )

    outcome = await run_generate_characters(db, job.id)

    assert outcome.state is JobState.FAILED
    refreshed = (await db.execute(select(Job).where(Job.id == job.id))).scalar_one()
    assert refreshed.state is JobState.FAILED
    assert refreshed.error
    assert await balance(db, user.id) == 500


async def test_a_redelivered_message_neither_regenerates_nor_re_settles(db, monkeypatch):
    """arq can deliver the same message twice. The second delivery must not
    call the provider again, and must not move the balance."""
    user, _meld, job, provider = await _arrange(db, monkeypatch)
    await run_generate_characters(db, job.id)
    balance_after_first = await balance(db, user.id)

    outcome = await run_generate_characters(db, job.id)

    assert outcome.state is JobState.SUCCEEDED
    assert provider.calls == 1
    assert await balance(db, user.id) == balance_after_first


async def test_a_job_with_no_hold_settles_without_error(db, monkeypatch):
    """M2's jobs carry credits_held=0. _close_hold is called on every
    terminal path, so it has to be a no-op for them."""
    user, _meld, job, _ = await _arrange(db, monkeypatch, held=0)

    outcome = await run_generate_characters(db, job.id)

    assert outcome.state is JobState.SUCCEEDED
    assert await balance(db, user.id) == 500


async def test_a_bad_response_shape_is_terminal_and_refunds(db, monkeypatch):
    """A model that answered with prose will answer with prose again, and
    each attempt costs."""
    user, _meld, job, _ = await _arrange(
        db, monkeypatch, provider=_FakeProvider(text="Here are your characters!")
    )

    outcome = await run_generate_characters(db, job.id)

    assert outcome.state is JobState.FAILED
    assert outcome.retryable is False
    assert await balance(db, user.id) == 500


async def test_an_unknown_job_is_cancelled_rather_than_crashing(db):
    outcome = await run_generate_characters(db, uuid.uuid4())
    assert outcome.state is JobState.CANCELLED
