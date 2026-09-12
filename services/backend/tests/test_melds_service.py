import uuid

from sqlalchemy import select

from framemeld.melds.service import (
    create_meld,
    get_meld,
    list_melds,
    rename_meld,
    save_story,
    soft_delete_meld,
    story_hash,
)
from framemeld.models.meld import Meld, MeldStage
from framemeld.models.user import User


async def _make_user(db, email) -> User:
    user = User(email=email, password_hash="x")
    db.add(user)
    await db.flush()
    return user


async def test_create_meld_also_creates_an_empty_story(db):
    user = await _make_user(db, "a@example.com")
    created = await create_meld(db, user.id, "My First Meld")

    meld = await get_meld(db, user.id, created.id)
    assert meld is not None
    assert meld.title == "My First Meld"
    assert meld.stage == MeldStage.STORY
    assert meld.story is not None
    assert meld.story.text == ""
    assert meld.story.content_hash == story_hash("")


async def test_list_melds_returns_newest_first(db):
    user = await _make_user(db, "b@example.com")
    first = await create_meld(db, user.id, "First")
    second = await create_meld(db, user.id, "Second")

    melds = await list_melds(db, user.id)
    assert [m.id for m in melds] == [second.id, first.id]


async def test_list_melds_excludes_other_users_melds(db):
    alice = await _make_user(db, "alice@example.com")
    bob = await _make_user(db, "bob@example.com")
    await create_meld(db, alice.id, "Alice's Meld")

    assert await list_melds(db, bob.id) == []


async def test_list_melds_excludes_soft_deleted(db):
    user = await _make_user(db, "c@example.com")
    keep = await create_meld(db, user.id, "Keep")
    drop = await create_meld(db, user.id, "Drop")

    assert await soft_delete_meld(db, user.id, drop.id) is True

    melds = await list_melds(db, user.id)
    assert [m.id for m in melds] == [keep.id]


async def test_get_meld_returns_none_for_another_users_meld(db):
    alice = await _make_user(db, "alice2@example.com")
    bob = await _make_user(db, "bob2@example.com")
    meld = await create_meld(db, alice.id, "Private")

    assert await get_meld(db, bob.id, meld.id) is None


async def test_get_meld_returns_none_for_an_unknown_id(db):
    user = await _make_user(db, "d@example.com")
    assert await get_meld(db, user.id, uuid.uuid4()) is None


async def test_soft_delete_does_not_remove_the_row(db):
    user = await _make_user(db, "e@example.com")
    meld = await create_meld(db, user.id, "Doomed")
    meld_id = meld.id

    await soft_delete_meld(db, user.id, meld_id)

    assert await get_meld(db, user.id, meld_id) is None

    # Expire the instance so the assertion below re-reads from Postgres
    # rather than from the identity map — the point of a soft delete is
    # that the row is still there.
    db.expire(meld)
    surviving = (await db.execute(select(Meld).where(Meld.id == meld_id))).scalar_one_or_none()
    assert surviving is not None
    assert surviving.deleted_at is not None


async def test_soft_delete_returns_false_for_another_users_meld(db):
    alice = await _make_user(db, "alice3@example.com")
    bob = await _make_user(db, "bob3@example.com")
    meld = await create_meld(db, alice.id, "Alice's")

    assert await soft_delete_meld(db, bob.id, meld.id) is False


async def test_rename_meld_changes_the_title(db):
    user = await _make_user(db, "f@example.com")
    meld = await create_meld(db, user.id, "Old Name")
    user_id, meld_id = user.id, meld.id

    renamed = await rename_meld(db, user_id, meld_id, "New Name")
    assert renamed is not None
    assert renamed.title == "New Name"

    # Expire so this reload comes from Postgres rather than returning the
    # same in-memory instance rename_meld already mutated — see
    # test_soft_delete_does_not_remove_the_row for why that distinction
    # matters with expire_on_commit=False and a shared test session. The ids
    # were captured above rather than read off `user`/`meld` afterwards:
    # under the async ORM, reading an expired attribute triggers an implicit
    # (unawaited) reload and raises MissingGreenlet.
    db.expire_all()
    reloaded = await get_meld(db, user_id, meld_id)
    assert reloaded is not None
    assert reloaded.title == "New Name"


async def test_save_story_stores_the_text_and_its_hash(db):
    user = await _make_user(db, "g@example.com")
    meld = await create_meld(db, user.id, "Storied")
    user_id, meld_id = user.id, meld.id

    story = await save_story(db, user_id, meld_id, "Once upon a time")
    assert story is not None

    db.expire_all()
    reloaded = await get_meld(db, user_id, meld_id)
    assert reloaded.story.text == "Once upon a time"
    assert reloaded.story.content_hash == story_hash("Once upon a time")


async def test_save_story_is_idempotent_for_unchanged_text(db):
    """Autosave fires often. Re-saving identical text must produce an
    identical hash, so that M2's staleness check sees no change."""
    user = await _make_user(db, "h@example.com")
    meld = await create_meld(db, user.id, "Same")
    user_id, meld_id = user.id, meld.id

    first = await save_story(db, user_id, meld_id, "unchanged")
    first_hash = first.content_hash
    await save_story(db, user_id, meld_id, "unchanged")

    db.expire_all()
    reloaded = await get_meld(db, user_id, meld_id)
    assert reloaded.story.content_hash == first_hash


async def test_save_story_changes_the_hash_when_the_text_changes(db):
    user = await _make_user(db, "i@example.com")
    meld = await create_meld(db, user.id, "Changing")
    user_id, meld_id = user.id, meld.id

    first = await save_story(db, user_id, meld_id, "version one")
    first_hash = first.content_hash
    await save_story(db, user_id, meld_id, "version two")

    db.expire_all()
    reloaded = await get_meld(db, user_id, meld_id)
    assert reloaded.story.content_hash != first_hash


async def test_rename_meld_returns_none_for_another_users_meld(db):
    alice = await _make_user(db, "alice6@example.com")
    bob = await _make_user(db, "bob6@example.com")
    meld = await create_meld(db, alice.id, "Alice's")

    assert await rename_meld(db, bob.id, meld.id, "Intruder") is None


async def test_save_story_returns_none_for_another_users_meld(db):
    alice = await _make_user(db, "alice4@example.com")
    bob = await _make_user(db, "bob4@example.com")
    meld = await create_meld(db, alice.id, "Alice's")

    assert await save_story(db, bob.id, meld.id, "intruder") is None
