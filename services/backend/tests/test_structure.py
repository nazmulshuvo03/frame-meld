"""Adding, deleting and reordering scenes, shots and characters.

Spec §2 says the user is in control at every stage. Until these endpoints
existed, control stopped at editing the text of whatever the model produced:
one unwanted shot could only be removed by regenerating the script, which
replaces rather than merges and takes every sheet, still and clip paid for
since.
"""

import uuid

from sqlalchemy import select
from test_render_api import drawn, scripted

from framemeld.credits.service import balance
from framemeld.models.cast import Character
from framemeld.models.script import ArtifactState, Scene, Shot
from framemeld.models.shots import AudioClip, AudioOwnerType, ShotCharacter, ShotImage
from framemeld.models.user import User
from framemeld.worker import run_generate_characters, run_generate_script


async def _script(client, meld_id):
    return (await client.get(f"/melds/{meld_id}/script")).json()


async def _shots_of(client, meld_id, scene_index=0):
    return (await _script(client, meld_id))["scenes"][scene_index]["shots"]


# --- scenes ---------------------------------------------------------------


async def test_a_scene_can_be_added_and_lands_at_the_end(logged_in, db):
    meld_id = await scripted(logged_in, db)
    before = len((await _script(logged_in, meld_id))["scenes"])

    response = await logged_in.post(f"/melds/{meld_id}/scenes")
    assert response.status_code == 201
    assert response.json()["order_index"] == before

    scenes = (await _script(logged_in, meld_id))["scenes"]
    assert len(scenes) == before + 1
    assert scenes[-1]["heading"] == "NEW SCENE"


async def test_an_added_scene_is_fresh_rather_than_permanently_out_of_date(logged_in, db):
    """It was not generated, but it belongs to this script. Giving it the
    same input_hash as its siblings means regenerating the story marks it
    stale along with them; an empty string would leave it stale forever."""
    meld_id = await scripted(logged_in, db)
    added = (await logged_in.post(f"/melds/{meld_id}/scenes")).json()
    assert added["state"] == "fresh"

    await logged_in.put(f"/melds/{meld_id}/story", json={"text": "A completely new story."})
    scenes = (await _script(logged_in, meld_id))["scenes"]
    assert all(scene["state"] == "stale" for scene in scenes)


async def test_deleting_a_scene_takes_its_shots_with_it(logged_in, db):
    meld_id = await scripted(logged_in, db)
    scenes = (await _script(logged_in, meld_id))["scenes"]
    doomed = scenes[0]
    survivors = sum(len(scene["shots"]) for scene in scenes[1:])

    assert (await logged_in.delete(f"/melds/{meld_id}/scenes/{doomed['id']}")).status_code == 204
    await db.commit()

    remaining = (await _script(logged_in, meld_id))["scenes"]
    assert len(remaining) == len(scenes) - 1
    assert sum(len(scene["shots"]) for scene in remaining) == survivors
    assert (
        await db.execute(select(Shot).where(Shot.scene_id == uuid.UUID(doomed["id"])))
    ).first() is None


async def test_deleting_a_scene_renumbers_the_rest(logged_in, db):
    """Gaps are harmless to display but `order_index` is inside a shot's
    content_hash, so a hole would silently rehash everything after it."""
    meld_id = await scripted(logged_in, db)
    scenes = (await _script(logged_in, meld_id))["scenes"]
    await logged_in.delete(f"/melds/{meld_id}/scenes/{scenes[0]['id']}")
    await db.commit()

    remaining = (await _script(logged_in, meld_id))["scenes"]
    assert [scene["order_index"] for scene in remaining] == list(range(len(remaining)))


async def test_the_last_scene_cannot_be_deleted(logged_in, db):
    """A Meld with no scenes has no way back except regenerating the script,
    which destroys everything paid for since."""
    meld_id = await scripted(logged_in, db)
    for scene in (await _script(logged_in, meld_id))["scenes"][:-1]:
        await logged_in.delete(f"/melds/{meld_id}/scenes/{scene['id']}")
        await db.commit()

    last = (await _script(logged_in, meld_id))["scenes"][0]
    response = await logged_in.delete(f"/melds/{meld_id}/scenes/{last['id']}")
    assert response.status_code == 422
    assert "at least one scene" in response.json()["detail"]
    assert len((await _script(logged_in, meld_id))["scenes"]) == 1


async def test_a_scene_moves_up_and_down(logged_in, db):
    meld_id = await scripted(logged_in, db)
    before = [scene["id"] for scene in (await _script(logged_in, meld_id))["scenes"]]

    assert (
        await logged_in.post(f"/melds/{meld_id}/scenes/{before[1]}/move", json={"delta": -1})
    ).status_code == 204
    await db.commit()

    after = [scene["id"] for scene in (await _script(logged_in, meld_id))["scenes"]]
    assert after[0] == before[1] and after[1] == before[0]
    assert sorted(after) == sorted(before)


async def test_moving_off_the_end_is_a_no_op_not_an_error(logged_in, db):
    """The button is disabled there, so reaching this is a double-click."""
    meld_id = await scripted(logged_in, db)
    first = (await _script(logged_in, meld_id))["scenes"][0]["id"]
    assert (
        await logged_in.post(f"/melds/{meld_id}/scenes/{first}/move", json={"delta": -1})
    ).status_code == 204
    assert (await _script(logged_in, meld_id))["scenes"][0]["id"] == first


async def test_a_move_of_more_than_one_step_is_refused(logged_in, db):
    """Clamping a 7 to a 1 would move the row somewhere nobody asked for."""
    meld_id = await scripted(logged_in, db)
    first = (await _script(logged_in, meld_id))["scenes"][0]["id"]
    assert (
        await logged_in.post(f"/melds/{meld_id}/scenes/{first}/move", json={"delta": 7})
    ).status_code == 422


# --- shots ----------------------------------------------------------------


async def test_a_shot_can_be_added_to_a_scene(logged_in, db):
    meld_id = await scripted(logged_in, db)
    scene = (await _script(logged_in, meld_id))["scenes"][0]

    response = await logged_in.post(f"/melds/{meld_id}/scenes/{scene['id']}/shots")
    assert response.status_code == 201
    added = response.json()
    assert added["order_index"] == len(scene["shots"])
    # The model limit, not zero: a zero-second shot is refused by the clip
    # provider and renders as nothing.
    assert added["duration_sec"] == "5.00"


async def test_deleting_a_shot_takes_its_still_and_audio(logged_in, db):
    meld_id = await drawn(logged_in, db)
    shots = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"]
    doomed = shots[0]
    assert doomed["still"] is not None

    assert (await logged_in.delete(f"/melds/{meld_id}/shots/{doomed['id']}")).status_code == 204
    await db.commit()

    shot_id = uuid.UUID(doomed["id"])
    assert (await db.execute(select(ShotImage).where(ShotImage.shot_id == shot_id))).first() is None
    assert (
        await db.execute(select(ShotCharacter).where(ShotCharacter.shot_id == shot_id))
    ).first() is None
    # audio_clips.owner_id is polymorphic and cascades nothing, so this one
    # has to be deleted by hand -- the bug M4 found when regenerating a
    # script orphaned every recording.
    assert (
        await db.execute(
            select(AudioClip).where(
                AudioClip.owner_type == AudioOwnerType.SHOT, AudioClip.owner_id == shot_id
            )
        )
    ).first() is None


async def test_deleting_a_shot_refunds_nothing(logged_in, db):
    """The user is choosing to throw the work away. Spec 4.3's refunds are
    for work that failed."""
    meld_id = await drawn(logged_in, db)
    user_id = (
        await db.execute(select(User.id).where(User.email == "owner@example.com"))
    ).scalar_one()
    before = await balance(db, user_id)

    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.delete(f"/melds/{meld_id}/shots/{shot['id']}")
    await db.commit()

    assert await balance(db, user_id) == before


async def test_a_scene_may_end_up_with_no_shots(logged_in, db):
    """Unlike a Meld with no scenes, this is recoverable -- Add shot is
    right there."""
    meld_id = await scripted(logged_in, db)
    scene = (await _script(logged_in, meld_id))["scenes"][0]
    for shot in scene["shots"]:
        assert (await logged_in.delete(f"/melds/{meld_id}/shots/{shot['id']}")).status_code == 204
        await db.commit()

    assert (await _script(logged_in, meld_id))["scenes"][0]["shots"] == []


async def test_moving_a_shot_reorders_it_and_rehashes_the_pair(logged_in, db):
    """`order_index` is inside a shot's content_hash, so the two that
    swapped really are different rows now -- which is what makes a render
    correctly go stale."""
    meld_id = await scripted(logged_in, db)
    scene = (await _script(logged_in, meld_id))["scenes"][0]
    before = scene["shots"]
    assert len(before) >= 2

    await logged_in.post(f"/melds/{meld_id}/shots/{before[1]['id']}/move", json={"delta": -1})
    await db.commit()

    after = await _shots_of(logged_in, meld_id)
    assert [shot["id"] for shot in after[:2]] == [before[1]["id"], before[0]["id"]]
    assert [shot["order_index"] for shot in after] == list(range(len(after)))
    moved = next(shot for shot in after if shot["id"] == before[1]["id"])
    assert moved["content_hash"] != before[1]["content_hash"]


async def test_reordering_shots_marks_the_render_stale_but_not_the_stills(logged_in, db):
    """The narrow-projection rule doing its job: a still keys on
    {image_prompt, camera} and a render on the whole ordered list."""
    from test_render_jobs import _FakeRenderClient

    import framemeld.worker as worker
    from framemeld.models.render import Render
    from framemeld.worker import run_render_preview

    meld_id = await drawn(logged_in, db)
    original = worker.get_render_client
    worker.get_render_client = lambda: _FakeRenderClient()
    try:
        started = await logged_in.post(f"/melds/{meld_id}/film/preview")
        await run_render_preview(db, uuid.UUID(started.json()["id"]))
        await db.commit()
    finally:
        worker.get_render_client = original

    shots = await _shots_of(logged_in, meld_id)
    await logged_in.post(f"/melds/{meld_id}/shots/{shots[1]['id']}/move", json={"delta": -1})
    await db.commit()

    render = (await db.execute(select(Render))).scalar_one()
    assert render.state is ArtifactState.STALE
    stills = (await db.execute(select(ShotImage))).scalars().all()
    assert all(still.state is ArtifactState.FRESH for still in stills)


# --- characters -----------------------------------------------------------


async def _cast(client, meld_id):
    return (await client.get(f"/melds/{meld_id}/cast")).json()["characters"]


async def _with_cast(client, db) -> str:
    meld_id = await scripted(client, db)
    cast = await client.post(f"/melds/{meld_id}/cast/generate")
    await run_generate_characters(db, uuid.UUID(cast.json()["id"]))
    await db.commit()
    return meld_id


async def test_a_character_can_be_added_with_their_own_voice(logged_in, db):
    """A character with no voice reads in the default narrator's, which
    silently makes two people sound identical."""
    meld_id = await _with_cast(logged_in, db)
    before = await _cast(logged_in, meld_id)

    response = await logged_in.post(f"/melds/{meld_id}/characters")
    assert response.status_code == 201
    added = response.json()
    assert added["voice_id"]
    assert added["voice_id"] not in [c["voice_id"] for c in before] or len(before) >= 8


async def test_deleting_a_character_clears_them_from_every_shot(logged_in, db):
    """`shot_characters` cascades, which is exactly why spec 5.2 made it a
    real table rather than a uuid[]."""
    meld_id = await drawn(logged_in, db)
    cast = await _cast(logged_in, meld_id)
    doomed = cast[0]

    assert (
        await logged_in.delete(f"/melds/{meld_id}/characters/{doomed['id']}")
    ).status_code == 204
    await db.commit()

    character_id = uuid.UUID(doomed["id"])
    assert (await db.execute(select(Character).where(Character.id == character_id))).first() is None
    assert (
        await db.execute(select(ShotCharacter).where(ShotCharacter.character_id == character_id))
    ).first() is None
    # And no shot is left pointing at them as its speaker, which is a real
    # foreign key with no ON DELETE.
    assert (
        await db.execute(select(Shot).where(Shot.speaker_character_id == character_id))
    ).first() is None


async def test_a_still_that_drew_a_deleted_character_keeps_its_record_and_goes_stale(logged_in, db):
    """`ref_character_image_ids` is a historical record of what was drawn,
    not a live relation -- it has to survive to explain a picture that
    exists. The still then goes stale, correctly: it contains a face the
    film no longer has."""
    meld_id = await drawn(logged_in, db)
    peopled = next(
        shot
        for shot in (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"]
        if shot["character_ids"]
    )
    character_id = peopled["character_ids"][0]

    await logged_in.delete(f"/melds/{meld_id}/characters/{character_id}")
    await db.commit()

    still = (
        await db.execute(select(ShotImage).where(ShotImage.shot_id == uuid.UUID(peopled["id"])))
    ).scalar_one()
    assert still.ref_character_image_ids != []
    assert still.asset_id is not None
    assert still.state is ArtifactState.STALE


async def test_another_users_structure_is_404(client, db):
    await client.post(
        "/auth/signup", json={"email": "owner@example.com", "password": "hunter2hunter2"}
    )
    await client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "hunter2hunter2"}
    )
    meld_id = await scripted(client, db)
    scene = (await _script(client, meld_id))["scenes"][0]
    await client.post("/auth/logout")

    stranger = {"email": "stranger@example.com", "password": "hunter2hunter2"}
    await client.post("/auth/signup", json=stranger)
    await client.post("/auth/login", json=stranger)

    assert (await client.post(f"/melds/{meld_id}/scenes")).status_code == 404
    assert (await client.delete(f"/melds/{meld_id}/scenes/{scene['id']}")).status_code == 404
    assert (
        await client.post(f"/melds/{meld_id}/scenes/{scene['id']}/move", json={"delta": 1})
    ).status_code == 404
    assert (
        await client.delete(f"/melds/{meld_id}/shots/{scene['shots'][0]['id']}")
    ).status_code == 404
    assert (await client.post(f"/melds/{meld_id}/characters")).status_code == 404


async def test_the_script_can_still_be_regenerated_after_hand_editing(logged_in, db):
    """Regeneration replaces, so a hand-added scene goes with it. That is
    the documented behaviour, not a bug -- but it must not fail."""
    meld_id = await scripted(logged_in, db)
    await logged_in.post(f"/melds/{meld_id}/scenes")
    await db.commit()

    await logged_in.put(f"/melds/{meld_id}/story", json={"text": "An entirely different story."})
    started = await logged_in.post(f"/melds/{meld_id}/script/generate")
    await run_generate_script(db, uuid.UUID(started.json()["id"]))
    await db.commit()

    scenes = (await _script(logged_in, meld_id))["scenes"]
    assert scenes and all(scene["heading"] != "NEW SCENE" for scene in scenes)
    assert (
        (await db.execute(select(Scene).where(Scene.meld_id == uuid.UUID(meld_id)))).scalars().all()
    )
