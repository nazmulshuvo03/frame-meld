import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.credits.pricing import quote_for
from framemeld.credits.service import hold
from framemeld.jobs.service import JobDispatch, ensure_job
from framemeld.melds.service import get_meld
from framemeld.models.job import JobKind
from framemeld.models.meld import Meld
from framemeld.models.render import Render, RenderKind
from framemeld.models.script import ArtifactState, Scene, Shot
from framemeld.models.shots import AudioClip, AudioKind, AudioOwnerType, ShotImage
from framemeld.moderation.service import Blocked, check_and_record
from framemeld.render.hashes import (
    MUSIC_PARAMS,
    music_clip_input_hash,
    music_hash_for_meld,
    music_prompt,
    render_input_hash,
)
from framemeld.render.spec import PREVIEW_SIZE, SceneSpec, SpecAudio, SpecShot, ken_burns
from framemeld.shots.generation import shots_in_film_order


class NothingToRender(Exception):
    """Asked for a film before there was a script. Refused in the API so it
    costs a 422 rather than a job that can only fail."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MissingStills(Exception):
    """A preview was asked for while some shot has no finished still (M5-D4).

    Rendering anyway produces a film with holes in it -- which is not the
    deliverable the user asked for, and whose only repair is to notice,
    fix and render again. A shot with no *audio* is not refused: a shot
    with no line is a correct shot, and silence over it is the right
    answer.
    """

    def __init__(self, count: int, total: int) -> None:
        self.count = count
        self.total = total
        super().__init__(f"{count}/{total}")
        self.reason = (
            f"{count} of {total} shots have no finished still yet. "
            "Draw them on the Shots page, then come back — a preview with gaps in it "
            "would not tell you anything about the film."
        )


@dataclass(frozen=True)
class FilmView:
    """Everything the Film page draws."""

    shot_count: int
    #: Shots with a still that actually has bytes. The page's readiness
    #: line is `stills_ready` of `shot_count`, and the render button is
    #: refused below that.
    stills_ready: int
    duration_sec: float
    music: AudioClip | None
    render: Render | None


async def _film_shots(db: DbSession, meld_id: uuid.UUID) -> list[Shot]:
    return await shots_in_film_order(db, meld_id)


async def _stills_by_shot(db: DbSession, shot_ids: list[uuid.UUID]) -> dict[uuid.UUID, ShotImage]:
    """Only stills that actually have bytes.

    A row in `generating` or `failed` has no asset and would put a null
    where a URL belongs, so from here on "has a still" means "has a file".
    """
    if not shot_ids:
        return {}
    rows = (
        await db.execute(
            select(ShotImage).where(ShotImage.shot_id.in_(shot_ids), ShotImage.asset_id.isnot(None))
        )
    ).scalars()
    return {still.shot_id: still for still in rows}


async def _speech_by_shot(db: DbSession, shot_ids: list[uuid.UUID]) -> dict[uuid.UUID, AudioClip]:
    if not shot_ids:
        return {}
    rows = (
        await db.execute(
            select(AudioClip).where(
                AudioClip.owner_type == AudioOwnerType.SHOT,
                AudioClip.owner_id.in_(shot_ids),
                AudioClip.asset_id.isnot(None),
            )
        )
    ).scalars()
    return {clip.owner_id: clip for clip in rows}


async def _music_clip(db: DbSession, meld_id: uuid.UUID) -> AudioClip | None:
    """The Meld's one score. `audio_clips`' polymorphic owner finally has
    its second writer (M4-D4 declared it for exactly this)."""
    return (
        await db.execute(
            select(AudioClip).where(
                AudioClip.owner_type == AudioOwnerType.MELD,
                AudioClip.owner_id == meld_id,
                AudioClip.kind == AudioKind.MUSIC,
            )
        )
    ).scalar_one_or_none()


async def _latest_render(db: DbSession, meld_id: uuid.UUID, kind: RenderKind) -> Render | None:
    """One render row per Meld per kind, rewritten in place -- the same rule
    as one still per shot. Ordered by created_at anyway, so a row left by an
    older schema cannot make this ambiguous."""
    return (
        await db.execute(
            select(Render)
            .where(Render.meld_id == meld_id, Render.kind == kind)
            .order_by(Render.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def film_duration(shots: list[Shot]) -> float:
    return float(sum(shot.duration_sec for shot in shots))


async def get_film(db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID) -> FilmView | None:
    if await get_meld(db, user_id, meld_id) is None:
        return None

    shots = await _film_shots(db, meld_id)
    stills = await _stills_by_shot(db, [shot.id for shot in shots])
    return FilmView(
        shot_count=len(shots),
        stills_ready=sum(1 for shot in shots if shot.id in stills),
        duration_sec=film_duration(shots),
        music=await _music_clip(db, meld_id),
        render=await _latest_render(db, meld_id, RenderKind.PREVIEW),
    )


async def build_scene_spec(db: DbSession, meld_id: uuid.UUID, kind: RenderKind) -> SceneSpec:
    """Resolve one Meld into the document the render service renders.

    The only place the film's shape is decided. Raises rather than
    returning a partial spec, because a spec missing a still is a film with
    a hole in it and there is no caller that wants one.

    Called twice per render -- once by the API to price and key the job,
    once by the worker to send it -- and deliberately so: the hash that
    identified the work and the document that performs it are then the same
    function of the same rows, rather than two walks that must agree.
    """
    shots = await _film_shots(db, meld_id)
    if not shots:
        raise NothingToRender("Generate the script first — there are no shots to assemble.")

    stills = await _stills_by_shot(db, [shot.id for shot in shots])
    missing = [shot for shot in shots if shot.id not in stills]
    if missing:
        raise MissingStills(len(missing), len(shots))

    speech = await _speech_by_shot(db, [shot.id for shot in shots])
    music = await _music_clip(db, meld_id)

    spec_shots = []
    for index, shot in enumerate(shots):
        still = stills[shot.id]
        clip = speech.get(shot.id)
        motion_from, motion_to = ken_burns(shot, index)
        spec_shots.append(
            SpecShot(
                shot_id=shot.id,
                duration_sec=float(shot.duration_sec),
                # Not Optional: _stills_by_shot filtered on asset_id above,
                # and the missing check ran before this loop.
                still_asset_id=still.asset_id,  # type: ignore[arg-type]
                still_content_hash=still.content_hash,
                motion_from=motion_from,
                motion_to=motion_to,
                audio=(
                    []
                    if clip is None or clip.asset_id is None
                    else [
                        SpecAudio(
                            asset_id=clip.asset_id,
                            content_hash=clip.content_hash,
                            # Speech starts with the shot. A lead-in would
                            # be a pacing decision, and pacing is what the
                            # user is watching this preview to judge.
                            start_sec=0.0,
                            gain_db=0.0,
                        )
                    ]
                ),
            )
        )

    width, height = PREVIEW_SIZE
    return SceneSpec(
        kind=kind,
        width=width,
        height=height,
        shots=spec_shots,
        music=(
            None
            if music is None or music.asset_id is None
            else SpecAudio(
                asset_id=music.asset_id,
                content_hash=music.content_hash,
                start_sec=0.0,
                # The duck lives in the spec's music_gain_db, not here, so
                # a reader of one shot's audio list is never looking at a
                # number that means something different from its neighbours.
                gain_db=0.0,
            )
        ),
    )


async def _scenes(db: DbSession, meld_id: uuid.UUID) -> list[Scene]:
    return list(
        (
            await db.execute(
                select(Scene).where(Scene.meld_id == meld_id).order_by(Scene.order_index)
            )
        ).scalars()
    )


async def music_brief(db: DbSession, meld: Meld) -> tuple[str, float]:
    """The prompt and the length this Meld's score is made from (M5-D3)."""
    shots = await _film_shots(db, meld.id)
    return music_prompt(meld, await _scenes(db, meld.id)), film_duration(shots)


async def request_music(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID
) -> JobDispatch | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None

    prompt, duration = await music_brief(db, meld)
    if duration <= 0:
        raise NothingToRender("Generate the script first — there is no film to score yet.")

    # Before ensure_job and before the hold (spec 4.5). The prompt is built
    # from the user's own title and scene headings, so it is user text
    # reaching a provider like any other.
    verdict = await check_and_record(db, meld, prompt)
    if not verdict.allowed:
        raise Blocked(verdict.reason or "This film can't be scored from that description.")

    input_hash = music_clip_input_hash(music_hash_for_meld(prompt, duration))

    clip = await _music_clip(db, meld_id)
    now = datetime.now(UTC)
    if clip is None:
        clip = AudioClip(
            owner_type=AudioOwnerType.MELD,
            owner_id=meld_id,
            kind=AudioKind.MUSIC,
            # `text` holds the prompt rather than spoken words here. It is
            # what was sent and what explains the audio that exists, which
            # is the column's job on a speech clip too.
            text=prompt,
            voice_id="",
            input_hash=input_hash,
            state=ArtifactState.GENERATING,
            created_at=now,
            updated_at=now,
        )
        db.add(clip)
        await db.flush()

    dispatch = await ensure_job(
        db,
        meld_id=meld_id,
        kind=JobKind.GENERATE_MUSIC,
        target_table="audio_clips",
        target_id=clip.id,
        input_hash=input_hash,
    )

    if dispatch.enqueued:
        credits = quote_for(JobKind.GENERATE_MUSIC)
        await hold(
            db,
            user_id=user_id,
            job_id=dispatch.job.id,
            meld_id=meld_id,
            credits=credits,
            description="film music",
        )
        dispatch.job.credits_held = credits
        clip.text = prompt
        # Written here, not by the worker, and for the reason M4's
        # _load_refs reads the clip row rather than the live shot: this is
        # what the job was keyed on, and the user can edit a shot's
        # duration while the job sits in the queue. A worker that measured
        # the film again would score a film nobody asked it to.
        clip.params = {**MUSIC_PARAMS, "duration_sec": round(duration)}
        clip.duration_sec = None
        clip.state = ArtifactState.GENERATING
        clip.updated_at = now
        await db.flush()

    return dispatch


async def request_preview(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID
) -> JobDispatch | None:
    """Queue a Preview render. No quote and no hold (M5-D7).

    Spec 4.2 and 9 both say Preview is free, and it is free for a reason:
    it is the approval gate that makes a $6 Final render humane. What stops
    a script pressing this thirty thousand times is the mechanism that
    already exists -- the render's input_hash is its idempotency key, so a
    second press with nothing changed starts no work at all.
    """
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None

    spec = await build_scene_spec(db, meld_id, RenderKind.PREVIEW)
    input_hash = render_input_hash(spec.to_hash_payload(), RenderKind.PREVIEW)

    render = await _latest_render(db, meld_id, RenderKind.PREVIEW)
    now = datetime.now(UTC)
    if render is None:
        render = Render(meld_id=meld_id, kind=RenderKind.PREVIEW, input_hash=input_hash)
        db.add(render)
        await db.flush()

    dispatch = await ensure_job(
        db,
        meld_id=meld_id,
        kind=JobKind.RENDER_PREVIEW,
        target_table="renders",
        target_id=render.id,
        input_hash=input_hash,
    )

    if dispatch.enqueued:
        # The spec is stored now rather than by the worker, so a render
        # that never finishes still says what it was asked to make. Asset
        # ids, never the signed URLs -- a stored signature is a stored
        # credential that also expires.
        render.scene_spec = spec.to_hash_payload()
        render.duration_sec = spec.duration_sec
        render.resolution = spec.resolution
        render.input_hash = input_hash
        render.state = ArtifactState.GENERATING
        render.updated_at = now
        await db.flush()

    return dispatch
