import uuid
from decimal import Decimal

from sqlalchemy import delete, select

import framemeld.worker as worker_module
from framemeld.assets.service import png_dimensions
from framemeld.cast.hashes import (
    character_image_input_hash,
    characters_input_hash,
    content_hash_for_character_image,
    visual_hash_for_character,
)
from framemeld.credits.service import balance, grant, hold
from framemeld.jobs.service import ensure_job
from framemeld.melds import service as melds_service
from framemeld.models.asset import Asset
from framemeld.models.cast import Character, CharacterImage, CharacterImageKind
from framemeld.models.job import Job, JobKind, JobState
from framemeld.models.script import ArtifactState
from framemeld.models.user import User
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResult,
    ProviderTransportError,
)
from framemeld.providers.images.stub import StubImageProvider, solid_png
from framemeld.script.hashes import content_hash_for_story
from framemeld.storage.registry import get_storage
from framemeld.worker import run_generate_character_image, run_generate_characters

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


class _FakeImageProvider:
    name = "fake-image"
    model = "fake-image-model"

    def __init__(self, data=None, error=None, mime="image/png", cost=Decimal("0.039")):
        self._data = data
        self._error = error
        self._mime = mime
        self._cost = cost
        self.prompts: list[str] = []

    async def generate(self, *, prompt, refs, params):
        self.prompts.append(prompt)
        if self._error is not None:
            raise self._error
        data = self._data if self._data is not None else solid_png(16, 16, (7, 8, 9))
        return ProviderResult(
            text=None,
            bytes_=data,
            cost_usd=self._cost,
            provider=self.name,
            model=self.model,
            raw={"mimeType": self._mime},
        )


IMAGE_QUOTE = 12


async def _arrange_image(db, monkeypatch, provider=None, *, held=IMAGE_QUOTE):
    """A Meld with a cast, and one empty CharacterImage row with a hold
    against it -- the state Task 12's API leaves behind."""
    user, _meld, cast_job, _ = await _arrange(db, monkeypatch)
    await run_generate_characters(db, cast_job.id)
    character = (
        (await db.execute(select(Character).order_by(Character.order_index))).scalars().first()
    )

    input_hash = character_image_input_hash(
        visual_hash_for_character(character), CharacterImageKind.SHEET
    )
    image = CharacterImage(
        character_id=character.id,
        kind=CharacterImageKind.SHEET,
        is_canonical=True,
        input_hash=input_hash,
        state=ArtifactState.EMPTY,
    )
    db.add(image)
    await db.flush()

    dispatch = await ensure_job(
        db,
        meld_id=cast_job.meld_id,
        kind=JobKind.GENERATE_CHARACTER_IMAGE,
        target_table="character_images",
        target_id=image.id,
        input_hash=input_hash,
    )
    job = dispatch.job
    if held:
        await hold(
            db,
            user_id=user.id,
            job_id=job.id,
            meld_id=cast_job.meld_id,
            credits=held,
            description="sheet",
        )
        job.credits_held = held
        await db.flush()

    provider = provider or _FakeImageProvider()
    monkeypatch.setattr(worker_module, "get_image_provider", lambda: provider)
    return user, character, image, job, provider


async def test_a_generated_sheet_is_stored_and_linked(db, monkeypatch):
    user, character, image, job, provider = await _arrange_image(db, monkeypatch)
    balance_before = await balance(db, user.id)

    outcome = await run_generate_character_image(db, job.id)

    assert outcome.state is JobState.SUCCEEDED
    asset = (await db.execute(select(Asset))).scalar_one()
    assert image.asset_id == asset.id
    assert image.state is ArtifactState.FRESH
    assert image.content_hash == content_hash_for_character_image(asset.sha256)
    assert image.provider == "fake-image"
    # The sheet is drawn from appearance, never from the plot.
    assert character.appearance[:20] in provider.prompts[0]
    assert await balance(db, user.id) == balance_before


async def test_the_stored_bytes_are_a_real_image(db, monkeypatch):
    """png_dimensions reads the IHDR, so this fails if the pipeline ever
    stores something that is not the image the provider returned."""
    _user, _character, _image, job, _ = await _arrange_image(db, monkeypatch)

    await run_generate_character_image(db, job.id)

    asset = (await db.execute(select(Asset))).scalar_one()
    stored = await get_storage().get(key=asset.storage_key)
    assert png_dimensions(stored) == (16, 16)
    assert asset.width == 16
    assert asset.height == 16


async def test_credits_spent_records_what_was_held(db, monkeypatch):
    _user, _character, image, job, _ = await _arrange_image(db, monkeypatch)

    await run_generate_character_image(db, job.id)

    assert image.credits_spent == IMAGE_QUOTE


async def test_a_failed_sheet_stores_nothing_and_refunds(db, monkeypatch):
    """A half-generated image is worse than none: it would show up in the
    gallery as the user's paid-for artwork."""
    user, _character, image, job, _ = await _arrange_image(
        db, monkeypatch, provider=_FakeImageProvider(error=ProviderRefusedError("no"))
    )
    balance_before = await balance(db, user.id)

    outcome = await run_generate_character_image(db, job.id)

    assert outcome.state is JobState.FAILED
    assert (await db.execute(select(Asset))).scalars().all() == []
    assert image.state is ArtifactState.FAILED
    assert image.asset_id is None
    assert await balance(db, user.id) == balance_before + IMAGE_QUOTE


async def test_an_unstorable_format_is_terminal(db, monkeypatch):
    """A file called '.bin' renders as a broken image forever. Better to
    refuse it and give the credits back."""
    user, _character, image, job, _ = await _arrange_image(
        db, monkeypatch, provider=_FakeImageProvider(mime="image/x-nonsense")
    )
    balance_before = await balance(db, user.id)

    outcome = await run_generate_character_image(db, job.id)

    assert outcome.state is JobState.FAILED
    assert outcome.retryable is False
    assert image.state is ArtifactState.FAILED
    assert (await db.execute(select(Asset))).scalars().all() == []
    assert await balance(db, user.id) == balance_before + IMAGE_QUOTE


async def test_a_transport_failure_retries_without_refunding(db, monkeypatch):
    user, _character, image, job, _ = await _arrange_image(
        db, monkeypatch, provider=_FakeImageProvider(error=ProviderTransportError("timeout"))
    )
    balance_before = await balance(db, user.id)

    outcome = await run_generate_character_image(db, job.id)

    assert outcome.state is JobState.QUEUED
    assert outcome.retryable is True
    # Not FAILED: the row is still on its way, and the hold stands.
    assert image.state is ArtifactState.EMPTY
    assert await balance(db, user.id) == balance_before


async def test_two_characters_get_two_different_sheets(db, monkeypatch):
    """A shared asset would mean every character in a Meld wore the same
    face."""
    _user, _character, _image, first_job, _ = await _arrange_image(db, monkeypatch)
    await run_generate_character_image(db, first_job.id)

    characters = (
        (await db.execute(select(Character).order_by(Character.order_index))).scalars().all()
    )
    second = characters[1]
    input_hash = character_image_input_hash(
        visual_hash_for_character(second), CharacterImageKind.SHEET
    )
    image = CharacterImage(
        character_id=second.id,
        kind=CharacterImageKind.SHEET,
        is_canonical=True,
        input_hash=input_hash,
        state=ArtifactState.EMPTY,
    )
    db.add(image)
    await db.flush()
    dispatch = await ensure_job(
        db,
        meld_id=first_job.meld_id,
        kind=JobKind.GENERATE_CHARACTER_IMAGE,
        target_table="character_images",
        target_id=image.id,
        input_hash=input_hash,
    )
    # A provider whose bytes follow the prompt, so two different characters
    # cannot accidentally share one picture.
    monkeypatch.setattr(worker_module, "get_image_provider", StubImageProvider)

    await run_generate_character_image(db, dispatch.job.id)

    assets = (await db.execute(select(Asset))).scalars().all()
    assert len(assets) == 2
    assert assets[0].storage_key != assets[1].storage_key
    assert assets[0].sha256 != assets[1].sha256


async def test_a_vanished_character_fails_and_refunds(db, monkeypatch):
    user, character, _image, job, _ = await _arrange_image(db, monkeypatch)
    balance_before = await balance(db, user.id)
    await db.execute(delete(Character).where(Character.id == character.id))
    await db.flush()

    outcome = await run_generate_character_image(db, job.id)

    assert outcome.state is JobState.FAILED
    assert await balance(db, user.id) == balance_before + IMAGE_QUOTE
