import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.orm import selectinload

from framemeld.jobs.service import JobDispatch, ensure_job
from framemeld.melds.service import get_meld
from framemeld.models.job import Job, JobKind
from framemeld.models.script import ArtifactState, Scene, Shot
from framemeld.script.hashes import (
    content_hash_for_scene,
    content_hash_for_shot,
    script_input_hash,
)
from framemeld.staleness import recompute_staleness


class EmptyStory(Exception):
    """Generation was asked for before there was anything to generate from.

    Refused in the API rather than in the job, so it costs a 422 instead of
    a free-tier request — those are capped at roughly 200 a day (spec §6.2).
    """


async def get_script(db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID) -> list[Scene] | None:
    """Every scene in one Meld, with shots eager-loaded, or None if the
    Meld is not this user's.

    Goes through get_meld first: that is the only place a Meld is resolved
    by id, and it is what makes an unauthorised read a 404.
    """
    if await get_meld(db, user_id, meld_id) is None:
        return None

    result = await db.execute(
        select(Scene)
        .options(selectinload(Scene.shots))
        .where(Scene.meld_id == meld_id)
        .order_by(Scene.order_index)
    )
    return list(result.scalars().all())


def count_stale(scenes: list[Scene]) -> tuple[int, int]:
    stale_scenes = sum(1 for scene in scenes if scene.state is ArtifactState.STALE)
    stale_shots = sum(
        1 for scene in scenes for shot in scene.shots if shot.state is ArtifactState.STALE
    )
    return stale_scenes, stale_shots


async def request_generation(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID
) -> JobDispatch | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None or meld.story is None:
        return None
    if not meld.story.text.strip():
        raise EmptyStory

    return await ensure_job(
        db,
        meld_id=meld_id,
        kind=JobKind.GENERATE_SCRIPT,
        target_table="melds",
        target_id=meld_id,
        input_hash=script_input_hash(meld.story.content_hash),
    )


async def get_job(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, job_id: uuid.UUID
) -> Job | None:
    if await get_meld(db, user_id, meld_id) is None:
        return None
    # Filtered on meld_id as well as id: a job id alone would let any
    # logged-in user poll any other user's job by guessing one.
    return (
        await db.execute(select(Job).where(Job.id == job_id, Job.meld_id == meld_id))
    ).scalar_one_or_none()


async def update_scene(
    db: DbSession,
    user_id: uuid.UUID,
    meld_id: uuid.UUID,
    scene_id: uuid.UUID,
    fields: dict[str, object],
) -> Scene | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    scene = (
        await db.execute(select(Scene).where(Scene.id == scene_id, Scene.meld_id == meld_id))
    ).scalar_one_or_none()
    if scene is None:
        return None

    now = datetime.now(UTC)
    for name, value in fields.items():
        setattr(scene, name, value)
    scene.content_hash = content_hash_for_scene(scene)
    # D4: `state` is deliberately untouched. A row the user just edited
    # holds what the user wants, so it is not out of date. What moves is
    # content_hash, which is what downstream artifacts read.
    scene.updated_at = now
    meld.updated_at = now
    await db.flush()
    return scene


async def update_shot(
    db: DbSession,
    user_id: uuid.UUID,
    meld_id: uuid.UUID,
    shot_id: uuid.UUID,
    fields: dict[str, object],
) -> Shot | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    # Joined to Scene so the Meld actually owns this shot. Selecting by
    # shot id alone would let a caller edit any shot in the system as long
    # as they owned *some* Meld.
    shot = (
        await db.execute(
            select(Shot)
            .join(Scene, Shot.scene_id == Scene.id)
            .where(Shot.id == shot_id, Scene.meld_id == meld_id)
        )
    ).scalar_one_or_none()
    if shot is None:
        return None

    now = datetime.now(UTC)
    for name, value in fields.items():
        if name == "duration_sec" and value is not None:
            # Quantised to the column's scale, and with the same rounding
            # mode Postgres numeric uses, so the stored value and the
            # hashed value agree; see _duration_at_column_scale. The
            # context default (ROUND_HALF_EVEN) would round 4.005 to 4.00
            # where Postgres stores 4.01.
            value = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        setattr(shot, name, value)
    shot.content_hash = content_hash_for_shot(shot)
    # D4: `state` is deliberately untouched. A row the user just edited holds
    # what the user wants, so it is not out of date. What moves is
    # content_hash, which is what downstream artifacts read.
    shot.updated_at = now
    meld.updated_at = now
    await db.flush()

    # M4-D7. Until M4 nothing depended on a shot's content, so this call was
    # not needed and was not made. It is needed now: a still drawn from an
    # old image_prompt, or a clip read from an old line, would otherwise go
    # on claiming to be fresh forever. That exact omission in
    # update_character was the first thing M3's browser pass found.
    await recompute_staleness(db, meld_id)
    return shot
