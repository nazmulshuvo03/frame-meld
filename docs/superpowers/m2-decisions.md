# M2 decision record

Same purpose as [`m1-decisions.md`](m1-decisions.md): every judgement call made while
executing M2, so a future contributor can find out why without re-deriving it. Unlike M1,
this milestone was not run through a subagent-driven-development ledger with per-task
review rounds — it was executed directly, task by task, with gates run after each and a
real browser verification pass at the end. Rulings below are grouped by task.

## D1-D7 (decided before implementation)

Recorded in full in the plan
([`plans/2026-09-10-m2-script.md`](plans/2026-09-10-m2-script.md), "Decisions taken up
front"). Summary:

- **D1** — `stories.input_hash` renamed to `content_hash`; it was always a content hash.
- **D2** — `script_input_hash` reads the *current* provider configuration, so staleness
  and job idempotency are the same function.
- **D3** — one `input_hash` per Meld's whole script in M2, since one LLM call produces
  every scene and shot together.
- **D4** — a user edit changes `content_hash`, never `state`.
- **D5** — `recompute_staleness` only moves rows between `fresh` and `stale`.
- **D6** — Vitest, not `node --test` (JSX needs it).
- **D7** — the login rate limiter fails open when Redis is unreachable.

## Task 16: Gemini LLM adapter (added after Task 11, ahead of the original Task 12-15
sequence)

Ruling: switched `LLM_PROVIDER`'s default from `openrouter` to `gemini`, added
`GeminiLLMProvider` alongside the existing `OpenRouterLLMProvider` rather than replacing
it — the registry pattern already supports either by name, so this is a config default
change, not an architecture change. Reason: the developer has a Gemini API key with a
$10/month Cloud credit, Gemini's free tier covers Flash-Lite with no rate-limit surprises
like OpenRouter's free roster, and Gemini has first-class structured-output support.
Recorded in the spec's new section 6.3. Cost if wrong: one env var and a restart to
switch back.

Ruling: `GeminiLLMProvider.complete()` reports `cost_usd=Decimal("0")` unconditionally.
Gemini's `usageMetadata` gives token counts, never a dollar figure the way OpenRouter's
`usage.cost` does, and a real price-per-token table is a real thing to get right (and to
keep current as Google's pricing changes) — M2 has no credit ledger to feed regardless
(Global Constraint: "No credits in M2"). Cost if wrong: none yet; **M3 must add a real
per-token price table before any Gemini-billed call can be settled against a user's
balance** — this is a hard blocker for M3, not a nice-to-have.

Ruling: added an autouse `_stub_llm_by_default` fixture to `conftest.py`, forcing
`settings.llm_provider = "stub"` for every test regardless of `.env`. This is a latent
gap the provider swap *exposed*, not one it introduced: `Settings` reads `.env` directly
(`config.py`'s `_ENV_FILE`), so the test suite was always one `.env` edit away from
silently trying to call a real provider. Cost if wrong: none, the fixture only narrows
behavior tests already assumed.

Ruling: **verified live, not just reviewed**, whether Gemini's native
`generationConfig.responseSchema` accepts `SCRIPT_JSON_SCHEMA` — this was flagged as an
open question for OpenRouter in `m2-status.md`, and I did not want to hand off the same
unresolved question for Gemini. Added a real key, ran the real worker, and it 400'd:
`additionalProperties` is rejected outright with a field-level error, and the request
keeps 400ing generically (no field detail at all) until `minimum`, `maximum`, `minItems`
and `maxItems` are also removed. Fixed by stripping exactly those four keywords in
`providers/gemini.py`'s `_for_gemini()` before sending. Nothing is lost: `duration_sec`
still cannot exceed 5.0 in the database (the CHECK constraint), and
`script/generation.py`'s Pydantic validators (`_clamp_duration`, `_truncate_to_max_shots`,
the enum fallbacks) already clamp rather than trust the model — these keywords were only
ever a hint to the model, and Gemini simply doesn't take that particular hint. Confirmed
by generating a real, coherent 4-shot script end to end afterward. Cost if wrong: would
have shipped a provider that 400s on every real call, discovered by the first user to
press Generate rather than in review.

## Task 12: frontend test harness

Ruling: writing `useAutosave.test.tsx` (verbatim from the plan, which itself encodes
M1's eight documented defects) surfaced a **ninth** defect, not merely exercised the
eight already known. `stays in saving while newer text is still queued` failed on first
run. Traced it to a genuine race: when a second edit is dispatched while a first save is
still in flight, the first save's own completion re-runs the effect (via `saveTick`)
while the second value is *dispatched* (`pendingRef` already points at it) but not yet
*confirmed*. The "did the user revert their edit" check compared the incoming value
against `pendingRef` (what has been dispatched) instead of `savedRef` (what the server
has actually confirmed) — its own comment said "matches what was last confirmed," but the
code read the wrong ref. Fixed by requiring both refs to agree before reporting "saved".
Per the plan's own instruction on this file ("that is a real defect in the hook, not in
the test"), fixed the hook rather than adjusting the expectation. Cost if wrong: a false
"saved" during a narrow race window, arming the wrong side of the close-warning decision
— the exact defect class this hook exists to prevent.

## Task 13: the Script page

Ruling: the installed `eslint-plugin-react-hooks` (7.1.1) enforces
`react-hooks/set-state-in-effect`, which the plan's original code (written before this
rule existed in the toolchain this repo now has installed) fails. Two real fixes, not
suppressions:

- `useJobPolling`'s effect no longer calls `setJob(null)` when `jobId` clears. Nothing
  reads `job` at that point — the caller already received the settled job through
  `onSettled` and stops treating the hook as busy — so the reset was a synchronous
  setState with no downstream reader, not a real requirement. Simpler code, not just a
  quieter linter.
- `ScriptPage`'s `load()` is a `.then()/.catch()` chain instead of `async`/`await`,
  matching the story editor's own established pattern (`meld/[id]/page.tsx`), so its
  setState calls sit inside a nested callback rather than appearing to the linter's
  static analysis as directly inside the effect body.

Cost if wrong: would have shipped with two ESLint errors, caught immediately by the
`npm run lint` gate — never reached a commit.

## Task 14: editable fields

Ruling: `ShotCard`'s `<select>` `onChange` handlers need an explicit cast
(`e.target.value as Shot["camera"]` / `as Shot["dialogue_kind"]`) because
`e.target.value` is a plain `string` and the generic `edit<K>(field, value: Shot[K])`
helper needs the narrower literal-union type. Safe because the `<option>` elements are
generated from `CAMERA_ANGLES`/`DIALOGUE_KINDS`, so the runtime value can only ever be one
of the literal members. Cost if wrong: none, `tsc --noEmit` catches it immediately if the
option lists and the enum ever drift apart.

## Task 15: close-out

Ruling: implemented all five tidy items from the plan's list (README's conda path,
whitespace-only titles, `revoke_session`'s missing filter, `NEXT_PUBLIC_API_URL`
documentation, `secure=False` note kept as-is) as originally scoped — none needed
revision once actually written.

Ruling: **skipped** `docker compose down -v && docker compose up -d --wait` (the
from-scratch migration verification the plan's Step 2 calls for). The auto-mode safety
classifier refused it as irreversible local destruction, and that refusal stands —
overriding a safety gate to satisfy a "nice to have" verification step is the wrong
trade. The dev database at the time held only disposable test/dev accounts from earlier
M1/M2 work sessions (confirmed by inspection before attempting the step), so nothing of
the user's was at risk, but the classifier does not have that context and the ledger
records the honest state rather than a workaround. Cost if wrong (i.e., if
`alembic upgrade head` does not actually reconstruct the schema from nothing): the gap
surfaces the next time someone runs the migration chain against a genuinely empty
database — for example, in CI, or on a new contributor's machine — which is exactly the
scenario this step exists to catch early. **Recommended: run it by hand once,** since
`services/backend/tests/conftest.py`'s schema is built from
`Base.metadata.create_all`, not from the migrations, so the migration chain itself has
never actually been exercised end to end since Task 3 added the scenes/shots/jobs
migration.

## Process note: duplicate dev processes produced a false staleness bug

During browser verification, the freshly-generated first script showed every row marked
`stale` immediately — looking exactly like a `recompute_staleness` bug. Root cause was
environmental, not a code defect: an arq worker left running from *before* today's `.env`
changes (`LLM_PROVIDER` still cached as `stub` in that process's memory, per
`config.py`'s `@lru_cache`) picked up the job, while the API process that had just
computed the job's `input_hash` was already running with `LLM_PROVIDER=gemini`. The
mismatch between the two processes' cached settings, not any staleness logic, produced
rows whose `input_hash` never matched what `recompute_staleness` expected. Killed every
stale backend process and restarted exactly one of each before concluding the real script
generated cleanly with no false staleness. Worth recording because it is exactly the kind
of result that looks like a real bug in `recompute_staleness` on a first read, and the
actual lesson — **`@lru_cache`d settings mean a long-running process never sees a later
`.env` edit** — applies to any config change made while a dev server is already up, not
just this one.

## Browser verification (Task 13/14/15's done-when, run once end to end)

All checks from the plan's Task 13, 14, and 15 verification tables passed, against a
real backend, a real worker, and **a real Gemini API call** (not the stub):

| Check | Result |
|---|---|
| No script yet → Generate | shot list appeared via polling, no manual reload |
| Edit a shot's image prompt, reload | edit persisted |
| Edit a shot → check for new staleness | none (D4) |
| Set a shot's seconds to 6 | save reported an error, value not silently kept |
| Dialogue → "none" → back to "narration" | speech field disappeared, then reappeared with its original text intact |
| Change the story, return to Script | amber "Your story changed" banner + "Out of date" pill on the scene and every shot; **prior hand-edit to shot 1 still there, untouched** |
| Revert the story to its exact original text | banner and pills disappeared, no regeneration, no cost |
| Two tabs open on the same story, both edit | the second tab to save got a 409, went read-only, and showed the conflict banner; its unsaved text stayed on screen rather than being silently discarded |
