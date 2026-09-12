import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from framemeld.models.job import Job, JobKind, JobState
from framemeld.models.meld import Meld
from framemeld.models.script import ArtifactState, CameraAngle, DialogueKind, Scene, Shot
from framemeld.models.user import User


async def _meld(db) -> Meld:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    meld = Meld(user_id=user.id, title="T")
    db.add(meld)
    await db.flush()
    return meld


async def _scene(db, meld, order_index=0) -> Scene:
    scene = Scene(
        meld_id=meld.id,
        order_index=order_index,
        heading="INT. KITCHEN - NIGHT",
        summary="Elena finds the letter.",
        location="Kitchen",
        time_of_day="night",
        content_hash="a" * 64,
        input_hash="b" * 64,
        state=ArtifactState.FRESH,
    )
    db.add(scene)
    await db.flush()
    return scene


def _shot(scene, *, duration="4.50", order_index=0, camera=CameraAngle.MEDIUM) -> Shot:
    return Shot(
        scene_id=scene.id,
        order_index=order_index,
        image_prompt="A woman reads a letter by lamplight.",
        motion_prompt="Slow push in; her hand trembles.",
        duration_sec=Decimal(duration),
        camera=camera,
        dialogue_kind=DialogueKind.NARRATION,
        speech_text="She had not expected an answer.",
        content_hash="c" * 64,
        input_hash="b" * 64,
        state=ArtifactState.FRESH,
    )


async def test_a_shot_round_trips(db):
    meld = await _meld(db)
    scene = await _scene(db, meld)
    db.add(_shot(scene))
    await db.flush()

    db.expire_all()
    stored = (await db.execute(select(Shot))).scalar_one()
    assert stored.duration_sec == Decimal("4.50")
    assert stored.camera is CameraAngle.MEDIUM
    assert stored.dialogue_kind is DialogueKind.NARRATION
    assert stored.state is ArtifactState.FRESH


async def test_five_seconds_is_allowed(db):
    """Spec section 8: the limit is <= 5.0, not < 5.0."""
    meld = await _meld(db)
    scene = await _scene(db, meld)
    db.add(_shot(scene, duration="5.00"))
    await db.flush()


async def test_a_longer_shot_is_rejected_by_the_database(db):
    """Every current image-to-video model has a hard clip-length limit, so
    this is a data invariant, not a validation nicety."""
    meld = await _meld(db)
    scene = await _scene(db, meld)
    db.add(_shot(scene, duration="5.01"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_a_zero_length_shot_is_rejected(db):
    meld = await _meld(db)
    scene = await _scene(db, meld)
    db.add(_shot(scene, duration="0.00"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_enum_values_are_checked_in_the_database(db):
    """Proves enum_column's create_constraint=True actually reached this
    table. Without it the column is an unconstrained VARCHAR and this
    UPDATE succeeds. Written as raw SQL because the ORM would reject
    'drone' before it ever left Python."""
    meld = await _meld(db)
    scene = await _scene(db, meld)
    shot = _shot(scene)
    db.add(shot)
    await db.flush()

    with pytest.raises(DBAPIError):
        await db.execute(text("update shots set camera = 'drone' where id = :id"), {"id": shot.id})


async def test_enums_store_their_values_not_their_member_names(db):
    """Proves values_callable reached this table: without it Postgres holds
    'MEDIUM' and the API would serve a value the spec never lists."""
    meld = await _meld(db)
    scene = await _scene(db, meld)
    db.add(_shot(scene))
    await db.flush()

    raw = await db.execute(text("select camera, dialogue_kind, state from shots"))
    assert raw.one() == ("medium", "narration", "fresh")


async def test_deleting_a_meld_cascades_to_scenes_and_shots(db):
    """Melds are soft-deleted in the app, so this never fires in practice --
    but the FKs must still be right, or a future hard delete or a data
    migration leaves orphans."""
    meld = await _meld(db)
    scene = await _scene(db, meld)
    db.add(_shot(scene))
    await db.flush()

    await db.execute(delete(Meld).where(Meld.id == meld.id))
    await db.flush()
    db.expire_all()

    assert (await db.execute(select(Scene))).scalars().all() == []
    assert (await db.execute(select(Shot))).scalars().all() == []


async def test_two_jobs_cannot_share_an_idempotency_key(db):
    """Spec section 5.4: this unique index is the mechanism that makes
    double-clicking Generate free rather than expensive."""
    meld = await _meld(db)
    for _ in range(2):
        db.add(
            Job(
                meld_id=meld.id,
                kind=JobKind.GENERATE_SCRIPT,
                target_table="melds",
                target_id=meld.id,
                input_hash="d" * 64,
                idempotency_key="e" * 64,
                state=JobState.QUEUED,
            )
        )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_the_longest_job_kind_fits_its_column(db):
    """generate_character_image is 24 characters; enum_column defaults to
    VARCHAR(20), so this column needs an explicit length. Without it the
    insert fails at runtime, in M3, far from this file."""
    meld = await _meld(db)
    db.add(
        Job(
            meld_id=meld.id,
            kind=JobKind.GENERATE_CHARACTER_IMAGE,
            target_table="character_images",
            target_id=None,
            input_hash="f" * 64,
            idempotency_key="0" * 64,
            state=JobState.QUEUED,
        )
    )
    await db.flush()
