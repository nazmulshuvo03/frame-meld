from collections.abc import AsyncGenerator

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Import every model module so Base.metadata is fully populated before
# create_all runs. Without these imports the test schema comes out empty.
import framemeld.models  # noqa: F401
from framemeld.api_main import app
from framemeld.config import settings
from framemeld.db import Base, get_session
from framemeld.jobs.queue import RecordingQueue, get_queue
from framemeld.redis_client import get_redis


@pytest.fixture(autouse=True)
def _stub_providers_by_default(monkeypatch):
    """The suite must never depend on the developer's own .env choice of
    provider: Settings reads that file directly (config.py's _ENV_FILE), so
    switching either provider to a real adapter for manual testing would
    otherwise break every test that reaches a registry -- and for images it
    would also spend money. A test that wants a real adapter overrides this
    with its own monkeypatch, which simply wins for that test's duration."""
    monkeypatch.setattr(settings, "llm_provider", "stub")
    monkeypatch.setattr(settings, "image_provider", "stub")


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """Every test writes into its own directory, so a suite run never
    leaves files in the repo and two tests can never see each other's
    assets."""
    monkeypatch.setattr(settings, "storage_local_root", str(tmp_path / "assets"))


@pytest.fixture(scope="session")
async def test_engine():
    assert settings.test_database_url != settings.database_url, (
        "TEST_DATABASE_URL must differ from DATABASE_URL — test setup drops all tables"
    )
    # NullPool: the session-scoped engine outlives individual tests, and a
    # pooled connection held across an event-loop boundary raises in asyncio.
    engine = create_async_engine(settings.test_database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """A session wrapped in a transaction that is always rolled back.

    Binding the session to an explicit connection (rather than letting it
    check one out of the pool) is what makes the rollback total: anything
    the request handler committed inside this transaction is undone too.
    """
    connection = await test_engine.connect()
    try:
        transaction = await connection.begin()
        session = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            # Route the session's own commits into savepoints instead of letting
            # them close the outer transaction. Route handlers in later tasks call
            # session.commit(); without this the first commit ends the transaction
            # this fixture rolls back, and tests start leaking state into each
            # other — passing alone, failing together.
            join_transaction_mode="create_savepoint",
        )()
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()
    finally:
        await connection.close()


@pytest.fixture
async def fake_redis():
    """A real Redis implementation, in memory.

    fakeredis rather than a hand-rolled double because the limiter depends
    on exact server semantics — pipelines, and EXPIRE's nx flag in
    particular — that a stub would assert nothing about.
    """
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def recording_queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
async def client(db, fake_redis, recording_queue) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_session():
        yield db

    async def override_get_queue():
        return recording_queue

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = override_get_queue
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_redis, None)
        app.dependency_overrides.pop(get_queue, None)


@pytest.fixture
async def logged_in(client) -> AsyncClient:
    """A client that has signed up and logged in. Meld tests need an
    authenticated caller for every request, so this keeps them readable.

    Both calls are asserted: a silent failure here would hand every
    dependent test an unauthenticated client, and they would fail with
    confusing 401s far from the actual cause.
    """
    signup = await client.post(
        "/auth/signup", json={"email": "owner@example.com", "password": "hunter2hunter2"}
    )
    assert signup.status_code == 201, signup.text

    login = await client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "hunter2hunter2"}
    )
    assert login.status_code == 200, login.text

    return client
