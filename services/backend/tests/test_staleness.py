import uuid
from decimal import Decimal

from sqlalchemy import select

from framemeld.melds import service as melds_service
from framemeld.models.script import ArtifactState, CameraAngle, DialogueKind, Scene, Shot
from framemeld.models.user import User
from framemeld.script.hashes import (
    content_hash_for_scene,
    content_hash_for_shot,
    content_hash_for_story,
    script_input_hash,
)
from framemeld.staleness import recompute_staleness


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
    """The story is changed *before* the delete, so there is real work the
    engine would do if the guard were missing. Without the edit this test
    passes with the guard removed, which is how it shipped."""
    user, meld, _ = await _meld_with_script(db, story_text="Original.")
    await melds_service.save_story(db, user.id, meld.id, "Changed.")
    await melds_service.soft_delete_meld(db, user.id, meld.id)

    report = await recompute_staleness(db, meld.id)

    assert (report.stale_scenes, report.stale_shots, report.changed) == (0, 0, 0)
    db.expire_all()
    assert all(
        scene.state is ArtifactState.FRESH for scene in (await db.execute(select(Scene))).scalars()
    )
