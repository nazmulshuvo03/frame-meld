import uuid
from decimal import Decimal

from sqlalchemy import select

from framemeld.cast.hashes import (
    character_image_input_hash,
    characters_input_hash,
    content_hash_for_character,
    content_hash_for_character_image,
    visual_hash_for_character,
)
from framemeld.config import settings
from framemeld.jobs.service import ensure_job
from framemeld.melds import service as melds_service
from framemeld.models.cast import (
    Character,
    CharacterImage,
    CharacterImageKind,
    CharacterRole,
)
from framemeld.models.job import JobKind, JobState
from framemeld.models.meld import Story
from framemeld.models.script import ArtifactState, CameraAngle, DialogueKind, Scene, Shot
from framemeld.models.user import User
from framemeld.script.hashes import (
    content_hash_for_scene,
    content_hash_for_shot,
    content_hash_for_story,
    script_input_hash,
)
from framemeld.staleness import recompute_staleness
from framemeld.worker import run_generate_characters


async def _meld_with_script(db, story_text="Elena finds a letter.", shots=2):
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    meld = await melds_service.create_meld(db, user.id, "T")
    await melds_service.save_story(db, user.id, meld.id, story_text)

    input_hash = script_input_hash(content_hash_for_story(story_text))
    scene = Scene(
        meld_id=meld.id,
        order_index=0,
        heading="INT. KITCHEN - NIGHT",
        summary="Elena finds the letter.",
        location="Kitchen",
        time_of_day="night",
        content_hash="",
        input_hash=input_hash,
        state=ArtifactState.FRESH,
    )
    scene.content_hash = content_hash_for_scene(scene)
    db.add(scene)
    await db.flush()

    for index in range(shots):
        shot = Shot(
            scene_id=scene.id,
            order_index=index,
            image_prompt=f"Shot {index}",
            motion_prompt="Slow push in.",
            duration_sec=Decimal("4.00"),
            camera=CameraAngle.MEDIUM,
            dialogue_kind=DialogueKind.NARRATION,
            speech_text="",
            content_hash="",
            input_hash=input_hash,
            state=ArtifactState.FRESH,
        )
        shot.content_hash = content_hash_for_shot(shot)
        db.add(shot)
    await db.flush()
    return user, meld, scene


async def test_an_unchanged_story_leaves_everything_fresh(db):
    _, meld, _ = await _meld_with_script(db)
    report = await recompute_staleness(db, meld.id)
    assert (report.stale_scenes, report.stale_shots, report.changed) == (0, 0, 0)


async def test_editing_the_story_marks_the_script_stale(db):
    """The milestone's headline behaviour, and the spec's done-when."""
    user, meld, _ = await _meld_with_script(db)
    await melds_service.save_story(db, user.id, meld.id, "Elena burns the letter instead.")

    report = await recompute_staleness(db, meld.id)
    assert report.stale_scenes == 1
    assert report.stale_shots == 2

    db.expire_all()
    assert all(
        scene.state is ArtifactState.STALE for scene in (await db.execute(select(Scene))).scalars()
    )


async def test_nothing_is_destroyed_by_going_stale(db):
    """Spec section 4.4: a stale row keeps its content."""
    user, meld, _ = await _meld_with_script(db)
    await melds_service.save_story(db, user.id, meld.id, "Something else.")
    await recompute_staleness(db, meld.id)

    db.expire_all()
    shots = (await db.execute(select(Shot).order_by(Shot.order_index))).scalars().all()
    assert [shot.image_prompt for shot in shots] == ["Shot 0", "Shot 1"]


async def test_reverting_the_story_restores_freshness(db):
    """D5. Spec section 4.4 promises reverting an edit costs nothing, and
    that only holds if stale -> fresh is reachable."""
    user, meld, _ = await _meld_with_script(db, story_text="Original.")
    await melds_service.save_story(db, user.id, meld.id, "Changed.")
    assert (await recompute_staleness(db, meld.id)).stale_shots == 2

    await melds_service.save_story(db, user.id, meld.id, "Original.")
    report = await recompute_staleness(db, meld.id)
    assert report.stale_shots == 0
    assert report.stale_scenes == 0


async def test_rows_mid_generation_are_left_alone(db):
    """D5: a worker owns a `generating` row. Flipping it to stale underneath
    would make the job's own write look like a regression."""
    user, meld, scene = await _meld_with_script(db)
    scene_id = scene.id
    scene.state = ArtifactState.GENERATING
    await db.flush()

    await melds_service.save_story(db, user.id, meld.id, "Changed.")
    await recompute_staleness(db, meld.id)

    # scene_id was captured above rather than read off `scene` afterwards:
    # under the async ORM, reading an expired attribute triggers an implicit
    # (unawaited) reload and raises MissingGreenlet -- see the same pattern
    # in test_melds_service.py.
    db.expire_all()
    reloaded = (await db.execute(select(Scene).where(Scene.id == scene_id))).scalar_one()
    assert reloaded.state is ArtifactState.GENERATING


async def test_a_meld_with_no_script_reports_nothing(db):
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    meld = await melds_service.create_meld(db, user.id, "T")

    report = await recompute_staleness(db, meld.id)
    assert (report.stale_scenes, report.stale_shots, report.changed) == (0, 0, 0)


async def test_scenes_of_other_melds_are_untouched(db):
    user_a, meld_a, _ = await _meld_with_script(db)
    _, meld_b, scene_b = await _meld_with_script(db)
    meld_b_id = meld_b.id
    scene_b_id = scene_b.id

    await melds_service.save_story(db, user_a.id, meld_a.id, "Changed.")
    await recompute_staleness(db, meld_a.id)

    # meld_b_id / scene_b_id captured above for the same reason as in
    # test_rows_mid_generation_are_left_alone.
    db.expire_all()
    reloaded = (await db.execute(select(Scene).where(Scene.id == scene_b_id))).scalar_one()
    assert reloaded.state is ArtifactState.FRESH

    # Shots are reached indirectly, through a join on Scene.meld_id -- the
    # riskier half of the scoping, and untested until now: every other
    # staleness test has only one Meld in scope, so a missing meld_id filter
    # on the shots query would not fail anything above this line.
    shots_b = (
        (
            await db.execute(
                select(Shot)
                .join(Scene, Shot.scene_id == Scene.id)
                .where(Scene.meld_id == meld_b_id)
            )
        )
        .scalars()
        .all()
    )
    assert shots_b and all(shot.state is ArtifactState.FRESH for shot in shots_b)


async def test_a_soft_deleted_meld_is_ignored(db):
    """The story is changed before the engine runs, so there is real work it
    would do if the deleted_at guard were missing.

    The change is made on the row rather than through save_story, because
    save_story now runs the engine itself — going through it would leave the
    rows already stale and this assertion would prove nothing.
    """
    user, meld, _ = await _meld_with_script(db, story_text="Original.")
    await melds_service.soft_delete_meld(db, user.id, meld.id)

    story = (await db.execute(select(Story).where(Story.meld_id == meld.id))).scalar_one()
    story.content_hash = content_hash_for_story("Changed.")
    await db.flush()

    report = await recompute_staleness(db, meld.id)

    assert (report.stale_scenes, report.stale_shots, report.changed) == (0, 0, 0)
    db.expire_all()
    assert all(
        scene.state is ArtifactState.FRESH for scene in (await db.execute(select(Scene))).scalars()
    )


async def test_saving_the_story_recomputes_staleness_without_being_asked(db):
    """The API must not depend on a caller remembering to run the engine."""
    from sqlalchemy import select

    from framemeld.melds import service as melds_service
    from framemeld.models.script import ArtifactState, Shot

    user, meld, _ = await _meld_with_script(db)
    await melds_service.save_story(db, user.id, meld.id, "Something quite different.")

    db.expire_all()
    states = {shot.state for shot in (await db.execute(select(Shot))).scalars()}
    assert states == {ArtifactState.STALE}


# --- Cast and sheets (M3) -------------------------------------------------


async def _meld_with_cast(db, story_text="Elena and Marcus find a letter.", count=2):
    """A Meld with a story, a fresh cast, and one fresh sheet per
    character -- the state a user reaches at the end of the Cast stage."""
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    meld = await melds_service.create_meld(db, user.id, "T")
    await melds_service.save_story(db, user.id, meld.id, story_text)

    cast_hash = characters_input_hash(content_hash_for_story(story_text))
    characters = []
    for index in range(count):
        character = Character(
            meld_id=meld.id,
            order_index=index,
            name=f"Character {index}",
            role=CharacterRole.LEAD if index == 0 else CharacterRole.SUPPORTING,
            description=f"Person {index}.",
            appearance=f"Distinct appearance {index}.",
            content_hash="",
            input_hash=cast_hash,
            state=ArtifactState.FRESH,
        )
        character.content_hash = content_hash_for_character(character)
        db.add(character)
        await db.flush()

        image = CharacterImage(
            character_id=character.id,
            kind=CharacterImageKind.SHEET,
            is_canonical=True,
            content_hash=content_hash_for_character_image(f"{index}" * 64),
            input_hash=character_image_input_hash(
                visual_hash_for_character(character), CharacterImageKind.SHEET
            ),
            state=ArtifactState.FRESH,
        )
        db.add(image)
        characters.append(character)

    await db.flush()
    return user, meld, characters


async def _sheets(db):
    return list(
        (
            await db.execute(select(CharacterImage).join(Character).order_by(Character.order_index))
        ).scalars()
    )


async def test_editing_a_character_marks_only_their_own_sheet_stale(db):
    """The headline promise of the two-hash rule, at the cast level. If
    this is wrong the user is either charged to regenerate artwork that was
    fine, or shown artwork of a character who no longer looks like that."""
    _user, meld, characters = await _meld_with_cast(db)
    first, second = characters

    first.appearance = "Now with a beard."
    first.content_hash = content_hash_for_character(first)
    await db.flush()

    report = await recompute_staleness(db, meld.id)

    first_sheet, second_sheet = await _sheets(db)
    assert first_sheet.state is ArtifactState.STALE
    assert second_sheet.state is ArtifactState.FRESH
    assert report.stale_sheets == 1
    # The characters themselves are untouched: an edit is not staleness (D4),
    # and the story that produced them has not changed.
    assert first.state is ArtifactState.FRESH
    assert second.state is ArtifactState.FRESH


async def test_editing_the_story_marks_the_cast_stale_but_not_the_sheets(db):
    """A sheet's input is its character, not the story. The cast goes out
    of date; the artwork of a character nobody has edited does not."""
    user, meld, characters = await _meld_with_cast(db)

    await melds_service.save_story(db, user.id, meld.id, "A completely different story.")

    report = await recompute_staleness(db, meld.id)

    assert all(character.state is ArtifactState.STALE for character in characters)
    assert report.stale_characters == 2
    assert all(sheet.state is ArtifactState.FRESH for sheet in await _sheets(db))
    assert report.stale_sheets == 0


async def test_reverting_a_character_edit_clears_the_staleness(db):
    """Spec 4.4: reverting restores the cache hit and costs nothing."""
    _user, meld, characters = await _meld_with_cast(db)
    first = characters[0]
    original = first.appearance

    first.appearance = "Now with a beard."
    first.content_hash = content_hash_for_character(first)
    await db.flush()
    await recompute_staleness(db, meld.id)
    assert (await _sheets(db))[0].state is ArtifactState.STALE

    first.appearance = original
    first.content_hash = content_hash_for_character(first)
    await db.flush()
    await recompute_staleness(db, meld.id)

    assert (await _sheets(db))[0].state is ArtifactState.FRESH


async def test_a_generating_sheet_is_left_alone(db):
    """D5: a `generating` row is owned by a worker that is about to write
    it. Flipping it to stale mid-flight would make the UI lie and could
    make the worker's own write look like a regression."""
    _user, meld, characters = await _meld_with_cast(db)
    first = characters[0]
    first_sheet = (await _sheets(db))[0]
    first_sheet.state = ArtifactState.GENERATING

    first.appearance = "Now with a beard."
    first.content_hash = content_hash_for_character(first)
    await db.flush()

    await recompute_staleness(db, meld.id)

    assert (await _sheets(db))[0].state is ArtifactState.GENERATING


async def test_a_failed_sheet_is_left_alone(db):
    """Also D5: a failed row's problem is not its inputs, and calling it
    stale would hide that it needs a retry rather than a re-edit."""
    _user, meld, _characters = await _meld_with_cast(db)
    first_sheet = (await _sheets(db))[0]
    first_sheet.state = ArtifactState.FAILED
    await db.flush()

    await recompute_staleness(db, meld.id)

    assert (await _sheets(db))[0].state is ArtifactState.FAILED


async def test_changing_the_image_model_marks_every_sheet_stale(db, monkeypatch):
    """True rather than surprising -- regenerating really would produce
    something different -- but it is a Meld-wide effect from an operator
    config change, so it is pinned rather than discovered."""
    _user, meld, _characters = await _meld_with_cast(db)

    monkeypatch.setattr(settings, "image_model", "some-other-image-model")
    report = await recompute_staleness(db, meld.id)

    assert report.stale_sheets == 2
    assert report.stale_characters == 0


async def test_a_story_edited_mid_job_leaves_the_new_cast_stale(db, monkeypatch):
    """The rows a job writes carry the hash of the story it was queued for.
    If that is no longer current they are genuinely out of date and must
    say so rather than claim fresh."""
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    meld = await melds_service.create_meld(db, user.id, "T")
    await melds_service.save_story(db, user.id, meld.id, "Elena finds a letter.")

    dispatch = await ensure_job(
        db,
        meld_id=meld.id,
        kind=JobKind.GENERATE_CHARACTERS,
        target_table="melds",
        target_id=meld.id,
        input_hash=characters_input_hash(content_hash_for_story("Elena finds a letter.")),
    )
    # The user keeps typing while the job is queued.
    await melds_service.save_story(db, user.id, meld.id, "Elena finds a letter. Then rain.")

    outcome = await run_generate_characters(db, dispatch.job.id)

    assert outcome.state is JobState.SUCCEEDED
    characters = list(
        (await db.execute(select(Character).where(Character.meld_id == meld.id))).scalars()
    )
    assert characters
    assert all(character.state is ArtifactState.STALE for character in characters)


async def test_editing_a_backstory_does_not_invalidate_the_artwork(db):
    """The reason `appearance` is a separate column from `description`
    (models/cast.py): rewriting who someone is must not cost the user a
    picture of what they look like. A sheet is drawn from `appearance`
    alone, so nothing else may appear in its input_hash.

    Found by the M3 browser pass, not by review: the cast stage's whole
    claim is that it does not charge you twice for the same picture.
    """
    _user, meld, characters = await _meld_with_cast(db)
    first = characters[0]

    first.description = "Actually, she was the one who wrote the letter."
    first.name = "Elena Vasquez"
    first.role = CharacterRole.SUPPORTING
    first.content_hash = content_hash_for_character(first)
    await db.flush()

    report = await recompute_staleness(db, meld.id)

    assert report.stale_sheets == 0
    assert all(sheet.state is ArtifactState.FRESH for sheet in await _sheets(db))


async def test_editing_an_appearance_through_the_api_marks_the_sheet_stale(
    logged_in, db, monkeypatch
):
    """The other half, end to end: an edit that really does change what
    gets drawn has to reach recompute_staleness. Editing a character used
    to skip it entirely, so the sheet quietly claimed to be fresh forever.
    """
    from framemeld.cast import service as cast_service

    _user, meld, characters = await _meld_with_cast(db)
    first = characters[0]

    await cast_service.update_character(
        db,
        meld.user_id,
        meld.id,
        first.id,
        {"appearance": "Now with close-cropped hair and a scar over one eye."},
    )

    first_sheet, second_sheet = await _sheets(db)
    assert first_sheet.state is ArtifactState.STALE
    assert second_sheet.state is ArtifactState.FRESH
