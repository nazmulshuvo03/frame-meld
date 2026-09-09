# FrameMeld M1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user can sign up, log in, create a named Meld, write a story into it, close the tab, and find the text intact on return.

**Architecture:** A monorepo with two deployables in M1 — a Python/FastAPI backend (`services/backend`) and a Next.js frontend (`apps/web`). Postgres is the source of truth, reached through async SQLAlchemy 2.0 with Alembic migrations. Auth is an opaque session token in an HttpOnly cookie, stored hashed in Postgres — no third-party auth vendor. The `render` service and the arq worker are deliberately absent; they arrive in M5 and M2 respectively.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 (async, asyncpg) · Alembic · pydantic-settings · argon2-cffi · pytest + pytest-asyncio + httpx · Postgres 16 · Next.js 15 (App Router) · TypeScript · Tailwind CSS 4 · Docker Compose · conda (interpreter only; pip installs the deps)

**Spec:** [docs/superpowers/specs/2026-09-09-framemeld-design.md](../specs/2026-09-09-framemeld-design.md)

## Global Constraints

Every task's requirements implicitly include these. Values are copied verbatim from the spec.

- **The container noun is "Meld".** Table `melds`, route `/meld/{id}`, UI copy "My Melds" / "New Meld". Never "project", never "storyboard".
- **The api service must never call an AI provider and must never run long work in-request.** (Spec §3.) M1 has no AI calls at all.
- **Autosave fires on a ~800 ms debounce after typing stops.** (Spec §4.4.)
- **Nothing is destroyed by an edit.** Deletes are soft (`deleted_at`), never `DELETE FROM`. (Spec §4.4.)
- **Every generated row carries `input_hash`, `state`, `provider`, `model`, `params`, `credits_spent`, `asset_id`.** (Spec §4.1.) M1 creates no generated rows, but `stories.input_hash` exists from the start and uses the same canonical hashing function.
- **`input_hash` is the SHA-256 of the canonical JSON of everything that produced the artifact.** (Spec §4.1.) One shared implementation, never re-derived per call site.
- **Enum columns are VARCHAR plus a named CHECK constraint, never a Postgres native enum.** Native enums need `ALTER TYPE` migrations, and `melds.stage` gains values in every later milestone. **Four arguments are required together** — `native_enum=False` alone is not enough:

  ```python
  Enum(
      MeldStage,
      native_enum=False,          # VARCHAR, not a native enum type
      length=20,
      create_constraint=True,     # REQUIRED: SQLAlchemy 2.0 defaults this False,
                                  # so native_enum=False alone gives a bare,
                                  # completely unconstrained VARCHAR
      name="meld_stage",          # names the CHECK so a migration can alter it
      values_callable=lambda e: [m.value for m in e],
                                  # store 'story', not the member name 'STORY'
  )
  ```

  Omitting `create_constraint=True` yields a column with no validation whatsoever. Omitting `values_callable` stores the uppercase member name, so the database disagrees with both the API and the values the spec lists. Verified against the installed SQLAlchemy 2.0.52.
- **All Meld data is user-scoped.** Every query filters on `user_id`. A request for another user's Meld returns 404, never 403 — do not leak existence.
- **Money is not in M1.** No credit ledger, no pricing table. Those arrive in M3.

---

## File Structure

```
framemeld/
├─ docker-compose.yml            # postgres:16 only in M1
├─ environment.yml               # conda env: the Python interpreter only
├─ .env.example
├─ .gitignore
├─ README.md
├─ services/
│  └─ backend/                   # ONE Python package, multiple entrypoints
│     ├─ pyproject.toml
│     ├─ alembic.ini
│     ├─ migrations/
│     │  ├─ env.py
│     │  └─ versions/
│     ├─ src/framemeld/
│     │  ├─ __init__.py
│     │  ├─ config.py            # pydantic-settings; all env vars
│     │  ├─ db.py                # engine, Base, get_session dependency
│     │  ├─ hashing.py           # canonical_hash() — used by every milestone
│     │  ├─ api_main.py          # the FastAPI app
│     │  ├─ models/
│     │  │  ├─ __init__.py
│     │  │  ├─ user.py           # User, Session
│     │  │  └─ meld.py           # Meld, Story, MeldStage, MeldStatus
│     │  ├─ auth/
│     │  │  ├─ passwords.py      # hash_password, verify_password
│     │  │  ├─ sessions.py       # issue_session, resolve_session, revoke_session
│     │  │  ├─ deps.py           # current_user dependency
│     │  │  └─ router.py         # /auth/signup, /auth/login, /auth/logout, /auth/me
│     │  └─ melds/
│     │     ├─ schemas.py        # pydantic request/response models
│     │     ├─ service.py        # all DB logic, no HTTP concerns
│     │     └─ router.py         # /melds endpoints
│     └─ tests/
│        ├─ conftest.py          # db fixture, client fixture, auth helpers
│        ├─ test_health.py
│        ├─ test_hashing.py
│        ├─ test_passwords.py
│        ├─ test_sessions.py
│        ├─ test_auth_api.py
│        └─ test_melds_api.py
└─ apps/
   └─ web/
      ├─ package.json
      ├─ tsconfig.json
      ├─ next.config.ts
      ├─ app/
      │  ├─ layout.tsx
      │  ├─ globals.css
      │  ├─ page.tsx             # redirects to /melds or /login
      │  ├─ login/page.tsx
      │  ├─ signup/page.tsx
      │  ├─ melds/page.tsx       # the dashboard
      │  └─ meld/[id]/page.tsx   # the story editor
      └─ lib/
         ├─ api.ts               # typed fetch wrapper
         └─ useAutosave.ts       # the 800 ms debounce hook
```

**Why `services/backend` is one package with several entrypoints:** the api and the worker (M2) share every SQLAlchemy model, the hashing function, and the config. Splitting them into separate packages would mean duplicating models or publishing an internal library. One package, two `__main__`s.

**Why `melds/service.py` is separate from `melds/router.py`:** the worker will need the same Meld queries in M2 without going through HTTP. Business logic must not live in route handlers.

---

## Task 1: Repo scaffold, Postgres, and a health endpoint

**Files:**
- Create: `.gitignore`, `.env.example`, `docker-compose.yml`, `environment.yml`
- Create: `services/backend/pyproject.toml`
- Create: `services/backend/src/framemeld/__init__.py`
- Create: `services/backend/src/framemeld/config.py`
- Create: `services/backend/src/framemeld/api_main.py`
- Test: `services/backend/tests/test_health.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `Settings` class with fields `database_url: str`, `test_database_url: str`, `session_cookie_name: str`, `session_ttl_days: int`, `cors_origins: list[str]`; a module-level `settings` instance; `get_settings()` returning it. FastAPI app object at `framemeld.api_main:app`.

- [ ] **Step 1: Initialise git and write the ignore file**

```bash
cd /f/FrameMeld
git init
```

Create `.gitignore`:

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
*.egg-info/

# Node
node_modules/
.next/
out/

# Env
.env
.env.local

# OS
.DS_Store
Thumbs.db
```

- [ ] **Step 2: Write docker-compose.yml**

Postgres only. Redis arrives in M2 with the worker; adding it now would be an unused container.

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: framemeld
      POSTGRES_PASSWORD: framemeld
      POSTGRES_DB: framemeld
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U framemeld"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  pgdata:
```

- [ ] **Step 3: Write .env.example**

```dotenv
DATABASE_URL=postgresql+asyncpg://framemeld:framemeld@localhost:5432/framemeld
TEST_DATABASE_URL=postgresql+asyncpg://framemeld:framemeld@localhost:5432/framemeld_test
SESSION_COOKIE_NAME=framemeld_session
SESSION_TTL_DAYS=30
CORS_ORIGINS=http://localhost:3000
```

Then `cp .env.example .env`.

- [ ] **Step 4: Write the backend pyproject.toml**

```toml
[project]
name = "framemeld"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic-settings>=2.6",
    "argon2-cffi>=23.1",
    "python-multipart>=0.0.12",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "httpx>=0.28",
    "ruff>=0.8",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/framemeld"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

`asyncio_mode = "auto"` means async test functions need no `@pytest.mark.asyncio` decorator. Every test in this plan relies on that.

- [ ] **Step 4b: Write environment.yml**

Conda supplies the Python interpreter and nothing else. Every project
dependency comes from pip, reading `pyproject.toml`. Installing the same
package from both conda and pip is what makes mixed environments break, so
this file deliberately lists no project packages.

Create `environment.yml` at the repo root:

```yaml
name: framemeld
channels:
  - defaults
dependencies:
  - python=3.12
  - pip
```

- [ ] **Step 5: Install dependencies**

The `framemeld` conda env already exists (Python 3.12.14). If it did not,
`conda env create -f environment.yml` would create it.

```bash
cd /f/FrameMeld/services/backend
conda run --no-capture-output -n framemeld pip install -e ".[dev]"
```

Verify the install landed in the right interpreter:

```bash
conda run --no-capture-output -n framemeld python -c "import fastapi, sqlalchemy, pydantic; print('deps ok')"
```

- [ ] **Step 6: Write config.py**

```python
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../../.env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str
    test_database_url: str
    session_cookie_name: str = "framemeld_session"
    session_ttl_days: int = 30

    # NoDecode switches off pydantic-settings' default JSON parsing for
    # complex types. Without it, CORS_ORIGINS would have to be written as a
    # JSON array in .env, and the validator below could never run — source
    # decoding happens before field validation.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept a comma-separated string, which is what Docker, Kubernetes,
        and every PaaS actually hand you in an environment variable."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
```

`CORS_ORIGINS` therefore stays human-readable in `.env` — `http://localhost:3000`,
or `http://localhost:3000,https://app.framemeld.com` for several origins.

- [ ] **Step 7: Write the failing test**

Create `services/backend/tests/test_health.py`:

```python
from httpx import ASGITransport, AsyncClient

from framemeld.api_main import app


async def test_health_returns_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 8: Run the test to verify it fails**

Run: `cd /f/FrameMeld/services/backend && conda run --no-capture-output -n framemeld pytest tests/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'framemeld.api_main'`

- [ ] **Step 9: Write the minimal app**

Create `services/backend/src/framemeld/__init__.py` (empty file), then `services/backend/src/framemeld/api_main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from framemeld.config import settings

app = FastAPI(title="FrameMeld API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

`allow_credentials=True` is required — the session cookie will not be sent cross-origin without it, and the frontend runs on port 3000 while the api runs on 8000.

- [ ] **Step 10: Run the test to verify it passes**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 11: Start Postgres and confirm it is healthy**

```bash
cd /f/FrameMeld
docker compose up -d
docker compose ps
```

Expected: the `postgres` service shows `healthy`.

- [ ] **Step 12: Commit**

```bash
cd /f/FrameMeld
git add .gitignore .env.example docker-compose.yml environment.yml services/backend
git commit -m "feat: scaffold backend with health endpoint and Postgres"
```

---

## Task 2: Database layer and transactional test fixtures

**Files:**
- Create: `services/backend/src/framemeld/db.py`
- Create: `services/backend/tests/conftest.py`
- Test: exercised by every later task

**Interfaces:**
- Consumes: `framemeld.config.settings`
- Produces: `Base` (SQLAlchemy `DeclarativeBase`), `engine`, `get_session()` async generator FastAPI dependency yielding `AsyncSession`. Test fixtures `db` (an `AsyncSession` rolled back after each test) and `client` (an `AsyncClient` whose app uses that same session).

**Why this task exists separately:** the transactional-rollback fixture is the single most error-prone piece of test infrastructure in an async SQLAlchemy project. Getting it wrong produces tests that pass alone and fail together. It gets its own task and its own review.

- [ ] **Step 1: Write db.py**

```python
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from framemeld.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
```

`expire_on_commit=False` matters: without it, accessing an ORM attribute after `commit()` triggers a lazy refresh, which raises in async code.

- [ ] **Step 2: Write conftest.py**

Create `services/backend/tests/conftest.py`:

```python
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
```

- [ ] **Step 3: Create the test database**

```bash
docker compose exec postgres createdb -U framemeld framemeld_test
```

If it already exists, the command errors harmlessly — that is fine.

- [ ] **Step 4: Write a placeholder models package so the conftest import resolves**

Create `services/backend/src/framemeld/models/__init__.py`:

```python
"""SQLAlchemy models. Import every model module here so that
Base.metadata is populated for Alembic autogenerate and for the
test-schema fixture."""
```

- [ ] **Step 5: Run the existing test suite to confirm nothing broke**

Run: `conda run --no-capture-output -n framemeld pytest -v`
Expected: PASS (`test_health_returns_ok`). The `db` and `client` fixtures are unused so far but must import cleanly.

- [ ] **Step 6: Commit**

```bash
git add services/backend/src/framemeld/db.py services/backend/src/framemeld/models services/backend/tests/conftest.py
git commit -m "feat: add async database layer and transactional test fixtures"
```

---

## Task 3: Canonical hashing

**Files:**
- Create: `services/backend/src/framemeld/hashing.py`
- Test: `services/backend/tests/test_hashing.py`

**Interfaces:**
- Consumes: nothing
- Produces: `canonical_hash(payload: dict) -> str` returning a 64-character lowercase hex SHA-256 digest.

**Why this is in M1 despite the staleness engine arriving in M2:** `stories.input_hash` exists from the first migration, and spec §4.1 requires one shared implementation. Two different hashing functions would silently break staleness the moment M2 lands.

- [ ] **Step 1: Write the failing tests**

Create `services/backend/tests/test_hashing.py`:

```python
from framemeld.hashing import canonical_hash


def test_returns_64_char_lowercase_hex():
    result = canonical_hash({"a": 1})
    assert len(result) == 64
    assert result == result.lower()
    assert all(c in "0123456789abcdef" for c in result)


def test_key_order_does_not_change_the_hash():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_different_values_produce_different_hashes():
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_nested_key_order_does_not_change_the_hash():
    left = canonical_hash({"outer": {"x": 1, "y": 2}})
    right = canonical_hash({"outer": {"y": 2, "x": 1}})
    assert left == right


def test_none_and_missing_key_are_different():
    """A field explicitly set to null must not hash the same as an absent
    field. Otherwise clearing a prompt would look like never having set one,
    and the artifact would wrongly stay fresh."""
    assert canonical_hash({"a": None}) != canonical_hash({})


def test_int_and_string_are_different():
    assert canonical_hash({"a": 1}) != canonical_hash({"a": "1"})


def test_list_order_is_significant():
    """Shot order matters, so list order must affect the hash."""
    assert canonical_hash({"a": [1, 2]}) != canonical_hash({"a": [2, 1]})


def test_set_raises_rather_than_hashing_unstably():
    """A set's str() order depends on PYTHONHASHSEED, so silently accepting
    one would mean a different hash on every process restart."""
    with pytest.raises(TypeError):
        canonical_hash({"a": {1, 2, 3}})


def test_non_json_native_value_raises_rather_than_colliding():
    """str() coercion would make this datetime hash identically to the
    string "2024-01-01 12:00:00"."""
    with pytest.raises(TypeError):
        canonical_hash({"a": datetime.datetime(2024, 1, 1, 12, 0, 0)})
```

The last two tests exist to stop a future contributor "fixing" an unexpected
`TypeError` by reinstating `default=str`. They need `import datetime` and
`import pytest` at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_hashing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'framemeld.hashing'`

- [ ] **Step 3: Write the implementation**

```python
import hashlib
import json
from typing import Any


def canonical_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of a payload serialised deterministically.

    This is the single implementation of `input_hash` for the whole
    system (spec section 4.1). Every generated artifact hashes the
    upstream artifacts' input_hash values plus its own prompt, provider,
    model and params through this function.

    sort_keys makes mapping key order irrelevant; separators removes the
    whitespace that would otherwise vary between Python versions;
    ensure_ascii keeps the bytes stable regardless of locale.

    Only JSON-native values are accepted. Anything else — datetime,
    Decimal, UUID, set, a custom object — raises TypeError, deliberately.
    Coercing them with str() would let a typed value collide with its own
    string form, and would make any set hash differently on every process
    restart. Callers must convert to a JSON-native value explicitly.

    One known ambiguity is accepted: tuples and lists serialise
    identically, so (1, 2) and [1, 2] hash the same. Both mean "ordered
    sequence" here, so that is intended rather than overlooked.
    """
    serialised = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()
```

**Do not add `default=str` to that call.** It looks like a convenience and is a
correctness bug: `datetime(2024, 1, 1, 12, 0, 0)` would hash identically to the
string `"2024-01-01 12:00:00"`, and any `set` would hash differently on every
process restart because `str(set)` ordering follows `PYTHONHASHSEED`. Since this
function decides whether paid, generated work is stale, either failure silently
bills users to regenerate work that was fine.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_hashing.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add services/backend/src/framemeld/hashing.py services/backend/tests/test_hashing.py
git commit -m "feat: add canonical hashing for input_hash"
```

---

## Task 4: Password hashing

**Files:**
- Create: `services/backend/src/framemeld/auth/__init__.py`
- Create: `services/backend/src/framemeld/auth/passwords.py`
- Test: `services/backend/tests/test_passwords.py`

**Interfaces:**
- Consumes: nothing
- Produces: `hash_password(plain: str) -> str` and `verify_password(plain: str, hashed: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `services/backend/tests/test_passwords.py`:

```python
from framemeld.auth.passwords import hash_password, verify_password


def test_hash_is_not_the_plaintext():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert hashed.startswith("$argon2")


def test_correct_password_verifies():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True


def test_wrong_password_does_not_verify():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password", hashed) is False


def test_same_password_hashes_differently_each_time():
    """Argon2 salts every hash, so two hashes of one password must differ.
    Identical hashes would leak which accounts share a password."""
    assert hash_password("same") != hash_password("same")


def test_malformed_hash_returns_false_rather_than_raising():
    """A corrupt or legacy value in the column must fail the login, not
    500 the endpoint."""
    assert verify_password("anything", "not-a-valid-hash") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_passwords.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'framemeld.auth'`

- [ ] **Step 3: Write the implementation**

Create `services/backend/src/framemeld/auth/__init__.py` (empty), then `services/backend/src/framemeld/auth/passwords.py`:

```python
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_passwords.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add services/backend/src/framemeld/auth services/backend/tests/test_passwords.py
git commit -m "feat: add argon2 password hashing"
```

---

## Task 5: User and Session models, Alembic, first migration

**Files:**
- Create: `services/backend/src/framemeld/models/user.py`
- Modify: `services/backend/src/framemeld/models/__init__.py`
- Create: `services/backend/alembic.ini`
- Create: `services/backend/migrations/env.py`
- Create: `services/backend/migrations/script.py.mako`
- Create: `services/backend/migrations/versions/` (generated)
- Test: `services/backend/tests/test_models_user.py`

**Interfaces:**
- Consumes: `framemeld.db.Base`
- Produces: `User` with columns `id: UUID`, `email: str`, `password_hash: str`, `created_at: datetime`, `deleted_at: datetime | None`. `Session` with columns `id: UUID`, `user_id: UUID`, `token_hash: str`, `expires_at: datetime`, `created_at: datetime`, `revoked_at: datetime | None`, and relationship `user`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_models_user.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from framemeld.models.user import Session, User


async def test_user_persists_with_generated_id_and_timestamp(db):
    user = User(email="a@example.com", password_hash="x")
    db.add(user)
    await db.flush()

    assert user.id is not None
    assert user.created_at is not None
    assert user.deleted_at is None


async def test_email_is_unique(db):
    db.add(User(email="dup@example.com", password_hash="x"))
    await db.flush()

    db.add(User(email="dup@example.com", password_hash="y"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_session_links_to_user(db):
    user = User(email="s@example.com", password_hash="x")
    db.add(user)
    await db.flush()

    session = Session(
        user_id=user.id,
        token_hash="deadbeef",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(session)
    await db.flush()

    found = (await db.execute(select(Session).where(Session.token_hash == "deadbeef"))).scalar_one()
    assert found.user_id == user.id
    assert found.revoked_at is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_models_user.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'framemeld.models.user'`

- [ ] **Step 3: Write the models**

Create `services/backend/src/framemeld/models/user.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from framemeld.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list["Session"]] = relationship(back_populates="user")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    # The raw token is never stored. Only its SHA-256, so a database leak
    # does not hand over live sessions.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")
```

- [ ] **Step 4: Register the models**

Replace `services/backend/src/framemeld/models/__init__.py` with:

```python
"""SQLAlchemy models. Import every model module here so that
Base.metadata is populated for Alembic autogenerate and for the
test-schema fixture."""

from framemeld.models.user import Session, User  # noqa: F401

__all__ = ["Session", "User"]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_models_user.py -v`
Expected: PASS, 3 tests

- [ ] **Step 6: Initialise Alembic**

```bash
cd /f/FrameMeld/services/backend
conda run --no-capture-output -n framemeld alembic init -t async migrations
```

- [ ] **Step 7: Point Alembic at the app config**

In `alembic.ini`, blank out the hardcoded URL so it comes from the app instead:

```ini
sqlalchemy.url =
```

Then replace the config block near the top of `migrations/env.py` (after the existing `config = context.config` line) with:

```python
from framemeld.config import settings
from framemeld.db import Base
import framemeld.models  # noqa: F401  - populates Base.metadata

config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata
```

Delete the `target_metadata = None` line that `alembic init` generated.

- [ ] **Step 8: Generate and apply the migration**

```bash
conda run --no-capture-output -n framemeld alembic revision --autogenerate -m "users and sessions"
conda run --no-capture-output -n framemeld alembic upgrade head
```

- [ ] **Step 9: Verify the tables exist**

```bash
cd /f/FrameMeld
docker compose exec postgres psql -U framemeld -c "\dt"
```

Expected: `users`, `sessions`, and `alembic_version` are listed.

- [ ] **Step 10: Commit**

```bash
git add services/backend/src/framemeld/models services/backend/alembic.ini services/backend/migrations services/backend/tests/test_models_user.py
git commit -m "feat: add User and Session models with Alembic migration"
```

---

## Task 6: Session issue, resolve, and revoke

**Files:**
- Create: `services/backend/src/framemeld/auth/sessions.py`
- Test: `services/backend/tests/test_sessions.py`

**Interfaces:**
- Consumes: `framemeld.models.user.Session`, `framemeld.models.user.User`
- Produces:
  - `issue_session(db: AsyncSession, user_id: uuid.UUID) -> str` — returns the **raw** token to put in the cookie
  - `resolve_session(db: AsyncSession, raw_token: str) -> User | None`
  - `revoke_session(db: AsyncSession, raw_token: str) -> None`

- [ ] **Step 1: Write the failing tests**

Create `services/backend/tests/test_sessions.py`:

```python
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from framemeld.auth.sessions import issue_session, resolve_session, revoke_session
from framemeld.models.user import Session, User


async def _make_user(db, email="u@example.com") -> User:
    user = User(email=email, password_hash="x")
    db.add(user)
    await db.flush()
    return user


async def test_issue_returns_a_long_opaque_token(db):
    user = await _make_user(db)
    token = await issue_session(db, user.id)
    assert isinstance(token, str)
    assert len(token) >= 32


async def test_raw_token_is_not_stored_in_the_database(db):
    user = await _make_user(db)
    token = await issue_session(db, user.id)

    stored = (await db.execute(select(Session))).scalars().all()
    assert len(stored) == 1
    assert stored[0].token_hash != token


async def test_resolve_returns_the_user(db):
    user = await _make_user(db)
    token = await issue_session(db, user.id)

    resolved = await resolve_session(db, token)
    assert resolved is not None
    assert resolved.id == user.id


async def test_resolve_returns_none_for_an_unknown_token(db):
    assert await resolve_session(db, "nonsense") is None


async def test_resolve_returns_none_for_an_expired_session(db):
    user = await _make_user(db)
    token = await issue_session(db, user.id)

    session = (await db.execute(select(Session))).scalar_one()
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.flush()

    assert await resolve_session(db, token) is None


async def test_resolve_returns_none_after_revoke(db):
    user = await _make_user(db)
    token = await issue_session(db, user.id)

    await revoke_session(db, token)

    assert await resolve_session(db, token) is None


async def test_resolve_returns_none_for_a_soft_deleted_user(db):
    """A deleted account's live session must stop working immediately."""
    user = await _make_user(db)
    token = await issue_session(db, user.id)

    user.deleted_at = datetime.now(UTC)
    await db.flush()

    assert await resolve_session(db, token) is None


async def test_two_sessions_for_one_user_are_independent(db):
    user = await _make_user(db)
    first = await issue_session(db, user.id)
    second = await issue_session(db, user.id)

    await revoke_session(db, first)

    assert await resolve_session(db, first) is None
    assert await resolve_session(db, second) is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_sessions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'framemeld.auth.sessions'`

- [ ] **Step 3: Write the implementation**

Create `services/backend/src/framemeld/auth/sessions.py`:

```python
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.config import settings
from framemeld.models.user import Session, User

TOKEN_BYTES = 32


def _hash_token(raw_token: str) -> str:
    """Session tokens are high-entropy random values, so a plain SHA-256 is
    the right tool here — unlike passwords, there is nothing to brute-force,
    and we need lookups to be fast and deterministic."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def issue_session(db: DbSession, user_id: uuid.UUID) -> str:
    raw_token = secrets.token_urlsafe(TOKEN_BYTES)
    db.add(
        Session(
            user_id=user_id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(days=settings.session_ttl_days),
        )
    )
    await db.flush()
    return raw_token


async def resolve_session(db: DbSession, raw_token: str) -> User | None:
    result = await db.execute(
        select(User)
        .join(Session, Session.user_id == User.id)
        .where(
            Session.token_hash == _hash_token(raw_token),
            Session.revoked_at.is_(None),
            Session.expires_at > datetime.now(UTC),
            User.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def revoke_session(db: DbSession, raw_token: str) -> None:
    result = await db.execute(
        select(Session).where(Session.token_hash == _hash_token(raw_token))
    )
    session = result.scalar_one_or_none()
    if session is not None:
        session.revoked_at = datetime.now(UTC)
        await db.flush()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_sessions.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add services/backend/src/framemeld/auth/sessions.py services/backend/tests/test_sessions.py
git commit -m "feat: add session issue, resolve, and revoke"
```

---

## Task 7: Auth endpoints and the current_user dependency

**Files:**
- Create: `services/backend/src/framemeld/auth/deps.py`
- Create: `services/backend/src/framemeld/auth/router.py`
- Modify: `services/backend/src/framemeld/api_main.py`
- Test: `services/backend/tests/test_auth_api.py`

**Interfaces:**
- Consumes: `hash_password`, `verify_password`, `issue_session`, `resolve_session`, `revoke_session`, `get_session`
- Produces:
  - `current_user` FastAPI dependency returning `User`, raising 401 when unauthenticated
  - `POST /auth/signup` `{email, password}` → 201 `{id, email}`
  - `POST /auth/login` `{email, password}` → 200 `{id, email}` + `Set-Cookie`
  - `POST /auth/logout` → 204
  - `GET /auth/me` → 200 `{id, email}`
  - Test helper `signup_and_login(client, email, password) -> None` added to `conftest.py`

- [ ] **Step 1: Write the failing tests**

Create `services/backend/tests/test_auth_api.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_auth_api.py -v`
Expected: FAIL — every request returns 404, because no `/auth` routes are registered yet.

- [ ] **Step 3: Write the current_user dependency**

Create `services/backend/src/framemeld/auth/deps.py`:

```python
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.auth.sessions import resolve_session
from framemeld.config import settings
from framemeld.db import get_session
from framemeld.models.user import User


async def current_user(
    request: Request,
    db: Annotated[DbSession, Depends(get_session)],
) -> User:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = await resolve_session(db, raw_token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    return user


CurrentUser = Annotated[User, Depends(current_user)]
```

- [ ] **Step 4: Write the auth router**

Create `services/backend/src/framemeld/auth/router.py`:

```python
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.auth.deps import CurrentUser
from framemeld.auth.passwords import hash_password, verify_password
from framemeld.auth.sessions import issue_session, revoke_session
from framemeld.config import settings
from framemeld.db import get_session
from framemeld.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])

Db = Annotated[DbSession, Depends(get_session)]


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        samesite="lax",
        secure=False,  # M1 runs on http://localhost; set True behind TLS
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        path="/",
    )


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def signup(credentials: Credentials, db: Db) -> User:
    existing = await db.execute(select(User).where(User.email == credentials.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=credentials.email, password_hash=hash_password(credentials.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Two concurrent signups for the same address both passed the check
        # above — reachable by double-clicking submit, not just by a
        # contrived race. The unique index is the real guard; convert its
        # error into the same 409 the sequential path returns rather than
        # letting it surface as a 500. The rollback is required: after a
        # failed commit the session refuses further use until it happens.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from None
    await db.refresh(user)
    return user


@router.post("/login", response_model=UserOut)
async def login(credentials: Credentials, response: Response, db: Db) -> User:
    result = await db.execute(
        select(User).where(User.email == credentials.email, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    # One error for both "no such user" and "wrong password", so the
    # response body cannot be used to enumerate registered emails.
    # Note the response *time* still can: argon2 only runs when a user
    # matched, so a wrong-password login is measurably slower (~69ms)
    # than an unknown-email one. Signup's 409 already reveals the same
    # fact, so this is accepted for M1 -- but if signup ever stops
    # enumerating, this needs a dummy-hash verify on the None path.
    if user is None or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    raw_token = await issue_session(db, user.id)
    await db.commit()
    _set_session_cookie(response, raw_token)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: Db) -> None:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        await revoke_session(db, raw_token)
        await db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
```

- [ ] **Step 5: Register the router**

In `services/backend/src/framemeld/api_main.py`, add the import and the include after the middleware block:

```python
from framemeld.auth.router import router as auth_router

app.include_router(auth_router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_auth_api.py -v`
Expected: PASS, 12 tests

- [ ] **Step 7: Add a login helper to conftest for later tasks**

Append to `services/backend/tests/conftest.py`:

```python
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
```

Every Meld test in Tasks 9 and 10 depends on this fixture. Without the
assertions a broken signup would surface as 401s inside those tests, pointing
at Meld code rather than at the fixture.

- [ ] **Step 8: Run the full suite**

Run: `conda run --no-capture-output -n framemeld pytest -v`
Expected: PASS, all tests

- [ ] **Step 9: Commit**

```bash
git add services/backend/src/framemeld/auth services/backend/src/framemeld/api_main.py services/backend/tests
git commit -m "feat: add signup, login, logout, and current_user dependency"
```

---

## Task 8: Meld and Story models and migration

**Files:**
- Create: `services/backend/src/framemeld/models/meld.py`
- Modify: `services/backend/src/framemeld/models/__init__.py`
- Create: `services/backend/migrations/versions/` (generated)
- Test: `services/backend/tests/test_models_meld.py`

**Interfaces:**
- Consumes: `framemeld.db.Base`, `framemeld.models.user.User`
- Produces:
  - `MeldStage` StrEnum with members `STORY`, `SCRIPT`, `CAST`, `SHOTS`, `FILM`
  - `MeldStatus` StrEnum with members `DRAFT`, `COMPLETE`, `ARCHIVED`
  - `Meld` with `id`, `user_id`, `title`, `stage`, `status`, `aspect_ratio`, `style_preset`, `moderation_state`, `created_at`, `updated_at`, `deleted_at`, relationship `story`
  - `Story` with `id`, `meld_id` (unique), `text`, `input_hash`, `updated_at`, relationship `meld`

**Note on the columns that look premature:** `aspect_ratio`, `style_preset`, and `moderation_state` are in the spec (§5.2) and cost nothing to add now. Adding them later means a migration against live user data.

- [ ] **Step 1: Write the failing tests**

Create `services/backend/tests/test_models_meld.py`:

```python
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from framemeld.models.meld import Meld, MeldStage, MeldStatus, Story
from framemeld.models.user import User


async def _make_user(db) -> User:
    user = User(email="m@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    return user


async def test_meld_defaults_to_story_stage_and_draft_status(db):
    user = await _make_user(db)
    meld = Meld(user_id=user.id, title="Untitled Meld")
    db.add(meld)
    await db.flush()

    assert meld.stage == MeldStage.STORY
    assert meld.status == MeldStatus.DRAFT
    assert meld.deleted_at is None
    assert meld.created_at is not None


async def test_story_is_one_to_one_with_meld(db):
    user = await _make_user(db)
    meld = Meld(user_id=user.id, title="A Meld")
    db.add(meld)
    await db.flush()

    db.add(Story(meld_id=meld.id, text="Once upon a time", input_hash="abc"))
    await db.flush()

    db.add(Story(meld_id=meld.id, text="A second story", input_hash="def"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_stage_rejects_a_value_outside_the_enum(db):
    user = await _make_user(db)
    db.add(Meld(user_id=user.id, title="Bad", stage="nonsense"))
    with pytest.raises((IntegrityError, LookupError, ValueError)):
        await db.flush()


async def test_deleting_a_meld_cascades_to_its_story(db):
    """Hard deletes only ever happen in tests and admin cleanup, but the
    FK must not leave orphaned stories behind when they do."""
    user = await _make_user(db)
    meld = Meld(user_id=user.id, title="Doomed")
    db.add(meld)
    await db.flush()
    db.add(Story(meld_id=meld.id, text="text", input_hash="abc"))
    await db.flush()
    meld_id = meld.id

    # Load the relationship before deleting: under asyncio, an ORM cascade
    # over an unloaded relationship raises MissingGreenlet.
    loaded = (
        await db.execute(
            select(Meld).options(selectinload(Meld.story)).where(Meld.id == meld_id)
        )
    ).scalar_one()

    await db.delete(loaded)
    await db.flush()

    orphan = (
        await db.execute(select(Story).where(Story.meld_id == meld_id))
    ).scalar_one_or_none()
    assert orphan is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_models_meld.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'framemeld.models.meld'`

- [ ] **Step 3: Write the models**

Create `services/backend/src/framemeld/models/meld.py`:

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from framemeld.db import Base


class MeldStage(enum.StrEnum):
    STORY = "story"
    SCRIPT = "script"
    CAST = "cast"
    SHOTS = "shots"
    FILM = "film"


class MeldStatus(enum.StrEnum):
    DRAFT = "draft"
    COMPLETE = "complete"
    ARCHIVED = "archived"


class ModerationState(enum.StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FLAGGED = "flagged"


def _enum_column(enum_cls: type[enum.StrEnum], constraint_name: str) -> Enum:
    """A VARCHAR column with a named CHECK constraint over the enum's *values*.

    All four arguments are load-bearing and each fails silently if omitted:

    - `native_enum=False` avoids a Postgres native enum, which would need an
      `ALTER TYPE` migration every time a member is added.
    - `create_constraint=True` is required. SQLAlchemy 2.0 defaults it to
      False, so `native_enum=False` on its own produces a bare, completely
      unconstrained VARCHAR — no validation at any layer.
    - `name` names the CHECK so a later migration can drop and recreate it.
    - `values_callable` stores `'story'` rather than the member name
      `'STORY'`, keeping the database consistent with the API and with the
      values listed in the spec.

    Later milestones add enums in other model files; move this to a shared
    module at that point rather than copying it.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        length=20,
        create_constraint=True,
        name=constraint_name,
        values_callable=lambda e: [member.value for member in e],
    )


class Meld(Base):
    __tablename__ = "melds"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))

    # See _enum_column above for why all four Enum arguments are required.
    stage: Mapped[MeldStage] = mapped_column(
        _enum_column(MeldStage, "meld_stage"), default=MeldStage.STORY
    )
    status: Mapped[MeldStatus] = mapped_column(
        _enum_column(MeldStatus, "meld_status"), default=MeldStatus.DRAFT
    )
    moderation_state: Mapped[ModerationState] = mapped_column(
        _enum_column(ModerationState, "meld_moderation_state"),
        default=ModerationState.PENDING,
    )

    aspect_ratio: Mapped[str] = mapped_column(String(10), default="16:9")
    style_preset: Mapped[str | None] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    story: Mapped["Story | None"] = relationship(
        back_populates="meld", cascade="all, delete-orphan", uselist=False
    )


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meld_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("melds.id", ondelete="CASCADE"), unique=True, index=True
    )
    text: Mapped[str] = mapped_column(Text, default="")
    input_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    meld: Mapped["Meld"] = relationship(back_populates="story")
```

- [ ] **Step 4: Register the models**

Replace `services/backend/src/framemeld/models/__init__.py` with:

```python
"""SQLAlchemy models. Import every model module here so that
Base.metadata is populated for Alembic autogenerate and for the
test-schema fixture."""

from framemeld.models.meld import Meld, MeldStage, MeldStatus, ModerationState, Story  # noqa: F401
from framemeld.models.user import Session, User  # noqa: F401

__all__ = [
    "Meld",
    "MeldStage",
    "MeldStatus",
    "ModerationState",
    "Session",
    "Story",
    "User",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_models_meld.py -v`
Expected: PASS, 4 tests

- [ ] **Step 6: Generate and apply the migration**

```bash
cd /f/FrameMeld/services/backend
conda run --no-capture-output -n framemeld alembic revision --autogenerate -m "melds and stories"
conda run --no-capture-output -n framemeld alembic upgrade head
```

- [ ] **Step 7: Commit**

```bash
git add services/backend/src/framemeld/models services/backend/migrations services/backend/tests/test_models_meld.py
git commit -m "feat: add Meld and Story models with migration"
```

---

## Task 9: Meld service layer

**Files:**
- Create: `services/backend/src/framemeld/melds/__init__.py`
- Create: `services/backend/src/framemeld/melds/service.py`
- Test: `services/backend/tests/test_melds_service.py`

**Interfaces:**
- Consumes: `Meld`, `Story`, `MeldStage`, `canonical_hash`
- Produces, all taking `db: AsyncSession` first and all scoped by `user_id`:
  - `create_meld(db, user_id: uuid.UUID, title: str) -> Meld` — also creates the empty Story
  - `list_melds(db, user_id: uuid.UUID) -> list[Meld]` — newest first, excludes soft-deleted
  - `get_meld(db, user_id: uuid.UUID, meld_id: uuid.UUID) -> Meld | None`
  - `rename_meld(db, user_id, meld_id, title: str) -> Meld | None`
  - `soft_delete_meld(db, user_id, meld_id) -> bool`
  - `save_story(db, user_id, meld_id, text: str) -> Story | None`
  - `story_hash(text: str) -> str`

**Why the service layer is separate from the router:** the M2 worker calls `get_meld` and `save_story` directly, without HTTP. Logic in route handlers would have to be duplicated or awkwardly re-invoked.

- [ ] **Step 1: Write the failing tests**

Create `services/backend/tests/test_melds_service.py`:

```python
import uuid

from framemeld.melds.service import (
    create_meld,
    get_meld,
    list_melds,
    rename_meld,
    save_story,
    soft_delete_meld,
    story_hash,
)
from framemeld.models.meld import MeldStage
from framemeld.models.user import User


async def _make_user(db, email) -> User:
    user = User(email=email, password_hash="x")
    db.add(user)
    await db.flush()
    return user


async def test_create_meld_also_creates_an_empty_story(db):
    user = await _make_user(db, "a@example.com")
    created = await create_meld(db, user.id, "My First Meld")

    meld = await get_meld(db, user.id, created.id)
    assert meld is not None
    assert meld.title == "My First Meld"
    assert meld.stage == MeldStage.STORY
    assert meld.story is not None
    assert meld.story.text == ""
    assert meld.story.input_hash == story_hash("")


async def test_list_melds_returns_newest_first(db):
    user = await _make_user(db, "b@example.com")
    first = await create_meld(db, user.id, "First")
    second = await create_meld(db, user.id, "Second")

    melds = await list_melds(db, user.id)
    assert [m.id for m in melds] == [second.id, first.id]


async def test_list_melds_excludes_other_users_melds(db):
    alice = await _make_user(db, "alice@example.com")
    bob = await _make_user(db, "bob@example.com")
    await create_meld(db, alice.id, "Alice's Meld")

    assert await list_melds(db, bob.id) == []


async def test_list_melds_excludes_soft_deleted(db):
    user = await _make_user(db, "c@example.com")
    keep = await create_meld(db, user.id, "Keep")
    drop = await create_meld(db, user.id, "Drop")

    assert await soft_delete_meld(db, user.id, drop.id) is True

    melds = await list_melds(db, user.id)
    assert [m.id for m in melds] == [keep.id]


async def test_get_meld_returns_none_for_another_users_meld(db):
    alice = await _make_user(db, "alice2@example.com")
    bob = await _make_user(db, "bob2@example.com")
    meld = await create_meld(db, alice.id, "Private")

    assert await get_meld(db, bob.id, meld.id) is None


async def test_get_meld_returns_none_for_an_unknown_id(db):
    user = await _make_user(db, "d@example.com")
    assert await get_meld(db, user.id, uuid.uuid4()) is None


async def test_soft_delete_does_not_remove_the_row(db):
    user = await _make_user(db, "e@example.com")
    meld = await create_meld(db, user.id, "Doomed")

    await soft_delete_meld(db, user.id, meld.id)

    assert await get_meld(db, user.id, meld.id) is None
    # The row still exists, it is only filtered out.
    assert meld.deleted_at is not None


async def test_soft_delete_returns_false_for_another_users_meld(db):
    alice = await _make_user(db, "alice3@example.com")
    bob = await _make_user(db, "bob3@example.com")
    meld = await create_meld(db, alice.id, "Alice's")

    assert await soft_delete_meld(db, bob.id, meld.id) is False


async def test_rename_meld_changes_the_title(db):
    user = await _make_user(db, "f@example.com")
    meld = await create_meld(db, user.id, "Old Name")

    renamed = await rename_meld(db, user.id, meld.id, "New Name")
    assert renamed is not None
    assert renamed.title == "New Name"


async def test_save_story_stores_the_text_and_its_hash(db):
    user = await _make_user(db, "g@example.com")
    meld = await create_meld(db, user.id, "Storied")

    story = await save_story(db, user.id, meld.id, "Once upon a time")
    assert story is not None
    assert story.text == "Once upon a time"
    assert story.input_hash == story_hash("Once upon a time")


async def test_save_story_is_idempotent_for_unchanged_text(db):
    """Autosave fires often. Re-saving identical text must produce an
    identical hash, so that M2's staleness check sees no change."""
    user = await _make_user(db, "h@example.com")
    meld = await create_meld(db, user.id, "Same")

    first = await save_story(db, user.id, meld.id, "unchanged")
    first_hash = first.input_hash
    second = await save_story(db, user.id, meld.id, "unchanged")

    assert second.input_hash == first_hash


async def test_save_story_changes_the_hash_when_the_text_changes(db):
    user = await _make_user(db, "i@example.com")
    meld = await create_meld(db, user.id, "Changing")

    first = await save_story(db, user.id, meld.id, "version one")
    first_hash = first.input_hash
    second = await save_story(db, user.id, meld.id, "version two")

    assert second.input_hash != first_hash


async def test_save_story_returns_none_for_another_users_meld(db):
    alice = await _make_user(db, "alice4@example.com")
    bob = await _make_user(db, "bob4@example.com")
    meld = await create_meld(db, alice.id, "Alice's")

    assert await save_story(db, bob.id, meld.id, "intruder") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_melds_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'framemeld.melds'`

- [ ] **Step 3: Write the service**

Create `services/backend/src/framemeld/melds/__init__.py` (empty), then `services/backend/src/framemeld/melds/service.py`:

```python
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.orm import selectinload

from framemeld.hashing import canonical_hash
from framemeld.models.meld import Meld, Story


def story_hash(text: str) -> str:
    """The story is the root of the dependency graph, so its hash is the
    seed for every downstream input_hash in M2 onwards."""
    return canonical_hash({"kind": "story", "text": text})


async def create_meld(db: DbSession, user_id: uuid.UUID, title: str) -> Meld:
    # created_at and updated_at are set explicitly rather than left to the
    # columns' server_default=func.now(). Postgres's now() is frozen for the
    # whole transaction, so two Melds created in the same transaction (as
    # happens in tests) would otherwise share a created_at and leave
    # "newest first" ordering undefined between them.
    #
    # Both come from one value so a new Meld cannot show updated_at before
    # created_at if the app and database clocks differ. The server defaults
    # stay in place as the fallback for any other insert path.
    now = datetime.now(UTC)
    meld = Meld(user_id=user_id, title=title, created_at=now, updated_at=now)
    db.add(meld)
    await db.flush()

    # Every Meld has a Story from birth, so the editor never has to handle
    # a missing one.
    db.add(Story(meld_id=meld.id, text="", input_hash=story_hash("")))
    await db.flush()
    return meld


async def list_melds(db: DbSession, user_id: uuid.UUID) -> list[Meld]:
    result = await db.execute(
        select(Meld)
        .where(Meld.user_id == user_id, Meld.deleted_at.is_(None))
        .order_by(Meld.created_at.desc())
    )
    return list(result.scalars().all())


async def get_meld(db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID) -> Meld | None:
    result = await db.execute(
        select(Meld)
        .options(selectinload(Meld.story))
        .where(Meld.id == meld_id, Meld.user_id == user_id, Meld.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def rename_meld(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, title: str
) -> Meld | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None
    meld.title = title
    await db.flush()
    return meld


async def soft_delete_meld(db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID) -> bool:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return False
    meld.deleted_at = datetime.now(UTC)
    await db.flush()
    return True


async def save_story(
    db: DbSession, user_id: uuid.UUID, meld_id: uuid.UUID, text: str
) -> Story | None:
    meld = await get_meld(db, user_id, meld_id)
    if meld is None:
        return None

    result = await db.execute(select(Story).where(Story.meld_id == meld_id))
    story = result.scalar_one()
    story.text = text
    story.input_hash = story_hash(text)
    await db.flush()
    return story
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_melds_service.py -v`
Expected: PASS, 13 tests

- [ ] **Step 5: Commit**

```bash
git add services/backend/src/framemeld/melds services/backend/tests/test_melds_service.py
git commit -m "feat: add Meld service layer with user scoping and soft delete"
```

---

## Task 10: Meld HTTP endpoints

**Files:**
- Create: `services/backend/src/framemeld/melds/schemas.py`
- Create: `services/backend/src/framemeld/melds/router.py`
- Modify: `services/backend/src/framemeld/api_main.py`
- Test: `services/backend/tests/test_melds_api.py`

**Interfaces:**
- Consumes: everything from `melds/service.py`, `CurrentUser`, `get_session`
- Produces:
  - `POST /melds` `{title?}` → 201 `MeldOut`
  - `GET /melds` → 200 `list[MeldSummary]`
  - `GET /melds/{meld_id}` → 200 `MeldDetail` (includes `story`)
  - `PATCH /melds/{meld_id}` `{title}` → 200 `MeldOut`
  - `DELETE /melds/{meld_id}` → 204
  - `PUT /melds/{meld_id}/story` `{text}` → 200 `StoryOut`
  - Pydantic models `MeldCreate`, `MeldRename`, `StorySave`, `MeldOut`, `MeldSummary`, `MeldDetail`, `StoryOut`

- [ ] **Step 1: Write the failing tests**

Create `services/backend/tests/test_melds_api.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_melds_api.py -v`
Expected: FAIL — every request returns 404, because no `/melds` routes are registered.

- [ ] **Step 3: Write the schemas**

Create `services/backend/src/framemeld/melds/schemas.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MeldCreate(BaseModel):
    title: str = Field(default="Untitled Meld", min_length=1, max_length=200)


class MeldRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class StorySave(BaseModel):
    # No min_length: clearing the editor is a valid save.
    text: str = Field(max_length=200_000)


class StoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str
    input_hash: str
    updated_at: datetime


class MeldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    stage: str
    status: str
    aspect_ratio: str
    created_at: datetime
    updated_at: datetime


class MeldSummary(MeldOut):
    pass


class MeldDetail(MeldOut):
    story: StoryOut | None
```

**`MeldOut` deliberately has no `story` field, and must not gain one.** Only
`get_meld` eager-loads the relationship (`selectinload(Meld.story)`);
`list_melds` and `create_meld` return Melds whose `story` is unloaded. Reading
an unloaded relationship under asyncio raises `MissingGreenlet`, so adding
`story` to `MeldOut` would break `GET /melds` and `POST /melds` immediately —
and only at runtime, since Pydantic cannot see the difference. `MeldDetail` is
the one model that exposes it, and it is used by exactly one route, the one
whose service call eager-loads. If a future endpoint needs the story, give it
`MeldDetail` and a service call that eager-loads, rather than widening
`MeldOut`.

- [ ] **Step 4: Write the router**

Create `services/backend/src/framemeld/melds/router.py`:

```python
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.auth.deps import CurrentUser
from framemeld.db import get_session
from framemeld.melds import service
from framemeld.melds.schemas import (
    MeldCreate,
    MeldDetail,
    MeldOut,
    MeldRename,
    MeldSummary,
    StoryOut,
    StorySave,
)

router = APIRouter(prefix="/melds", tags=["melds"])

Db = Annotated[DbSession, Depends(get_session)]

NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meld not found")


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MeldOut)
async def create_meld(payload: MeldCreate, user: CurrentUser, db: Db):
    meld = await service.create_meld(db, user.id, payload.title)
    await db.commit()
    await db.refresh(meld)
    return meld


@router.get("", response_model=list[MeldSummary])
async def list_melds(user: CurrentUser, db: Db):
    return await service.list_melds(db, user.id)


@router.get("/{meld_id}", response_model=MeldDetail)
async def get_meld(meld_id: uuid.UUID, user: CurrentUser, db: Db):
    meld = await service.get_meld(db, user.id, meld_id)
    if meld is None:
        raise NOT_FOUND
    return meld


@router.patch("/{meld_id}", response_model=MeldOut)
async def rename_meld(meld_id: uuid.UUID, payload: MeldRename, user: CurrentUser, db: Db):
    meld = await service.rename_meld(db, user.id, meld_id, payload.title)
    if meld is None:
        raise NOT_FOUND
    await db.commit()
    await db.refresh(meld)
    return meld


@router.delete("/{meld_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meld(meld_id: uuid.UUID, user: CurrentUser, db: Db) -> None:
    if not await service.soft_delete_meld(db, user.id, meld_id):
        raise NOT_FOUND
    await db.commit()


@router.put("/{meld_id}/story", response_model=StoryOut)
async def save_story(meld_id: uuid.UUID, payload: StorySave, user: CurrentUser, db: Db):
    story = await service.save_story(db, user.id, meld_id, payload.text)
    if story is None:
        raise NOT_FOUND
    await db.commit()
    await db.refresh(story)
    return story
```

- [ ] **Step 5: Register the router**

In `services/backend/src/framemeld/api_main.py`, add:

```python
from framemeld.melds.router import router as melds_router

app.include_router(melds_router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `conda run --no-capture-output -n framemeld pytest tests/test_melds_api.py -v`
Expected: PASS, 16 tests

- [ ] **Step 7: Run the full suite and the linter**

Run: `conda run --no-capture-output -n framemeld pytest -v && conda run --no-capture-output -n framemeld ruff check src tests`
Expected: all tests PASS, ruff reports no errors.

- [ ] **Step 8: Commit**

```bash
git add services/backend/src/framemeld/melds services/backend/src/framemeld/api_main.py services/backend/tests/test_melds_api.py
git commit -m "feat: add Meld CRUD and story autosave endpoints"
```

---

## Task 11: Next.js scaffold and typed API client

**Files:**
- Create: `apps/web/` (via `create-next-app`)
- Create: `apps/web/lib/api.ts`
- Create: `apps/web/.env.local`
- Modify: `apps/web/app/page.tsx`

**Interfaces:**
- Consumes: the HTTP API from Tasks 7 and 10
- Produces: `apps/web/lib/api.ts` exporting types `User`, `MeldSummary`, `MeldDetail`, `Story`, and functions `signup`, `login`, `logout`, `me`, `listMelds`, `createMeld`, `getMeld`, `renameMeld`, `deleteMeld`, `saveStory`, plus the `ApiError` class.

- [ ] **Step 1: Scaffold the app**

```bash
cd /f/FrameMeld/apps
npx create-next-app@latest web --typescript --tailwind --app --eslint --no-src-dir --import-alias "@/*"
```

- [ ] **Step 2: Point the frontend at the API**

Create `apps/web/.env.local`:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

- [ ] **Step 3: Write the API client**

Create `apps/web/lib/api.ts`:

```typescript
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type User = { id: string; email: string };

export type MeldSummary = {
  id: string;
  title: string;
  stage: string;
  status: string;
  aspect_ratio: string;
  created_at: string;
  updated_at: string;
};

export type Story = { text: string; input_hash: string; updated_at: string };

export type MeldDetail = MeldSummary & { story: Story | null };

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    // Required so the HttpOnly session cookie travels cross-origin
    // (web on :3000, api on :8000). Omitting this is the single most
    // common cause of unexplained 401s here.
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Body was not JSON; keep the status text.
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const signup = (email: string, password: string) =>
  request<User>("/auth/signup", { method: "POST", body: JSON.stringify({ email, password }) });

export const login = (email: string, password: string) =>
  request<User>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });

export const logout = () => request<void>("/auth/logout", { method: "POST" });

export const me = () => request<User>("/auth/me");

export const listMelds = () => request<MeldSummary[]>("/melds");

export const createMeld = (title?: string) =>
  request<MeldSummary>("/melds", {
    method: "POST",
    body: JSON.stringify(title ? { title } : {}),
  });

export const getMeld = (id: string) => request<MeldDetail>(`/melds/${id}`);

export const renameMeld = (id: string, title: string) =>
  request<MeldSummary>(`/melds/${id}`, { method: "PATCH", body: JSON.stringify({ title }) });

export const deleteMeld = (id: string) => request<void>(`/melds/${id}`, { method: "DELETE" });

export const saveStory = (id: string, text: string) =>
  request<Story>(`/melds/${id}/story`, { method: "PUT", body: JSON.stringify({ text }) });
```

- [ ] **Step 4: Make the root page route by auth state**

Replace `apps/web/app/page.tsx` with:

```tsx
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { me } from "@/lib/api";

export default function Home() {
  const router = useRouter();

  useEffect(() => {
    me()
      .then(() => router.replace("/melds"))
      .catch(() => router.replace("/login"));
  }, [router]);

  return <main className="p-8 text-sm text-neutral-500">Loading…</main>;
}
```

- [ ] **Step 5: Verify both services run together**

In one terminal:

```bash
cd /f/FrameMeld/services/backend && conda run --no-capture-output -n framemeld uvicorn framemeld.api_main:app --reload --port 8000
```

In another:

```bash
cd /f/FrameMeld/apps/web && npm run dev
```

Open `http://localhost:3000`. Expected: it redirects to `/login` (a 404 page for now — that page arrives in Task 12) rather than hanging or throwing a CORS error in the console.

- [ ] **Step 6: Commit**

```bash
cd /f/FrameMeld
git add apps/web
git commit -m "feat: scaffold Next.js frontend with typed API client"
```

---

## Task 12: Auth UI

**Files:**
- Create: `apps/web/app/login/page.tsx`
- Create: `apps/web/app/signup/page.tsx`
- Create: `apps/web/components/AuthForm.tsx`

**Interfaces:**
- Consumes: `login`, `signup`, `ApiError` from `@/lib/api`
- Produces: `AuthForm` component with props `{ mode: "login" | "signup" }`

**Note on why there is a shared component:** the login and signup forms differ only in which API call they make and their copy. Two near-identical 80-line pages would drift.

- [ ] **Step 1: Write the shared form**

Create `apps/web/components/AuthForm.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ApiError, login, signup } from "@/lib/api";

const COPY = {
  login: {
    heading: "Log in to FrameMeld",
    submit: "Log in",
    altText: "Need an account?",
    altLabel: "Sign up",
    altHref: "/signup",
  },
  signup: {
    heading: "Create your FrameMeld account",
    submit: "Sign up",
    altText: "Already have an account?",
    altLabel: "Log in",
    altHref: "/login",
  },
} as const;

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const copy = COPY[mode];

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "signup") {
        await signup(email, password);
      }
      await login(email, password);
      router.replace("/melds");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("That email is already registered.");
      } else if (err instanceof ApiError && err.status === 401) {
        setError("Invalid email or password.");
      } else if (err instanceof ApiError && err.status === 422) {
        setError("Enter a valid email and a password of at least 8 characters.");
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center p-8">
      <h1 className="mb-6 text-2xl font-semibold">{copy.heading}</h1>

      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1 text-sm">
          Email
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded border border-neutral-300 px-3 py-2"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          Password
          <input
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded border border-neutral-300 px-3 py-2"
          />
        </label>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <button
          type="submit"
          disabled={busy}
          className="rounded bg-neutral-900 px-4 py-2 text-white disabled:opacity-50"
        >
          {busy ? "Working…" : copy.submit}
        </button>
      </form>

      <p className="mt-6 text-sm text-neutral-600">
        {copy.altText}{" "}
        <Link href={copy.altHref} className="underline">
          {copy.altLabel}
        </Link>
      </p>
    </main>
  );
}
```

- [ ] **Step 2: Write the two pages**

Create `apps/web/app/login/page.tsx`:

```tsx
import { AuthForm } from "@/components/AuthForm";

export default function LoginPage() {
  return <AuthForm mode="login" />;
}
```

Create `apps/web/app/signup/page.tsx`:

```tsx
import { AuthForm } from "@/components/AuthForm";

export default function SignupPage() {
  return <AuthForm mode="signup" />;
}
```

- [ ] **Step 3: Verify manually**

With both services running, visit `http://localhost:3000/signup`. Check all four:

1. Signing up with a valid email and an 8+ character password lands on `/melds`.
2. Signing up with the same email again shows "That email is already registered."
3. Logging out is not possible yet — clear the cookie in devtools, then log in at `/login` and confirm it lands on `/melds`.
4. Logging in with a wrong password shows "Invalid email or password."

- [ ] **Step 4: Commit**

```bash
git add apps/web/app/login apps/web/app/signup apps/web/components
git commit -m "feat: add login and signup pages"
```

---

## Task 13: Melds dashboard

**Files:**
- Create: `apps/web/app/melds/page.tsx`
- Create: `apps/web/components/MeldCard.tsx`

**Interfaces:**
- Consumes: `listMelds`, `createMeld`, `renameMeld`, `deleteMeld`, `logout`, `me`, `MeldSummary`
- Produces: `MeldCard` component with props `{ meld: MeldSummary; onRename: (id: string, title: string) => void; onDelete: (id: string) => void }`

- [ ] **Step 1: Write the card component**

Create `apps/web/components/MeldCard.tsx`:

```tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import type { MeldSummary } from "@/lib/api";

const STAGE_LABELS: Record<string, string> = {
  story: "Story",
  script: "Script",
  cast: "Cast",
  shots: "Shots",
  film: "Film",
};

export function MeldCard({
  meld,
  onRename,
  onDelete,
}: {
  meld: MeldSummary;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(meld.title);

  function commitRename() {
    const trimmed = draftTitle.trim();
    if (trimmed && trimmed !== meld.title) {
      onRename(meld.id, trimmed);
    } else {
      setDraftTitle(meld.title);
    }
    setEditing(false);
  }

  return (
    <li className="flex items-center justify-between rounded border border-neutral-200 p-4">
      <div className="min-w-0">
        {editing ? (
          <input
            autoFocus
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitRename();
              if (e.key === "Escape") {
                setDraftTitle(meld.title);
                setEditing(false);
              }
            }}
            className="rounded border border-neutral-300 px-2 py-1"
          />
        ) : (
          <Link href={`/meld/${meld.id}`} className="block truncate font-medium hover:underline">
            {meld.title}
          </Link>
        )}
        <p className="mt-1 text-xs text-neutral-500">
          {STAGE_LABELS[meld.stage] ?? meld.stage} · updated{" "}
          {new Date(meld.updated_at).toLocaleDateString()}
        </p>
      </div>

      <div className="flex shrink-0 gap-3 text-sm">
        <button onClick={() => setEditing(true)} className="text-neutral-600 hover:underline">
          Rename
        </button>
        <button onClick={() => onDelete(meld.id)} className="text-red-600 hover:underline">
          Delete
        </button>
      </div>
    </li>
  );
}
```

- [ ] **Step 2: Write the dashboard page**

Create `apps/web/app/melds/page.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { MeldCard } from "@/components/MeldCard";
import {
  createMeld,
  deleteMeld,
  listMelds,
  logout,
  renameMeld,
  type MeldSummary,
} from "@/lib/api";

export default function MeldsPage() {
  const router = useRouter();
  const [melds, setMelds] = useState<MeldSummary[] | null>(null);

  const refresh = useCallback(async () => {
    try {
      setMelds(await listMelds());
    } catch {
      router.replace("/login");
    }
  }, [router]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function onNewMeld() {
    const created = await createMeld();
    router.push(`/meld/${created.id}`);
  }

  async function onRename(id: string, title: string) {
    await renameMeld(id, title);
    await refresh();
  }

  async function onDelete(id: string) {
    if (!confirm("Delete this Meld? You can't undo this from the app.")) return;
    await deleteMeld(id);
    await refresh();
  }

  async function onLogout() {
    await logout();
    router.replace("/login");
  }

  return (
    <main className="mx-auto max-w-2xl p-8">
      <header className="mb-8 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">My Melds</h1>
        <div className="flex items-center gap-4">
          <button
            onClick={onNewMeld}
            className="rounded bg-neutral-900 px-4 py-2 text-sm text-white"
          >
            New Meld
          </button>
          <button onClick={onLogout} className="text-sm text-neutral-600 hover:underline">
            Log out
          </button>
        </div>
      </header>

      {melds === null && <p className="text-sm text-neutral-500">Loading…</p>}

      {melds !== null && melds.length === 0 && (
        <div className="rounded border border-dashed border-neutral-300 p-12 text-center">
          <p className="mb-4 text-neutral-600">You haven&apos;t started a Meld yet.</p>
          <button onClick={onNewMeld} className="rounded bg-neutral-900 px-4 py-2 text-sm text-white">
            Write your first story
          </button>
        </div>
      )}

      {melds !== null && melds.length > 0 && (
        <ul className="flex flex-col gap-3">
          {melds.map((meld) => (
            <MeldCard key={meld.id} meld={meld} onRename={onRename} onDelete={onDelete} />
          ))}
        </ul>
      )}
    </main>
  );
}
```

- [ ] **Step 3: Verify manually**

With both services running and logged in, check all six:

1. `/melds` shows the empty state for a fresh account.
2. "New Meld" creates one and navigates to `/meld/{id}` (a 404 until Task 14).
3. Going back to `/melds` shows the new Meld with stage "Story".
4. Rename works via Enter, and Escape cancels it.
5. Delete asks for confirmation and removes the Meld from the list.
6. "Log out" returns to `/login`, and visiting `/melds` afterwards redirects to `/login`.

- [ ] **Step 4: Commit**

```bash
git add apps/web/app/melds apps/web/components/MeldCard.tsx
git commit -m "feat: add Melds dashboard with create, rename, and delete"
```

---

## Task 14: Story editor with debounced autosave

**Files:**
- Create: `apps/web/lib/useAutosave.ts`
- Create: `apps/web/app/meld/[id]/page.tsx`

**Interfaces:**
- Consumes: `getMeld`, `saveStory`, `MeldDetail`
- Produces: `useAutosave<T>({ value, onSave, delayMs })` returning `{ status: "idle" | "unsaved" | "saving" | "saved" | "error" }`

**This task delivers the M1 acceptance criterion.** The 800 ms debounce is a global constraint from spec §4.4.

- [ ] **Step 1: Write the autosave hook**

Create `apps/web/lib/useAutosave.ts`:

```typescript
"use client";

import { useEffect, useRef, useState } from "react";

export type SaveStatus = "idle" | "unsaved" | "saving" | "saved" | "error";

export function useAutosave<T>({
  value,
  onSave,
  delayMs = 800,
}: {
  value: T;
  onSave: (value: T) => Promise<void>;
  delayMs?: number;
}): { status: SaveStatus } {
  const [status, setStatus] = useState<SaveStatus>("idle");

  // The value as the server last confirmed it. Comparing against this
  // rather than the previous render's value means an in-flight save that
  // resolves late cannot mark stale text as "saved".
  const savedRef = useRef<T>(value);
  const firstRunRef = useRef(true);

  useEffect(() => {
    // Skip the initial mount: the value just came from the server, so
    // saving it back immediately would be a pointless write.
    if (firstRunRef.current) {
      firstRunRef.current = false;
      savedRef.current = value;
      return;
    }

    if (value === savedRef.current) return;

    setStatus("unsaved");

    const timer = setTimeout(async () => {
      const attempted = value;
      setStatus("saving");
      try {
        await onSave(attempted);
        savedRef.current = attempted;
        setStatus("saved");
      } catch {
        setStatus("error");
      }
    }, delayMs);

    // Every keystroke clears the pending timer, so only the last one in a
    // burst actually fires.
    return () => clearTimeout(timer);
  }, [value, onSave, delayMs]);

  // Warn on close while a save is pending or failed, so work is not lost.
  useEffect(() => {
    if (status !== "unsaved" && status !== "error") return;

    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [status]);

  return { status };
}
```

- [ ] **Step 2: Write the editor page**

Create `apps/web/app/meld/[id]/page.tsx`:

```tsx
"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getMeld, saveStory, type MeldDetail } from "@/lib/api";
import { useAutosave, type SaveStatus } from "@/lib/useAutosave";

const STATUS_TEXT: Record<SaveStatus, string> = {
  idle: "",
  unsaved: "Unsaved changes",
  saving: "Saving…",
  saved: "Saved",
  error: "Couldn't save — check your connection",
};

export default function MeldEditorPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();

  const [meld, setMeld] = useState<MeldDetail | null>(null);
  const [text, setText] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    getMeld(id)
      .then((detail) => {
        setMeld(detail);
        setText(detail.story?.text ?? "");
        setLoaded(true);
      })
      .catch(() => router.replace("/melds"));
  }, [id, router]);

  const onSave = useCallback(
    async (value: string) => {
      await saveStory(id, value);
    },
    [id],
  );

  const { status } = useAutosave({ value: text, onSave, delayMs: 800 });

  if (!loaded || meld === null) {
    return <main className="p-8 text-sm text-neutral-500">Loading…</main>;
  }

  const wordCount = text.trim() ? text.trim().split(/\s+/).length : 0;

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col p-8">
      <header className="mb-6 flex items-baseline justify-between">
        <div>
          <Link href="/melds" className="text-sm text-neutral-500 hover:underline">
            ← My Melds
          </Link>
          <h1 className="mt-1 text-2xl font-semibold">{meld.title}</h1>
        </div>
        <span
          className={`text-sm ${status === "error" ? "text-red-600" : "text-neutral-500"}`}
          role="status"
          aria-live="polite"
        >
          {STATUS_TEXT[status]}
        </span>
      </header>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Write your story here. Anything from a paragraph to a few pages — you'll turn it into a script next."
        className="min-h-[60vh] flex-1 resize-none rounded border border-neutral-200 p-6 font-serif text-lg leading-relaxed outline-none focus:border-neutral-400"
      />

      <footer className="mt-4 flex items-center justify-between text-sm text-neutral-500">
        <span>
          {wordCount} {wordCount === 1 ? "word" : "words"}
        </span>
        <span className="text-neutral-400">Next up: Script (arrives in M2)</span>
      </footer>
    </main>
  );
}
```

- [ ] **Step 3: Verify the autosave behaviour manually**

With both services running:

1. Open a Meld, type a sentence. Within a second the indicator goes `Unsaved changes` → `Saving…` → `Saved`.
2. Type continuously for five seconds. The indicator stays on `Unsaved changes` and only saves ~800 ms after you stop — confirming the debounce, not a save per keystroke.
3. Open the Network tab and type a 20-character sentence in one burst. Expected: **one** `PUT /melds/{id}/story` request, not 20.
4. Reload the page. The text is still there.
5. Stop the backend (`Ctrl-C`), type more. The indicator shows the red error message.
6. Restart the backend, type one more character. It recovers to `Saved`.

- [ ] **Step 4: Verify the M1 acceptance criterion end to end**

This is the milestone's definition of done, so run it as a single clean pass:

1. Open a private/incognito window at `http://localhost:3000`.
2. Sign up with a fresh email.
3. Click "New Meld", rename it to "The Lighthouse".
4. Type two paragraphs. Wait for "Saved".
5. **Close the browser window entirely.**
6. Open a new window at `http://localhost:3000`. You are still logged in (the session cookie has a 30-day TTL).
7. `/melds` lists "The Lighthouse". Open it. Both paragraphs are intact.

- [ ] **Step 5: Run the full backend suite one more time**

Run: `cd /f/FrameMeld/services/backend && conda run --no-capture-output -n framemeld pytest -v && conda run --no-capture-output -n framemeld ruff check src tests`
Expected: all tests PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
cd /f/FrameMeld
git add apps/web/lib/useAutosave.ts apps/web/app/meld
git commit -m "feat: add story editor with debounced autosave"
```

---

## Task 15: README and M1 close-out

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything built in Tasks 1-14
- Produces: nothing consumed by later tasks

- [ ] **Step 1: Write the README**

Replace `README.md` with the following. (The outer fence here is four
backticks because the content itself contains fenced blocks.)

````markdown
# FrameMeld

Turn a written story into a short AI-generated film.

- **Design spec:** [docs/superpowers/specs/2026-09-09-framemeld-design.md](docs/superpowers/specs/2026-09-09-framemeld-design.md)
- **Plans:** [docs/superpowers/plans/](docs/superpowers/plans/)

## Status

**M1 (Foundation) complete.** A user can sign up, create a Meld, write a story,
and find it intact on return. No AI calls yet — those start in M2.

## Layout

| Path | What |
|---|---|
| `services/backend` | FastAPI api (and, from M2, the arq worker) |
| `apps/web` | Next.js frontend |
| `docs/superpowers/specs` | Design spec |
| `docs/superpowers/plans` | Per-milestone implementation plans |

## Running locally

Requires Docker, conda (miniconda is fine), and Node 20+.

```bash
cp .env.example .env
docker compose up -d
docker compose exec postgres createdb -U framemeld framemeld_test  # once

cd services/backend
conda env create -f environment.yml   # once; creates the `framemeld` env
conda run --no-capture-output -n framemeld pip install -e ".[dev]"
conda run --no-capture-output -n framemeld alembic upgrade head
conda run --no-capture-output -n framemeld uvicorn framemeld.api_main:app --reload --port 8000

# in a second terminal
cd apps/web && npm install && npm run dev
```

Then open <http://localhost:3000>. API docs are at <http://localhost:8000/docs>.

## Tests

```bash
cd services/backend && conda run --no-capture-output -n framemeld pytest -v && conda run --no-capture-output -n framemeld ruff check src tests
```
````

- [ ] **Step 2: Confirm a clean checkout works**

```bash
cd /f/FrameMeld
git status
```

Expected: no untracked files that should have been committed, and nothing in `.env` or `node_modules` staged.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document M1 setup and status"
```

---

## M1 Acceptance Criteria

From spec §9. All must hold before M2 begins.

- [ ] A user signs up, creates a Meld, types a story, closes the tab, and finds the text intact on return.
- [ ] `conda run --no-capture-output -n framemeld pytest` passes — 71 tests: health 1, hashing 9, passwords 5, user models 3, sessions 8, auth API 12, meld models 4, meld service 13, meld API 16.
- [ ] `conda run --no-capture-output -n framemeld ruff check src tests` is clean.
- [ ] A logged-out request to any `/melds` endpoint returns 401.
- [ ] A request for another user's Meld returns **404**, not 403.
- [ ] Deleting a Meld sets `deleted_at` and leaves the row in the table.
- [ ] Typing a burst of characters produces **one** save request, not one per keystroke.
- [ ] `stories.input_hash` changes when the text changes and is stable when it does not.

---

## What M1 deliberately does not include

Recorded so a reviewer does not flag these as omissions.

| Absent | Arrives in |
|---|---|
| Redis, the arq worker, the `jobs` table | M2 |
| Any AI provider call, any provider adapter | M2 |
| `scenes`, `shots`, staleness propagation | M2 |
| `characters`, credit ledger, pricing, quote/hold/settle | M3 |
| `assets` table and object storage | M4 |
| The Remotion render service | M5 |
| Email verification, password reset, rate limiting | post-M6 (noted below) |

**Two security gaps that are acceptable at M1 but must not ship to real users:**

1. **No login rate limiting.** The login endpoint is brute-forceable. Add a Redis-backed limiter in M2, when Redis arrives.
2. **`secure=False` on the session cookie.** Correct for `http://localhost`; must become `True` behind TLS before any public deploy.

Both are listed here rather than in a task because M1 has no deployment step.
