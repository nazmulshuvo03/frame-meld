import uuid

STORY = (
    "Elena found the letter beneath the floorboard. She read it twice.\n\n"
    "Outside, the rain began. She burned the letter in the sink."
)


async def _meld_with_story(logged_in, text=STORY):
    created = await logged_in.post("/melds", json={"title": "T"})
    meld_id = created.json()["id"]
    saved = await logged_in.put(f"/melds/{meld_id}/story", json={"text": text})
    assert saved.status_code == 200, saved.text
    return meld_id


async def _generate(logged_in, meld_id):
    response = await logged_in.post(f"/melds/{meld_id}/script/generate")
    assert response.status_code == 202, response.text
    return response.json()


async def test_a_new_meld_has_an_empty_script(logged_in):
    meld_id = await _meld_with_story(logged_in)
    body = (await logged_in.get(f"/melds/{meld_id}/script")).json()
    assert body["scenes"] == []
    assert body["stale_scenes"] == 0


async def test_generating_queues_exactly_one_job(logged_in, recording_queue):
    meld_id = await _meld_with_story(logged_in)
    await _generate(logged_in, meld_id)
    assert len(recording_queue.enqueued) == 1
    assert recording_queue.enqueued[0][0] == "generate_script"


async def test_clicking_generate_twice_queues_once(logged_in, recording_queue):
    """Spec section 5.4 in the shape the user meets it: a double click."""
    meld_id = await _meld_with_story(logged_in)
    first = await _generate(logged_in, meld_id)
    second = await _generate(logged_in, meld_id)

    assert second["id"] == first["id"]
    assert len(recording_queue.enqueued) == 1


async def test_editing_the_story_makes_generate_a_new_job(logged_in, recording_queue):
    meld_id = await _meld_with_story(logged_in)
    first = await _generate(logged_in, meld_id)
    await logged_in.put(f"/melds/{meld_id}/story", json={"text": "A different story."})
    second = await _generate(logged_in, meld_id)

    assert second["id"] != first["id"]
    assert len(recording_queue.enqueued) == 2


async def test_generating_from_an_empty_story_is_refused_immediately(logged_in, recording_queue):
    """Better a 422 the user can read than a queued job that burns a
    free-tier request to fail."""
    created = await logged_in.post("/melds", json={"title": "T"})
    meld_id = created.json()["id"]

    response = await logged_in.post(f"/melds/{meld_id}/script/generate")
    assert response.status_code == 422
    assert recording_queue.enqueued == []


async def test_the_job_can_be_polled(logged_in):
    meld_id = await _meld_with_story(logged_in)
    job = await _generate(logged_in, meld_id)
    polled = await logged_in.get(f"/melds/{meld_id}/jobs/{job['id']}")
    assert polled.status_code == 200
    assert polled.json()["state"] == "queued"


async def test_the_script_is_readable_after_the_worker_runs(logged_in, db):
    from framemeld.worker import run_generate_script

    meld_id = await _meld_with_story(logged_in)
    job = await _generate(logged_in, meld_id)
    await run_generate_script(db, uuid.UUID(job["id"]))

    body = (await logged_in.get(f"/melds/{meld_id}/script")).json()
    assert len(body["scenes"]) == 2
    assert body["scenes"][0]["shots"]
    assert body["scenes"][0]["state"] == "fresh"
    first_shot = body["scenes"][0]["shots"][0]
    assert set(first_shot) >= {
        "id",
        "order_index",
        "image_prompt",
        "motion_prompt",
        "duration_sec",
        "camera",
        "dialogue_kind",
        "speech_text",
        "state",
        "content_hash",
    }


async def test_editing_a_shot_changes_its_content_hash_but_not_its_state(logged_in, db):
    """D4: the row holds what the user asked for, so it is not out of date.
    Its content_hash moves, which is what M4's images will notice."""
    from framemeld.worker import run_generate_script

    meld_id = await _meld_with_story(logged_in)
    job = await _generate(logged_in, meld_id)
    await run_generate_script(db, uuid.UUID(job["id"]))

    before = (await logged_in.get(f"/melds/{meld_id}/script")).json()["scenes"][0]["shots"][0]
    patched = await logged_in.patch(
        f"/melds/{meld_id}/shots/{before['id']}",
        json={"image_prompt": "A different frame entirely."},
    )
    assert patched.status_code == 200
    assert patched.json()["content_hash"] != before["content_hash"]
    assert patched.json()["state"] == before["state"] == "fresh"


async def test_a_partial_patch_leaves_other_fields_alone(logged_in, db):
    from framemeld.worker import run_generate_script

    meld_id = await _meld_with_story(logged_in)
    job = await _generate(logged_in, meld_id)
    await run_generate_script(db, uuid.UUID(job["id"]))

    before = (await logged_in.get(f"/melds/{meld_id}/script")).json()["scenes"][0]["shots"][0]
    patched = (
        await logged_in.patch(f"/melds/{meld_id}/shots/{before['id']}", json={"camera": "close"})
    ).json()
    assert patched["camera"] == "close"
    assert patched["image_prompt"] == before["image_prompt"]
    assert patched["motion_prompt"] == before["motion_prompt"]


async def test_a_shot_longer_than_five_seconds_is_refused(logged_in, db):
    from framemeld.worker import run_generate_script

    meld_id = await _meld_with_story(logged_in)
    job = await _generate(logged_in, meld_id)
    await run_generate_script(db, uuid.UUID(job["id"]))

    shot = (await logged_in.get(f"/melds/{meld_id}/script")).json()["scenes"][0]["shots"][0]
    response = await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}", json={"duration_sec": "5.5"}
    )
    # 422, not a 500 from the database CHECK.
    assert response.status_code == 422


async def test_editing_the_story_marks_the_script_stale_over_the_api(logged_in, db):
    """The milestone's done-when, end to end."""
    from framemeld.worker import run_generate_script

    meld_id = await _meld_with_story(logged_in)
    job = await _generate(logged_in, meld_id)
    await run_generate_script(db, uuid.UUID(job["id"]))

    await logged_in.put(f"/melds/{meld_id}/story", json={"text": "She never found it."})

    body = (await logged_in.get(f"/melds/{meld_id}/script")).json()
    assert body["stale_scenes"] == 2
    assert body["stale_shots"] > 0
    assert all(scene["state"] == "stale" for scene in body["scenes"])
    # Not destroyed: spec section 4.4.
    assert body["scenes"][0]["shots"][0]["image_prompt"]


async def test_another_users_script_is_not_found(client, logged_in, db):
    from framemeld.worker import run_generate_script

    meld_id = await _meld_with_story(logged_in)
    job = await _generate(logged_in, meld_id)
    await run_generate_script(db, uuid.UUID(job["id"]))
    shot = (await logged_in.get(f"/melds/{meld_id}/script")).json()["scenes"][0]["shots"][0]

    await logged_in.post("/auth/logout")
    await client.post(
        "/auth/signup", json={"email": "thief@example.com", "password": "hunter2hunter2"}
    )
    await client.post(
        "/auth/login", json={"email": "thief@example.com", "password": "hunter2hunter2"}
    )

    # 404 everywhere, never 403: a 403 would confirm the rows exist.
    assert (await client.get(f"/melds/{meld_id}/script")).status_code == 404
    assert (await client.post(f"/melds/{meld_id}/script/generate")).status_code == 404
    assert (
        await client.patch(f"/melds/{meld_id}/shots/{shot['id']}", json={"camera": "wide"})
    ).status_code == 404


async def test_the_script_endpoints_need_a_session(client):
    meld_id = str(uuid.uuid4())
    assert (await client.get(f"/melds/{meld_id}/script")).status_code == 401
    assert (await client.post(f"/melds/{meld_id}/script/generate")).status_code == 401


async def test_a_shot_from_another_meld_is_not_found(logged_in, db):
    """The shot id is real and the Meld id is real, but they do not belong
    together. Scoping only by shot id would let this through."""
    from framemeld.worker import run_generate_script

    meld_a = await _meld_with_story(logged_in)
    job = await _generate(logged_in, meld_a)
    await run_generate_script(db, uuid.UUID(job["id"]))
    shot = (await logged_in.get(f"/melds/{meld_a}/script")).json()["scenes"][0]["shots"][0]

    meld_b = await _meld_with_story(logged_in, text="Another story entirely.")
    response = await logged_in.patch(f"/melds/{meld_b}/shots/{shot['id']}", json={"camera": "wide"})
    assert response.status_code == 404


async def test_editing_a_scene_changes_its_content_hash_but_not_its_state(logged_in, db):
    """The scene half of D4, and the only thing standing behind the
    SceneOut/SceneDetail split: SceneOut deliberately omits `shots` because
    update_scene does not eager-load them, and a response model that
    declared them would lazy-load under asyncio and 500."""
    from framemeld.worker import run_generate_script

    meld_id = await _meld_with_story(logged_in)
    job = await _generate(logged_in, meld_id)
    await run_generate_script(db, uuid.UUID(job["id"]))

    before = (await logged_in.get(f"/melds/{meld_id}/script")).json()["scenes"][0]
    patched = await logged_in.patch(
        f"/melds/{meld_id}/scenes/{before['id']}",
        json={"heading": "EXT. ROADSIDE - DAWN"},
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["heading"] == "EXT. ROADSIDE - DAWN"
    assert patched.json()["content_hash"] != before["content_hash"]
    assert patched.json()["state"] == before["state"] == "fresh"
    # The PATCH response carries the scene's own fields only.
    assert "shots" not in patched.json()

    # The shots survived an edit to their scene, and the listing still
    # returns them.
    after = (await logged_in.get(f"/melds/{meld_id}/script")).json()["scenes"][0]
    assert len(after["shots"]) == len(before["shots"])


async def test_a_scene_from_another_meld_is_not_found(logged_in, db):
    """A real scene id paired with a Meld that does not own it."""
    from framemeld.worker import run_generate_script

    meld_a = await _meld_with_story(logged_in)
    job = await _generate(logged_in, meld_a)
    await run_generate_script(db, uuid.UUID(job["id"]))
    scene = (await logged_in.get(f"/melds/{meld_a}/script")).json()["scenes"][0]

    meld_b = await _meld_with_story(logged_in, text="Another story entirely.")
    response = await logged_in.patch(
        f"/melds/{meld_b}/scenes/{scene['id']}", json={"heading": "Mine now."}
    )
    assert response.status_code == 404


async def test_a_job_from_another_meld_is_not_found(logged_in):
    """Without the meld_id clause in get_job, a job id alone is enough for
    any logged-in user to poll anyone's job."""
    meld_a = await _meld_with_story(logged_in)
    job = await _generate(logged_in, meld_a)

    meld_b = await _meld_with_story(logged_in, text="Another story entirely.")
    response = await logged_in.get(f"/melds/{meld_b}/jobs/{job['id']}")
    assert response.status_code == 404


async def test_duration_survives_a_round_trip_unchanged(logged_in, db):
    """duration_sec crosses the wire as a string because a binary float
    cannot hold 4.05 exactly. The client sends the value straight back on
    the next edit, so a float round trip would change it and the shot would
    look edited when it was not."""
    from framemeld.worker import run_generate_script

    meld_id = await _meld_with_story(logged_in)
    job = await _generate(logged_in, meld_id)
    await run_generate_script(db, uuid.UUID(job["id"]))

    shot = (await logged_in.get(f"/melds/{meld_id}/script")).json()["scenes"][0]["shots"][0]
    assert isinstance(shot["duration_sec"], str)

    # Send it straight back, untouched, exactly as the editor does.
    patched = await logged_in.patch(
        f"/melds/{meld_id}/shots/{shot['id']}",
        json={"duration_sec": shot["duration_sec"]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["duration_sec"] == shot["duration_sec"]
    # Unchanged value, unchanged hash: re-sending what you were given is
    # not an edit.
    assert patched.json()["content_hash"] == shot["content_hash"]
