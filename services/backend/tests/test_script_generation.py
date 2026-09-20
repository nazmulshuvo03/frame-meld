import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

import framemeld.worker as worker_module
from framemeld.jobs.service import ensure_job
from framemeld.melds import service as melds_service
from framemeld.models.job import Job, JobKind, JobState
from framemeld.models.meld import Meld, MeldStage
from framemeld.models.script import ArtifactState, Scene, Shot
from framemeld.models.user import User
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderTransportError,
)
from framemeld.script.generation import MIN_SHOT_SECONDS, apply_script, parse_script
from framemeld.script.hashes import (
    content_hash_for_scene,
    content_hash_for_shot,
    content_hash_for_story,
    script_input_hash,
)
from framemeld.worker import run_generate_script

STORY = (
    "Elena found the letter beneath the floorboard. She read it twice. "
    "Her hands would not stop shaking.\n\n"
    "Outside, the rain began. She burned the letter in the sink."
)


async def _meld_with_story(db, text=STORY):
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    meld = await melds_service.create_meld(db, user.id, "T")
    await melds_service.save_story(db, user.id, meld.id, text)
    return user, meld


async def _queue_script_job(db, meld, story_text=STORY):
    dispatch = await ensure_job(
        db,
        meld_id=meld.id,
        kind=JobKind.GENERATE_SCRIPT,
        target_table="melds",
        target_id=meld.id,
        input_hash=script_input_hash(content_hash_for_story(story_text)),
    )
    return dispatch.job


def test_a_non_json_response_is_a_terminal_error():
    with pytest.raises(ProviderResponseError):
        parse_script("I'm sorry, I can't help with that.")


def test_a_json_response_with_no_scenes_is_an_error():
    with pytest.raises(ProviderResponseError):
        parse_script('{"scenes": []}')


def test_an_over_long_shot_is_clamped_not_rejected():
    """Models ignore numeric bounds routinely. Losing the whole script over
    one bad duration would be the wrong trade."""
    draft = parse_script(
        '{"scenes":[{"heading":"A","summary":"s","location":"l","time_of_day":"day",'
        '"shots":[{"image_prompt":"p","motion_prompt":"m","duration_sec":9.5,'
        '"camera":"wide","dialogue_kind":"none","speech_text":""}]}]}'
    )
    assert draft.scenes[0].shots[0].duration_sec == Decimal("5.00")


def test_an_invented_camera_angle_falls_back():
    """One invented enum value should cost that field's fidelity, not the
    whole generation."""
    draft = parse_script(
        '{"scenes":[{"heading":"A","summary":"s","location":"l","time_of_day":"day",'
        '"shots":[{"image_prompt":"p","motion_prompt":"m","duration_sec":3,'
        '"camera":"drone","dialogue_kind":"singing","speech_text":""}]}]}'
    )
    shot = draft.scenes[0].shots[0]
    assert shot.camera.value == "medium"
    assert shot.dialogue_kind.value == "none"


def test_a_nan_duration_is_clamped_not_left_to_crash():
    """json.loads accepts NaN by default. NaN survives Decimal(str(...))
    and only raises InvalidOperation from the comparison that follows --
    an ArithmeticError, not a ProviderError, that must not escape the
    parser and strand the worker outside its except clause."""
    draft = parse_script(
        '{"scenes":[{"heading":"A","summary":"s","location":"l","time_of_day":"day",'
        '"shots":[{"image_prompt":"p","motion_prompt":"m","duration_sec":NaN,'
        '"camera":"wide","dialogue_kind":"none","speech_text":""}]}]}'
    )
    assert draft.scenes[0].shots[0].duration_sec == MIN_SHOT_SECONDS


def test_the_shot_count_is_capped():
    """Spec section 1 targets 10-30 shots; a runaway model must not produce
    a 400-shot film the user is later asked to pay for."""
    shot = (
        '{"image_prompt":"p","motion_prompt":"m","duration_sec":3,'
        '"camera":"wide","dialogue_kind":"none","speech_text":""}'
    )
    scenes = ",".join(
        '{"heading":"A","summary":"s","location":"l","time_of_day":"day","shots":[' + shot + "]}"
        for _ in range(50)
    )
    draft = parse_script('{"scenes":[' + scenes + "]}")
    assert sum(len(scene.shots) for scene in draft.scenes) == 30


def test_an_overlong_heading_is_truncated_not_rejected():
    """A 250-character heading must not fail ScriptDraft.model_validate and
    lose the entire script -- the same clamp-not-reject policy the numeric
    and enum fields already get."""
    draft = parse_script(
        '{"scenes":[{"heading":"'
        + "H" * 250
        + '","summary":"s","location":"l","time_of_day":"day",'
        '"shots":[{"image_prompt":"p","motion_prompt":"m","duration_sec":3,'
        '"camera":"wide","dialogue_kind":"none","speech_text":""}]}]}'
    )
    assert len(draft.scenes[0].heading) == 200


async def test_apply_script_returns_the_scene_and_shot_counts(db):
    """(scenes, shots) is a specified interface (task-8-brief's Produces
    list) with no coverage until now."""
    shot = (
        '{"image_prompt":"p","motion_prompt":"m","duration_sec":3,'
        '"camera":"wide","dialogue_kind":"none","speech_text":""}'
    )
    draft = parse_script(
        '{"scenes":['
        '{"heading":"A","summary":"s","location":"l","time_of_day":"day","shots":['
        + shot
        + ","
        + shot
        + "]},"
        '{"heading":"B","summary":"s","location":"l","time_of_day":"day","shots":[' + shot + "]}"
        "]}"
    )
    _user, meld = await _meld_with_story(db)
    result = await apply_script(db, meld, draft, input_hash="a" * 64)
    assert result == (2, 3)


async def test_a_generated_script_is_persisted_and_fresh(db):
    _user, meld = await _meld_with_story(db)
    job = await _queue_script_job(db, meld)
    # Captured before expire_all(): reading an expired ORM attribute outside
    # an awaited call raises MissingGreenlet under the async ORM.
    job_input_hash = job.input_hash

    assert (await run_generate_script(db, job.id)).state is JobState.SUCCEEDED

    db.expire_all()
    scenes = (await db.execute(select(Scene).order_by(Scene.order_index))).scalars().all()
    shots = (await db.execute(select(Shot))).scalars().all()
    assert len(scenes) == 2
    # Exact, not >=: an off-by-one (a dropped or duplicated shot) would
    # pass a >= check silently. The stub is deterministic for STORY, so an
    # exact count is not a flaky assertion.
    assert len(shots) == 5
    assert all(row.state is ArtifactState.FRESH for row in (*scenes, *shots))
    assert all(row.input_hash == job_input_hash for row in (*scenes, *shots))
    # Not just non-empty: the stored hash must be the one the hash functions
    # produce for these rows, or M4's shot images will never notice an edit
    # (their input_hash is derived from this value).
    assert all(scene.content_hash == content_hash_for_scene(scene) for scene in scenes)
    assert all(shot.content_hash == content_hash_for_shot(shot) for shot in shots)


async def test_generating_advances_the_meld_to_the_script_stage(db):
    _user, meld = await _meld_with_story(db)
    job = await _queue_script_job(db, meld)
    meld_id = meld.id
    await run_generate_script(db, job.id)

    db.expire_all()
    reloaded = (await db.execute(select(Meld).where(Meld.id == meld_id))).scalar_one()
    assert reloaded.stage is MeldStage.SCRIPT


async def test_regenerating_replaces_rather_than_appends(db):
    user, meld = await _meld_with_story(db)
    first = await _queue_script_job(db, meld)
    await run_generate_script(db, first.id)

    await melds_service.save_story(db, user.id, meld.id, "One sentence only.")
    second = await _queue_script_job(db, meld, story_text="One sentence only.")
    await run_generate_script(db, second.id)

    db.expire_all()
    scenes = (await db.execute(select(Scene))).scalars().all()
    shots = (await db.execute(select(Shot))).scalars().all()
    assert len(scenes) == 1
    # The scene count alone would not catch the first script's shots
    # surviving alongside the second's -- only the shot count would.
    assert len(shots) == 1


async def test_a_story_that_changed_mid_job_leaves_the_result_stale(db):
    """The job hashes the story it was queued for. If the user edits while
    it runs, the rows it writes really are out of date the moment they
    land, and the UI must say so rather than claim fresh."""
    user, meld = await _meld_with_story(db)
    job = await _queue_script_job(db, meld)
    await melds_service.save_story(db, user.id, meld.id, "Completely different now.")

    assert (await run_generate_script(db, job.id)).state is JobState.SUCCEEDED

    db.expire_all()
    assert all(
        scene.state is ArtifactState.STALE for scene in (await db.execute(select(Scene))).scalars()
    )


async def test_a_provider_refusal_fails_the_job_with_a_readable_reason(db):
    """An empty story makes the stub refuse, which is the terminal path."""
    _user, meld = await _meld_with_story(db, text="")
    job = await _queue_script_job(db, meld, story_text="")
    job_id = job.id

    assert (await run_generate_script(db, job.id)).state is JobState.FAILED

    db.expire_all()
    reloaded = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert reloaded.error
    assert "story" in reloaded.error.lower()


async def test_a_job_that_is_not_queued_is_skipped(db):
    _user, meld = await _meld_with_story(db)
    job = await _queue_script_job(db, meld)
    job_id = job.id

    first = await run_generate_script(db, job_id)
    assert first.state is JobState.SUCCEEDED
    attempts_after_first = (
        await db.execute(select(Job.attempts).where(Job.id == job_id))
    ).scalar_one()

    # A redelivered message: the job already succeeded.
    again = await run_generate_script(db, job_id)

    assert again.state is JobState.SUCCEEDED
    # The guard held: nothing re-claimed the job, so no second generation
    # was attempted. Without it this passes anyway -- the stub is
    # deterministic and apply_script replaces, so the end state is
    # identical; attempts is what actually distinguishes "skipped" from
    # "ran again and produced the same thing."
    assert (
        await db.execute(select(Job.attempts).where(Job.id == job_id))
    ).scalar_one() == attempts_after_first
    db.expire_all()
    assert len((await db.execute(select(Scene))).scalars().all()) == 2


class _FailingProvider:
    """A stand-in adapter whose `complete` always raises the given error.

    Monkeypatched onto `framemeld.worker.get_llm_provider` rather than onto
    the registry: worker.py does `from ...registry import get_llm_provider`,
    so the name is already bound in worker's own namespace by the time a
    test runs, and patching the registry module would not reach it.
    """

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def complete(self, *, system, user, json_schema):
        raise self._error


async def test_a_retryable_failure_returns_the_job_to_the_queue(db, monkeypatch):
    """Spec 5.4 promises three attempts on transport errors. A job left
    `failed` is one the redelivery skips, because mark_running only claims
    from `queued`."""
    _user, meld = await _meld_with_story(db)
    job = await _queue_script_job(db, meld)
    job_id = job.id

    monkeypatch.setattr(
        worker_module,
        "get_llm_provider",
        lambda: _FailingProvider(ProviderTransportError("upstream timed out")),
    )

    outcome = await run_generate_script(db, job_id)
    assert outcome.state is JobState.QUEUED
    assert outcome.retryable is True

    db.expire_all()
    reloaded = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert reloaded.state is JobState.QUEUED
    assert reloaded.attempts == 1


async def test_the_attempt_budget_is_eventually_spent(db, monkeypatch):
    """After MAX_ATTEMPTS the job fails terminally rather than retrying
    forever."""
    _user, meld = await _meld_with_story(db)
    job = await _queue_script_job(db, meld)
    job_id = job.id

    monkeypatch.setattr(
        worker_module,
        "get_llm_provider",
        lambda: _FailingProvider(ProviderTransportError("upstream timed out")),
    )

    outcome = None
    # Each call stands in for one arq redelivery: mark_running only claims
    # from `queued`, which is exactly the state the previous attempt left
    # the row in.
    for _ in range(worker_module.MAX_ATTEMPTS):
        outcome = await run_generate_script(db, job_id)

    assert outcome.state is JobState.FAILED
    assert outcome.retryable is False

    db.expire_all()
    reloaded = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert reloaded.state is JobState.FAILED
    assert reloaded.attempts == worker_module.MAX_ATTEMPTS


async def test_a_terminal_failure_is_not_retried(db, monkeypatch):
    """A refusal retried is money spent to be refused again."""
    _user, meld = await _meld_with_story(db)
    job = await _queue_script_job(db, meld)
    job_id = job.id

    monkeypatch.setattr(
        worker_module,
        "get_llm_provider",
        lambda: _FailingProvider(ProviderRefusedError("the model declined")),
    )

    outcome = await run_generate_script(db, job_id)
    assert outcome.state is JobState.FAILED
    assert outcome.retryable is False

    db.expire_all()
    reloaded = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()
    # Not requeued: a terminal failure must not be handed back to a
    # redelivery, or the budget check above is pointless.
    assert reloaded.state is JobState.FAILED
    assert reloaded.attempts == 1


async def test_an_unforeseen_exception_still_leaves_the_job_actionable(db, monkeypatch):
    """A crash outside the ProviderError hierarchy (the NaN/InvalidOperation
    case this guards against, or anything else unforeseen) must not escape
    run_generate_script: doing so would roll back mark_running's RUNNING and
    strand the row in `queued` with no worker behind it, and ensure_job
    treats `queued` as live forever."""

    def _boom(text):
        raise ArithmeticError("simulated: an InvalidOperation-shaped crash")

    monkeypatch.setattr(worker_module, "parse_script", _boom)

    _user, meld = await _meld_with_story(db)
    job = await _queue_script_job(db, meld)
    job_id = job.id

    outcome = await run_generate_script(db, job_id)
    assert outcome.state is JobState.FAILED
    assert outcome.retryable is False

    db.expire_all()
    reloaded = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert reloaded.state is JobState.FAILED
    assert reloaded.error


def _one_shot_script(**shot: object) -> str:
    """A minimal well-formed script whose single shot carries `shot`."""
    import json

    base = {"image_prompt": "A foundry at night", "duration_sec": "5.00"}
    return json.dumps({"scenes": [{"shots": [{**base, **shot}]}]})


def test_a_dialogue_kind_with_no_words_becomes_none():
    """Probed against Qwen3-8B: it returned all fourteen shots of a script
    as off_screen with an empty speech_text. Both fields are individually
    valid, so nothing rejected it, and the result was a silent film whose
    Shots page showed an empty speech box on every shot."""
    parsed = parse_script(_one_shot_script(dialogue_kind="off_screen", speech_text=""))
    assert parsed.scenes[0].shots[0].dialogue_kind.value == "none"


def test_words_with_no_dialogue_kind_become_narration():
    """The other direction: lines that would otherwise never be recorded."""
    parsed = parse_script(
        _one_shot_script(dialogue_kind="none", speech_text="The foundry had been cold nine years.")
    )
    assert parsed.scenes[0].shots[0].dialogue_kind.value == "narration"


def test_a_kind_that_agrees_with_its_words_is_left_alone():
    parsed = parse_script(
        _one_shot_script(dialogue_kind="off_screen", speech_text="Why did you come back?")
    )
    shot = parsed.scenes[0].shots[0]
    assert shot.dialogue_kind.value == "off_screen"
    assert shot.speech_text == "Why did you come back?"
