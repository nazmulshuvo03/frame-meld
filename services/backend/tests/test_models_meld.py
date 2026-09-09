import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from framemeld.models.meld import Meld, MeldStage, MeldStatus, Story
from framemeld.models.user import User


async def _make_user(db) -> User:
    user = User(email="m@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    return user


async def test_meld_defaults_to_story_stage_and_draft_status(db):
    user = await _make_user(db)
    meld = Meld(user_id=user.id, title="Untitled Meld")
    db.add(meld)
    await db.flush()

    assert meld.stage == MeldStage.STORY
    assert meld.status == MeldStatus.DRAFT
    assert meld.deleted_at is None
    assert meld.created_at is not None


async def test_story_is_one_to_one_with_meld(db):
    user = await _make_user(db)
    meld = Meld(user_id=user.id, title="A Meld")
    db.add(meld)
    await db.flush()

    db.add(Story(meld_id=meld.id, text="Once upon a time", input_hash="abc"))
    await db.flush()

    db.add(Story(meld_id=meld.id, text="A second story", input_hash="def"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_stage_rejects_a_value_outside_the_enum(db):
    user = await _make_user(db)
    db.add(Meld(user_id=user.id, title="Bad", stage="nonsense"))
    with pytest.raises((IntegrityError, LookupError, ValueError)):
        await db.flush()


async def test_stage_check_constraint_exists_in_the_database(db):
    """The Python-side enum rejects bad values before SQL is emitted, so the
    test above passes even with create_constraint=False. This one proves the
    database itself refuses — which is what protects rows written by anything
    that is not this ORM."""
    user = await _make_user(db)
    with pytest.raises(IntegrityError):
        await db.execute(
            text(
                "INSERT INTO melds (id, user_id, title, stage, status,"
                " moderation_state, aspect_ratio, created_at, updated_at)"
                " VALUES (gen_random_uuid(), :uid, 'Bad', 'nonsense', 'draft',"
                " 'pending', '16:9', now(), now())"
            ),
            {"uid": user.id},
        )


async def test_deleting_a_meld_cascades_to_its_story(db):
    """Hard deletes only ever happen in tests and admin cleanup, but the
    FK must not leave orphaned stories behind when they do."""
    user = await _make_user(db)
    meld = Meld(user_id=user.id, title="Doomed")
    db.add(meld)
    await db.flush()
    db.add(Story(meld_id=meld.id, text="text", input_hash="abc"))
    await db.flush()
    meld_id = meld.id

    # Load the relationship before deleting: under asyncio, an ORM cascade
    # over an unloaded relationship raises MissingGreenlet.
    loaded = (
        await db.execute(select(Meld).options(selectinload(Meld.story)).where(Meld.id == meld_id))
    ).scalar_one()

    await db.delete(loaded)
    await db.flush()

    orphan = (await db.execute(select(Story).where(Story.meld_id == meld_id))).scalar_one_or_none()
    assert orphan is None
