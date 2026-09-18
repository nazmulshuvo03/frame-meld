import uuid

from sqlalchemy import select

from framemeld.credits.pricing import quote_for
from framemeld.credits.service import balance
from framemeld.models.job import Job, JobKind
from framemeld.models.shots import AudioClip, AudioKind, AudioOwnerType
from framemeld.models.user import User
from framemeld.worker import (
    run_generate_character_image,
    run_generate_characters,
    run_generate_script,
    run_generate_shot_image,
    run_map_shot_characters,
)

STORY = (
    "Elena found the letter beneath the floorboard. She read it twice. "
    "Her hands would not stop shaking.\n\n"
    "Outside, the rain began. Marcus watched her from the doorway and said nothing."
)


async def _user_id(db) -> uuid.UUID:
    return (await db.execute(select(User.id).where(User.email == "owner@example.com"))).scalar_one()


async def scripted(client, db) -> str:
    """A Meld with a script and nothing drawn."""
    meld_id = (await client.post("/melds", json={"title": "T"})).json()["id"]
    await client.put(f"/melds/{meld_id}/story", json={"text": STORY})
    script = await client.post(f"/melds/{meld_id}/script/generate")
    await run_generate_script(db, uuid.UUID(script.json()["id"]))
    await db.commit()
    return meld_id


async def drawn(client, db) -> str:
    """A Meld with a still for every shot -- the state a preview needs."""
    meld_id = await scripted(client, db)

    cast = await client.post(f"/melds/{meld_id}/cast/generate")
    await run_generate_characters(db, uuid.UUID(cast.json()["id"]))
    await db.commit()
    for character in (await client.get(f"/melds/{meld_id}/cast")).json()["characters"]:
        sheet = await client.post(f"/melds/{meld_id}/characters/{character['id']}/sheet")
        await run_generate_character_image(db, uuid.UUID(sheet.json()["id"]))
        await db.commit()
    casting = await client.post(f"/melds/{meld_id}/shots/cast")
    await run_map_shot_characters(db, uuid.UUID(casting.json()["id"]))
    await db.commit()

    for shot in (await client.get(f"/melds/{meld_id}/shots")).json()["shots"]:
        started = await client.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
        await run_generate_shot_image(db, uuid.UUID(started.json()["id"]))
        await db.commit()
    return meld_id


# --- the film view --------------------------------------------------------


async def test_the_film_page_says_how_much_is_drawn(logged_in, db):
    meld_id = await scripted(logged_in, db)
    film = (await logged_in.get(f"/melds/{meld_id}/film")).json()

    assert film["shot_count"] > 0
    assert film["stills_ready"] == 0
    assert film["duration_sec"] > 0
    assert film["music"] is None
    assert film["preview"] is None


async def test_a_drawn_film_is_ready(logged_in, db):
    meld_id = await drawn(logged_in, db)
    film = (await logged_in.get(f"/melds/{meld_id}/film")).json()
    assert film["stills_ready"] == film["shot_count"]


async def test_another_users_film_is_404(client, db):
    await client.post(
        "/auth/signup", json={"email": "owner@example.com", "password": "hunter2hunter2"}
    )
    await client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "hunter2hunter2"}
    )
    meld_id = await scripted(client, db)
    await client.post("/auth/logout")

    stranger = {"email": "stranger@example.com", "password": "hunter2hunter2"}
    await client.post("/auth/signup", json=stranger)
    await client.post("/auth/login", json=stranger)
    assert (await client.get(f"/melds/{meld_id}/film")).status_code == 404


# --- the preview gate (M5-D4) ---------------------------------------------


async def test_a_preview_is_refused_while_a_shot_has_no_still(logged_in, db):
    """A film with holes in it is not the deliverable, and the only repair
    is to notice, fix and render again."""
    meld_id = await scripted(logged_in, db)
    response = await logged_in.post(f"/melds/{meld_id}/film/preview")

    assert response.status_code == 422
    assert "no finished still" in response.json()["detail"]
    assert (await db.execute(select(Job).where(Job.kind == JobKind.RENDER_PREVIEW))).first() is None


async def test_a_preview_before_a_script_says_so(logged_in):
    meld_id = (await logged_in.post("/melds", json={"title": "T"})).json()["id"]
    response = await logged_in.post(f"/melds/{meld_id}/film/preview")
    assert response.status_code == 422
    assert "script" in response.json()["detail"]


async def test_a_drawn_film_renders_and_costs_nothing(logged_in, db, recording_queue):
    """M5-D7. Spec 4.2 and 9 both make Preview free, because it is the gate
    that makes a $6 Final render humane."""
    meld_id = await drawn(logged_in, db)
    before = await balance(db, await _user_id(db))

    response = await logged_in.post(f"/melds/{meld_id}/film/preview")
    assert response.status_code == 202
    assert response.json()["kind"] == "render_preview"
    assert await balance(db, await _user_id(db)) == before

    job = (
        await db.execute(select(Job).where(Job.id == uuid.UUID(response.json()["id"])))
    ).scalar_one()
    assert job.credits_held == 0
    assert ("render_preview", job.id) in recording_queue.enqueued


async def test_asking_twice_for_an_unchanged_film_starts_no_second_render(
    logged_in, db, recording_queue
):
    """The idempotency key is this action's whole abuse control (M5-D7):
    with no price on it, "free" has to mean "does nothing"."""
    meld_id = await drawn(logged_in, db)
    first = await logged_in.post(f"/melds/{meld_id}/film/preview")
    second = await logged_in.post(f"/melds/{meld_id}/film/preview")

    assert first.json()["id"] == second.json()["id"]
    assert len([task for task, _ in recording_queue.enqueued if task == "render_preview"]) == 1


async def test_editing_a_shots_line_re_renders_but_editing_its_motion_does_not(logged_in, db):
    """The narrow-projection rule at the last node in the graph: the render
    reads speech and duration, and does not read motion_prompt (M5-D5)."""
    meld_id = await drawn(logged_in, db)
    first = (await logged_in.post(f"/melds/{meld_id}/film/preview")).json()["id"]

    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"motion_prompt": "a violent whip pan"}
    )
    unchanged = (await logged_in.post(f"/melds/{meld_id}/film/preview")).json()["id"]
    assert unchanged == first

    await logged_in.patch(f"/melds/{meld_id}/shots/{shot['id']}", json={"duration_sec": "2.5"})
    changed = (await logged_in.post(f"/melds/{meld_id}/film/preview")).json()["id"]
    assert changed != first


# --- music ----------------------------------------------------------------


async def test_music_is_quoted_held_and_queued(logged_in, db, recording_queue):
    meld_id = await scripted(logged_in, db)
    before = await balance(db, await _user_id(db))

    response = await logged_in.post(f"/melds/{meld_id}/film/music")
    assert response.status_code == 202
    assert await balance(db, await _user_id(db)) == before - quote_for(JobKind.GENERATE_MUSIC)
    assert ("generate_music", uuid.UUID(response.json()["id"])) in recording_queue.enqueued


async def test_the_music_brief_is_stored_on_the_clip(logged_in, db):
    """Read by the worker rather than recomputed there, for the reason M4's
    _load_refs reads the clip row: the user can edit a shot while the job
    sits in the queue."""
    meld_id = await scripted(logged_in, db)
    await logged_in.post(f"/melds/{meld_id}/film/music")
    await db.commit()

    clip = (
        await db.execute(
            select(AudioClip).where(
                AudioClip.owner_type == AudioOwnerType.MELD, AudioClip.kind == AudioKind.MUSIC
            )
        )
    ).scalar_one()
    assert "instrumental score" in clip.text.lower()
    assert clip.params["duration_sec"] > 0


async def test_asking_twice_for_the_same_score_is_free(logged_in, db):
    meld_id = await scripted(logged_in, db)
    first = await logged_in.post(f"/melds/{meld_id}/film/music")
    after_first = await balance(db, await _user_id(db))

    second = await logged_in.post(f"/melds/{meld_id}/film/music")
    assert second.json()["id"] == first.json()["id"]
    assert await balance(db, await _user_id(db)) == after_first


async def test_music_before_a_script_says_so(logged_in):
    meld_id = (await logged_in.post("/melds", json={"title": "T"})).json()["id"]
    response = await logged_in.post(f"/melds/{meld_id}/film/music")
    assert response.status_code == 422
    assert "script" in response.json()["detail"]


async def test_music_with_no_credits_is_402_and_creates_nothing(logged_in, db, monkeypatch):
    from framemeld.config import settings

    monkeypatch.setattr(settings, "signup_grant_credits", 0)
    meld_id = await scripted(logged_in, db)
    # Spend the signup grant down to nothing.
    from framemeld.credits.service import _add
    from framemeld.models.credits import CreditKind

    user_id = await _user_id(db)
    await _add(
        db,
        user_id=user_id,
        delta=-(await balance(db, user_id)),
        kind=CreditKind.HOLD,
        description="drain",
    )
    await db.commit()

    response = await logged_in.post(f"/melds/{meld_id}/film/music")
    assert response.status_code == 402
    assert (
        await db.execute(select(AudioClip).where(AudioClip.owner_type == AudioOwnerType.MELD))
    ).first() is None
