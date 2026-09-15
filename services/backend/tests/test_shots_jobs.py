import uuid
from decimal import Decimal

from sqlalchemy import select

from framemeld.credits.service import balance
from framemeld.models.job import Job, JobState
from framemeld.models.script import ArtifactState
from framemeld.models.shots import AudioClip, ShotImage
from framemeld.models.user import User
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResult,
    ProviderTransportError,
)
from framemeld.providers.images.stub import solid_png
from framemeld.providers.speech.stub import StubSpeechProvider
from framemeld.worker import (
    run_generate_character_image,
    run_generate_characters,
    run_generate_script,
    run_generate_shot_image,
    run_generate_speech,
    run_map_shot_characters,
)

STORY = (
    "Elena found the letter beneath the floorboard. She read it twice. "
    "Her hands would not stop shaking.\n\n"
    "Outside, the rain began. Marcus watched her from the doorway and said nothing."
)


class _FakeImageProvider:
    """Records what it was asked to draw, or raises what it was built with."""

    name = "fake"
    model = "fake-image-model"

    def __init__(self, error=None):
        self._error = error
        self.calls: list[tuple[str, list[bytes]]] = []

    async def generate(self, *, prompt, refs, params):
        self.calls.append((prompt, list(refs)))
        if self._error is not None:
            raise self._error
        return ProviderResult(
            text=None,
            bytes_=solid_png(8, 8, (9, 9, 9)),
            cost_usd=Decimal("0.039"),
            provider=self.name,
            model=self.model,
            raw={"mimeType": "image/png"},
        )


class _FakeSpeechProvider:
    name = "fake"
    model = "fake-tts"

    def __init__(self, error=None):
        self._error = error
        self.calls: list[tuple[str, str]] = []

    async def speak(self, *, text, voice_id, params):
        self.calls.append((text, voice_id))
        if self._error is not None:
            raise self._error
        return await StubSpeechProvider().speak(text=text, voice_id=voice_id, params=params)


async def _ready(client, db):
    """A Meld with a script, a cast, sheets, and the shots cast."""
    meld_id = (await client.post("/melds", json={"title": "T"})).json()["id"]
    await client.put(f"/melds/{meld_id}/story", json={"text": STORY})

    script = await client.post(f"/melds/{meld_id}/script/generate")
    await run_generate_script(db, uuid.UUID(script.json()["id"]))
    await db.commit()

    cast = await client.post(f"/melds/{meld_id}/cast/generate")
    await run_generate_characters(db, uuid.UUID(cast.json()["id"]))
    await db.commit()

    for character in (await client.get(f"/melds/{meld_id}/cast")).json()["characters"]:
        sheet = await client.post(f"/melds/{meld_id}/characters/{character['id']}/sheet")
        await run_generate_character_image(db, uuid.UUID(sheet.json()["id"]))
        await db.commit()

    casting = await client.post(f"/melds/{meld_id}/shots/cast")
    await run_map_shot_characters(db, uuid.UUID(casting.json()["id"]))
    await db.commit()
    return meld_id


async def _shots(client, meld_id):
    return (await client.get(f"/melds/{meld_id}/shots")).json()["shots"]


async def _user_id(db) -> uuid.UUID:
    return (await db.execute(select(User.id).where(User.email == "owner@example.com"))).scalar_one()


# --- the point of the whole milestone ------------------------------------


async def test_the_reference_sheets_actually_reach_the_provider(logged_in, db, monkeypatch):
    """M4's entire reason to exist. Everything else can be right and the
    faces still drift if the bytes never leave the database."""
    meld_id = await _ready(logged_in, db)
    peopled = next(s for s in await _shots(logged_in, meld_id) if s["character_ids"])

    provider = _FakeImageProvider()
    monkeypatch.setattr("framemeld.worker.get_image_provider", lambda: provider)

    started = await logged_in.post(f"/melds/{meld_id}/shots/{peopled['id']}/still")
    outcome = await run_generate_shot_image(db, uuid.UUID(started.json()["id"]))

    assert outcome.state is JobState.SUCCEEDED
    prompt, refs = provider.calls[0]
    assert len(refs) == len(peopled["character_ids"])
    assert all(ref.startswith(b"\x89PNG") for ref in refs)
    # And the prompt says who each one is, in the order they were attached.
    for name in peopled["character_names"]:
        assert name in prompt


async def test_a_shot_with_nobody_in_it_sends_no_references(logged_in, db, monkeypatch):
    meld_id = await _ready(logged_in, db)
    empty = next(s for s in await _shots(logged_in, meld_id) if not s["character_ids"])

    provider = _FakeImageProvider()
    monkeypatch.setattr("framemeld.worker.get_image_provider", lambda: provider)

    started = await logged_in.post(f"/melds/{meld_id}/shots/{empty['id']}/still")
    await run_generate_shot_image(db, uuid.UUID(started.json()["id"]))

    prompt, refs = provider.calls[0]
    assert refs == []
    assert "reference images" not in prompt


# --- money ----------------------------------------------------------------


async def test_a_refused_still_refunds_in_full_and_marks_the_row_failed(logged_in, db, monkeypatch):
    meld_id = await _ready(logged_in, db)
    shot = (await _shots(logged_in, meld_id))[0]
    monkeypatch.setattr(
        "framemeld.worker.get_image_provider",
        lambda: _FakeImageProvider(ProviderRefusedError("no")),
    )

    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")

    outcome = await run_generate_shot_image(db, uuid.UUID(started.json()["id"]))

    assert outcome.state is JobState.FAILED
    assert outcome.retryable is False
    still = (
        await db.execute(select(ShotImage).where(ShotImage.shot_id == uuid.UUID(shot["id"])))
    ).scalar_one()
    assert still.state is ArtifactState.FAILED


async def test_a_failed_still_gives_every_credit_back(logged_in, db, monkeypatch):
    meld_id = await _ready(logged_in, db)
    shot = (await _shots(logged_in, meld_id))[0]
    user_id = await _user_id(db)
    before = await balance(db, user_id)

    monkeypatch.setattr(
        "framemeld.worker.get_image_provider",
        lambda: _FakeImageProvider(ProviderRefusedError("no")),
    )
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
    await run_generate_shot_image(db, uuid.UUID(started.json()["id"]))

    assert await balance(db, user_id) == before


async def test_a_transport_failure_retries_without_refunding(logged_in, db, monkeypatch):
    """The hold stays until the job is terminal: a retry would otherwise
    have to take it again and could fail on a balance the user has since
    spent elsewhere."""
    meld_id = await _ready(logged_in, db)
    shot = (await _shots(logged_in, meld_id))[0]
    monkeypatch.setattr(
        "framemeld.worker.get_image_provider",
        lambda: _FakeImageProvider(ProviderTransportError("timeout")),
    )

    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
    user_id = await _user_id(db)
    held_balance = await balance(db, user_id)

    outcome = await run_generate_shot_image(db, uuid.UUID(started.json()["id"]))

    assert outcome.state is JobState.QUEUED
    assert outcome.retryable is True
    assert await balance(db, user_id) == held_balance


async def test_a_redelivered_message_does_not_generate_twice(logged_in, db, monkeypatch):
    meld_id = await _ready(logged_in, db)
    shot = (await _shots(logged_in, meld_id))[0]
    provider = _FakeImageProvider()
    monkeypatch.setattr("framemeld.worker.get_image_provider", lambda: provider)

    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
    job_id = uuid.UUID(started.json()["id"])
    await run_generate_shot_image(db, job_id)
    second = await run_generate_shot_image(db, job_id)

    assert len(provider.calls) == 1
    assert second.state is JobState.SUCCEEDED
    assert second.retryable is False


async def test_the_job_records_what_the_provider_billed(logged_in, db, monkeypatch):
    meld_id = await _ready(logged_in, db)
    shot = (await _shots(logged_in, meld_id))[0]
    monkeypatch.setattr("framemeld.worker.get_image_provider", _FakeImageProvider)

    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
    job_id = uuid.UUID(started.json()["id"])
    await run_generate_shot_image(db, job_id)

    job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert job.cost_usd == Decimal("0.039")


# --- speech ---------------------------------------------------------------


async def test_speech_reads_the_clip_row_not_the_live_shot(logged_in, db, monkeypatch):
    """The row is what the hold was priced against. Reading the shot in the
    worker would let a job quietly record something the user never
    approved, and never paid a quote for."""
    meld_id = await _ready(logged_in, db)
    spoken = next(s for s in await _shots(logged_in, meld_id) if s["dialogue_kind"] != "none")

    provider = _FakeSpeechProvider()
    monkeypatch.setattr("framemeld.worker.get_speech_provider", lambda: provider)

    started = await logged_in.post(f"/melds/{meld_id}/shots/{spoken['id']}/speech")
    # The user edits the line while the job is queued.
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{spoken['id']}", json={"speech_text": "Something else entirely."}
    )
    await run_generate_speech(db, uuid.UUID(started.json()["id"]))

    said, _voice = provider.calls[0]
    assert said == spoken["speech_text"]


async def test_a_failed_recording_refunds_and_marks_the_clip_failed(logged_in, db, monkeypatch):
    meld_id = await _ready(logged_in, db)
    spoken = next(s for s in await _shots(logged_in, meld_id) if s["dialogue_kind"] != "none")
    user_id = await _user_id(db)
    before = await balance(db, user_id)

    monkeypatch.setattr(
        "framemeld.worker.get_speech_provider",
        lambda: _FakeSpeechProvider(ProviderRefusedError("no")),
    )
    started = await logged_in.post(f"/melds/{meld_id}/shots/{spoken['id']}/speech")
    outcome = await run_generate_speech(db, uuid.UUID(started.json()["id"]))

    assert outcome.state is JobState.FAILED
    assert await balance(db, user_id) == before
    clip = (
        await db.execute(select(AudioClip).where(AudioClip.owner_id == uuid.UUID(spoken["id"])))
    ).scalar_one()
    assert clip.state is ArtifactState.FAILED


async def test_the_clip_records_its_own_duration(logged_in, db):
    """Duplicated from the asset deliberately: M5's scene spec lays out a
    timeline from this column."""
    meld_id = await _ready(logged_in, db)
    spoken = next(s for s in await _shots(logged_in, meld_id) if s["dialogue_kind"] != "none")

    started = await logged_in.post(f"/melds/{meld_id}/shots/{spoken['id']}/speech")
    await run_generate_speech(db, uuid.UUID(started.json()["id"]))

    clip = (
        await db.execute(select(AudioClip).where(AudioClip.owner_id == uuid.UUID(spoken["id"])))
    ).scalar_one()
    assert clip.duration_sec is not None
    assert clip.duration_sec > 0


# --- casting --------------------------------------------------------------


async def test_a_casting_job_whose_cast_vanished_fails_terminally(logged_in, db, monkeypatch):
    """Terminal rather than retried: re-running would map a cast that is no
    longer there."""
    from sqlalchemy import delete

    from framemeld.models.cast import Character

    meld_id = (await logged_in.post("/melds", json={"title": "T"})).json()["id"]
    await logged_in.put(f"/melds/{meld_id}/story", json={"text": STORY})
    script = await logged_in.post(f"/melds/{meld_id}/script/generate")
    await run_generate_script(db, uuid.UUID(script.json()["id"]))
    await db.commit()
    cast = await logged_in.post(f"/melds/{meld_id}/cast/generate")
    await run_generate_characters(db, uuid.UUID(cast.json()["id"]))
    await db.commit()

    started = await logged_in.post(f"/melds/{meld_id}/shots/cast")
    await db.execute(delete(Character).where(Character.meld_id == uuid.UUID(meld_id)))
    await db.flush()

    outcome = await run_map_shot_characters(db, uuid.UUID(started.json()["id"]))

    assert outcome.state is JobState.FAILED
    assert outcome.retryable is False
