import uuid

from sqlalchemy import select

from framemeld.credits.pricing import QUOTES
from framemeld.models.job import Job, JobKind
from framemeld.worker import run_generate_characters

STORY = (
    "Elena found the letter beneath the floorboard. She read it twice.\n\n"
    "Outside, the rain began. Marcus watched her from the doorway."
)

CAST_QUOTE = QUOTES[JobKind.GENERATE_CHARACTERS]
SHEET_QUOTE = QUOTES[JobKind.GENERATE_CHARACTER_IMAGE]


async def _meld_with_story(client, text=STORY):
    created = await client.post("/melds", json={"title": "T"})
    assert created.status_code == 201, created.text
    meld_id = created.json()["id"]
    saved = await client.put(f"/melds/{meld_id}/story", json={"text": text})
    assert saved.status_code == 200, saved.text
    return meld_id


async def _balance(client) -> int:
    return (await client.get("/credits")).json()["balance"]


async def _cast(logged_in, db, monkeypatch):
    """A Meld whose extraction job has actually run, so there are characters
    to ask sheets for."""
    meld_id = await _meld_with_story(logged_in)
    response = await logged_in.post(f"/melds/{meld_id}/cast/generate")
    assert response.status_code == 202, response.text

    job_id = uuid.UUID(response.json()["id"])
    outcome = await run_generate_characters(db, job_id)
    assert outcome.state.value == "succeeded"
    await db.commit()

    body = (await logged_in.get(f"/melds/{meld_id}/cast")).json()
    assert body["characters"], body
    return meld_id, body["characters"][0]["id"]


async def _login_as_second_user(client, email="stranger-cast@example.com"):
    credentials = {"email": email, "password": "hunter2hunter2"}
    assert (await client.post("/auth/signup", json=credentials)).status_code == 201
    assert (await client.post("/auth/login", json=credentials)).status_code == 200


async def test_a_new_meld_has_an_empty_cast(logged_in):
    meld_id = await _meld_with_story(logged_in)
    body = (await logged_in.get(f"/melds/{meld_id}/cast")).json()
    assert body["characters"] == []
    assert body["stale_characters"] == 0
    assert body["stale_sheets"] == 0


async def test_generating_holds_exactly_the_quoted_credits(logged_in, recording_queue):
    meld_id = await _meld_with_story(logged_in)
    before = await _balance(logged_in)

    response = await logged_in.post(f"/melds/{meld_id}/cast/generate")

    assert response.status_code == 202, response.text
    assert await _balance(logged_in) == before - CAST_QUOTE
    assert recording_queue.enqueued == [
        (
            "generate_characters",
            uuid.UUID(response.json()["id"]),
        )
    ]


async def test_clicking_generate_twice_queues_once_and_charges_once(logged_in, recording_queue):
    """A double click must not cost twice. The job's idempotency key is the
    guard, and the hold is only taken when work will actually happen."""
    meld_id = await _meld_with_story(logged_in)
    before = await _balance(logged_in)

    first = await logged_in.post(f"/melds/{meld_id}/cast/generate")
    second = await logged_in.post(f"/melds/{meld_id}/cast/generate")

    assert second.json()["id"] == first.json()["id"]
    assert len(recording_queue.enqueued) == 1
    assert await _balance(logged_in) == before - CAST_QUOTE


async def test_a_user_who_cannot_pay_gets_402_and_is_charged_nothing(logged_in, db, monkeypatch):
    """402, not 403: the request is authorised, it just cannot be paid for.
    Nothing may be left behind -- no job row, no ledger row."""
    monkeypatch.setitem(QUOTES, JobKind.GENERATE_CHARACTERS, 10_000)
    meld_id = await _meld_with_story(logged_in)
    before = await _balance(logged_in)

    response = await logged_in.post(f"/melds/{meld_id}/cast/generate")

    assert response.status_code == 402
    assert "10000" in response.json()["detail"]
    assert await _balance(logged_in) == before
    assert (await db.execute(select(Job))).scalars().all() == []


async def test_generating_from_an_empty_story_is_422_before_any_hold(logged_in):
    created = await logged_in.post("/melds", json={"title": "T"})
    meld_id = created.json()["id"]
    await logged_in.put(f"/melds/{meld_id}/story", json={"text": "   "})
    before = await _balance(logged_in)

    response = await logged_in.post(f"/melds/{meld_id}/cast/generate")

    assert response.status_code == 422
    assert await _balance(logged_in) == before


async def test_reading_another_users_cast_is_404(logged_in, client):
    meld_id = await _meld_with_story(logged_in)
    await _login_as_second_user(client)

    assert (await client.get(f"/melds/{meld_id}/cast")).status_code == 404


async def test_generating_another_users_cast_is_404(logged_in, client):
    meld_id = await _meld_with_story(logged_in)
    await _login_as_second_user(client, "stranger-cast2@example.com")

    assert (await client.post(f"/melds/{meld_id}/cast/generate")).status_code == 404


async def test_the_cast_requires_authentication(client):
    assert (await client.get(f"/melds/{uuid.uuid4()}/cast")).status_code == 401


async def test_a_sheet_holds_the_image_quote(logged_in, db, monkeypatch, recording_queue):
    meld_id, character_id = await _cast(logged_in, db, monkeypatch)
    before = await _balance(logged_in)
    recording_queue.enqueued.clear()

    response = await logged_in.post(f"/melds/{meld_id}/characters/{character_id}/sheet")

    assert response.status_code == 202, response.text
    assert await _balance(logged_in) == before - SHEET_QUOTE
    assert recording_queue.enqueued[0][0] == "generate_character_image"


async def test_asking_for_the_same_sheet_twice_charges_once(logged_in, db, monkeypatch):
    """Spec 4.1: running a generation again with an unchanged hash is
    instant and costs zero. If this regresses, every user is charged twice
    for pressing a button twice."""
    meld_id, character_id = await _cast(logged_in, db, monkeypatch)
    before = await _balance(logged_in)

    first = await logged_in.post(f"/melds/{meld_id}/characters/{character_id}/sheet")
    second = await logged_in.post(f"/melds/{meld_id}/characters/{character_id}/sheet")

    assert second.json()["id"] == first.json()["id"]
    assert await _balance(logged_in) == before - SHEET_QUOTE


async def test_a_sheet_for_a_character_with_no_appearance_is_422(logged_in, db, monkeypatch):
    """An image call is the expensive one, and 'draw nothing' is a prompt a
    real model answers with something arbitrary rather than an error."""
    meld_id, character_id = await _cast(logged_in, db, monkeypatch)
    patched = await logged_in.patch(
        f"/melds/{meld_id}/characters/{character_id}", json={"appearance": "  "}
    )
    assert patched.status_code == 200, patched.text
    before = await _balance(logged_in)

    response = await logged_in.post(f"/melds/{meld_id}/characters/{character_id}/sheet")

    assert response.status_code == 422
    assert await _balance(logged_in) == before


async def test_a_sheet_for_another_users_character_is_404(logged_in, db, monkeypatch, client):
    meld_id, character_id = await _cast(logged_in, db, monkeypatch)
    await _login_as_second_user(client, "stranger-sheet@example.com")

    response = await client.post(f"/melds/{meld_id}/characters/{character_id}/sheet")
    assert response.status_code == 404


async def test_editing_a_character_changes_its_hash_and_not_its_state(logged_in, db, monkeypatch):
    """D4 still holds here: a row the user just edited holds what the user
    wants, so it is not out of date."""
    meld_id, character_id = await _cast(logged_in, db, monkeypatch)
    before = (await logged_in.get(f"/melds/{meld_id}/cast")).json()["characters"][0]

    response = await logged_in.patch(
        f"/melds/{meld_id}/characters/{character_id}",
        json={"appearance": "A woman in her thirties, close-cropped hair."},
    )

    assert response.status_code == 200, response.text
    after = response.json()
    assert after["content_hash"] != before["content_hash"]
    assert after["state"] == before["state"]
    assert after["appearance"].startswith("A woman in her thirties")


async def test_an_edit_survives_a_reload(logged_in, db, monkeypatch):
    meld_id, character_id = await _cast(logged_in, db, monkeypatch)
    await logged_in.patch(
        f"/melds/{meld_id}/characters/{character_id}", json={"name": "Elena Vasquez"}
    )

    body = (await logged_in.get(f"/melds/{meld_id}/cast")).json()
    assert body["characters"][0]["name"] == "Elena Vasquez"


async def test_patching_another_users_character_is_404(logged_in, db, monkeypatch, client):
    meld_id, character_id = await _cast(logged_in, db, monkeypatch)
    await _login_as_second_user(client, "stranger-patch@example.com")

    response = await client.patch(
        f"/melds/{meld_id}/characters/{character_id}", json={"name": "Mine now"}
    )
    assert response.status_code == 404


async def test_an_explicit_null_is_a_422_not_a_500(logged_in, db, monkeypatch):
    meld_id, character_id = await _cast(logged_in, db, monkeypatch)

    response = await logged_in.patch(
        f"/melds/{meld_id}/characters/{character_id}", json={"name": None}
    )
    assert response.status_code == 422


async def test_an_unknown_field_is_refused_rather_than_silently_dropped(logged_in, db, monkeypatch):
    meld_id, character_id = await _cast(logged_in, db, monkeypatch)

    response = await logged_in.patch(
        f"/melds/{meld_id}/characters/{character_id}", json={"favourite_colour": "blue"}
    )
    assert response.status_code == 422


async def test_the_cast_listing_carries_the_sheet(logged_in, db, monkeypatch):
    meld_id, character_id = await _cast(logged_in, db, monkeypatch)
    await logged_in.post(f"/melds/{meld_id}/characters/{character_id}/sheet")

    body = (await logged_in.get(f"/melds/{meld_id}/cast")).json()
    sheet = body["characters"][0]["sheet"]
    assert sheet is not None
    assert sheet["kind"] == "sheet"
    assert sheet["state"] == "generating"
    # No asset until the job has actually run.
    assert sheet["asset_id"] is None
    assert body["characters"][1]["sheet"] is None


async def test_a_character_belonging_to_another_meld_is_404(logged_in, db, monkeypatch):
    """Selecting by character id alone would let a caller edit any
    character in the system as long as they owned some Meld."""
    _meld_id, character_id = await _cast(logged_in, db, monkeypatch)
    other_meld_id = await _meld_with_story(logged_in, "A different story entirely.")

    response = await logged_in.patch(
        f"/melds/{other_meld_id}/characters/{character_id}", json={"name": "Nope"}
    )
    assert response.status_code == 404
