async def _new_meld(logged_in):
    created = await logged_in.post("/melds", json={"title": "T"})
    assert created.status_code == 201, created.text
    meld_id = created.json()["id"]
    detail = await logged_in.get(f"/melds/{meld_id}")
    return meld_id, detail.json()["story"]["content_hash"]


async def test_a_matching_precondition_saves(logged_in):
    meld_id, hash_ = await _new_meld(logged_in)
    response = await logged_in.put(
        f"/melds/{meld_id}/story", json={"text": "Once.", "if_content_hash": hash_}
    )
    assert response.status_code == 200
    assert response.json()["content_hash"] != hash_


async def test_a_stale_precondition_is_refused(logged_in):
    """The two-tab case: the second tab is still holding the hash it loaded
    before the first tab saved."""
    meld_id, hash_ = await _new_meld(logged_in)
    first = await logged_in.put(
        f"/melds/{meld_id}/story", json={"text": "Tab one.", "if_content_hash": hash_}
    )
    assert first.status_code == 200

    second = await logged_in.put(
        f"/melds/{meld_id}/story", json={"text": "Tab two.", "if_content_hash": hash_}
    )
    assert second.status_code == 409


async def test_a_refused_save_does_not_change_the_stored_text(logged_in):
    """A 409 that still wrote would be worse than no precondition at all."""
    meld_id, hash_ = await _new_meld(logged_in)
    await logged_in.put(
        f"/melds/{meld_id}/story", json={"text": "Tab one.", "if_content_hash": hash_}
    )
    await logged_in.put(
        f"/melds/{meld_id}/story", json={"text": "Tab two.", "if_content_hash": hash_}
    )

    detail = await logged_in.get(f"/melds/{meld_id}")
    assert detail.json()["story"]["text"] == "Tab one."


async def test_the_precondition_is_optional(logged_in):
    """Omitting it keeps the M1 behaviour, which the first save after a page
    load relies on."""
    meld_id, _ = await _new_meld(logged_in)
    response = await logged_in.put(f"/melds/{meld_id}/story", json={"text": "Once."})
    assert response.status_code == 200


async def test_the_precondition_does_not_leak_other_users_melds(client, logged_in):
    """A wrong hash on someone else's Meld must still be 404, not 409 --
    a 409 would confirm the Meld exists."""
    meld_id, _ = await _new_meld(logged_in)
    await logged_in.post("/auth/logout")
    await client.post(
        "/auth/signup", json={"email": "other@example.com", "password": "hunter2hunter2"}
    )
    await client.post(
        "/auth/login", json={"email": "other@example.com", "password": "hunter2hunter2"}
    )

    response = await client.put(
        f"/melds/{meld_id}/story", json={"text": "Mine now.", "if_content_hash": "0" * 64}
    )
    assert response.status_code == 404
