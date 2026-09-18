import uuid

import pytest
from sqlalchemy import select
from test_render_api import _user_id, drawn, scripted
from test_render_jobs import FAKE_MP4, _FakeRenderClient

from framemeld.credits.pricing import quote_for
from framemeld.credits.service import balance, grant
from framemeld.models.job import Job, JobKind, JobState
from framemeld.models.render import Render, RenderKind
from framemeld.models.script import ArtifactState
from framemeld.models.shots import ShotClip
from framemeld.providers.base import ProviderRefusedError
from framemeld.staleness import recompute_staleness
from framemeld.worker import run_generate_shot_clip, run_render_final, run_render_preview


async def animated(client, db) -> str:
    """A Meld with a still and a clip for every shot -- what a Final needs.

    Grants first: at 80 credits a clip (M6-D3) a six-shot film costs more
    than the 500-credit signup grant, which is the pricing working rather
    than a fixture problem -- spec section 7 puts a whole film at 2,500-3,000.
    """
    meld_id = await drawn(client, db)
    await grant(db, await _user_id(db), 3000, "test")
    await db.commit()
    for shot in (await client.get(f"/melds/{meld_id}/shots")).json()["shots"]:
        started = await client.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")
        assert started.status_code == 202, started.text
        await run_generate_shot_clip(db, uuid.UUID(started.json()["id"]))
        await db.commit()
    return meld_id


async def _clips(db) -> list[ShotClip]:
    return list((await db.execute(select(ShotClip))).scalars())


# --- the clip -------------------------------------------------------------


async def test_a_clip_is_animated_from_the_still_and_stored(logged_in, db):
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]

    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")
    outcome = await run_generate_shot_clip(db, uuid.UUID(started.json()["id"]))
    assert outcome.state is JobState.SUCCEEDED

    clip = (await db.execute(select(ShotClip))).scalar_one()
    assert clip.state is ArtifactState.FRESH
    assert clip.asset_id is not None
    # It records which picture it was animated from -- spec section 11's
    # drift mitigation rests on that being answerable.
    assert clip.source_image_id == uuid.UUID(shot["still"]["id"])
    assert clip.credits_spent == quote_for(JobKind.GENERATE_SHOT_CLIP)


async def test_two_shots_get_two_different_clips(logged_in, db):
    await animated(logged_in, db)
    assets = {clip.asset_id for clip in await _clips(db)}
    assert len(assets) == len(await _clips(db))


async def test_a_clip_before_the_still_is_refused_and_charges_nothing(logged_in, db):
    """M6-D2: image-to-video is conditioned on a picture, so there is
    literally nothing to send."""
    meld_id = await scripted(logged_in, db)
    before = await balance(db, await _user_id(db))
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]

    response = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")
    assert response.status_code == 422
    assert "still" in response.json()["detail"]
    assert await balance(db, await _user_id(db)) == before


async def test_a_clip_from_a_stale_still_is_allowed(logged_in, db):
    """Allowed, deliberately (M6-D2). Spec section 11's mitigation is that
    stills are approved before any clip is made, and a user animating a
    still they can see on screen is that approval. Refusing would make a
    Meld unfinishable after any upstream edit."""
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "something else"}
    )
    await db.commit()

    fresh = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"]
    assert next(s for s in fresh if s["id"] == shot["id"])["still"]["state"] == "stale"
    assert (await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")).status_code == 202


async def test_asking_twice_for_an_unchanged_clip_is_free(logged_in, db):
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]

    first = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")
    after = await balance(db, await _user_id(db))
    second = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")

    assert second.json()["id"] == first.json()["id"]
    assert await balance(db, await _user_id(db)) == after


async def test_a_refused_clip_refunds_all_eighty_credits(logged_in, db, monkeypatch):
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    before = await balance(db, await _user_id(db))

    class _Refusing:
        name, model = "fake", "fake"

        async def animate(self, *, image, motion_prompt, duration_sec, params):
            raise ProviderRefusedError("No.")

    monkeypatch.setattr("framemeld.worker.get_video_provider", lambda: _Refusing())
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")
    outcome = await run_generate_shot_clip(db, uuid.UUID(started.json()["id"]))

    assert outcome.state is JobState.FAILED
    assert await balance(db, await _user_id(db)) == before


async def test_the_worker_reads_the_clip_row_not_the_live_shot(logged_in, db, monkeypatch):
    """M4's rule at twenty times the stake: the user can redraw a still or
    rewrite a motion prompt while an 80-credit job sits in the queue, and a
    job that animated the new one would charge for something nobody
    approved."""
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")

    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"motion_prompt": "a violent whip pan"}
    )
    await db.commit()

    seen: list[str] = []

    class _Recording:
        name, model = "fake", "fake"

        async def animate(self, *, image, motion_prompt, duration_sec, params):
            seen.append(motion_prompt)
            from framemeld.providers.video.stub import StubVideoProvider

            return await StubVideoProvider().animate(
                image=image, motion_prompt=motion_prompt, duration_sec=duration_sec, params=params
            )

    monkeypatch.setattr("framemeld.worker.get_video_provider", lambda: _Recording())
    await run_generate_shot_clip(db, uuid.UUID(started.json()["id"]))

    assert seen == [shot["motion_prompt"]]
    assert "whip pan" not in seen[0]


# --- staleness ------------------------------------------------------------


async def test_redrawing_the_still_marks_its_clip_stale(logged_in, db, monkeypatch):
    """The link M6-D1 exists for: the model is handed the picture, so new
    bytes mean a different clip."""
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")
    await run_generate_shot_clip(db, uuid.UUID(started.json()["id"]))
    await db.commit()

    from framemeld.worker import run_generate_shot_image

    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "a different room entirely"}
    )
    redraw = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
    await run_generate_shot_image(db, uuid.UUID(redraw.json()["id"]))
    await db.commit()

    await recompute_staleness(db, uuid.UUID(meld_id))
    clip = (await db.execute(select(ShotClip))).scalar_one()
    assert clip.state is ArtifactState.STALE
    assert clip.asset_id is not None


async def test_rewording_an_image_prompt_without_redrawing_leaves_the_clip_fresh(logged_in, db):
    """The other half of M6-D1. The clip keys on the still's *bytes*, not on
    the sentence that produced them, so an edit nobody has paid to act on
    must not invalidate 80 credits of video."""
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")
    await run_generate_shot_clip(db, uuid.UUID(started.json()["id"]))
    await db.commit()

    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "reworded, not redrawn"}
    )
    await db.commit()

    await recompute_staleness(db, uuid.UUID(meld_id))
    assert (await db.execute(select(ShotClip))).scalar_one().state is ArtifactState.FRESH


async def test_rewriting_the_motion_prompt_marks_the_clip_stale(logged_in, db):
    """`motion_prompt` finally has a hash to belong to: M5-D5 kept it out of
    the Preview's, because the Preview never reads it."""
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")
    await run_generate_shot_clip(db, uuid.UUID(started.json()["id"]))
    await db.commit()

    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"motion_prompt": "the camera whips left"}
    )
    await db.commit()

    await recompute_staleness(db, uuid.UUID(meld_id))
    assert (await db.execute(select(ShotClip))).scalar_one().state is ArtifactState.STALE


async def test_rewriting_the_line_leaves_the_clip_alone(logged_in, db):
    meld_id = await drawn(logged_in, db)
    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")
    await run_generate_shot_clip(db, uuid.UUID(started.json()["id"]))
    await db.commit()

    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}",
        json={"speech_text": "Something else entirely.", "dialogue_kind": "narration"},
    )
    await db.commit()

    await recompute_staleness(db, uuid.UUID(meld_id))
    assert (await db.execute(select(ShotClip))).scalar_one().state is ArtifactState.FRESH


# --- the Final render -----------------------------------------------------


async def test_a_final_is_refused_while_a_shot_has_no_clip(logged_in, db):
    meld_id = await drawn(logged_in, db)
    before = await balance(db, await _user_id(db))

    response = await logged_in.post(f"/melds/{meld_id}/film/final")
    assert response.status_code == 422
    assert "no clip" in response.json()["detail"]
    assert await balance(db, await _user_id(db)) == before
    assert (await db.execute(select(Job).where(Job.kind == JobKind.RENDER_FINAL))).first() is None


async def test_a_final_is_charged_unlike_a_preview(logged_in, db, recording_queue):
    meld_id = await animated(logged_in, db)
    before = await balance(db, await _user_id(db))

    response = await logged_in.post(f"/melds/{meld_id}/film/final")
    assert response.status_code == 202
    assert await balance(db, await _user_id(db)) == before - quote_for(JobKind.RENDER_FINAL)
    assert ("render_final", uuid.UUID(response.json()["id"])) in recording_queue.enqueued


async def test_the_final_sends_the_clips_and_the_preview_sends_the_stills(
    logged_in, db, monkeypatch
):
    """Spec section 4.2's one difference, in one assertion: the same
    composition, the same spec, a different visual source."""
    meld_id = await animated(logged_in, db)
    client = _FakeRenderClient()
    monkeypatch.setattr("framemeld.worker.get_render_client", lambda: client)

    preview = await logged_in.post(f"/melds/{meld_id}/film/preview")
    await run_render_preview(db, uuid.UUID(preview.json()["id"]))
    await db.commit()

    final = await logged_in.post(f"/melds/{meld_id}/film/final")
    outcome = await run_render_final(db, uuid.UUID(final.json()["id"]))
    assert outcome.state is JobState.SUCCEEDED

    preview_spec, final_spec = client.specs
    assert all(shot["clip_url"] is None for shot in preview_spec["shots"])
    assert all(shot["clip_url"] is not None for shot in final_spec["shots"])
    assert preview_spec["width"] == 1280
    assert final_spec["width"] == 1920


async def test_a_finished_final_is_stored_alongside_the_preview(logged_in, db, monkeypatch):
    meld_id = await animated(logged_in, db)
    monkeypatch.setattr("framemeld.worker.get_render_client", lambda: _FakeRenderClient())

    started = await logged_in.post(f"/melds/{meld_id}/film/final")
    await run_render_final(db, uuid.UUID(started.json()["id"]))
    await db.commit()

    render = (await db.execute(select(Render).where(Render.kind == RenderKind.FINAL))).scalar_one()
    assert render.state is ArtifactState.FRESH
    assert render.resolution == "1920x1080"
    assert render.credits_spent == quote_for(JobKind.RENDER_FINAL)

    film = (await logged_in.get(f"/melds/{meld_id}/film")).json()
    assert film["clips_ready"] == film["shot_count"]
    assert film["final"]["asset_id"] is not None


async def test_a_failed_final_refunds_in_full(logged_in, db, monkeypatch):
    from framemeld.providers.base import ProviderResponseError

    meld_id = await animated(logged_in, db)
    before = await balance(db, await _user_id(db))
    monkeypatch.setattr(
        "framemeld.worker.get_render_client",
        lambda: _FakeRenderClient(ProviderResponseError("the render failed")),
    )

    started = await logged_in.post(f"/melds/{meld_id}/film/final")
    outcome = await run_render_final(db, uuid.UUID(started.json()["id"]))

    assert outcome.state is JobState.FAILED
    assert await balance(db, await _user_id(db)) == before


async def test_animating_a_shot_marks_the_final_stale_and_leaves_the_preview_fresh(
    logged_in, db, monkeypatch
):
    """A Preview is made of stills and a Final of clips, so they have
    genuinely different parents -- which only holds because a Preview's hash
    deliberately ignores the clips."""
    meld_id = await animated(logged_in, db)
    monkeypatch.setattr("framemeld.worker.get_render_client", lambda: _FakeRenderClient())

    preview = await logged_in.post(f"/melds/{meld_id}/film/preview")
    await run_render_preview(db, uuid.UUID(preview.json()["id"]))
    final = await logged_in.post(f"/melds/{meld_id}/film/final")
    await run_render_final(db, uuid.UUID(final.json()["id"]))
    await db.commit()

    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"motion_prompt": "a whip pan"}
    )
    started = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/clip")
    await run_generate_shot_clip(db, uuid.UUID(started.json()["id"]))
    await db.commit()

    await recompute_staleness(db, uuid.UUID(meld_id))
    renders = {r.kind: r for r in (await db.execute(select(Render))).scalars()}
    assert renders[RenderKind.FINAL].state is ArtifactState.STALE
    assert renders[RenderKind.PREVIEW].state is ArtifactState.FRESH


async def test_asking_twice_for_an_unchanged_final_is_free(logged_in, db):
    meld_id = await animated(logged_in, db)
    first = await logged_in.post(f"/melds/{meld_id}/film/final")
    after = await balance(db, await _user_id(db))
    second = await logged_in.post(f"/melds/{meld_id}/film/final")

    assert second.json()["id"] == first.json()["id"]
    assert await balance(db, await _user_id(db)) == after


# --- the download (M6-D10) ------------------------------------------------


async def test_a_download_is_served_as_an_attachment(logged_in, db, monkeypatch):
    """The HTML `download` attribute is ignored cross-origin, and web and
    api are different origins -- without this the film opens in a tab."""
    meld_id = await animated(logged_in, db)
    monkeypatch.setattr("framemeld.worker.get_render_client", lambda: _FakeRenderClient())
    started = await logged_in.post(f"/melds/{meld_id}/film/final")
    await run_render_final(db, uuid.UUID(started.json()["id"]))
    await db.commit()

    asset_id = (await logged_in.get(f"/melds/{meld_id}/film")).json()["final"]["asset_id"]

    plain = await logged_in.get(f"/assets/{asset_id}")
    assert "content-disposition" not in plain.headers
    assert plain.content == FAKE_MP4

    saved = await logged_in.get(f"/assets/{asset_id}?download=true")
    assert saved.headers["content-disposition"] == (
        f'attachment; filename="framemeld-{asset_id}.mp4"'
    )
    assert saved.content == FAKE_MP4


async def test_a_download_still_needs_a_credential(client, db):
    from framemeld.assets.service import store_asset
    from framemeld.models.user import User

    await client.post(
        "/auth/signup", json={"email": "dl@example.com", "password": "hunter2hunter2"}
    )
    owner = (await db.execute(select(User).where(User.email == "dl@example.com"))).scalar_one()
    asset = await store_asset(db, user_id=owner.id, data=FAKE_MP4, mime="video/mp4", ext="mp4")
    await db.commit()

    assert (await client.get(f"/assets/{asset.id}?download=true")).status_code == 401


@pytest.mark.parametrize("mime", ["video/mp4", "audio/wav", "image/png"])
async def test_the_filename_extension_matches_the_media_type(logged_in, db, mime):
    from framemeld.assets.service import store_asset
    from framemeld.models.user import User

    user = (await db.execute(select(User))).scalars().first()
    asset = await store_asset(db, user_id=user.id, data=b"x" * 16, mime=mime, ext="bin")
    await db.commit()

    response = await logged_in.get(f"/assets/{asset.id}?download=true")
    assert response.headers["content-disposition"].endswith(f'.{mime.split("/")[1]}"')
