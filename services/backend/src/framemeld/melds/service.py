import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.orm import selectinload

from framemeld.models.meld import Meld, Story
from framemeld.script.hashes import content_hash_for_story
from framemeld.staleness import recompute_staleness


class StoryConflict(Exception):
    """The caller's `if_content_hash` no longer matches what is stored.

    An exception rather than a sentinel return, because the caller has three
    outcomes to tell apart — saved, no such Meld, someone else got there
    first — and a `Story | None` return can only carry two.
    """


async def create_meld(db: DbSession, user_id: uuid.UUID, title: str) -> Meld:
    # created_at and updated_at are set explicitly rather than left to the
    # columns' server_default=func.now(). Postgres's now() is frozen for the
    # whole transaction, so two Melds created in the same transaction (as
    # happens in tests) would otherwise share a created_at and leave
    # "newest first" ordering undefined between them.
    #
    # Both come from one value so a new Meld cannot show updated_at before
    # created_at if the app and database clocks differ. The server defaults
    # stay in place as the fallback for any other insert path.
    now = datetime.now(UTC)
    meld = Meld(user_id=user_id, title=title, created_at=now, updated_at=now)
    db.add(meld)
    await db.flush()

    # Every Meld has a Story from birth, so the editor never has to handle
    # a missing one.
    db.add(Story(meld_id=meld.id, text="", content_hash=content_hash_for_story("")))
    await db.flush()
    return meld


async def list_melds(db: DbSession, user_id: uuid.UUID) -> list[Meld]:
    result = await db.execute(
        select(Meld)
        .where(Meld.user_id == user_id, Meld.deleted_at.is_(None))
        # id as a tie-break makes this a total order independent of clock
        # resolution, rather than relying purely on wall-clock created_at.
        .order_by(Meld.created_at.desc(), Meld.id.desc())
    )
    return list(result.scalars().all())


async def get_meld(db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID) -> Meld | None:
    result = await db.execute(
        select(Meld)
        .options(selectinload(Meld.story))
        .where(Meld.id == meld_id, Meld.user_id == user_id, Meld.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def rename_meld(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, title: str
) -> Meld | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    meld.title = title
    # Assigned explicitly so onupdate=func.now() does not apply. Every
    # timestamp in this service comes from the app clock, because
    # created_at does; func.now() is transaction-scoped and would be
    # earlier than a created_at taken later in the same transaction.
    meld.updated_at = datetime.now(UTC)
    await db.flush()
    return meld


async def soft_delete_meld(db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID) -> bool:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return False
    meld.deleted_at = meld.updated_at = datetime.now(UTC)
    await db.flush()
    return True


async def save_story(
    db: DbSession,
    user_id: uuid.UUID,
    meld_id: uuid.UUID,
    text: str,
    if_content_hash: str | None = None,
) -> Story | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None

    # get_meld already eager-loaded this via selectinload, so no second
    # query is needed. A Meld with no Story returns None rather than
    # raising, so it becomes a 404 instead of a 500.
    story = meld.story
    if story is None:
        return None

    # Checked after the ownership funnel, never before: a precondition
    # failure on someone else's Meld would confirm that Meld exists.
    # Optional because the first save after a page load has nothing to
    # compare against yet.
    if if_content_hash is not None and if_content_hash != story.content_hash:
        raise StoryConflict

    now = datetime.now(UTC)
    story.text = text
    story.content_hash = content_hash_for_story(text)
    story.updated_at = now
    # Editing a Meld's story updates the Meld. The dashboard renders
    # "updated {meld.updated_at}" on every card, so without this a user
    # who writes for an hour still sees their Meld labelled with its
    # creation date.
    meld.updated_at = now
    await db.flush()

    # The story is the root of the dependency graph, so its edit is the one
    # event that can invalidate anything downstream. Run here rather than in
    # the router so no future caller can forget.
    await recompute_staleness(db, meld_id)
    return story
