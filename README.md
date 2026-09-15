# FrameMeld

Turn a written story into a short AI-generated film.

A **Meld** is the container a user creates and moves through five stages:
Story → Script → Cast → Shots → Film.

- **Design spec:** [docs/superpowers/specs/2026-09-09-framemeld-design.md](docs/superpowers/specs/2026-09-09-framemeld-design.md)
- **Plans:** [docs/superpowers/plans/](docs/superpowers/plans/)

## Status

**M4 (Shots) complete.** Every shot gets a still drawn from the character
sheets of the people actually in it, plus an audio clip in the speaker's
voice. An LLM pass works out who is in which shot; editing a character
marks exactly the stills that referenced them stale, and nothing else.
This is the first milestone with a multi-parent hash — a still depends on
its shot *and* on every sheet it was drawn from — and the first with three
independent provider domains, so switching image models re-draws nothing
that was spoken and switching voices re-records nothing that was drawn.

What's verified and how:

- The backend test suite (516 tests) passes, plus a frontend suite (29
  tests) covering the autosave hook, the API client, the quote button and
  the shot card — see [Tests](#tests) below.
- **The migration chain builds the schema from nothing.** All seven
  migrations applied in order against an empty database, and `alembic
  check` reports no drift between the chain and the models.
- Money rules are enforced at the API and pinned by tests: a hold is taken
  in the same transaction as the job row; a second click on unchanged
  inputs is a cache hit that charges nothing; too few credits is a **402**
  that creates no job and moves no credits; a failed generation refunds in
  full; and exactly one settle or refund may ever close a hold, because
  arq redelivers messages and an append-only ledger has no undo.
- **The whole M4 flow has been driven through a real browser** against a
  real backend and a real worker (all three providers were the stubs —
  see Known gaps):
  - The Shots page listed 11 shots in **film order**, and the batch button
    read **"Draw the 11 remaining · 132 credits"** — the price of the whole
    action on the button face, before the click.
  - Casting the shots placed characters into frames via polling with no
    reload, and moved the balance by exactly the quote. Shots with nobody
    in them rendered as "nobody in frame", which is a correct answer for an
    insert, not a missing one.
  - Asking for a still whose character had no sheet was a **422 naming
    that character**, with the balance unmoved.
  - A still for a two-character shot was drawn from **two references**, and
    two shots got two different pictures.
  - Generating audio produced a player without a reload, **in the speaker's
    voice**, with a real duration read off the WAV.
  - **Editing a character's description left everything fresh and charged
    nothing.** Editing their *appearance* and redrawing their sheet marked
    **exactly the still that referenced them** stale — the other still
    stayed fresh, and both kept their images.
  - Changing a character's voice marked their clip stale, **moved no
    still**, and left their sheet fresh.
  - "Draw the 9 remaining" drained one job at a time to 11 stills with 11
    distinct assets. Re-asking for an unchanged still returned `succeeded`
    immediately and cost zero.
  - A **real provider 429** retried three times with 2 s then 4 s backoff,
    failed terminally, and returned the balance to exactly where it
    started.
- Ownership checks are enforced at the API: another user's cast,
  character, or asset returns 404 (not 403), and a logged-out request
  returns 401.

## Layout

| Path | What |
|---|---|
| `services/backend` | FastAPI api and the arq worker (`framemeld.worker`) |
| `services/backend/src/framemeld/credits` | pricing table, and the ledger's four operations |
| `services/backend/src/framemeld/cast` | character hashing, extraction, the Cast API |
| `services/backend/src/framemeld/storage` | the `Storage` protocol and the local-disk backend |
| `services/backend/src/framemeld/assets` | the file registry and the authenticated asset route |
| `services/backend/src/framemeld/moderation` | the classifier that gates generation before a hold |
| `services/backend/src/framemeld/shots` | shot/character casting, the multi-parent hash, the Shots API |
| `services/backend/src/framemeld/providers/images` | `ImageProvider`, its stub, and the Gemini adapter |
| `services/backend/src/framemeld/providers/speech` | `SpeechProvider`, the voice list, its stub, and the Gemini TTS adapter |
| `apps/web` | Next.js frontend — signup/login, Meld list, story editor, Script page, Cast page, Shots page |
| `docs/superpowers/specs` | Design spec |
| `docs/superpowers/plans` | Per-milestone implementation plans |
| `docs/superpowers/m1-decisions.md` … `m4-decisions.md` | Execution ledgers — the *why* behind non-obvious code |

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

# in a second terminal — the arq worker, required from M2 on for generation
cd services/backend
conda run --no-capture-output -n framemeld arq framemeld.worker.WorkerSettings

# in a third terminal
cd apps/web && npm install && npm run dev
```

Then open <http://localhost:3000>. API docs are at <http://localhost:8000/docs>.

**Only ever run one of each process.** `config.py` caches settings with `@lru_cache`, so a
server or worker left running from before an `.env` edit keeps the old values and will
happily pick up jobs with them — which looks exactly like a staleness bug. This has now
cost three milestones an hour each (see `m2-decisions.md`, `m3-decisions.md` and
`m4-decisions.md`). `taskkill` on whatever holds the port is **not** enough — `conda run`
wraps the process, so the child survives. Kill `arq.exe` and `uvicorn.exe` by name and
confirm exactly one of each. A job's `cost_usd` is the cheapest way to tell which provider
actually ran: zero means the stub.

`alembic upgrade head` applies seven migrations, in order: `7d9b40163474` (users and
sessions), `8cea41eb5150` (melds and stories), `4440a9739020` (renames
`stories.input_hash` to `content_hash`), `cd586f9b74e2` (scenes, shots, jobs),
`63b07598bddb` (credits, cast, assets, `jobs.cost_usd`), `abc2ff5e822a`
(`melds.moderation_hash`), `77f9548e4df1` (`shot_characters`, `shot_images`, `audio_clips`,
and a hand-written widening of the `job_kind` CHECK — autogenerate does not diff a CHECK
body, so a new job kind produces no migration at all unless you write it).

### Granting credits

Signup grants 500 credits (`SIGNUP_GRANT_CREDITS`). There is no way to buy more — Stripe is
post-M6 — so top up by hand:

```bash
cd services/backend
conda run --no-capture-output -n framemeld \
    python -m framemeld.scripts.grant_credits someone@example.com 500
```

It refuses a negative amount rather than doubling as a clawback.

**Environment variables of note** (see `.env.example` for the full list):

- `LLM_PROVIDER` — `stub` (default; deterministic, no network, what the test suite always
  uses), `gemini`, or `openrouter`. Set `GEMINI_API_KEY` or `OPENROUTER_API_KEY`
  accordingly. See the design spec's §6.3 for why Gemini is the current local-dev
  default.
- `IMAGE_PROVIDER` / `IMAGE_MODEL` — `stub` (default) or `gemini`. **Switching this to
  `gemini` spends real money**: image generation has no free tier and is billed per image.
  Defaults to `gemini-2.5-flash-image`; the Imagen 4 family was shut down on 2026-08-17 and
  is not a valid choice.
- `SPEECH_PROVIDER` / `SPEECH_MODEL` — `stub` (default) or `gemini`. Unlike images,
  Gemini TTS has a free tier — but no call has ever succeeded from this account, so the
  adapter is unverified (M4-D9). Defaults to `gemini-2.5-flash-preview-tts`.
- `STORAGE_BACKEND` / `STORAGE_LOCAL_ROOT` — `local` (default), writing under `./var/assets`,
  which is gitignored.
- `MODERATION_ENABLED` — `false` by default, because the stub LLM cannot classify anything.
  Any deployment reaching real users must set it `true` with a real provider configured.
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

`pytest` passes: **516 passed, 4 skipped**. All four skips are opt-in live-API calls:
`FRAMEMELD_LIVE_LLM=1` for `test_providers_openrouter.py` / `test_providers_gemini.py`,
`FRAMEMELD_LIVE_IMAGE=1` for `test_providers_image_gemini.py` (a separate flag, because that
one spends money), and `FRAMEMELD_LIVE_SPEECH=1` for `test_providers_speech_gemini.py`.
`ruff check` and `ruff format --check` are both clean.

Every test always runs against the deterministic stub providers regardless of `.env` —
`conftest.py` forces the LLM and the image provider, and `SPEECH_PROVIDER` defaults to the
stub, so a `.env` edit can never make the suite reach the network or spend money — and each
test writes assets into its own temporary directory.

To prove the migration chain still builds the schema from nothing, without destroying your
dev data:

```bash
docker compose exec postgres psql -U framemeld -d postgres -c "CREATE DATABASE framemeld_migrationcheck"
cd services/backend
DATABASE_URL="postgresql+asyncpg://framemeld:framemeld@localhost:5432/framemeld_migrationcheck" \
    conda run --no-capture-output -n framemeld alembic upgrade head
DATABASE_URL="postgresql+asyncpg://framemeld:framemeld@localhost:5432/framemeld_migrationcheck" \
    conda run --no-capture-output -n framemeld alembic check
docker compose exec postgres psql -U framemeld -d postgres -c "DROP DATABASE framemeld_migrationcheck"
```

```bash
cd apps/web
npm test
npx tsc --noEmit && npm run lint && npm run build
```

`npm test` (Vitest) passes: **29 passed** — 11 on `useAutosave`, 4 on the API client's
timeout and error handling, 5 on `QuoteButton`'s affordability rules, and 9 on
`ShotMediaCard`. `npm run build` compiles and prerenders every static route cleanly;
`/meld/[id]`, `/meld/[id]/script`, `/meld/[id]/cast` and `/meld/[id]/shots` are
server-rendered on demand.

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

**An `input_hash` covers what actually produced the row, not everything upstream.** A
character sheet is drawn from `appearance` and nothing else — the image model never sees
the name, the role or the backstory — so its `input_hash` keys on
`visual_hash_for_character`, a projection of the character rather than the whole of it.
Hashing the full `content_hash` in (as M3 first did) claims a dependency that does not
exist, and makes rewriting a backstory invalidate artwork that would come back
byte-identical. That is precisely what splitting `appearance` out of `description` was for.

M4 generalised this and paid for the lesson twice over. There are now five such
projections, and the rule is: **hash exactly the fields that reach the prompt, and nothing
else.** Too wide charges the user to regenerate byte-identical work. Too wide *and
self-referential* charges them forever — `map_shot_characters` writes
`shots.speaker_character_id`, so keying it on the shot's whole `content_hash` meant the job
moved its own idempotency key every time it succeeded and could never be a cache hit.
`m4-decisions.md` has the full account; the cache-hit test is the only thing that found it.

**A still has more than one parent.** `shot_image_input_hash` covers the shot's visual hash
*and* the sorted `content_hash` of every character sheet the still was drawn from (spec
§4.1). That is the link that catches "the character changed" — the case that otherwise
silently ships a film where a face changes halfway through. Sorted, because the order the
sheets came back in is a query-plan detail, and a hash that depended on it would charge a
user to redraw an identical picture the first time Postgres changed its mind.

**Credits are integers and the balance is never stored.** It is `SUM(delta)` over the
append-only `credit_ledger`, read inside a transaction holding a `FOR UPDATE` lock on the
user's row — because SUM-then-INSERT is a time-of-check/time-of-use race that lets two
clicks 5 ms apart spend the same credits twice. A quote is a **ceiling**, not an estimate:
settlement refunds the difference when less work was done and absorbs the overage when
more was, so a balance can never go further negative than the number the user agreed to.

## Known gaps

Intentional scope cuts and open issues, so a reviewer doesn't mistake
them for oversights:

- **The shot/character casting pass has never run against a live language model.** Gemini's
  free tier answered 429 on every attempt during M4's verification, so the browser pass ran
  on the stub. The mapping logic, the money paths and the staleness behaviour are all
  covered — but **the quality of a real model's answer is unknown**: whether it reliably
  tells "seen in the frame" from "mentioned", and whether it returns one entry per shot.
  First thing to check when quota allows.
- **`GeminiSpeechProvider` has never made a successful call.** Unlike image generation,
  Gemini TTS has a free tier, so this is expected to work — but the PCM-to-WAV wrapping
  follows the documented `audio/L16` shape rather than an observed one, and that is the part
  most likely to be wrong. Run
  `FRAMEMELD_LIVE_SPEECH=1 pytest tests/test_providers_speech_gemini.py` and correct
  `_extract_audio` / `_to_wav` before trusting it with a user's credits.

- **`GeminiImageProvider`'s success path has never run against the live API.** The probe
  M3-D11 required did run, and found that image generation has zero free-tier quota and
  that this key's project has no billing enabled — so every call 429s. The request shape,
  error mapping and key handling are tested; the response-parsing path follows the
  documented `inlineData` shape rather than an observed one. **Enable billing and run
  `FRAMEMELD_LIVE_IMAGE=1 pytest tests/test_providers_image_gemini.py` before trusting it
  with a user's credits.** Nothing in M3 reaches it — `IMAGE_PROVIDER` defaults to `stub`.
- **No Stripe.** Credits are granted, never bought (spec §10). `grant_credits` is the only
  top-up path.
- **`<img>` asset loading depends on same-site cookies.** The frontend renders sheets with
  a plain `<img src="{API}/assets/{id}">`, which relies on the session cookie being sent
  with a subresource request. That works in development because `localhost:3000` and
  `localhost:8000` are same-site (SameSite ignores the port). **The day web and api live on
  different domains, every image will 401.** Signed URLs are the fix, and M5's render
  service needs them anyway — do not "fix" this early by loosening the cookie.
- **Storage is local disk.** Nothing outside the api can read an asset, which is exactly
  what M5's render service will need (M3-D1).
- **Moderation fails open and is off by default.** It is a gate in front of a paid provider,
  not a security boundary: a provider outage that stopped everyone generating would be a
  bigger outage than the window it closes.
- **A regenerated cast deletes the sheets you paid for.** `apply_characters` replaces rather
  than merges, and the cascade takes the images with it. The UI confirms before the click;
  there is no merge.
- **No orphan cleanup.** Bytes are written before the asset row is inserted, so a rolled-back
  transaction can leave a file nobody references. That is the intended failure direction —
  the other order leaves a row pointing at nothing, which is a 404 in a user's gallery — but
  nothing sweeps them up.
- **No add, delete, or reorder for scenes, shots or characters.** Regeneration replaces
  rather than merging.
- **No reference-sheet cache** (M4-D10). A four-character shot reads four sheets from
  storage on every still. M3's roadmap flagged a cache here as worth measuring before
  building; it has not been measured.
- **No batch job.** "Draw the N remaining" is N ordinary per-shot jobs run one at a time
  (M4-D5), so a shot that fails refunds its own credit and the rest still get their
  pictures. `settle`'s variable-units path is still unexercised and belongs to M6's
  resumable render.
- **`shot_images.seed` is declared and never set** — no configured provider accepts one —
  and `audio_clips`' `music`/`sfx` kinds and `meld` owner type are declared and never
  produced. M5's music is the first writer.
- **Dark mode is unstyled.** Every page uses fixed `text-neutral-*` colours with no
  dark-mode variants, so under a dark OS theme some text renders dark-on-dark. This affects
  every page equally and is a whole-app theming decision, not a bug in any one of them.
- **Changing `LLM_MODEL` marks every script and cast stale; changing `IMAGE_MODEL` marks
  every sheet stale.** True rather than surprising — regenerating really would produce
  something different — but it is a Meld-wide effect from an operator config change.
- **The rate limiter fails open if Redis is unreachable** (D7), and it keys on the
  socket address, not `X-Forwarded-For`. Behind a proxy, every request shares one key
  until a proxy-headers middleware is added.
- **`secure=False` on the session cookie** (`src/framemeld/auth/router.py`). Correct for
  `http://localhost`; must become `True` behind TLS before any public deploy.
- **No job cancellation.** A queued generation runs to completion; stopping the worker
  leaves the button on "Queued…" indefinitely — a known limitation, not a hang.
- Also absent, arriving in later milestones: speech and shot images (M4), the Remotion
  render service (M5), email verification and password reset (post-M6).

There is no deployment story yet — M3 has no deployment step.
