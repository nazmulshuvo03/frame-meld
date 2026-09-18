import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.auth.deps import CurrentUser
from framemeld.db import get_session
from framemeld.jobs.queue import Queue, get_queue
from framemeld.jobs.schemas import JobOut
from framemeld.script import service
from framemeld.script.schemas import (
    MoveIn,
    SceneDetail,
    SceneOut,
    ScenePatch,
    ScriptOut,
    ShotOut,
    ShotPatch,
)

router = APIRouter(prefix="/melds/{meld_id}", tags=["script"])

Db = Annotated[DbSession, Depends(get_session)]
Jobs = Annotated[Queue, Depends(get_queue)]

GENERATE_SCRIPT_TASK = "generate_script"


def _not_found() -> HTTPException:
    """A fresh instance per raise — see melds/router.py for why."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.get("/script", response_model=ScriptOut)
async def get_script(meld_id: uuid.UUID, user: CurrentUser, db: Db) -> ScriptOut:
    scenes = await service.get_script(db, user.id, meld_id)
    if scenes is None:
        raise _not_found()

    stale_scenes, stale_shots = service.count_stale(scenes)
    return ScriptOut(
        scenes=[SceneDetail.model_validate(scene) for scene in scenes],
        stale_scenes=stale_scenes,
        stale_shots=stale_shots,
    )


@router.post("/script/generate", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
async def generate_script(meld_id: uuid.UUID, user: CurrentUser, db: Db, jobs: Jobs):
    try:
        dispatch = await service.request_generation(db, user.id, meld_id)
    except service.EmptyStory:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Write some story first — there is nothing to turn into a script yet.",
        ) from None
    if dispatch is None:
        raise _not_found()

    # Committed *before* the message is pushed. The worker resolves the job
    # by id in its own session, so enqueueing first would race it to a row
    # that does not exist yet.
    await db.commit()
    if dispatch.enqueued:
        await jobs.enqueue(GENERATE_SCRIPT_TASK, dispatch.job.id)
    return dispatch.job


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(meld_id: uuid.UUID, job_id: uuid.UUID, user: CurrentUser, db: Db):
    job = await service.get_job(db, user.id, meld_id, job_id)
    if job is None:
        raise _not_found()
    return job


@router.patch("/scenes/{scene_id}", response_model=SceneOut)
async def patch_scene(
    meld_id: uuid.UUID, scene_id: uuid.UUID, payload: ScenePatch, user: CurrentUser, db: Db
):
    scene = await service.update_scene(
        db, user.id, meld_id, scene_id, payload.model_dump(exclude_unset=True)
    )
    if scene is None:
        raise _not_found()
    await db.commit()
    return scene


@router.patch("/shots/{shot_id}", response_model=ShotOut)
async def patch_shot(
    meld_id: uuid.UUID, shot_id: uuid.UUID, payload: ShotPatch, user: CurrentUser, db: Db
):
    shot = await service.update_shot(
        db, user.id, meld_id, shot_id, payload.model_dump(exclude_unset=True)
    )
    if shot is None:
        raise _not_found()
    await db.commit()
    return shot


# --- structure: add, delete, move -----------------------------------------
#
# Spec section 2 says the user is in control at every stage, and until now
# "control" stopped at editing the text of whatever the model produced: a
# shot you did not want could only be removed by regenerating the script,
# which destroys every sheet, still and clip paid for since.
#
# A move is one step rather than a target index or an ordered list of ids.
# The UI offers up and down arrows, a swap is two assignments, and a reorder
# payload is a validation problem -- duplicate ids, missing ids, ids from
# another Meld -- for a feature nobody has asked for.


@router.post("/scenes", status_code=status.HTTP_201_CREATED, response_model=SceneOut)
async def add_scene(meld_id: uuid.UUID, user: CurrentUser, db: Db) -> SceneOut:
    scene = await service.add_scene(db, user.id, meld_id)
    if scene is None:
        raise _not_found()
    await db.commit()
    return SceneOut.model_validate(scene)


@router.delete("/scenes/{scene_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scene(meld_id: uuid.UUID, scene_id: uuid.UUID, user: CurrentUser, db: Db) -> None:
    try:
        deleted = await service.delete_scene(db, user.id, meld_id, scene_id)
    except service.LastScene as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.reason
        ) from None
    if deleted is None:
        raise _not_found()
    await db.commit()


@router.post("/scenes/{scene_id}/move", status_code=status.HTTP_204_NO_CONTENT)
async def move_scene(
    meld_id: uuid.UUID, scene_id: uuid.UUID, body: MoveIn, user: CurrentUser, db: Db
) -> None:
    if await service.move_scene(db, user.id, meld_id, scene_id, body.delta) is None:
        raise _not_found()
    await db.commit()


@router.post(
    "/scenes/{scene_id}/shots", status_code=status.HTTP_201_CREATED, response_model=ShotOut
)
async def add_shot(meld_id: uuid.UUID, scene_id: uuid.UUID, user: CurrentUser, db: Db) -> ShotOut:
    shot = await service.add_shot(db, user.id, meld_id, scene_id)
    if shot is None:
        raise _not_found()
    await db.commit()
    return ShotOut.model_validate(shot)


@router.delete("/shots/{shot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shot(meld_id: uuid.UUID, shot_id: uuid.UUID, user: CurrentUser, db: Db) -> None:
    if await service.delete_shot(db, user.id, meld_id, shot_id) is None:
        raise _not_found()
    await db.commit()


@router.post("/shots/{shot_id}/move", status_code=status.HTTP_204_NO_CONTENT)
async def move_shot(
    meld_id: uuid.UUID, shot_id: uuid.UUID, body: MoveIn, user: CurrentUser, db: Db
) -> None:
    if await service.move_shot(db, user.id, meld_id, shot_id, body.delta) is None:
        raise _not_found()
    await db.commit()
