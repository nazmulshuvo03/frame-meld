import uuid

from sqlalchemy import select
from test_render_api import drawn, scripted
from test_render_jobs import _FakeRenderClient

from framemeld.models.render import Render
from framemeld.models.script import ArtifactState
from framemeld.models.shots import AudioClip, AudioOwnerType
from framemeld.staleness import recompute_staleness
from framemeld.worker import run_generate_music, run_render_preview


async def _music(db) -> AudioClip:
    return (
        await db.execute(select(AudioClip).where(AudioClip.owner_type == AudioOwnerType.MELD))
    ).scalar_one()


async def _render(db, meld_id) -> Render:
    return (await db.execute(select(Render).where(Render.meld_id == meld_id))).scalar_one()


async def _scored(client, db) -> str:
    meld_id = await scripted(client, db)
    started = await client.post(f"/melds/{meld_id}/film/music")
    await run_generate_music(db, uuid.UUID(started.json()["id"]))
    await db.commit()
    return meld_id


async def _rendered(client, db, monkeypatch) -> str:
    meld_id = await drawn(client, db)
    monkeypatch.setattr("framemeld.worker.get_render_client", lambda: _FakeRenderClient())
    started = await client.post(f"/melds/{meld_id}/film/preview")
    await run_render_preview(db, uuid.UUID(started.json()["id"]))
    await db.commit()
    return meld_id


# --- the score ------------------------------------------------------------


async def test_a_fresh_score_stays_fresh(logged_in, db):
    meld_id = await _scored(logged_in, db)
    await recompute_staleness(db, uuid.UUID(meld_id))
    assert (await _music(db)).state is ArtifactState.FRESH


async def test_re_heading_a_scene_re_scores_the_film(logged_in, db):
    """The prompt is built from the Meld's title, style and scene headings
    (M5-D3), so this is a real change to what the score was made from."""
    meld_id = await _scored(logged_in, db)
    scene = (await logged_in.get(f"/melds/{meld_id}/script")).json()["scenes"][0]
    await logged_in.patch(f"/melds/{meld_id}/scenes/{scene['id']}", json={"heading": "A NEW ROOM"})
    await db.commit()

    await recompute_staleness(db, uuid.UUID(meld_id))
    assert (await _music(db)).state is ArtifactState.STALE


async def test_rewriting_a_shots_image_prompt_leaves_the_score_alone(logged_in, db):
    """Redrawing a face must not re-score a film. The disjoint-projection
    rule, at the fourth artifact it has applied to."""
    meld_id = await _scored(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "a completely different room"}
    )
    await db.commit()

    await recompute_staleness(db, uuid.UUID(meld_id))
    assert (await _music(db)).state is ArtifactState.FRESH


async def test_reverting_a_heading_makes_the_score_fresh_again_for_free(logged_in, db):
    meld_id = await _scored(logged_in, db)
    scene = (await logged_in.get(f"/melds/{meld_id}/script")).json()["scenes"][0]
    original = scene["heading"]

    await logged_in.patch(f"/melds/{meld_id}/scenes/{scene['id']}", json={"heading": "ELSEWHERE"})
    await db.commit()
    await recompute_staleness(db, uuid.UUID(meld_id))
    assert (await _music(db)).state is ArtifactState.STALE

    await logged_in.patch(f"/melds/{meld_id}/scenes/{scene['id']}", json={"heading": original})
    await db.commit()
    await recompute_staleness(db, uuid.UUID(meld_id))
    assert (await _music(db)).state is ArtifactState.FRESH


# --- the render, which has every other node upstream of it ----------------


async def test_a_fresh_render_stays_fresh(logged_in, db, monkeypatch):
    meld_id = await _rendered(logged_in, db, monkeypatch)
    await recompute_staleness(db, uuid.UUID(meld_id))
    assert (await _render(db, meld_id)).state is ArtifactState.FRESH


async def test_changing_a_shots_duration_marks_the_render_stale(logged_in, db, monkeypatch):
    meld_id = await _rendered(logged_in, db, monkeypatch)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(f"/melds/{meld_id}/shots/{shot['id']}", json={"duration_sec": "2.0"})
    await db.commit()

    await recompute_staleness(db, uuid.UUID(meld_id))
    render = await _render(db, meld_id)
    assert render.state is ArtifactState.STALE
    # And the film the user already watched is still there.
    assert render.asset_id is not None


async def test_editing_a_motion_prompt_leaves_the_render_fresh(logged_in, db, monkeypatch):
    """M5-D5. The renderer never reads it, so it must not invalidate a
    render -- the same rule that keeps a still fresh through it."""
    meld_id = await _rendered(logged_in, db, monkeypatch)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"motion_prompt": "a violent whip pan"}
    )
    await db.commit()

    await recompute_staleness(db, uuid.UUID(meld_id))
    assert (await _render(db, meld_id)).state is ArtifactState.FRESH


async def test_adding_music_marks_the_render_stale(logged_in, db, monkeypatch):
    """A silent film and a scored one are different films."""
    meld_id = await _rendered(logged_in, db, monkeypatch)
    started = await logged_in.post(f"/melds/{meld_id}/film/music")
    await run_generate_music(db, uuid.UUID(started.json()["id"]))
    await db.commit()

    await recompute_staleness(db, uuid.UUID(meld_id))
    assert (await _render(db, meld_id)).state is ArtifactState.STALE


async def test_a_film_that_can_no_longer_be_assembled_goes_stale_not_failed(
    logged_in, db, monkeypatch
):
    """A still mid-regeneration leaves a Meld that cannot be assembled. The
    file already produced is still watchable and still that user's -- it
    just no longer describes the Meld."""
    meld_id = await _rendered(logged_in, db, monkeypatch)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "something else entirely"}
    )
    # Queue the still again without running it: its row loses its asset only
    # when a worker rewrites it, so drop the asset by hand to reach the
    # genuinely unassemblable state.
    from framemeld.models.shots import ShotImage

    still = (
        await db.execute(select(ShotImage).where(ShotImage.shot_id == uuid.UUID(shot["id"])))
    ).scalar_one()
    still.asset_id = None
    await db.commit()

    await recompute_staleness(db, uuid.UUID(meld_id))
    render = await _render(db, meld_id)
    assert render.state is ArtifactState.STALE
    assert render.asset_id is not None
