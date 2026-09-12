import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.models.meld import Meld, Story
from framemeld.models.script import ArtifactState, Scene, Shot
from framemeld.script.hashes import script_input_hash

# Only these two states are recomputed. A row that is `empty` has nothing to
# be stale about; a `generating` row is owned by a worker that is about to
# write it; a `failed` row's problem is not its inputs. (D5)
_RECOMPUTABLE = (ArtifactState.FRESH, ArtifactState.STALE)


@dataclass(frozen=True)
class StalenessReport:
    stale_scenes: int
    stale_shots: int
    #: How many rows this call actually moved. Zero means the API can skip
    #: telling the user anything changed.
    changed: int


async def recompute_staleness(db: DbSession, meld_id: uuid.UUID) -> StalenessReport:
    """Bring every generated row in one Meld back in line with its inputs.

    The one place staleness is decided, so there is one answer rather than
    one per call site. Called after any edit to an upstream artifact —
    in M2 that means the story.

    Rows are never regenerated here and never deleted: regeneration costs
    money, so it stays an explicit user action (spec §4.1). All this does is
    flip `state` between `fresh` and `stale`, in both directions.

    Loads the rows rather than issuing a bulk UPDATE. A Meld holds at most
    30 shots (spec §1), and the D5 filter plus the per-row counts are far
    clearer in Python than in SQL. Revisit if a Meld ever holds thousands.
    """
    meld = (
        await db.execute(select(Meld).where(Meld.id == meld_id, Meld.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if meld is None:
        return StalenessReport(0, 0, 0)

    story = (await db.execute(select(Story).where(Story.meld_id == meld_id))).scalar_one_or_none()
    if story is None:
        return StalenessReport(0, 0, 0)

    expected = script_input_hash(story.content_hash)

    scenes = list((await db.execute(select(Scene).where(Scene.meld_id == meld_id))).scalars())
    shots = list(
        (
            await db.execute(
                select(Shot).join(Scene, Shot.scene_id == Scene.id).where(Scene.meld_id == meld_id)
            )
        ).scalars()
    )

    now = datetime.now(UTC)
    changed = 0
    for row in (*scenes, *shots):
        if row.state not in _RECOMPUTABLE:
            continue
        wanted = ArtifactState.FRESH if row.input_hash == expected else ArtifactState.STALE
        if row.state is not wanted:
            row.state = wanted
            row.updated_at = now
            changed += 1

    if changed:
        await db.flush()

    return StalenessReport(
        stale_scenes=sum(1 for scene in scenes if scene.state is ArtifactState.STALE),
        stale_shots=sum(1 for shot in shots if shot.state is ArtifactState.STALE),
        changed=changed,
    )
