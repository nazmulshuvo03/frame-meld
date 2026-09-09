import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from framemeld.api_main import app
from framemeld.config import settings
from framemeld.db import Base, get_session

# Import every model module so Base.metadata is fully populated before
# create_all runs. Without these imports the test schema comes out empty.
import framemeld.models  # noqa: F401


@pytest.fixture(scope="session")
async def test_engine():
    # NullPool: the session-scoped engine outlives individual tests, and a
    # pooled connection held across an event-loop boundary raises in asyncio.
    engine = create_async_engine(settings.test_database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(test_engine) -> AsyncSession:
    """A session wrapped in a transaction that is always rolled back.

    Binding the session to an explicit connection (rather than letting it
    check one out of the pool) is what makes the rollback total: anything
    the request handler committed inside this transaction is undone too.
    """
    connection = await test_engine.connect()
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

    yield session

    await session.close()
    await transaction.rollback()
    await connection.close()


@pytest.fixture
async def client(db) -> AsyncClient:
    async def override_get_session():
        yield db

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
