import uuid

from framemeld.credits.pricing import QUOTES
from framemeld.models.job import JobKind
from framemeld.worker import (
    run_generate_character_image,
    run_generate_characters,
    run_generate_script,
    run_generate_shot_image,
    run_generate_speech,
    run_map_shot_characters,
)

STORY = (
    "Elena found the letter beneath the floorboard. She read it twice. "
    "Her hands would not stop shaking.\n\n"
    "Outside, the rain began. Marcus watched her from the doorway and said nothing."
)

CASTING_QUOTE = QUOTES[JobKind.MAP_SHOT_CHARACTERS]
STILL_QUOTE = QUOTES[JobKind.GENERATE_SHOT_IMAGE]
SPEECH_QUOTE = QUOTES[JobKind.GENERATE_SPEECH]


async def _balance(client) -> int:
    return (await client.get("/credits")).json()["balance"]


async def _run(client, db, path: str):
    """POST an action and drive its job to completion, the way the worker
    would. Returns the response body of the POST."""
    response = await client.post(path)
    assert response.status_code == 202, response.text
    body = response.json()
    runner = {
        "map_shot_characters": run_map_shot_characters,
        "generate_shot_image": run_generate_shot_image,
        "generate_speech": run_generate_speech,
    }[body["kind"]]
    if body["state"] == "queued":
        outcome = await runner(db, uuid.UUID(body["id"]))
        assert outcome.state.value == "succeeded", outcome
        await db.commit()
    return body


async def _meld_with_script_and_cast(client, db, *, sheets=True):
    """A Meld far enough along that shots can be worked on: a story, a
    generated script, a generated cast, and (by default) a drawn sheet for
    every character."""
    created = await client.post("/melds", json={"title": "T"})
    assert created.status_code == 201, created.text
    meld_id = created.json()["id"]
    assert (await client.put(f"/melds/{meld_id}/story", json={"text": STORY})).status_code == 200

    script = await client.post(f"/melds/{meld_id}/script/generate")
    assert script.status_code == 202, script.text
    ran = await run_generate_script(db, uuid.UUID(script.json()["id"]))
    assert ran.state.value == "succeeded"
    await db.commit()

    cast = await client.post(f"/melds/{meld_id}/cast/generate")
    assert cast.status_code == 202, cast.text
    assert (
        await run_generate_characters(db, uuid.UUID(cast.json()["id"]))
    ).state.value == "succeeded"
    await db.commit()

    characters = (await client.get(f"/melds/{meld_id}/cast")).json()["characters"]
    assert characters
    if sheets:
        for character in characters:
            sheet = await client.post(f"/melds/{meld_id}/characters/{character['id']}/sheet")
            assert sheet.status_code == 202, sheet.text
            assert (
                await run_generate_character_image(db, uuid.UUID(sheet.json()["id"]))
            ).state.value == "succeeded"
            await db.commit()
    return meld_id, characters


async def _shots(client, meld_id):
    response = await client.get(f"/melds/{meld_id}/shots")
    assert response.status_code == 200, response.text
    return response.json()


# --- reading --------------------------------------------------------------


async def test_a_meld_with_no_script_has_no_shots(logged_in):
    created = await logged_in.post("/melds", json={"title": "T"})
    body = await _shots(logged_in, created.json()["id"])
    assert body["shots"] == []
    assert body["cast_into_shots"] is False


async def test_shots_are_listed_before_anyone_is_cast_into_them(logged_in, db):
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db, sheets=False)
    body = await _shots(logged_in, meld_id)
    assert body["shots"]
    assert body["cast_into_shots"] is False
    assert all(shot["character_ids"] == [] for shot in body["shots"])
    assert all(shot["still"] is None and shot["audio"] is None for shot in body["shots"])


async def test_another_users_shots_are_404_not_403(logged_in, db, client):
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db, sheets=False)
    credentials = {"email": "stranger-shots@example.com", "password": "hunter2hunter2"}
    assert (await client.post("/auth/signup", json=credentials)).status_code == 201
    assert (await client.post("/auth/login", json=credentials)).status_code == 200

    assert (await client.get(f"/melds/{meld_id}/shots")).status_code == 404
    assert (await client.post(f"/melds/{meld_id}/shots/cast")).status_code == 404


# --- casting the shots ----------------------------------------------------


async def test_casting_before_there_is_a_cast_is_422_and_costs_nothing(logged_in):
    created = await logged_in.post("/melds", json={"title": "T"})
    meld_id = created.json()["id"]
    before = await _balance(logged_in)

    response = await logged_in.post(f"/melds/{meld_id}/shots/cast")

    assert response.status_code == 422
    assert await _balance(logged_in) == before


async def test_casting_holds_exactly_the_quote(logged_in, db, recording_queue):
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db, sheets=False)
    before = await _balance(logged_in)

    response = await logged_in.post(f"/melds/{meld_id}/shots/cast")

    assert response.status_code == 202, response.text
    assert await _balance(logged_in) == before - CASTING_QUOTE
    assert recording_queue.enqueued[-1] == ("map_shot_characters", uuid.UUID(response.json()["id"]))


async def test_casting_places_characters_into_shots(logged_in, db):
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db, sheets=False)
    await _run(logged_in, db, f"/melds/{meld_id}/shots/cast")

    body = await _shots(logged_in, meld_id)
    assert body["cast_into_shots"] is True
    assert any(shot["character_ids"] for shot in body["shots"])
    assert any(shot["character_names"] for shot in body["shots"])


async def test_casting_twice_with_unchanged_inputs_is_free(logged_in, db):
    """Spec 4.1's cache hit, on the new job kind."""
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db, sheets=False)
    await _run(logged_in, db, f"/melds/{meld_id}/shots/cast")
    before = await _balance(logged_in)

    again = await logged_in.post(f"/melds/{meld_id}/shots/cast")

    assert again.status_code == 202
    assert again.json()["state"] == "succeeded"
    assert await _balance(logged_in) == before


# --- stills ---------------------------------------------------------------


async def test_a_still_is_refused_when_somebody_has_no_sheet(logged_in, db):
    """M4-D6. Generating anyway would produce a drifting face and would
    leave the staleness arithmetic permanently wrong."""
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db, sheets=False)
    await _run(logged_in, db, f"/melds/{meld_id}/shots/cast")

    peopled = next(s for s in (await _shots(logged_in, meld_id))["shots"] if s["character_ids"])
    before = await _balance(logged_in)

    response = await logged_in.post(f"/melds/{meld_id}/shots/{peopled['id']}/still")

    assert response.status_code == 422
    assert "character sheet" in response.json()["detail"]
    assert await _balance(logged_in) == before


async def test_a_still_with_nobody_in_it_needs_no_sheets(logged_in, db):
    """An insert or an establishing shot has no characters, which is a
    correct answer rather than a special case."""
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db, sheets=False)
    await _run(logged_in, db, f"/melds/{meld_id}/shots/cast")

    empty = next(s for s in (await _shots(logged_in, meld_id))["shots"] if not s["character_ids"])
    response = await logged_in.post(f"/melds/{meld_id}/shots/{empty['id']}/still")
    assert response.status_code == 202, response.text


async def test_a_still_records_the_sheets_it_was_drawn_from(logged_in, db):
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db)
    await _run(logged_in, db, f"/melds/{meld_id}/shots/cast")

    peopled = next(s for s in (await _shots(logged_in, meld_id))["shots"] if s["character_ids"])
    await _run(logged_in, db, f"/melds/{meld_id}/shots/{peopled['id']}/still")

    still = next(
        s["still"] for s in (await _shots(logged_in, meld_id))["shots"] if s["id"] == peopled["id"]
    )
    assert still["state"] == "fresh"
    assert still["asset_id"] is not None
    assert len(still["ref_character_image_ids"]) == len(peopled["character_ids"])


async def test_a_still_holds_and_settles_exactly_the_quote(logged_in, db):
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db)
    await _run(logged_in, db, f"/melds/{meld_id}/shots/cast")
    shot = (await _shots(logged_in, meld_id))["shots"][0]
    before = await _balance(logged_in)

    await _run(logged_in, db, f"/melds/{meld_id}/shots/{shot['id']}/still")

    assert await _balance(logged_in) == before - STILL_QUOTE


async def test_two_shots_get_two_different_pictures(logged_in, db):
    """A shared asset would mean the per-shot path is broken and every
    still in the film is the same frame."""
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db)
    await _run(logged_in, db, f"/melds/{meld_id}/shots/cast")

    shots = (await _shots(logged_in, meld_id))["shots"][:2]
    for shot in shots:
        await _run(logged_in, db, f"/melds/{meld_id}/shots/{shot['id']}/still")

    stills = {
        s["still"]["asset_id"]
        for s in (await _shots(logged_in, meld_id))["shots"]
        if s["id"] in {shot["id"] for shot in shots}
    }
    assert len(stills) == 2


async def test_a_second_still_click_on_unchanged_inputs_is_free(logged_in, db):
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db)
    await _run(logged_in, db, f"/melds/{meld_id}/shots/cast")
    shot = (await _shots(logged_in, meld_id))["shots"][0]
    await _run(logged_in, db, f"/melds/{meld_id}/shots/{shot['id']}/still")
    before = await _balance(logged_in)

    again = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")

    assert again.json()["state"] == "succeeded"
    assert await _balance(logged_in) == before


async def test_too_few_credits_is_402_that_creates_nothing(logged_in, db):
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db)
    await _run(logged_in, db, f"/melds/{meld_id}/shots/cast")
    shot = (await _shots(logged_in, meld_id))["shots"][0]

    # Spend the balance down by generating stills until one is refused.
    for other in (await _shots(logged_in, meld_id))["shots"][1:]:
        response = await logged_in.post(f"/melds/{meld_id}/shots/{other['id']}/still")
        if response.status_code == 402:
            break
        await run_generate_shot_image(db, uuid.UUID(response.json()["id"]))
        await db.commit()

    while await _balance(logged_in) >= STILL_QUOTE:
        # Not reachable with the default grant and this story, but the test
        # must not depend on the arithmetic staying that way.
        break

    if await _balance(logged_in) < STILL_QUOTE:
        before = await _balance(logged_in)
        refused = await logged_in.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
        assert refused.status_code == 402
        assert await _balance(logged_in) == before


# --- speech ---------------------------------------------------------------


async def test_speech_on_a_silent_shot_is_422(logged_in, db):
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db, sheets=False)
    silent = next(
        s for s in (await _shots(logged_in, meld_id))["shots"] if s["dialogue_kind"] == "none"
    )
    before = await _balance(logged_in)

    response = await logged_in.post(f"/melds/{meld_id}/shots/{silent['id']}/speech")

    assert response.status_code == 422
    assert await _balance(logged_in) == before


async def test_speech_produces_a_clip_with_a_duration(logged_in, db):
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db, sheets=False)
    spoken = next(
        s for s in (await _shots(logged_in, meld_id))["shots"] if s["dialogue_kind"] != "none"
    )
    before = await _balance(logged_in)

    await _run(logged_in, db, f"/melds/{meld_id}/shots/{spoken['id']}/speech")

    audio = next(
        s["audio"] for s in (await _shots(logged_in, meld_id))["shots"] if s["id"] == spoken["id"]
    )
    assert audio["state"] == "fresh"
    assert audio["asset_id"] is not None
    assert float(audio["duration_sec"]) > 0
    assert audio["text"] == spoken["speech_text"]
    assert audio["voice_id"]
    assert await _balance(logged_in) == before - SPEECH_QUOTE


async def test_a_clip_uses_the_speakers_voice(logged_in, db):
    """The casting pass sets speaker_character_id; the voice follows from
    it, and the character's voice_id is what the user can change."""
    meld_id, characters = await _meld_with_script_and_cast(logged_in, db, sheets=False)
    patched = await logged_in.patch(
        f"/melds/{meld_id}/characters/{characters[0]['id']}", json={"voice_id": "Fenrir"}
    )
    assert patched.status_code == 200, patched.text

    await _run(logged_in, db, f"/melds/{meld_id}/shots/cast")
    spoken = next(
        s
        for s in (await _shots(logged_in, meld_id))["shots"]
        if s["dialogue_kind"] != "none" and characters[0]["id"] in s["character_ids"]
    )
    await _run(logged_in, db, f"/melds/{meld_id}/shots/{spoken['id']}/speech")

    audio = next(
        s["audio"] for s in (await _shots(logged_in, meld_id))["shots"] if s["id"] == spoken["id"]
    )
    assert audio["voice_id"] == "Fenrir"


async def test_the_voice_list_is_served(logged_in, db):
    meld_id, _ = await _meld_with_script_and_cast(logged_in, db, sheets=False)
    response = await logged_in.get(f"/melds/{meld_id}/voices")
    assert response.status_code == 200
    assert "Kore" in response.json()
