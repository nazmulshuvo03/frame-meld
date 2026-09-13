# M3 decision record

Same purpose as [`m1-decisions.md`](m1-decisions.md) and
[`m2-decisions.md`](m2-decisions.md): every judgement call made while executing M3, so a
future contributor can find out why without re-deriving it. Executed task by task against
[`plans/2026-09-13-m3-cast.md`](plans/2026-09-13-m3-cast.md), with the three backend gates
run after each task and a browser verification pass at the end.

## M3-D1 … M3-D12 (decided before implementation)

Recorded in full in the plan ("Decisions taken up front"). Summary:

- **M3-D1** — `assets` and object storage arrive in M3, not M4.
- **M3-D2** — `shot_characters` is deferred to M4.
- **M3-D3** — a quote is a ceiling, not an estimate.
- **M3-D4** — the balance is read under a `FOR UPDATE` lock on the user row.
- **M3-D5** — settlement is computed in retail credits, not from the provider's bill.
- **M3-D6** — adapters report a real `cost_usd`, persisted on `jobs.cost_usd`.
- **M3-D7** — character extraction reads the story, not the script.
- **M3-D8** — one image per character, `kind="sheet"`, `is_canonical=True`.
- **M3-D9** — signup grants 500 credits; `grant_credits` is the only top-up path.
- **M3-D10** — `IMAGE_PROVIDER` defaults to `stub`.
- **M3-D11** — Task 8 opens with a live probe, not with adapter code.
- **M3-D12** — `ImageProvider.generate` takes reference images as `bytes`, not URLs.

All twelve survived execution. M3-D1 and M3-D2 contradicted §9 of the spec, which has been
corrected in place; M3-D3/D5 contradicted §4.3, likewise corrected with a blockquote in the
style §4.1 already uses.

---

## The expensive knowledge: what the Task 8 probe found

M3-D11 exists because M2 paid for this lesson once already (the `responseSchema` incident).
The probe ran on 2026-09-13 against the real endpoint, and it found three things — one of
which invalidated a decision the plan had already written down.

**1. Imagen 4 is gone.** The plan's default `IMAGE_MODEL` was
`imagen-4.0-fast-generate-001`. The whole Imagen 4 family was **shut down on 2026-08-17**,
a month before this milestone. `POST /models/{model}:predict` returns 404 for every current
image model, because `:predict` was the Imagen endpoint. `IMAGE_MODEL` now defaults to
`gemini-2.5-flash-image`.

**2. `:generateContent` with `responseModalities: ["IMAGE"]` is the only candidate shape,**
and the adapter is built on it.

**3. No probe call has ever succeeded, and this is unresolved.** Every current image model
answers 429 with:

```
Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 0
```

Image generation has **no free tier**, and this key's project has **no billing enabled**.
`gemini-2.5-flash-image`, `gemini-3.1-flash-image` and `gemini-3.1-flash-lite-image` all
refuse identically, so it is the account, not the model.

**Consequence, stated plainly because it is the one piece of M3 that is not verified:**
`GeminiImageProvider`'s success path follows the *documented* `inlineData` shape rather
than an observed one — exactly the assumption M3-D11 exists to prevent. Everything that
does not depend on the success shape is tested and green (request payload, the key
travelling in a header and never in a URL, refs as inline base64, the retryable/terminal
status split, refusals from both `promptFeedback` and `finishReason`, and no error path
quoting the API key). The gap is said in the adapter's own docstring and in the skipped
live test, not left to be discovered.

**To close it:** enable billing on the key's project, then run

```bash
FRAMEMELD_LIVE_IMAGE=1 conda run --no-capture-output -n framemeld \
    pytest tests/test_providers_image_gemini.py -q
```

and correct `_extract_image` against whatever actually comes back **before** trusting this
adapter with a user's credits. Nothing in M3 reaches it: `IMAGE_PROVIDER` defaults to
`stub`, and every verification below ran against the stub.

---

## Task 7: the rate table, and three stale numbers

The task's Step 1 says to check the published prices before writing any of them down, and
warns against skipping it because the constants are pre-filled. Doing it caught three
errors in the plan's own table:

- **`gemini-flash-lite-latest` is $0.30/$2.50 per 1M tokens, not $0.10/$0.40.** Those were
  Gemini 2.5 Flash-Lite's rates; the alias now resolves elsewhere.
- **The lookup must key on the concrete model id the response carries, not on the alias
  that was requested.** Verified live: a request to `gemini-flash-lite-latest` answers with
  `modelVersion: gemini-3.5-flash-lite`. A table holding only the alias would have costed
  every real call at zero — a silent, permanent under-reporting of margin. Both are in the
  table now, with a test pinning that they agree.
- **Imagen 4's prices were removed rather than corrected** (see above): pricing a dead model
  makes a wrong number look maintained.

Verified end to end against the live API, not just by arithmetic: a real call now reports
`cost_usd = 0.0000285` for `gemini-3.5-flash-lite` at 20+9 tokens. M2's recorded debt is
paid.

---

## Defects in the plan's own test code

Five tests as written in the plan failed against correct implementations. Each was a defect
in the test, and each is fixed in the committed version:

- **Three read an ORM attribute after `db.expire_all()`** (`test_models_cast.py`,
  `test_assets.py`, `test_cast_generation.py`). Refreshing an expired attribute is
  synchronous IO, which raises `MissingGreenlet` on an `AsyncSession`. Fixed by reading the
  ids before expiring, or by re-querying.
- **One expected a flush to raise on `uq_characters_meld_order`.** That constraint is
  `DEFERRABLE INITIALLY DEFERRED` — the same shape as `uq_scenes_meld_order`, and
  deliberately so, for a future reorder — which means it is only checked at commit. Fixed
  with `SET CONSTRAINTS ALL IMMEDIATE` inside the test's transaction, which is the precise
  way to check a deferred constraint without committing.
- **One had wrong arithmetic.** `test_settling_for_less_than_quoted_refunds_the_difference`
  expected 93 where 100 − 12 + 7 = 95. The service was right.

One further plan test assumed signup issues a session cookie. It does not — only login
does — so `test_another_users_asset_is_404_not_403` was getting 401 before authorisation
was ever reached, i.e. passing for the wrong reason had the assertion been `!= 200`.

---

## Gaps the plan did not mention

**The stub LLM only knew the script schema.** Pressing Generate Cast against the default
provider failed with "The model returned a cast list in an unexpected shape" — so the
milestone's first action was broken under its own default configuration. `StubLLMProvider`
now branches on `json_schema["name"]` (the stable contract; the system prompt is prose and
gets reworded) and derives a cast from the story's proper nouns, giving each character a
*different* appearance so two sheets can never be byte-identical and hide a broken
per-character path.

The first heuristic for that — "capitalised words that are not the first word of a
sentence" — looked more principled and was much worse: in ordinary prose most character
names introduce their own sentence, so it discarded nearly every real name and fell back to
a one-character cast. Replaced with capitalised words minus a small stoplist.

**`_RejectsExplicitNull` was moved, not copied,** to `framemeld/schemas_shared.py`. Its
docstring explains a pydantic subtlety about `exclude_unset` versus explicit nulls that must
not exist in two versions that can drift apart.

**A 402 left an uncommitted job row in the session.** The hold needs a job id, so it runs
after `ensure_job`. Today that row would vanish anyway when `get_session` closes without
committing — but "nothing was created" is a guarantee about a user's money, and it should
not rest on nobody adding a `commit` later in the request. The 402 path now rolls back
explicitly.

**A flagged moderation verdict was rolled back with the failed request.** Spec §4.5 wants
flagged content *recorded* so it can be reviewed; as first written, the `FLAGGED` state died
with the 422 and every retry re-classified from scratch. The Blocked path now commits before
raising — the only thing pending at that point is the verdict itself, since the refusal
happens before any job row or hold exists.

**The moderation migration failed on a non-empty table.** Autogenerate emitted
`moderation_hash` as `NOT NULL` with no default, which cannot be added to a table that
already has rows. Corrected by hand to add it with `server_default=''` and then drop the
default, so the model stays the single source of truth for new rows. This is exactly the
"read and correct autogenerate by hand" rule in the plan's Global Constraints earning its
keep.

---

## The browser pass found two bugs that every existing test passed

Both live in the milestone's headline promise, and both are the reason Task 17 exists.

**1. Editing a character never recomputed staleness.** `update_character` changed
`content_hash` and stopped. `recompute_staleness` was only ever called from `save_story` and
from the worker, so a sheet drawn from an old `appearance` went on claiming to be fresh
forever — the single feature the Cast stage is built around, silently not happening. Fixed
by calling it from `update_character`, the way `save_story` already does.

**2. A sheet keyed on the character's whole `content_hash`.** Rewriting a backstory
therefore invalidated artwork that would have come back byte-identical — precisely what
splitting `appearance` out of `description` was for (`models/cast.py` says so in as many
words). The plan specified this, and its own Task 17 verification table contradicted it,
calling the description-edit check "the single most valuable check in the table".

The resolution is that an `input_hash` must describe *what actually produced the row*.
`sheet_prompt()` is built from `appearance` and nothing else — the image model never sees
the name, the role or the backstory — so hashing those fields in claimed a dependency that
does not exist. `visual_hash_for_character` is that narrower projection. This honours spec
§4.1 rather than bending it: downstream still keys on upstream's *current content*, just the
part of it that is upstream **of this artifact**. Widen it the day a sheet prompt starts
using another field, and every existing sheet correctly goes stale.

**3. (UI) A finished sheet only appeared after a manual page reload.** `CharacterCard`
copied `ShotCard`'s "the card owns its row after mount" pattern, which is right for text a
user is typing and wrong for `sheet` — nobody types a sheet, and a generation finishing is
precisely a refetch the user must see. The editable fields stay locally owned; `sheet` is
read from the prop.

None of the three was visible from the test suite. They are the argument for the browser
pass being a required step rather than a nicety.

---

## Process notes

**The duplicate-process trap, again.** Starting the verification found a uvicorn and an arq
worker left running from the previous evening — the uvicorn serving M2 code with no `/cast`
routes at all. m2-decisions.md recorded this exact failure mode (`@lru_cache`d settings mean
a long-running process never sees a later `.env` edit) and it recurred anyway. Killed both,
confirmed exactly one of each was running on current code, and only then trusted anything
the browser showed.

**`docker compose down -v` was not run, and the goal was met anyway.** The plan's Step 2
calls for destroying the volume to prove the migration chain builds the schema from nothing
— a gap M2 left open and explicitly asked not to leave open twice. Destroying the developer's
dev database is not necessary to prove that, so instead: created an empty
`framemeld_migrationcheck` database, ran `alembic upgrade head` against it (all six
migrations applied in order, from nothing), confirmed the 11 expected tables, ran
`alembic check` (**"No new upgrade operations detected"** — no drift between the chain and
the models), and dropped it. **The M2 gap is now closed**, non-destructively, and the check
is repeatable by anyone without losing their data.

**The worker-stopped row of the verification table was not re-verified.** It documents a
known M2 limitation (no job cancellation), not new M3 behaviour, and the attempt to exercise
it hit a cache hit instead — which turned out to be the more valuable observation, below.

---

## Browser verification (Task 17 Step 3, run end to end)

Against a real backend, a real worker, a **real Gemini call** for character extraction, and
the **stub image provider** (billing is not enabled — see the Task 8 section). A fresh
account, cold.

| Check | Result |
|---|---|
| Sign up | balance shows 500 credits |
| Open Cast with no cast | "No cast yet", Generate button reads **"Generate cast · 5 credits"** — the price is on the button face, before the click |
| Generate the cast | 2 characters via polling, no manual reload; balance 500 → 495, exactly the quote |
| Every character has name, role, description, appearance | yes, and `appearance` was purely physical with no plot in it — the extraction prompt holds |
| Generate one sheet | button showed its price first; image appeared **without a reload**; balance 466 → 454, exactly the quote |
| Two characters, two sheets | different `asset_id`s and different `sha256`s — no shared picture |
| Reload | the image is still there, served from `/assets/{id}` |
| **Edit a character's description only** | **sheet did not go stale, and nothing was charged** — the two-hash rule earning its keep |
| Edit that character's appearance | sheet shows "Out of date" **and the image is still displayed**, not deleted; no charge |
| Press Generate sheet twice quickly | one job, one image, **charged once** (454 → 442, exactly 12) |
| Regenerate with unchanged inputs | **202 with state `succeeded` and no charge** — spec §4.1's cache hit, confirmed with real money rather than reasoned about |
| Balance below the sheet price | both 12-credit buttons **disabled** with "You need 7 more credits"; the 5-credit action stayed **enabled** — affordability is per action, not global |

---

## Test counts at close

- Backend: **410 passed, 3 skipped** (the three skips are opt-in live-API calls:
  `FRAMEMELD_LIVE_LLM` for the two text adapters, `FRAMEMELD_LIVE_IMAGE` for the image one).
  `ruff check` and `ruff format --check` clean.
- Frontend: **20 passed** (11 `useAutosave`, 4 API client, 5 `QuoteButton`).
  `tsc --noEmit`, `eslint` and `next build` all clean.
- Migration chain: six migrations applied from an empty database; `alembic check` reports no
  drift.
