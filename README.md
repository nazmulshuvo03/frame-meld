# FrameMeld

Turn a written story into a short AI-generated film.

A **Meld** is the container a user creates and moves through five stages:
Story → Script → Cast → Shots → Film.

- **Design spec:** [docs/superpowers/specs/2026-09-09-framemeld-design.md](docs/superpowers/specs/2026-09-09-framemeld-design.md)
- **Plans:** [docs/superpowers/plans/](docs/superpowers/plans/)

## Status

**M6 (Film) complete — every milestone in the spec is implemented.** A story
becomes a script, a cast, character sheets, one still and one audio clip per
shot, an animated-stills Preview you can watch for free, a clip per shot, and
a 1920×1080 MP4 you pay credits for and download.

**One honest caveat, stated up front:** no generative model has ever been
called from this codebase. Images, speech, music and video each ship a
`Protocol`, a registry, a stub that produces the real media format, and — for
images and speech — an adapter written against the provider's documentation
that has never returned a success. The key this was built with has no billing,
and free tiers either do not exist for these models or answered 429 throughout.
So what is finished is the *system*: the staleness graph, the credit ledger,
the job pipeline, the render service and the UI are all real and verified end
to end. Swapping in a paid model is a config line, which is exactly what the
adapter boundary was for — but nobody has done it, and the first real call will
find bugs. See each milestone's decision record for what is verified and what
is not.

What's verified and how:

- The backend test suite (624 tests) passes, plus a frontend suite (45 tests)
  and a clean `tsc --noEmit` on the render service — see [Tests](#tests).
- **The migration chain builds the schema from nothing.** All nine migrations
  applied in order against an empty database, and `alembic check` reports no
  drift. The last three needed no enum widening at all: every job kind and
  media kind they write was declared a milestone or more earlier.
- **Money is enforced at the API and pinned by tests.** A hold is taken in the
  same transaction as the job row; a second click on unchanged inputs is a
  cache hit that charges nothing; too few credits is a **402** that creates no
  job; a failed generation refunds in full; and exactly one settle or refund
  may ever close a hold. A Preview render is deliberately free (spec §4.2) and
  a Final is charged.
- **The whole pipeline has been driven end to end** against a real api, worker
  and render service, on a fresh account, with every provider on its stub:
  - Story → script → cast → sheets → casting → 6 stills → speech → music,
    every job succeeding, giving a 6-shot, 21.5-second film.
  - The **Preview** rendered in 21 s at 1280×720 for **0 credits**, with the
    narration and the score in it, read by the render service over 10 signed
    asset fetches from a process with no session cookie.
  - A **Final render before any clip existed was a 422** naming how many shots
    were missing, with the balance unmoved.
  - **6 clips in 9 s for exactly 480 credits** (6 × 80), then a **Final render
    in 36 s for 60** — **1920×1080, 21.55 s, h264 + aac, 932 KB** — served as a
    download, not opened in a tab.
  - Pressing Render again with nothing changed returned `succeeded` and
    rendered nothing.
  - Rewriting one shot's motion prompt and re-animating it marked the **Final**
    stale and kept its file, while the **Preview stayed fresh** — a Preview is
    made of stills and a Final of clips, so they have genuinely different
    parents.
- Ownership checks are enforced at the API: another user's film, cast,
  character, or asset returns 404 (not 403), and a logged-out request returns
  401 — including with `?download=true`.

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
| `services/backend/src/framemeld/providers/music` | `MusicProvider` and its stub. No real adapter yet (M5-D2) |
| `services/backend/src/framemeld/providers/video` | `VideoProvider` and an ffmpeg-backed stub. No real adapter yet (M6-D9) |
| `services/backend/src/framemeld/render` | scene spec assembly, the render hashes, the HTTP client for the render service, the Film API |
| `services/render` | The Remotion render service: a scene spec in, an MP4 out. No database, no auth, no knowledge of users (spec §3.1) |
| `apps/web` | Next.js frontend — signup/login, Meld list, story editor, Script page, Cast page, Shots page, Film page |
| `docs/superpowers/specs` | Design spec |
| `docs/superpowers/plans` | Per-milestone implementation plans |
| `docs/superpowers/m1-decisions.md` … `m6-decisions.md` | Execution ledgers — the *why* behind non-obvious code |

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

# in a third terminal — the Remotion render service, required from M5 on for previews
cd services/render && npm install && npm start

# in a fourth terminal
cd apps/web && npm install && npm run dev
```

Then open <http://localhost:3000>. API docs are at <http://localhost:8000/docs>.

**Set `ASSET_SIGNING_SECRET` before rendering anything.** It has no default (M5-D1), and
without it every preview fails at the point the worker tries to build a URL the render
service can fetch.

**The render service downloads a ~113 MB headless Chrome on first start**, and says so. It
answers `/health` while that is happening, so "up" and "ready to render" are not the same
thing for the first minute or two. It listens on **8790**, not Remotion's usual port, because
8787 was already taken on the development machine — see `m5-decisions.md`.

**Only ever run one of each process.** `config.py` caches settings with `@lru_cache`, so a
server or worker left running from before an `.env` edit keeps the old values and will
happily pick up jobs with them — which looks exactly like a staleness bug. This has now
cost three milestones an hour each (see `m2-decisions.md`, `m3-decisions.md` and
`m4-decisions.md`). `taskkill` on whatever holds the port is **not** enough — `conda run`
wraps the process, so the child survives. Kill `arq.exe` and `uvicorn.exe` by name and
confirm exactly one of each. A job's `cost_usd` is the cheapest way to tell which provider
actually ran: zero means the stub.

`alembic upgrade head` applies nine migrations, in order: `7d9b40163474` (users and
sessions), `8cea41eb5150` (melds and stories), `4440a9739020` (renames
`stories.input_hash` to `content_hash`), `cd586f9b74e2` (scenes, shots, jobs),
`63b07598bddb` (credits, cast, assets, `jobs.cost_usd`), `abc2ff5e822a`
(`melds.moderation_hash`), `77f9548e4df1` (`shot_characters`, `shot_images`, `audio_clips`,
and a hand-written widening of the `job_kind` CHECK — autogenerate does not diff a CHECK
body, so a new job kind produces no migration at all unless you write it), and
`ca0a9cfdfb6b` (`renders`) and `782fe8813ebd` (`shot_clips`). The last two needed **no** enum work: `generate_music`,
`render_preview`, `AudioKind.MUSIC` and `AudioOwnerType.MELD` were all declared in their
CHECK constraints a milestone or more before anything wrote them, which is exactly the cost
`77f9548e4df1` had to pay for a value that was not.

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
- `MUSIC_PROVIDER` / `MUSIC_MODEL` — `stub`, and `stub` is the only name that resolves
  (M5-D2). No real music adapter exists; the registry raises on anything else rather than
  silently scoring a paying user's film with a sine chord.
- `VIDEO_PROVIDER` / `VIDEO_MODEL` — `stub`, likewise the only name that resolves (M6-D9).
  The stub shells out to the ffmpeg that `imageio-ffmpeg` bundles (a dev dependency), so a
  clip is a real, decodable, correctly-timed MP4 of a flat colour. It is the adapter whose
  real calls would cost dollars rather than cents, which is why the registry raises rather
  than falling back.
- `ASSET_SIGNING_SECRET` — **required before any render will work.** It signs the asset URLs
  the render service fetches (M5-D1). There is no default, deliberately: a signing key with
  a default is a signing key everybody has. Generate one with
  `python -c "import secrets; print(secrets.token_hex(32))"`.
- `RENDER_SERVICE_URL` / `PUBLIC_API_URL` / `RENDER_TIMEOUT_SECONDS` — where the render
  service answers, how it reaches this api, and how long the worker waits for one film.
  `PUBLIC_API_URL` is baked into signed URLs, so it must be an address the *render service*
  can resolve — not necessarily the one a browser uses.
- `STORAGE_BACKEND` / `STORAGE_LOCAL_ROOT` — `local` (default), writing under `./var/assets`,
  which is gitignored.
- `MODERATION_ENABLED` — `false` by default, because the stub LLM cannot classify anything.
  Any deployment reaching real users must set it `true` with a real provider configured.
- `NEXT_PUBLIC_API_URL` — read by the Next.js app (not `.env`, since it must be set in
  `apps/web`'s own environment or `apps/web/.env.local`); defaults to
  `http://localhost:8000` if unset.

## Making a film

Five stages, each its own page, each one a link forward from the last. Nothing
is ever destroyed by going back: an edit marks downstream work **out of date**
rather than deleting it, and reverting the edit makes it fresh again for free.

1. **Story** — `/melds`, then *New Meld*. Type prose into the editor. It
   autosaves ~800 ms after you stop typing; close the tab whenever.
2. **Script** — *Generate the script*. The LLM turns the story into scenes and
   shots, each with an image prompt, a motion prompt, a duration (≤ 5 s, a hard
   limit of every image-to-video model), a camera angle and a line. Every field
   is editable.
3. **Cast** — *Generate the cast*, then draw a **character sheet** for each
   person. The sheet is the visual anchor: every still that person appears in
   is drawn from it, which is what keeps a face the same across the film. Each
   character also gets a voice you can change.
4. **Shots** — *Cast the shots* first: an LLM pass works out who is in which
   frame, and that mapping is what tells each still which sheets to use. Then
   *Draw the N remaining*, and generate audio for any shot with a line. A still
   is refused if somebody in the shot has no sheet.
5. **Film** — three things, in this order:
   - *Score the film* (optional) — one instrumental track under the narration.
   - *Render the preview* — **free**, and the point of the whole design. Your
     stills animated with a slow pan and zoom, with the full audio over them.
     Watch it as often as you like; iterate on pacing, wording and shot order
     until you are happy, because none of that costs anything.
   - *Animate the N remaining*, then *Render the film*. This is the expensive
     part — 80 credits a shot — which is why the free preview comes first. The
     result is a 1920×1080 MP4 with a **Download the film** link.

Every button shows its price before you press it, a second press with nothing
changed costs nothing, and a failed generation refunds in full.

**Before you start:** signup grants 500 credits and a finished film costs
roughly 2,900, so top yourself up with `grant_credits` (above) — there is no
checkout yet.

**With the stubs (the default), nothing above calls a real model.** Sheets and
stills are flat colours, narration is a tone, the score is a chord and the
clips are solid frames. The pipeline, the timings, the money and the staleness
are all real; the pictures are not. Point `LLM_PROVIDER`, `IMAGE_PROVIDER`,
`SPEECH_PROVIDER`, `MUSIC_PROVIDER` and `VIDEO_PROVIDER` at real adapters to
change that — and read [Known gaps](#known-gaps) first, because only the LLM
adapters have ever returned a successful response.

## Tests

```bash
cd services/backend
conda run --no-capture-output -n framemeld pytest -v
conda run --no-capture-output -n framemeld ruff check src tests
conda run --no-capture-output -n framemeld ruff format --check src tests
```

`pytest` passes: **624 passed, 4 skipped**. All four skips are opt-in live-API calls:
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

`npm test` (Vitest) passes: **45 passed** — 11 on `useAutosave`, 4 on the API client's
timeout and error handling, 5 on `QuoteButton`'s affordability rules, 9 on `ShotMediaCard`,
and 16 on the Film page. `npm run build` compiles and prerenders every static route cleanly;
`/meld/[id]` and its `script`, `cast`, `shots` and `film` pages are server-rendered on
demand.

The render service has no test suite of its own — its one job is to turn a scene spec into
an MP4, and the only meaningful check is whether the file plays:

```bash
cd services/render
npm install
npx tsc --noEmit
```

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

**The render's inputs are a document, and that changes how it is hashed.** Every other
generated row keys on a hash of upstream *rows*. A render keys on the scene spec — and the
scene spec contains signed URLs, and a signature contains an expiry. Hash the document as
sent and every render is unique, so the idempotency key never matches, the cache never hits,
and a free button whose only abuse control is "an unchanged film starts no work" starts work
every time. `SceneSpec` therefore produces two documents from one walk: `to_request()`, with
signed URLs, and `to_hash_payload()`, with each artifact's `content_hash` in the same
position. They are written next to each other on purpose — the failure being guarded against
is the two drifting into describing different films.

**The render service knows nothing.** No database, no session, no users, no credits (spec
§3.1). It receives one versioned JSON document containing a signed URL for every asset it
needs and returns an MP4. That is the whole cross-language contract, and it is why the second
runtime costs so little: `services/render/src/types.ts` is the Node half of
`framemeld/render/spec.py`, kept in step by hand, because a contract with twelve fields does
not need a code generator.

**Signed asset URLs are twenty lines of HMAC, not S3.** The signature covers the asset id and
an expiry, so it opens one file for one hour and cannot be moved to another. The asset route
accepts a signature *or* a session; the signature path is checked first and is sufficient on
its own, which is why `CurrentUser` is not a dependency on that route — a dependency raises
401 before any handler code could check a signature.

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
  top-up path — and since a whole film costs ~2,900 credits against a 500-credit signup
  grant, that is no longer a convenience: it is the only way to finish one.
- **`<img>` asset loading still depends on same-site cookies.** The frontend renders
  sheets, stills and previews with a plain `<img>`/`<video src="{API}/assets/{id}">`, which
  relies on the session cookie being sent with a subresource request. That works in
  development because `localhost:3000` and `localhost:8000` are same-site (SameSite ignores
  the port). **The day web and api live on different domains, every image will 401.** M5
  built the signing that fixes this — the route accepts a signature *or* a session — but the
  frontend does not use it: it would mean the API handing the browser signed URLs for every
  thumbnail, which is a change to several list endpoints for a problem that does not exist
  yet. Do not "fix" it by loosening the cookie.
- **Storage is local disk** (M5-D1). Signed URLs, not S3, are what let the render service
  read a file, so R2 is still deferred — and the `Storage` protocol is untouched, so adding
  it is a two-file change whenever egress or multi-host deployment demands one.
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
- **No batch job**, for stills or for clips. "Draw the N remaining" and "Animate the N
  remaining" are each N ordinary per-shot jobs run one at a time (M4-D5, M6-D4), so a shot
  that fails refunds its own credit — 80 of them, for a clip — and the rest still get made.
  `settle`'s variable-units path is therefore **permanently unexercised**: every billable
  action here is one indivisible unit of work. It stays because deleting it would be a
  regression against spec §4.3, not because anything is waiting to use it.
- **No per-clip regenerate** (M6-D11). The Film page's one button queues every shot whose
  clip is missing or stale.
- **`shot_images.seed` is declared and never set** — no configured provider accepts one —
  and `audio_clips`' `sfx` kind, `RenderKind.FINAL`, and `renders`' `params` and
  `credits_spent` columns are declared and never written. M5 wrote the first `music` clip
  and the first `meld`-owned one, which M4 declared for exactly that.
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
- **No real music model exists** (M5-D2). `MUSIC_PROVIDER=stub` is the only name the
  registry resolves. The stub emits a real WAV — a four-note chord with a fade — so every
  path downstream of it is exercised, but no film has ever been scored by a model. Lyria is
  paid and this key has no billing; ACE-Step is the commercially-safe self-hosted option
  (spec §6).
- **No real video model exists either** (M6-D9), which is the biggest single gap in the
  product. Spec §9's M6 says "downloads a real AI-generated MP4"; everything in that
  sentence is built and verified except the word *AI*. The stub encodes a flat colour, so
  the clips decode, time and mux correctly and look like nothing. Wan 2.2 (spec §6) or Veo
  3.1 Lite (§6.3) is a `VIDEO_PROVIDER` away — and will be the first real call this codebase
  has ever made, so expect it to find bugs.
- **The Final composition has never rendered a real generated clip.** `OffthreadVideo`
  works and the timing is right, but on flat colours. The likeliest first bug with a real
  provider is a clip that comes back at 4.8 s where the spec asked for 5.0 and leaves a
  black frame at the cut.
- **The 80-credit clip price has never been checked against a bill** (M6-D3). It comes from
  spec §7's $6.00-of-$6.90 estimate, verified 2026-09-09, which §13 already says to re-check
  before wiring a provider up.
- **The Ken Burns numbers have never been judged against a real image.** Every frame
  rendered so far came from `solid_png`, a flat colour. The arithmetic is tested and the
  transform demonstrably applies, but whether a slow push into a real generated still reads
  as cinematic or as a wobble is a question only a real still answers. Watch one preview and
  adjust `_MOTION` in `render/spec.py` the day an image key works.
- **No preview has been rendered at the product's stated size.** The end-to-end pass was 6
  shots and 21.5 seconds; spec §1 says 10–30 shots and 1–3 minutes. `job_timeout` is
  `RENDER_TIMEOUT_SECONDS + 120` on the extrapolation that 30 shots stay well inside ten
  minutes.
- **The composition does not branch on `clip_url` yet.** The field is in the contract on
  both sides and the assembler writes `null`, but nothing in M5 can produce a clip, so the
  branch that would read one is not there — an unexercised path inside a renderer is the
  worst place to have one, because a wrong film still renders. M6 adds a conditional.
- **No render progress reaches the user.** The render service tracks a progress fraction and
  the worker reads it only to decide whether to keep waiting; the job row has nowhere to put
  it, so the button says "Assembling…" until the film appears. Honest at 21 seconds, not at
  M6's minutes.
- **No orphan sweep for render temp files.** The render service forgets an index entry 30
  minutes after it finishes and loses the lot on restart, but a file whose worker died
  between "done" and "downloaded" stays on disk.
- Also absent, and not in any milestone: everything in spec §10 (Stripe, lip-sync, burned-in
  captions, per-character LoRA training, collaboration, films over 3 minutes, a mobile app),
  plus email verification and password reset.

There is no deployment story yet. M5 adds a second runtime to deploy, which makes that a
real question rather than a deferred one: the render service needs Chrome, a writable
temp directory, and network access to the api's `PUBLIC_API_URL`.
