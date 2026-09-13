from framemeld.credits.pricing import PRICING_VERSION
from framemeld.models.job import JobKind


async def test_a_new_account_starts_with_the_signup_grant(logged_in):
    """M3-D9: 500 credits, enough to work through the whole Cast stage
    without topping up."""
    response = await logged_in.get("/credits")
    assert response.status_code == 200
    assert response.json()["balance"] == 500


async def test_the_balance_requires_authentication(client):
    assert (await client.get("/credits")).status_code == 401


async def test_pricing_lists_every_billable_action(logged_in):
    response = await logged_in.get("/credits/pricing")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == PRICING_VERSION
    assert body["quotes"][JobKind.GENERATE_CHARACTER_IMAGE.value] > 0
    assert body["quotes"][JobKind.GENERATE_CHARACTERS.value] > 0


async def test_one_users_balance_is_not_anothers(client):
    first = {"email": "alice-credits@example.com", "password": "hunter2hunter2"}
    second = {"email": "bob-credits@example.com", "password": "hunter2hunter2"}
    for credentials in (first, second):
        assert (await client.post("/auth/signup", json=credentials)).status_code == 201

    assert (await client.post("/auth/login", json=first)).status_code == 200
    assert (await client.get("/credits")).json()["balance"] == 500
