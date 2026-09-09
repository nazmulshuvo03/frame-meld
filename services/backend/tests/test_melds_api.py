import uuid


async def test_create_meld_returns_201_with_a_default_title(logged_in):
    response = await logged_in.post("/melds", json={})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Untitled Meld"
    assert body["stage"] == "story"
    assert body["status"] == "draft"


async def test_create_meld_accepts_a_title(logged_in):
    response = await logged_in.post("/melds", json={"title": "The Lighthouse"})
    assert response.status_code == 201
    assert response.json()["title"] == "The Lighthouse"


async def test_create_meld_requires_authentication(client):
    response = await client.post("/melds", json={})
    assert response.status_code == 401


async def test_list_melds_returns_newest_first(logged_in):
    await logged_in.post("/melds", json={"title": "First"})
    await logged_in.post("/melds", json={"title": "Second"})

    response = await logged_in.get("/melds")
    assert response.status_code == 200
    assert [m["title"] for m in response.json()] == ["Second", "First"]


async def test_list_melds_requires_authentication(client):
    assert (await client.get("/melds")).status_code == 401


async def test_get_meld_includes_the_story(logged_in):
    created = (await logged_in.post("/melds", json={"title": "With Story"})).json()

    response = await logged_in.get(f"/melds/{created['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "With Story"
    assert body["story"]["text"] == ""
    assert len(body["story"]["input_hash"]) == 64


async def test_get_meld_returns_404_for_an_unknown_id(logged_in):
    response = await logged_in.get(f"/melds/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_get_meld_returns_404_for_another_users_meld(client):
    """404 rather than 403 — the response must not confirm that the Meld
    exists at all."""
    await client.post("/auth/signup", json={"email": "alice@example.com", "password": "hunter2hunter2"})
    await client.post("/auth/login", json={"email": "alice@example.com", "password": "hunter2hunter2"})
    created = (await client.post("/melds", json={"title": "Alice's"})).json()
    await client.post("/auth/logout")

    await client.post("/auth/signup", json={"email": "bob@example.com", "password": "hunter2hunter2"})
    await client.post("/auth/login", json={"email": "bob@example.com", "password": "hunter2hunter2"})

    response = await client.get(f"/melds/{created['id']}")
    assert response.status_code == 404


async def test_rename_meld(logged_in):
    created = (await logged_in.post("/melds", json={"title": "Old"})).json()

    response = await logged_in.patch(f"/melds/{created['id']}", json={"title": "New"})
    assert response.status_code == 200
    assert response.json()["title"] == "New"


async def test_rename_rejects_an_empty_title(logged_in):
    created = (await logged_in.post("/melds", json={"title": "Old"})).json()
    response = await logged_in.patch(f"/melds/{created['id']}", json={"title": ""})
    assert response.status_code == 422


async def test_delete_meld_removes_it_from_the_list(logged_in):
    created = (await logged_in.post("/melds", json={"title": "Doomed"})).json()

    assert (await logged_in.delete(f"/melds/{created['id']}")).status_code == 204
    assert (await logged_in.get("/melds")).json() == []
    assert (await logged_in.get(f"/melds/{created['id']}")).status_code == 404


async def test_delete_meld_returns_404_for_an_unknown_id(logged_in):
    assert (await logged_in.delete(f"/melds/{uuid.uuid4()}")).status_code == 404


async def test_save_story_persists_the_text(logged_in):
    created = (await logged_in.post("/melds", json={"title": "Writing"})).json()

    save = await logged_in.put(
        f"/melds/{created['id']}/story", json={"text": "It was a dark and stormy night."}
    )
    assert save.status_code == 200
    assert save.json()["text"] == "It was a dark and stormy night."

    reloaded = await logged_in.get(f"/melds/{created['id']}")
    assert reloaded.json()["story"]["text"] == "It was a dark and stormy night."


async def test_save_story_changes_the_input_hash(logged_in):
    created = (await logged_in.post("/melds", json={"title": "Hashing"})).json()

    first = (await logged_in.put(f"/melds/{created['id']}/story", json={"text": "one"})).json()
    second = (await logged_in.put(f"/melds/{created['id']}/story", json={"text": "two"})).json()

    assert first["input_hash"] != second["input_hash"]


async def test_save_story_accepts_an_empty_string(logged_in):
    """Clearing the editor is a legitimate save, not a validation error."""
    created = (await logged_in.post("/melds", json={"title": "Emptying"})).json()
    await logged_in.put(f"/melds/{created['id']}/story", json={"text": "something"})

    response = await logged_in.put(f"/melds/{created['id']}/story", json={"text": ""})
    assert response.status_code == 200
    assert response.json()["text"] == ""


async def test_save_story_returns_404_for_another_users_meld(client):
    await client.post("/auth/signup", json={"email": "alice5@example.com", "password": "hunter2hunter2"})
    await client.post("/auth/login", json={"email": "alice5@example.com", "password": "hunter2hunter2"})
    created = (await client.post("/melds", json={"title": "Alice's"})).json()
    await client.post("/auth/logout")

    await client.post("/auth/signup", json={"email": "bob5@example.com", "password": "hunter2hunter2"})
    await client.post("/auth/login", json={"email": "bob5@example.com", "password": "hunter2hunter2"})

    response = await client.put(f"/melds/{created['id']}/story", json={"text": "intruder"})
    assert response.status_code == 404
