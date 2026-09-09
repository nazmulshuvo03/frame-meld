# FrameMeld

Turn a written story into a short AI-generated film.

A **Meld** is the container a user creates and moves through five stages:
Story → Script → Cast → Shots → Film.

- **Design spec:** [docs/superpowers/specs/2026-09-09-framemeld-design.md](docs/superpowers/specs/2026-09-09-framemeld-design.md)
- **Plans:** [docs/superpowers/plans/](docs/superpowers/plans/)

## Status

**M1 (Foundation) complete.** A user can sign up, log in, create a Meld, write
a story in it, and find the text intact after closing the tab and coming
back. No AI calls yet — those start in M2.

What's verified and how:

- The backend test suite (74 tests: auth, sessions, password hashing, Meld
  CRUD, story save/hash) passes — see [Tests](#tests) below.
- Ownership checks are enforced at the API: a logged-out request to any
  `/melds` endpoint returns 401, and a request for another user's Meld
  returns 404 (not 403). Covered by `test_melds_api.py` /
  `test_melds_service.py`.
- Deleting a Meld soft-deletes it (`deleted_at` is set; the row stays).
- The story editor's autosave debounce (one save request per pause in
  typing, not one per keystroke) is implemented in
  `apps/web/lib/useAutosave.ts`. **It has not been driven through a real
  browser in an automated session** — the debounce behavior is verified by
  code review and a one-off simulation of the hook's decision logic that was
  not retained in the repo, not by an actual person typing into the page.
  Treat the end-to-end "type → close tab → reopen → text is there" claim as
  plausible but not browser-verified.

## Layout

| Path | What |
|---|---|
| `services/backend` | FastAPI api (and, from M2, the arq worker) |
| `apps/web` | Next.js frontend (signup/login, Meld list, story editor) |
| `docs/superpowers/specs` | Design spec |
| `docs/superpowers/plans` | Per-milestone implementation plans |

## Running locally

Requires Docker, conda (miniconda is fine), and Node 20+ (developed and
tested against Node v25.9.0 with Next.js 16.3.4 / React 19.2.8).

```bash
cp .env.example .env
docker compose up -d --wait
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

`alembic upgrade head` applies two migrations: `7d9b40163474` (users and
sessions) then `8cea41eb5150` (melds and stories).

## Tests

```bash
cd services/backend
conda run --no-capture-output -n framemeld pytest -v
conda run --no-capture-output -n framemeld ruff check src tests
conda run --no-capture-output -n framemeld ruff format --check src tests
```

`pytest` passes: **74 passed**. `ruff check` and `ruff format --check` are
both clean against the explicit rule selection in `pyproject.toml`.
`config.py` resolves `.env` relative to this file's own location rather
than the working directory, so `pytest` also works unmodified from the
repo root.

Frontend: `cd apps/web && npm run build` compiles and prerenders all
routes cleanly. `npm run lint` and `npx tsc --noEmit` are also clean.
There is no frontend test suite in M1.

## Known gaps

Intentional M1 scope cuts and open issues, so a reviewer doesn't mistake
them for oversights:

- **No login rate limiting.** The login endpoint is brute-forceable as
  written. A Redis-backed limiter arrives in M2, when Redis is introduced
  for the job queue.
- **`secure=False` on the session cookie** (`src/framemeld/auth/router.py`).
  Correct for `http://localhost`; must become `True` behind TLS before any
  public deploy.
- Also absent, arriving in later milestones: Redis, the arq worker, and
  the `jobs` table; any AI provider call or adapter; `scenes`, `shots`,
  and staleness propagation (M2); `characters`, the credit ledger,
  pricing, and quote/hold/settle (M3); the `assets` table and object
  storage (M4); the Remotion render service (M5); email verification and
  password reset (post-M6).

There is no deployment story yet — M1 has no deployment step.
