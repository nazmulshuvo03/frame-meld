import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.orm import selectinload

from framemeld.hashing import canonical_hash
from framemeld.models.meld import Meld, Story


def story_hash(text: str) -> str:
    """The story is the root of the dependency graph, so its hash is the
    seed for every downstream input_hash in M2 onwards."""
    return canonical_hash({"kind": "story", "text": text})


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
    db.add(Story(meld_id=meld.id, text="", input_hash=story_hash("")))
    await db.flush()
    return meld


async def list_melds(db: DbSession, user_id: uuid.UUID) -> list[Meld]:
    result = await db.execute(
        select(Meld)
        .where(Meld.user_id == user_id, Meld.deleted_at.is_(None))
        .order_by(Meld.created_at.desc())
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
    await db.flush()
    return meld


async def soft_delete_meld(db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID) -> bool:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return False
    meld.deleted_at = datetime.now(UTC)
    await db.flush()
    return True


async def save_story(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, text: str
) -> Story | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None

    result = await db.execute(select(Story).where(Story.meld_id == meld_id))
    story = result.scalar_one()
    story.text = text
    story.input_hash = story_hash(text)
    await db.flush()
    return story
