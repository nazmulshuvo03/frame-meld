async def test_signup_creates_a_user(client):
    response = await client.post(
        "/auth/signup", json={"email": "new@example.com", "password": "hunter2hunter2"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert "id" in body
    assert "password" not in body
    assert "password_hash" not in body


async def test_signup_rejects_a_duplicate_email(client):
    await client.post("/auth/signup", json={"email": "d@example.com", "password": "hunter2hunter2"})
    response = await client.post(
        "/auth/signup", json={"email": "d@example.com", "password": "hunter2hunter2"}
    )
    assert response.status_code == 409


async def test_signup_rejects_a_short_password(client):
    response = await client.post("/auth/signup", json={"email": "s@example.com", "password": "abc"})
    assert response.status_code == 422


async def test_signup_rejects_a_malformed_email(client):
    response = await client.post("/auth/signup", json={"email": "notanemail", "password": "hunter2hunter2"})
    assert response.status_code == 422


async def test_login_sets_a_session_cookie(client):
    await client.post("/auth/signup", json={"email": "l@example.com", "password": "hunter2hunter2"})
    response = await client.post(
        "/auth/login", json={"email": "l@example.com", "password": "hunter2hunter2"}
    )
    assert response.status_code == 200
    assert "framemeld_session" in response.cookies


async def test_login_rejects_a_wrong_password(client):
    await client.post("/auth/signup", json={"email": "w@example.com", "password": "hunter2hunter2"})
    response = await client.post(
        "/auth/login", json={"email": "w@example.com", "password": "wrongwrongwrong"}
    )
    assert response.status_code == 401


async def test_login_rejects_an_unknown_email(client):
    response = await client.post(
        "/auth/login", json={"email": "ghost@example.com", "password": "hunter2hunter2"}
    )
    assert response.status_code == 401


async def test_me_returns_the_current_user_when_logged_in(client):
    await client.post("/auth/signup", json={"email": "m@example.com", "password": "hunter2hunter2"})
    await client.post("/auth/login", json={"email": "m@example.com", "password": "hunter2hunter2"})

    response = await client.get("/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "m@example.com"


async def test_me_returns_401_when_not_logged_in(client):
    response = await client.get("/auth/me")
    assert response.status_code == 401


async def test_logout_invalidates_the_session(client):
    await client.post("/auth/signup", json={"email": "o@example.com", "password": "hunter2hunter2"})
    await client.post("/auth/login", json={"email": "o@example.com", "password": "hunter2hunter2"})

    logout = await client.post("/auth/logout")
    assert logout.status_code == 204

    after = await client.get("/auth/me")
    assert after.status_code == 401


async def test_logout_with_a_forged_cookie_returns_204_not_500(client):
    """Logout calls revoke_session with whatever cookie the client presents.
    A stale or forged token must be a silent no-op. Without this test, a
    refactor of revoke_session to scalar_one() would turn every stale
    cookie into a 500 and the suite would stay green."""
    client.cookies.set("framemeld_session", "not-a-real-token")

    response = await client.post("/auth/logout")
    assert response.status_code == 204


async def test_logout_without_a_cookie_returns_204(client):
    """A client with no session cookie at all must not get a 500 — the
    raw token would be None, and None.encode() would raise."""
    response = await client.post("/auth/logout")
    assert response.status_code == 204
