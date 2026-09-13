from httpx import ASGITransport, AsyncClient

from framemeld.api_main import app
from framemeld.config import settings


async def test_health_returns_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_docs_are_reachable_when_enabled():
    """Anonymous callers can otherwise enumerate every endpoint and schema
    (api_main.py), so docs_enabled gates all three URLs together. `app` is
    built once at import time from settings.docs_enabled, which defaults
    to True and is not overridden anywhere in this test suite's env, so
    this only exercises the enabled path.

    The disabled path (docs_enabled=False) is NOT covered here: it would
    need a second FastAPI app built with the setting patched, since
    docs_url/redoc_url/openapi_url are baked in at construction rather
    than read live — more machinery than this gap is worth today.
    """
    assert settings.docs_enabled is True
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/docs")).status_code == 200
        assert (await client.get("/redoc")).status_code == 200
        assert (await client.get("/openapi.json")).status_code == 200
