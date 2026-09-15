"""M4's headline promise: editing a character marks exactly the right
stills stale, and nothing else.

Every test here drives the real endpoints and the real worker, because the
two bugs M3's browser pass found were both invisible from unit tests of the
pieces — a hash that was right and a caller that never ran it.
"""

import uuid

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


async def _ready(client, db):
    """A Meld with a script, a cast, a sheet each, and the shots cast."""
    created = await client.post("/melds", json={"title": "T"})
    meld_id = created.json()["id"]
    assert (await client.put(f"/melds/{meld_id}/story", json={"text": STORY})).status_code == 200

    script = await client.post(f"/melds/{meld_id}/script/generate")
    await run_generate_script(db, uuid.UUID(script.json()["id"]))
    await db.commit()

    cast = await client.post(f"/melds/{meld_id}/cast/generate")
    await run_generate_characters(db, uuid.UUID(cast.json()["id"]))
    await db.commit()

    characters = (await client.get(f"/melds/{meld_id}/cast")).json()["characters"]
    for character in characters:
        sheet = await client.post(f"/melds/{meld_id}/characters/{character['id']}/sheet")
        await run_generate_character_image(db, uuid.UUID(sheet.json()["id"]))
        await db.commit()

    casting = await client.post(f"/melds/{meld_id}/shots/cast")
    await run_map_shot_characters(db, uuid.UUID(casting.json()["id"]))
    await db.commit()

    return meld_id, characters


async def _shots(client, meld_id):
    return (await client.get(f"/melds/{meld_id}/shots")).json()


async def _still_for(client, db, meld_id, shot):
    response = await client.post(f"/melds/{meld_id}/shots/{shot['id']}/still")
    assert response.status_code == 202, response.text
    await run_generate_shot_image(db, uuid.UUID(response.json()["id"]))
    await db.commit()


async def _speech_for(client, db, meld_id, shot):
    response = await client.post(f"/melds/{meld_id}/shots/{shot['id']}/speech")
    assert response.status_code == 202, response.text
    await run_generate_speech(db, uuid.UUID(response.json()["id"]))
    await db.commit()


def _by_id(body, shot_id):
    return next(shot for shot in body["shots"] if shot["id"] == shot_id)


async def test_redrawing_a_sheet_marks_that_characters_stills_stale(logged_in, db):
    """The link spec 4.1 calls out by name: the case that otherwise
    silently ships a film where a face changes halfway through."""
    meld_id, _ = await _ready(logged_in, db)

    peopled = next(s for s in (await _shots(logged_in, meld_id))["shots"] if s["character_ids"])
    await _still_for(logged_in, db, meld_id, peopled)
    assert _by_id(await _shots(logged_in, meld_id), peopled["id"])["still"]["state"] == "fresh"

    # Change the character's appearance, which moves their sheet's
    # content_hash once it is redrawn.
    who = peopled["character_ids"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/characters/{who}", json={"appearance": "Completely different now."}
    )
    redraw = await logged_in.post(f"/melds/{meld_id}/characters/{who}/sheet")
    await run_generate_character_image(db, uuid.UUID(redraw.json()["id"]))
    await db.commit()

    body = await _shots(logged_in, meld_id)
    assert _by_id(body, peopled["id"])["still"]["state"] == "stale"
    assert body["stale_stills"] >= 1
    # Still displayed, never deleted -- spec 4.1's rule about stale rows.
    assert _by_id(body, peopled["id"])["still"]["asset_id"] is not None


async def test_a_character_change_leaves_other_shots_alone(logged_in, db):
    """ "Exactly the right stills" is half the promise. A change that marked
    every still stale would be just as wrong as one that marked none."""
    meld_id, _ = await _ready(logged_in, db)
    body = await _shots(logged_in, meld_id)

    peopled = next(s for s in body["shots"] if s["character_ids"])
    empty = next(s for s in body["shots"] if not s["character_ids"])
    await _still_for(logged_in, db, meld_id, peopled)
    await _still_for(logged_in, db, meld_id, empty)

    who = peopled["character_ids"][0]
    await logged_in.patch(
        f"/melds/{meld_id}/characters/{who}", json={"appearance": "Completely different now."}
    )
    redraw = await logged_in.post(f"/melds/{meld_id}/characters/{who}/sheet")
    await run_generate_character_image(db, uuid.UUID(redraw.json()["id"]))
    await db.commit()

    after = await _shots(logged_in, meld_id)
    assert _by_id(after, peopled["id"])["still"]["state"] == "stale"
    assert _by_id(after, empty["id"])["still"]["state"] == "fresh"


async def test_editing_the_image_prompt_marks_the_still_stale(logged_in, db):
    meld_id, _ = await _ready(logged_in, db)
    shot = (await _shots(logged_in, meld_id))["shots"][0]
    await _still_for(logged_in, db, meld_id, shot)

    patched = await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "Something else entirely."}
    )
    assert patched.status_code == 200, patched.text

    assert _by_id(await _shots(logged_in, meld_id), shot["id"])["still"]["state"] == "stale"


async def test_editing_the_motion_prompt_does_not_mark_the_still_stale(logged_in, db):
    """M4-D2's payoff, and the M3 lesson repeating: the still prompt never
    reads motion_prompt, so an edit there must cost nothing."""
    meld_id, _ = await _ready(logged_in, db)
    shot = (await _shots(logged_in, meld_id))["shots"][0]
    await _still_for(logged_in, db, meld_id, shot)

    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"motion_prompt": "Hold completely still."}
    )

    assert _by_id(await _shots(logged_in, meld_id), shot["id"])["still"]["state"] == "fresh"


async def test_editing_the_line_marks_the_audio_stale_and_the_still_fresh(logged_in, db):
    """The two projections, proved against the database rather than
    reasoned about: one edit must move exactly one artifact."""
    meld_id, _ = await _ready(logged_in, db)
    spoken = next(
        s for s in (await _shots(logged_in, meld_id))["shots"] if s["dialogue_kind"] != "none"
    )
    await _still_for(logged_in, db, meld_id, spoken)
    await _speech_for(logged_in, db, meld_id, spoken)

    await logged_in.patch(
        f"/melds/{meld_id}/shots/{spoken['id']}", json={"speech_text": "A completely new line."}
    )

    after = _by_id(await _shots(logged_in, meld_id), spoken["id"])
    assert after["audio"]["state"] == "stale"
    assert after["still"]["state"] == "fresh"


async def test_editing_the_frame_marks_the_still_stale_and_the_audio_fresh(logged_in, db):
    meld_id, _ = await _ready(logged_in, db)
    spoken = next(
        s for s in (await _shots(logged_in, meld_id))["shots"] if s["dialogue_kind"] != "none"
    )
    await _still_for(logged_in, db, meld_id, spoken)
    await _speech_for(logged_in, db, meld_id, spoken)

    await logged_in.patch(
        f"/melds/{meld_id}/shots/{spoken['id']}", json={"image_prompt": "A different frame."}
    )

    after = _by_id(await _shots(logged_in, meld_id), spoken["id"])
    assert after["still"]["state"] == "stale"
    assert after["audio"]["state"] == "fresh"


async def test_changing_a_voice_marks_their_lines_stale_and_their_sheet_fresh(logged_in, db):
    """voice_id is inside content_hash_for_character and outside
    visual_hash_for_character, so this works with no new machinery -- and
    breaking that arrangement is exactly what this pins."""
    meld_id, _ = await _ready(logged_in, db)
    spoken = next(
        s
        for s in (await _shots(logged_in, meld_id))["shots"]
        if s["dialogue_kind"] != "none" and s["character_ids"]
    )
    await _speech_for(logged_in, db, meld_id, spoken)

    speaker = spoken["character_ids"][0]
    patched = await logged_in.patch(
        f"/melds/{meld_id}/characters/{speaker}", json={"voice_id": "Fenrir"}
    )
    assert patched.status_code == 200, patched.text

    assert _by_id(await _shots(logged_in, meld_id), spoken["id"])["audio"]["state"] == "stale"
    sheet = next(
        c["sheet"]
        for c in (await logged_in.get(f"/melds/{meld_id}/cast")).json()["characters"]
        if c["id"] == speaker
    )
    assert sheet["state"] == "fresh"


async def test_reverting_an_edit_clears_the_stale_badge_for_free(logged_in, db):
    """Staleness runs in both directions, which is what makes "nothing is
    deleted" a real promise rather than a consolation."""
    meld_id, _ = await _ready(logged_in, db)
    shot = (await _shots(logged_in, meld_id))["shots"][0]
    original = shot["image_prompt"]
    await _still_for(logged_in, db, meld_id, shot)

    await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": "Something else."}
    )
    assert _by_id(await _shots(logged_in, meld_id), shot["id"])["still"]["state"] == "stale"

    await logged_in.patch(f"/melds/{meld_id}/shots/{shot['id']}", json={"image_prompt": original})

    assert _by_id(await _shots(logged_in, meld_id), shot["id"])["still"]["state"] == "fresh"


async def test_regenerating_the_script_takes_the_audio_with_it(logged_in, db):
    """audio_clips.owner_id is polymorphic and carries no foreign key, so
    nothing cascades it. Without the hand-written delete in apply_script,
    every recording survives a script regeneration pointing at a shot id
    that no longer resolves."""
    from sqlalchemy import func, select

    from framemeld.models.shots import AudioClip

    meld_id, _ = await _ready(logged_in, db)
    spoken = next(
        s for s in (await _shots(logged_in, meld_id))["shots"] if s["dialogue_kind"] != "none"
    )
    await _speech_for(logged_in, db, meld_id, spoken)
    assert await db.scalar(select(func.count()).select_from(AudioClip)) == 1

    await logged_in.put(f"/melds/{meld_id}/story", json={"text": STORY + "\n\nAnd then it ended."})
    again = await logged_in.post(f"/melds/{meld_id}/script/generate")
    await run_generate_script(db, uuid.UUID(again.json()["id"]))
    await db.commit()

    assert await db.scalar(select(func.count()).select_from(AudioClip)) == 0
