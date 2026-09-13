# FrameMeld

Turn a written story into a short AI-generated film.

A **Meld** is the container a user creates and moves through five stages:
Story → Script → Cast → Shots → Film.

- **Design spec:** [docs/superpowers/specs/2026-09-09-framemeld-design.md](docs/superpowers/specs/2026-09-09-framemeld-design.md)
- **Plans:** [docs/superpowers/plans/](docs/superpowers/plans/)

## Status

**M2 (Script) complete.** A user writes a story, generates an editable
shot list from it, edits any field of any shot, and — when they change the
story — sees exactly which parts of the script have gone out of date,
without losing a word of them.

What's verified and how:

- The backend test suite (251 tests) passes, plus a frontend suite (15
  tests) covering the autosave hook and the API client — see
  [Tests](#tests) below.
- Ownership checks are enforced at the API: a logged-out request to any
  `/melds` endpoint returns 401, and a request for another user's Meld or
  script returns 404 (not 403).
- Deleting a Meld soft-deletes it (`deleted_at` is set; the row stays).
- **The whole M2 flow has been driven through a real browser against a real
  Gemini API call** (not the deterministic stub), not only reasoned about:
  - Generate produced a coherent, story-specific shot list via polling, with
    no manual reload.
  - Editing a shot's field autosaves it, survives a reload, and does **not**
    mark it stale (a user's edit is not staleness).
  - Setting a shot's duration above 5 seconds reports a save error rather
    than silently keeping the value.
  - Editing the story marks every existing scene and shot `stale` with an
    amber banner and "Out of date" pills — **and a prior hand-edit to one of
    those shots was still there, untouched**. Reverting the story to its
    exact original text cleared every stale mark with no regeneration and
    no cost.
  - Two browser tabs open on the same story: the second to save gets a 409,
    goes read-only, and shows a conflict banner — its unsaved text stays on
    screen rather than being silently discarded.
  - The story editor's autosave behavior from M1 (debounce collapsing a
    burst of keystrokes, no spurious save on open, the unmount flush, full
    text surviving a reload) is now also guarded by 11 automated Vitest
    tests, not only the hand-verification M1 shipped with.

## Layout

| Path | What |
|---|---|
| `services/backend` | FastAPI api and the arq worker (`framemeld.worker`) |
| `apps/web` | Next.js frontend — signup/login, Meld list, story editor, Script page |
| `docs/superpowers/specs` | Design spec |
| `docs/superpowers/plans` | Per-milestone implementation plans |
| `docs/superpowers/m1-decisions.md`, `m2-decisions.md` | Execution ledgers — the *why* behind non-obvious code |

## Running locally

Requires Docker, conda (miniconda is fine), and Node 20+ (developed and
tested against Node v25.9.0 with Next.js 16.3.4 / React 19.2.8).

`environment.yml` lives at the **repo root** (not `services/backend`) — it only pins the
conda-managed Python interpreter; every project dependency is installed from
`pyproject.toml` inside that env.

```bash
cp .env.example .env
docker compose up -d --wait
docker compose exec postgres createdb -U framemeld framemeld_test  # once

conda env create -f environment.yml   # once; creates the `framemeld` env, from the repo root
cd services/backend
conda run --no-capture-output -n framemeld pip install -e ".[dev]"
conda run --no-capture-output -n framemeld alembic upgrade head
conda run --no-capture-output -n framemeld uvicorn framemeld.api_main:app --reload --port 8000

# in a second terminal — the arq worker, required from M2 on for script generation
cd services/backend
conda run --no-capture-output -n framemeld arq framemeld.worker.WorkerSettings

# in a third terminal
cd apps/web && npm install && npm run dev
```

Then open <http://localhost:3000>. API docs are at <http://localhost:8000/docs>.

`alembic upgrade head` applies four migrations, in order: `7d9b40163474` (users and
sessions), `8cea41eb5150` (melds and stories), `4440a9739020` (renames
`stories.input_hash` to `content_hash`), `cd586f9b74e2` (scenes, shots, jobs).

**Environment variables of note** (see `.env.example` for the full list):

- `LLM_PROVIDER` — `stub` (default; deterministic, no network, what the test suite always
  uses), `gemini`, or `openrouter`. Set `GEMINI_API_KEY` or `OPENROUTER_API_KEY`
  accordingly. See the design spec's §6.3 for why Gemini is the current local-dev
  default and how that can change later without touching this codebase's structure.
- `NEXT_PUBLIC_API_URL` — read by the Next.js app (not `.env`, since it must be set in
  `apps/web`'s own environment or `apps/web/.env.local`); defaults to
  `http://localhost:8000` if unset.

## Tests

```bash
cd services/backend
conda run --no-capture-output -n framemeld pytest -v
conda run --no-capture-output -n framemeld ruff check src tests
conda run --no-capture-output -n framemeld ruff format --check src tests
```

`pytest` passes: **251 passed, 2 skipped** (both are opt-in live-API calls — see
`test_providers_openrouter.py` / `test_providers_gemini.py`, run with
`FRAMEMELD_LIVE_LLM=1`). `ruff check` and `ruff format --check` are both clean against
the explicit rule selection in `pyproject.toml`. `config.py` resolves `.env` relative to
this file's own location rather than the working directory, so `pytest` also works
unmodified from the repo root. Every test always runs against the deterministic stub
provider regardless of `.env`'s `LLM_PROVIDER` (`conftest.py` forces it) — the app and
the suite are independent on this point.

```bash
cd apps/web
npm test
npx tsc --noEmit && npm run lint && npm run build
```

`npm test` (Vitest) passes: **15 passed** — 11 on `useAutosave`, 4 on the API client's
timeout and error handling. `npm run build` compiles and prerenders every static route
cleanly; `/meld/[id]` and `/meld/[id]/script` are server-rendered on demand. `npm run
lint` and `npx tsc --noEmit` are also clean.

## Architecture notes

**The two-hash rule** (design spec §4.1) is the one thing a new reader most needs and
least expects: every user-editable row carries a `content_hash` — a hash of what the row
*says right now* — and every generated row additionally carries an `input_hash` — a hash
of what *produced* it (the upstream rows' `content_hash` values, plus the provider,
model, and params). Staleness always compares against upstream's `content_hash`, never
its `input_hash`. This is what lets a human edit a character's description and correctly
invalidate every shot image generated from it: an edit changes `content_hash`
immediately, but never touches `input_hash` (which only ever describes *how the row it's
on* was generated) — so the downstream row's stored `input_hash` stops matching the
freshly recomputed expectation, and only then does it flip to `stale`. Getting this
backwards (an earlier draft of this spec did) means an edited upstream row can never
mark anything stale, and the milestone's headline feature fails silently.

## Known gaps

Intentional M2 scope cuts and open issues, so a reviewer doesn't mistake
them for oversights:

- **No add, delete, or reorder for scenes and shots.** M2 edits fields, not structure.
  Regeneration replaces the whole script rather than merging into it.
- **No moderation of story text.** Design spec §4.5 puts the prompt check before a
  credit hold is taken, so it lands with the credit system in M3.
- **Changing `LLM_MODEL` (or `LLM_PROVIDER`) marks every existing script stale.** True
  rather than surprising — regenerating really would produce something different — but
  worth knowing before swapping models, since it is a Meld-wide effect triggered by an
  operator config change, not a per-user action.
- **`GeminiLLMProvider` always reports `cost_usd=0`.** Gemini's API returns token counts,
  not a dollar figure the way OpenRouter's does. Harmless in M2 (no credit ledger
  exists yet), but M3 must add a real per-token price table before a Gemini-billed call
  can be charged against a user's balance.
- **The rate limiter fails open if Redis is unreachable** (D7), and it keys on the
  socket address, not `X-Forwarded-For`. Behind a proxy, every request shares one key
  until a proxy-headers middleware is added.
- **`secure=False` on the session cookie** (`src/framemeld/auth/router.py`). Correct for
  `http://localhost`; must become `True` behind TLS before any public deploy.
- **No job cancellation.** A queued script generation runs to completion; stopping the
  worker leaves the button on "Queued…" indefinitely — a known limitation, not a hang.
- Also absent, arriving in later milestones: `characters`, the credit ledger, pricing,
  and quote/hold/settle (M3); the `assets` table and object storage (M4); the Remotion
  render service (M5); email verification and password reset (post-M6).

There is no deployment story yet — M2 has no deployment step.
