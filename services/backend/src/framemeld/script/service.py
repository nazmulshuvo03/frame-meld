import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.orm import selectinload

from framemeld.jobs.service import JobDispatch, ensure_job
from framemeld.melds.service import get_meld
from framemeld.models.job import Job, JobKind
from framemeld.models.meld import Meld
from framemeld.models.script import (
    MAX_SHOT_SECONDS,
    ArtifactState,
    CameraAngle,
    DialogueKind,
    Scene,
    Shot,
)
from framemeld.models.shots import AudioClip, AudioOwnerType
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


class LastScene(Exception):
    """The only scene was asked to be deleted.

    A Meld with no scenes has nothing to render and no way back except
    regenerating the script, which destroys every sheet and still paid for
    since. Refused with a 422 that says to regenerate instead, which is the
    action the user actually wants.
    """

    reason = (
        "A film needs at least one scene. Regenerate the script if you want to start over — "
        "that replaces the whole thing rather than leaving you with nothing."
    )


async def _owned_scene(db: DbSession, meld_id: uuid.UUID, scene_id: uuid.UUID) -> Scene | None:
    return (
        await db.execute(select(Scene).where(Scene.id == scene_id, Scene.meld_id == meld_id))
    ).scalar_one_or_none()


async def _owned_shot(db: DbSession, meld_id: uuid.UUID, shot_id: uuid.UUID) -> Shot | None:
    """Joined to Scene so the Meld actually owns this shot -- selecting by
    shot id alone would let a caller edit any shot in the system as long as
    they owned *some* Meld."""
    return (
        await db.execute(
            select(Shot)
            .join(Scene, Shot.scene_id == Scene.id)
            .where(Shot.id == shot_id, Scene.meld_id == meld_id)
        )
    ).scalar_one_or_none()


async def _renumber(rows: list, now: datetime) -> None:
    """Close the gaps in a list's order_index, in place.

    Called after every add, delete and move. Gaps are harmless to *display*
    -- everything sorts by this column -- but `order_index` is inside a
    shot's content_hash, so leaving a gap where a deleted sibling used to be
    would silently change the hash of every shot after it and mark work
    stale that nobody touched. Renumbering makes the numbers a function of
    position and nothing else.
    """
    for index, row in enumerate(rows):
        if row.order_index != index:
            row.order_index = index
            row.updated_at = now


async def _shots_in(db: DbSession, scene_id: uuid.UUID) -> list[Shot]:
    return list(
        (
            await db.execute(
                select(Shot).where(Shot.scene_id == scene_id).order_by(Shot.order_index)
            )
        ).scalars()
    )


async def _scenes_in(db: DbSession, meld_id: uuid.UUID) -> list[Scene]:
    return list(
        (
            await db.execute(
                select(Scene).where(Scene.meld_id == meld_id).order_by(Scene.order_index)
            )
        ).scalars()
    )


async def _drop_orphan_audio(db: DbSession, shot_ids: list[uuid.UUID]) -> None:
    """Delete the audio clips belonging to shots that are about to go.

    `audio_clips.owner_id` is polymorphic (spec 5.3) so it carries no
    foreign key and nothing cascades it. M4 found this the first time, when
    regenerating a script left recordings pointing at shot ids that no
    longer resolved; every other path that deletes a shot has to do the
    same thing by hand, which is what this function is for.
    """
    if not shot_ids:
        return
    for clip in (
        await db.execute(
            select(AudioClip).where(
                AudioClip.owner_type == AudioOwnerType.SHOT, AudioClip.owner_id.in_(shot_ids)
            )
        )
    ).scalars():
        await db.delete(clip)


async def _touch(db: DbSession, meld: Meld, now: datetime) -> None:
    """Flush the structural change, then recompute.

    In that order, and in the caller's transaction: recompute_staleness
    reads the rows back, so an unflushed delete would be reconciled against
    a film that still contains it.
    """
    meld.updated_at = now
    await db.flush()
    await recompute_staleness(db, meld.id)


async def add_scene(db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID) -> Scene | None:
    """Append an empty scene for the user to fill in.

    Its `input_hash` is the script's current expectation rather than "" --
    the scene was not generated, but it belongs to this script, and giving
    it the same hash as its siblings means regenerating the story marks it
    stale along with them. An empty string would leave it permanently and
    misleadingly out of date.
    """
    meld = await get_meld(db, user_id, meld_id)
    if meld is None or meld.story is None:
        return None

    now = datetime.now(UTC)
    scenes = await _scenes_in(db, meld_id)
    scene = Scene(
        meld_id=meld_id,
        order_index=len(scenes),
        heading="NEW SCENE",
        summary="",
        location="",
        time_of_day="",
        input_hash=script_input_hash(meld.story.content_hash),
        state=ArtifactState.FRESH,
        created_at=now,
        updated_at=now,
    )
    scene.content_hash = content_hash_for_scene(scene)
    db.add(scene)
    await _touch(db, meld, now)
    return scene


async def delete_scene(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, scene_id: uuid.UUID
) -> bool | None:
    """Remove a scene and everything under it.

    Postgres cascades the shots, and the shots cascade their stills, clips
    and join rows; the audio has to be taken by hand (see
    _drop_orphan_audio). Nothing is refunded: the user is choosing to throw
    the work away, and spec 4.3's refunds are for work that failed.
    """
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    scene = await _owned_scene(db, meld_id, scene_id)
    if scene is None:
        return None
    if len(await _scenes_in(db, meld_id)) == 1:
        raise LastScene

    await _drop_orphan_audio(db, [shot.id for shot in await _shots_in(db, scene.id)])
    await db.delete(scene)
    await db.flush()

    now = datetime.now(UTC)
    await _renumber(await _scenes_in(db, meld_id), now)
    await _touch(db, meld, now)
    return True


async def move_scene(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, scene_id: uuid.UUID, delta: int
) -> bool | None:
    """Swap a scene with its neighbour.

    A delta of one step rather than a target index or a full ordered list:
    the UI offers up and down arrows, a swap is two assignments, and a
    reorder payload is a validation problem (duplicate ids, missing ids,
    ids from another Meld) for a feature nobody has asked for.

    Moving off either end is a no-op rather than an error -- the button is
    disabled there, so reaching this is a double-click, not a mistake worth
    a 4xx.
    """
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    if await _owned_scene(db, meld_id, scene_id) is None:
        return None

    scenes = await _scenes_in(db, meld_id)
    index = next(i for i, scene in enumerate(scenes) if scene.id == scene_id)
    target = index + delta
    if not 0 <= target < len(scenes):
        return True

    scenes[index], scenes[target] = scenes[target], scenes[index]
    now = datetime.now(UTC)
    await _renumber(scenes, now)
    await _touch(db, meld, now)
    return True


async def add_shot(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, scene_id: uuid.UUID
) -> Shot | None:
    """Append an empty shot to a scene.

    Its duration is the model limit (spec 8), not zero: a zero-second shot
    is refused by the clip provider and renders as nothing, so the default
    has to be a value the rest of the pipeline accepts.
    """
    meld = await get_meld(db, user_id, meld_id)
    if meld is None or meld.story is None:
        return None
    scene = await _owned_scene(db, meld_id, scene_id)
    if scene is None:
        return None

    now = datetime.now(UTC)
    shots = await _shots_in(db, scene.id)
    shot = Shot(
        scene_id=scene.id,
        order_index=len(shots),
        image_prompt="",
        motion_prompt="",
        # Quantised to the column's scale, like every other write of
        # this field: Decimal("5.0") and Decimal("5.00") serialise
        # differently and therefore hash differently.
        duration_sec=MAX_SHOT_SECONDS.quantize(Decimal("0.01")),
        # Spelled out rather than left to the column defaults: those are
        # applied at flush, and content_hash_for_shot reads `.value` off
        # both enums before then.
        camera=CameraAngle.MEDIUM,
        dialogue_kind=DialogueKind.NONE,
        speech_text="",
        input_hash=script_input_hash(meld.story.content_hash),
        state=ArtifactState.FRESH,
        created_at=now,
        updated_at=now,
    )
    shot.content_hash = content_hash_for_shot(shot)
    db.add(shot)
    await _touch(db, meld, now)
    return shot


async def delete_shot(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, shot_id: uuid.UUID
) -> bool | None:
    """Remove one shot and everything generated from it.

    A scene with no shots is allowed, unlike a Meld with no scenes: it is a
    recoverable state -- the Add shot button is right there -- rather than a
    dead end.
    """
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    shot = await _owned_shot(db, meld_id, shot_id)
    if shot is None:
        return None

    scene_id = shot.scene_id
    await _drop_orphan_audio(db, [shot.id])
    await db.delete(shot)
    await db.flush()

    now = datetime.now(UTC)
    await _renumber(await _shots_in(db, scene_id), now)
    await _touch(db, meld, now)
    return True


async def move_shot(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, shot_id: uuid.UUID, delta: int
) -> bool | None:
    """Swap a shot with its neighbour, within its own scene.

    Within the scene, deliberately: moving a shot across a scene boundary
    would change what scene it belongs to, which is a different edit and
    reads as one. A shot that belongs in the next scene is a delete and an
    add.
    """
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    shot = await _owned_shot(db, meld_id, shot_id)
    if shot is None:
        return None

    shots = await _shots_in(db, shot.scene_id)
    index = next(i for i, row in enumerate(shots) if row.id == shot.id)
    target = index + delta
    if not 0 <= target < len(shots):
        return True

    shots[index], shots[target] = shots[target], shots[index]
    now = datetime.now(UTC)
    await _renumber(shots, now)
    # Renumbering moves order_index, which is inside a shot's content_hash.
    # Rehashing here rather than leaving it to the next edit is what makes
    # the render correctly go stale: the film's shot order really did change.
    for row in shots:
        row.content_hash = content_hash_for_shot(row)
    await _touch(db, meld, now)
    return True
